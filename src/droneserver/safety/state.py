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

from droneserver.telemetry import ground_stream
from droneserver.telemetry.ground import IN_THE_AIR_STATES, height_above_launch_m
from droneserver.telemetry.home import read_home


@dataclass
class FlightState:
    """Server-side view of the vehicle, refreshed on demand."""

    armed: bool = False
    in_air: bool = False
    unknown: bool = True
    home: tuple[float, float] | None = None
    #: Elevation of the autopilot's HOME. Careful: ArduPilot moves home to
    #: wherever the aircraft last armed, so this datum travels with the flight.
    #: Prefer ``session_launch_amsl_m`` wherever a stable ground reference is
    #: needed - this is the fallback, kept because it is the only other thing
    #: there is (see :mod:`droneserver.telemetry.ground`).
    home_altitude_m: float | None = None
    #: Elevation of the point this SESSION started from, taken from the
    #: connector's ``session_launch`` (recorded at link-up, before anything
    #: armed, and never overwritten). This one does not move.
    session_launch_amsl_m: float | None = None
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
            "session_launch_amsl_m": self.session_launch_amsl_m,
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

        Once is enough only while the request survives. When one of these
        one-shot requests is lost the topic goes silent for the rest of the
        connection, so the READS re-request it themselves:
        :mod:`droneserver.telemetry.ground_stream` for the two ground topics
        and :func:`droneserver.telemetry.home.read_home` for home (FIX 15).
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

    def note_session_launch(self, session_launch: dict | None) -> None:
        """Adopt the connector's launch elevation. First writer wins.

        The connector records it once, at link-up, before anything armed, so it
        is the elevation of the ground the aircraft was standing on when this
        session began. Never overwritten here for the same reason it is never
        overwritten there: a datum that follows the aircraft is the defect this
        exists to avoid.
        """
        if self.state.session_launch_amsl_m is not None or not session_launch:
            return
        amsl = session_launch.get("absolute_altitude_m")
        if amsl is not None:
            self.state.session_launch_amsl_m = float(amsl)

    def reanchor_session_launch(self, session_launch: dict | None) -> None:
        """Adopt a launch elevation that was moved DELIBERATELY (FIX 13).

        The counterpart to :meth:`note_session_launch`'s first-writer-wins, and
        the only thing that may overwrite the datum. The connector refuses to
        move it for anything the aircraft does; the trial layer moves it when
        it has parked the aircraft on the point the next flight starts from,
        and this layer must measure the ceiling from the same point or the two
        would disagree about how high the vehicle is.
        """
        if not session_launch:
            return
        amsl = session_launch.get("absolute_altitude_m")
        if amsl is not None:
            self.state.session_launch_amsl_m = float(amsl)

    async def refresh(self, drone, ttl_s: float, timeout_s: float = 8.0, session_launch: dict | None = None) -> dict:
        """Return a state snapshot, refreshing from telemetry if stale.

        Topics are read independently and degrade gracefully: on ArduPilot
        ``in_air`` and ``home`` can take several seconds to emit their first
        sample, and ``in_air`` is not published at all on some setups, so it
        falls back to ``landed_state`` and then to altitude. The snapshot is
        only marked ``unknown`` when the vehicle's armed state - the one fact
        every precondition needs - cannot be read.

        ``session_launch`` is the connector's launch record, threaded through so
        heights can be measured against a datum the autopilot cannot move.
        """
        self.note_session_launch(session_launch)
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

            position_ok = False
            with contextlib.suppress(Exception):
                position = await _first(drone.telemetry.position(), timeout_s)
                position_ok = True
                # Raw capture, both frames. Nothing here compares them: the
                # rules ask validation._current_height_m, which prefers the
                # absolute reading against session_launch_amsl_m and keeps the
                # relative one only as the fallback for a vehicle that has no
                # launch elevation recorded.
                self.state.position = {
                    "latitude_deg": position.latitude_deg,
                    "longitude_deg": position.longitude_deg,
                    "relative_altitude_m": position.relative_altitude_m,
                    "absolute_altitude_m": position.absolute_altitude_m,
                }

            in_air = await _read_in_air(drone, timeout_s, self.state.session_launch_amsl_m, link_live=position_ok)
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


async def _read_in_air(
    drone, timeout_s: float, launch_amsl_m: float | None = None, link_live: bool | None = None
) -> bool | None:
    """Is the vehicle airborne? Tries in_air, then landed_state, then altitude.

    Returns None when none of the three answer (state genuinely unknown).

    The order is deliberate and is NOT
    :func:`droneserver.telemetry.ground.ground_evidence`'s: ``in_air`` is the
    direct answer to the question this function asks, it is the order this
    layer has always used, and where the two topics disagree (a bounce, a
    hard landing) the established reading decides. Both are the autopilot's
    own assessment, so neither moves with an arming.

    The third fallback did move. It compared ``relative_altitude_m`` - measured
    from wherever the aircraft last ARMED - against 1 m, so a parked aircraft
    carrying a +4.1 m datum offset (measured 2026-08-19) read as flying, and
    the navigation preconditions that require "airborne" would have let a
    phantom return fly from a vehicle standing on its pad. It is now measured
    against the session's launch elevation where one is known.

    It is still the LAST resort: a height above the launch field is not a
    height above the ground the aircraft is actually standing on, so over
    different terrain it can still be wrong. The two topics above are the
    evidence; this only speaks when both are silent.

    Both topics are read through
    :mod:`droneserver.telemetry.ground_stream`, which re-requests a stream that
    has gone silent while the rest of the link is live (FIX 15). Without it a
    single lost SET_MESSAGE_INTERVAL retires BOTH of the evidence-bearing
    answers for the rest of the connection, and every state refresh falls
    through to the altitude fallback below - the one reading in the list that
    can be wrong.
    """
    in_air = await ground_stream.read_in_air(drone, timeout_s, link_live=link_live)
    if in_air is not None:
        return in_air
    name = await ground_stream.read_landed_state(drone, timeout_s, link_live=link_live)
    if name in IN_THE_AIR_STATES:
        return True
    if name == "ON_GROUND":
        return False
    try:
        position = await _first(drone.telemetry.position(), timeout_s)
        height = height_above_launch_m(
            launch_amsl_m,
            getattr(position, "absolute_altitude_m", None),
            position.relative_altitude_m,
        )
        return None if height is None else height > 1.0
    except Exception:
        return None
    return None


async def _first(stream, timeout_s: float):
    async def read():
        async for item in stream:
            return item
        raise TimeoutError("stream ended")

    return await asyncio.wait_for(read(), timeout=timeout_s)
