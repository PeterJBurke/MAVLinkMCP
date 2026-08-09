"""Vehicle-state snapshot used by the precondition rules.

Reading telemetry on every tool call would add a MAVLink round-trip to each
one, so state is cached for ``state_cache_ttl_s`` (default 2 s). Two facts the
autopilot cannot tell us are tracked from our own command history:

- ``seconds_since_takeoff`` - when *we* commanded takeoff (the settling window)
- ``mission_uploaded``      - whether a mission was uploaded in this session

If telemetry cannot be read the snapshot is marked ``unknown`` and the
precondition rules apply the configured fail-open/fail-closed policy.
"""

import asyncio
import contextlib
import time
from dataclasses import dataclass, field

from droneserver.telemetry.home import read_home


@dataclass
class FlightState:
    """Server-side view of the vehicle, refreshed on demand."""

    armed: bool = False
    in_air: bool = False
    unknown: bool = True
    home: tuple[float, float] | None = None
    home_altitude_m: float | None = None
    #: Live position - required to fence offset/velocity commands.
    position: dict | None = None
    fetched_at: float = 0.0

    takeoff_commanded_at: float | None = field(default=None)
    mission_uploaded: bool | None = field(default=None)

    def snapshot(self) -> dict:
        since = None
        if self.takeoff_commanded_at is not None:
            since = time.monotonic() - self.takeoff_commanded_at
        return {
            "armed": self.armed,
            "in_air": self.in_air,
            "unknown": self.unknown,
            "home": self.home,
            "home_altitude_m": self.home_altitude_m,
            "position": self.position,
            "seconds_since_takeoff": since,
            "mission_uploaded": self.mission_uploaded,
        }


class StateTracker:
    """Caches telemetry-derived state and records our own command history."""

    def __init__(self) -> None:
        self.state = FlightState()
        self._lock = asyncio.Lock()
        self._rates_requested = False
        self._home_attempted_at = 0.0

    async def _ensure_rates(self, drone, timeout_s: float = 5.0) -> None:
        """Ask the autopilot to stream the topics the preconditions need.

        ArduPilot does not publish in_air / landed_state / home until asked;
        without this the first read of each takes seconds or times out (a
        25 s state refresh, measured). Best effort, requested once.
        """
        if self._rates_requested:
            return
        self._rates_requested = True
        for setter, hz in (
            ("set_rate_in_air", 2.0),
            ("set_rate_landed_state", 2.0),
            ("set_rate_home", 1.0),
        ):
            try:
                await asyncio.wait_for(getattr(drone.telemetry, setter)(hz), timeout=timeout_s)
            except Exception:
                pass  # firmware may not support this topic; the fallbacks cover it

    async def refresh(self, drone, ttl_s: float, timeout_s: float = 8.0) -> dict:
        """Return a state snapshot, refreshing from telemetry if stale.

        Topics are read independently and degrade gracefully: on ArduPilot
        ``in_air`` and ``home`` can take several seconds to emit their first
        sample, and ``in_air`` is not published at all on some setups, so it
        falls back to ``landed_state`` and then to relative altitude. The
        snapshot is only marked ``unknown`` when the vehicle's armed state -
        the one fact every precondition needs - cannot be read.
        """
        now = time.monotonic()
        if drone is None:
            self.state.unknown = True
            return self.state.snapshot()
        if (now - self.state.fetched_at) < ttl_s and not self.state.unknown:
            return self.state.snapshot()

        async with self._lock:
            now = time.monotonic()
            if (now - self.state.fetched_at) < ttl_s and not self.state.unknown:
                return self.state.snapshot()
            await self._ensure_rates(drone)
            try:
                self.state.armed = bool(await _first(drone.telemetry.armed(), timeout_s))
            except Exception:
                self.state.unknown = True
                return self.state.snapshot()

            with contextlib.suppress(Exception):
                position = await _first(drone.telemetry.position(), timeout_s)
                self.state.position = {
                    "latitude_deg": position.latitude_deg,
                    "longitude_deg": position.longitude_deg,
                    "relative_altitude_m": position.relative_altitude_m,
                    "absolute_altitude_m": position.absolute_altitude_m,
                }

            in_air = await _read_in_air(drone, timeout_s)
            if in_air is None:
                # Cannot tell whether we are flying. Treat as unknown so the
                # configured fail-open/fail-closed policy decides, rather than
                # silently assuming "on the ground".
                self.state.unknown = True
                return self.state.snapshot()
            self.state.in_air = in_air
            self.state.unknown = False

            # Home is optional (radius fence + AMSL conversion). Retry at most
            # every 30 s so a missing home cannot add a timeout to every call.
            if self.state.home is None and (time.monotonic() - self._home_attempted_at) > 30.0:
                self._home_attempted_at = time.monotonic()
                try:
                    # read_home re-requests the topic if the subscription is
                    # silent - _ensure_rates asks once per connection, and an
                    # autopilot that reconnected since then has forgotten it.
                    home = await read_home(drone, timeout_s)
                    self.state.home = (home.latitude_deg, home.longitude_deg)
                    self.state.home_altitude_m = home.absolute_altitude_m
                except Exception:
                    pass
            # Stamp freshness AFTER every read, so a slow read does not make
            # the snapshot instantly stale again.
            self.state.fetched_at = time.monotonic()
        return self.state.snapshot()

    # ---- command history (recorded by the middleware after execution) ----

    def note_takeoff(self) -> None:
        self.state.takeoff_commanded_at = time.monotonic()
        self.state.fetched_at = 0.0  # force a telemetry refresh next call

    def note_landed(self) -> None:
        self.state.takeoff_commanded_at = None
        self.state.fetched_at = 0.0

    def note_mission_uploaded(self, uploaded: bool = True) -> None:
        self.state.mission_uploaded = uploaded

    def invalidate(self) -> None:
        self.state.fetched_at = 0.0

    def reset(self) -> None:
        self.state = FlightState()
        self._rates_requested = False
        self._home_attempted_at = 0.0


async def _read_in_air(drone, timeout_s: float) -> bool | None:
    """Is the vehicle airborne? Tries in_air, then landed_state, then altitude.

    Returns None when none of the three answer (state genuinely unknown).
    """
    try:
        return bool(await _first(drone.telemetry.in_air(), timeout_s))
    except Exception:
        pass
    try:
        landed = await _first(drone.telemetry.landed_state(), timeout_s)
        name = str(landed).rsplit(".", 1)[-1].upper()
        if name in ("IN_AIR", "TAKING_OFF", "LANDING"):
            return True
        if name == "ON_GROUND":
            return False
    except Exception:
        pass
    try:
        position = await _first(drone.telemetry.position(), timeout_s)
        return position.relative_altitude_m > 1.0
    except Exception:
        return None
    return None


async def _first(stream, timeout_s: float):
    async def read():
        async for item in stream:
            return item
        raise TimeoutError("stream ended")

    return await asyncio.wait_for(read(), timeout=timeout_s)
