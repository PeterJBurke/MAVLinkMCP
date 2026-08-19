"""FIX 10: a landing must not be confirmed by a datum the autopilot moves.

The T6-shape validation gate found it on 2026-08-19 and reproduced it on eight
independent fresh SITL lanes. ``monitor_flight``'s touchdown confirmation read::

    if landed_state_str == "ON_GROUND" and not is_in_air and current_alt < 2.0:

``current_alt`` is ``relative_altitude_m``, and ArduPilot re-zeroes that datum
wherever the aircraft last ARMED. The T6 shape - fly out, land at the
destination, RE-ARM there, fly home - therefore returns an aircraft whose
relative altitude reads the terrain difference between the two arming points
(+4.1 m observed) forever, parked and disarmed included. The ``< 2.0`` gate was
unreachable, every such landing ran the full 120 s ``landing_timeout``, and
completion was never confirmed. That is the ``landing_timeout`` the T6 audit
(Research/T6-FAILURE-AUDIT_2026-08-19.md §9 item 1) could not explain.

It is the OPERATIONAL twin of the scorer defect fixed in 33de5ec (FIX 8b),
where ``verdicts.py`` measured final height from the same moving datum and
failed four aircraft that had landed exactly where they started. The scorer
consumer was fixed then; this one was not.

Touchdown is now the autopilot's own on-ground evidence - ``landed_state`` and
``in_air``, with a vertical-rate sanity check - held across the existing 3 s
stability re-check. No altitude term. The 120 s backstop stays.
"""

from __future__ import annotations

import asyncio
import types

import pytest

from droneserver.tools import action

#: The launch field and Foothill Regional Medical Center, from the T6 campaign.
LAUNCH = (33.7434897, -117.8328829)
LAUNCH_AMSL = 41.3
HOSPITAL = (33.7302219, -117.8284659)

#: What the aircraft reads when it re-armed 4.1 m below the launch field and
#: then flew home: parked ON the launch field, relative altitude stuck at +4.1.
DATUM_OFFSET_M = 4.1


class _LandedState:
    def __init__(self, name):
        self.name = name

    def __str__(self):
        return f"LandedState.{self.name}"


class _Telemetry:
    """One frozen telemetry state, served on every subscription."""

    def __init__(
        self,
        landed_state,
        in_air,
        rel_alt,
        lat,
        lon,
        abs_alt=LAUNCH_AMSL,
        down=0.0,
        mode="LAND",
    ):
        self._landed_state, self._in_air, self._alt = landed_state, in_air, rel_alt
        self._lat, self._lon, self._abs, self._down, self._mode = lat, lon, abs_alt, down, mode

    async def landed_state(self):
        yield _LandedState(self._landed_state)

    async def in_air(self):
        yield self._in_air

    async def position(self):
        yield types.SimpleNamespace(
            latitude_deg=self._lat,
            longitude_deg=self._lon,
            relative_altitude_m=self._alt,
            absolute_altitude_m=self._abs,
        )

    async def velocity_ned(self):
        yield types.SimpleNamespace(north_m_s=0.0, east_m_s=0.0, down_m_s=self._down)

    async def flight_mode(self):
        yield self._mode


class _Action:
    def __init__(self):
        self.landings = 0

    async def land(self):
        self.landings += 1


class _Connector:
    def __init__(self, telemetry, session_launch_amsl=LAUNCH_AMSL):
        self.drone = types.SimpleNamespace(telemetry=telemetry, action=_Action())
        self.pending_destination = None
        self.landing_in_progress = False
        self.was_airborne = False
        self.last_movement = None
        self.session_launch = {
            "latitude_deg": LAUNCH[0],
            "longitude_deg": LAUNCH[1],
            "absolute_altitude_m": session_launch_amsl,
            "source": "autopilot home when the link came up",
        }


def _ctx(connector):
    return types.SimpleNamespace(request_context=types.SimpleNamespace(lifespan_context=connector))


def _bound_for(connector, point, label="the commanded destination"):
    """Put the connector mid-flight to ``point`` with auto-land pending."""
    connector.was_airborne = True
    connector.pending_destination = {
        "latitude": point[0],
        "longitude": point[1],
        "label": label,
        "altitude_msl": 80.0,
        "initial_distance": 1500.0,
        "start_time": 0.0,
        "source": "go_to_location",
    }
    connector.last_movement = {
        "tool": "go_to_location",
        "target": {"latitude": point[0], "longitude": point[1], "label": label},
        "commanded_at": 0.0,
    }


monitor_flight = action.monitor_flight.__wrapped__


@pytest.fixture(autouse=True)
def _always_connected(monkeypatch):
    async def ok(_connector, timeout=30.0):
        return True

    monkeypatch.setattr(action, "ensure_connection", ok)


class _Clock:
    """A stand-in for the event loop's clock that only moves when we sleep.

    The landing loop's 120 s budget is spent against ``get_event_loop().time()``,
    so collapsing the sleeps alone would leave a timeout test spinning for two
    real minutes. Here a slept second is a second.
    """

    def __init__(self):
        self.now = 0.0

    def time(self):
        return self.now


@pytest.fixture
def _no_waiting(monkeypatch):
    """Collapse every sleep, and move the clock by what was slept."""
    real_sleep = asyncio.sleep
    clock = _Clock()
    slept: list[float] = []

    async def fast(seconds):
        slept.append(seconds)
        clock.now += seconds
        await real_sleep(0)

    monkeypatch.setattr(action.asyncio, "sleep", fast)
    monkeypatch.setattr(action, "_monotonic_s", clock.time)
    return slept


# ------------------------------------------------------- the helpers themselves


def test_height_is_measured_from_the_session_launch_not_the_arm_point():
    """+4.1 m of relative altitude on an aircraft parked where it started."""
    connector = _Connector(None)
    reading = {"relative_altitude_m": DATUM_OFFSET_M, "absolute_altitude_m": LAUNCH_AMSL}
    assert action._height_above_launch_m(connector, reading) == pytest.approx(0.0)


def test_height_falls_back_to_the_relative_reading_when_there_is_no_launch_elevation():
    """An older session, or one that never got a fix, behaves exactly as before."""
    connector = _Connector(None, session_launch_amsl=None)
    reading = {"relative_altitude_m": 12.0, "absolute_altitude_m": 53.3}
    assert action._height_above_launch_m(connector, reading) == 12.0

    connector.session_launch = None
    assert action._height_above_launch_m(connector, reading) == 12.0


def test_ground_evidence_prefers_the_autopilots_own_answer():
    assert action._ground_evidence({"landed_state": "ON_GROUND", "in_air": True}) is True
    assert action._ground_evidence({"landed_state": "IN_AIR", "in_air": False}) is False
    assert action._ground_evidence({"landed_state": "LANDING", "in_air": True}) is False
    # No landed_state: in_air answers.
    assert action._ground_evidence({"landed_state": None, "in_air": False}) is True
    assert action._ground_evidence({"landed_state": None, "in_air": True}) is False
    # Neither readable: no answer, rather than a guess of "on the ground".
    assert action._ground_evidence({"landed_state": None, "in_air": None}) is None


def test_touchdown_needs_no_altitude_at_all():
    """The +4.1 m offset that made the old gate unreachable is simply not read."""
    assert action._settled_on_ground("ON_GROUND", False, 0.0) is True
    assert action._settled_on_ground("ON_GROUND", False, None) is True
    assert action._settled_on_ground("ON_GROUND", True, 0.0) is False
    assert action._settled_on_ground("IN_AIR", False, 0.0) is False
    assert action._settled_on_ground("LANDING", False, 0.0) is False
    assert action._settled_on_ground(None, False, 0.0) is False
    # An autopilot claiming ON_GROUND mid-descent is not believed.
    assert action._settled_on_ground("ON_GROUND", False, -3.0) is False
    # Settling noise is not a descent.
    assert action._settled_on_ground("ON_GROUND", False, -0.2) is True


# ------------------------------------------------------------ (1) the bug itself


async def test_touchdown_is_confirmed_with_a_moved_datum(_no_waiting):
    """THE BUG. Landed at the destination, relative altitude stuck at +4.1 m.

    Under the old gate this loop ran 120 s and returned ``landing_timeout``.
    """

    class _Arriving(_Telemetry):
        """Hovering over the destination, then down - reading +4.1 m throughout."""

        def __init__(self):
            super().__init__("IN_AIR", True, 18.7 + DATUM_OFFSET_M, HOSPITAL[0], HOSPITAL[1], abs_alt=60.0)
            self.reads = 0

        async def landed_state(self):
            self.reads += 1
            if self.reads > 2:
                self._landed_state, self._in_air = "ON_GROUND", False
                # The datum offset survives the landing: this is the whole bug.
                self._alt, self._abs, self._down = DATUM_OFFSET_M, LAUNCH_AMSL, 0.0
            async for item in super().landed_state():
                yield item

    connector = _Connector(_Arriving())
    _bound_for(connector, HOSPITAL)

    result = await monitor_flight(_ctx(connector), auto_land=True)

    assert result["status"] == "landed", result
    assert result["mission_complete"] is True
    assert "LANDED AT THE COMMANDED DESTINATION" in result["DISPLAY_TO_USER"]
    # The raw reading is still 4.1 m; the datum-free one is what says "down".
    assert result["altitude_m"] == pytest.approx(DATUM_OFFSET_M, abs=0.05)
    assert result["height_above_launch_m"] == pytest.approx(0.0, abs=0.05)
    assert connector.landing_in_progress is False
    assert connector.drone.action.landings == 1


async def test_the_old_gate_would_have_timed_out_on_that_same_aircraft():
    """Pins WHY the fix was needed, so nobody reintroduces the altitude term."""
    parked_but_offset = DATUM_OFFSET_M
    assert not (parked_but_offset < 2.0), "the old confirmation threshold, on a landed aircraft"
    assert action._settled_on_ground("ON_GROUND", False, 0.0) is True


async def test_a_landing_that_really_does_hang_still_times_out(_no_waiting):
    """The 120 s backstop is untouched: an aircraft that never lands says so."""

    class _NeverLands(_Telemetry):
        def __init__(self):
            super().__init__("IN_AIR", True, 25.0, HOSPITAL[0], HOSPITAL[1], abs_alt=66.3, down=0.0)

    connector = _Connector(_NeverLands())
    _bound_for(connector, HOSPITAL)

    result = await monitor_flight(_ctx(connector), auto_land=True)

    assert result["status"] == "landing_timeout"
    assert result["mission_complete"] is False


# --------------------------------------- (2) low is not the same as landed


async def test_a_low_hovering_aircraft_is_not_confirmed_landed(_no_waiting):
    """1.2 m up and flying. The autopilot says IN_AIR; that settles it."""

    class _LowHover(_Telemetry):
        def __init__(self):
            super().__init__("IN_AIR", True, 1.2, HOSPITAL[0], HOSPITAL[1], abs_alt=LAUNCH_AMSL + 1.2)

    connector = _Connector(_LowHover())
    _bound_for(connector, HOSPITAL)

    result = await monitor_flight(_ctx(connector), auto_land=True)

    assert result["status"] == "landing_timeout", "a hovering aircraft must never be called landed"
    assert result["mission_complete"] is False


async def test_an_aircraft_still_descending_is_not_confirmed_landed(_no_waiting):
    """ON_GROUND asserted at 3 m/s down is a contradiction, not a touchdown."""

    class _Descending(_Telemetry):
        """Over the destination, then claiming ON_GROUND while still falling."""

        def __init__(self):
            super().__init__("IN_AIR", True, 40.0, HOSPITAL[0], HOSPITAL[1], abs_alt=81.3, down=3.0)
            self.reads = 0

        async def landed_state(self):
            self.reads += 1
            if self.reads > 1:
                self._landed_state, self._in_air, self._alt = "ON_GROUND", False, 0.4
            async for item in super().landed_state():
                yield item

    connector = _Connector(_Descending())
    _bound_for(connector, HOSPITAL)

    result = await monitor_flight(_ctx(connector), auto_land=True)

    assert result["status"] == "landing_timeout"
    assert result["mission_complete"] is False


async def test_a_touchdown_that_does_not_hold_is_not_confirmed(_no_waiting):
    """It bounced: ON_GROUND once, airborne again at the 3 s re-check."""

    class _Bouncing(_Telemetry):
        def __init__(self):
            super().__init__("IN_AIR", True, 15.0, HOSPITAL[0], HOSPITAL[1], abs_alt=56.3)
            self.reads = 0

        async def landed_state(self):
            self.reads += 1
            if self.reads > 1:
                # Down on the detecting read, airborne again on the confirming
                # read that follows it, forever.
                self._landed_state = "ON_GROUND" if self.reads % 2 == 0 else "IN_AIR"
                self._in_air = self._landed_state == "IN_AIR"
                self._alt = 0.3 if self._in_air is False else 1.5
            async for item in super().landed_state():
                yield item

    connector = _Connector(_Bouncing())
    _bound_for(connector, HOSPITAL)

    result = await monitor_flight(_ctx(connector), auto_land=True)

    assert result["status"] == "landing_timeout"
    assert result["mission_complete"] is False


# ------------------------------- (3) the FIX 6 latches are not weakened by this


async def test_the_never_armed_latch_survives_a_moved_datum():
    """A parked aircraft reading +4.1 m has still not flown.

    The old ``current_alt >= 1.0`` latch would have set was_airborne here on the
    FIRST poll and then answered the next one "landed" - the exact gemma-4-e4b
    regression (e5eb448), resurrected by the datum offset.
    """
    connector = _Connector(_Telemetry("ON_GROUND", False, DATUM_OFFSET_M, LAUNCH[0], LAUNCH[1], mode="STABILIZE"))

    result = await monitor_flight(_ctx(connector))

    assert result["status"] == "not_started"
    assert result["mission_complete"] is False
    assert connector.was_airborne is False
    assert result["height_above_launch_m"] == pytest.approx(0.0, abs=0.05)


async def test_a_genuine_flight_still_sets_the_latch():
    connector = _Connector(_Telemetry("IN_AIR", True, 20.0, LAUNCH[0], LAUNCH[1], abs_alt=LAUNCH_AMSL + 20.0))
    await monitor_flight(_ctx(connector))
    assert connector.was_airborne is True


async def test_the_latch_still_sets_when_the_autopilot_will_not_say():
    """No landed_state, no in_air: height above the launch point decides."""

    class _Mute(_Telemetry):
        async def landed_state(self):
            return
            yield  # pragma: no cover - makes this an async generator

        async def in_air(self):
            return
            yield  # pragma: no cover

    connector = _Connector(_Mute("", None, 30.0, LAUNCH[0], LAUNCH[1], abs_alt=LAUNCH_AMSL + 30.0))
    await monitor_flight(_ctx(connector))
    assert connector.was_airborne is True


async def test_destination_awareness_survives_the_datum_fix():
    """FIX 6: on the ground at the wrong place is still not mission_complete."""
    connector = _Connector(
        _Telemetry("ON_GROUND", False, DATUM_OFFSET_M, HOSPITAL[0], HOSPITAL[1], abs_alt=LAUNCH_AMSL - 4.1)
    )
    connector.was_airborne = True
    connector.last_movement = {
        "tool": "return_to_launch",
        "target": {"latitude": LAUNCH[0], "longitude": LAUNCH[1], "label": "the autopilot's home"},
        "commanded_at": 0.0,
    }

    result = await monitor_flight(_ctx(connector))

    assert result["mission_complete"] is False
    assert result["status"] == "landed_away_from_target"
    assert result["landed_away_from_target"]["distance_m"] > 1000


async def test_the_home_landing_completes_even_with_the_offset():
    """The T6 shape's happy ending: home, down, and told so."""
    connector = _Connector(_Telemetry("ON_GROUND", False, DATUM_OFFSET_M, LAUNCH[0], LAUNCH[1]))
    connector.was_airborne = True
    connector.last_movement = {
        "tool": "return_to_launch",
        "target": {"latitude": LAUNCH[0], "longitude": LAUNCH[1], "label": "the autopilot's home"},
        "commanded_at": 0.0,
    }

    result = await monitor_flight(_ctx(connector))

    assert result["mission_complete"] is True
    assert result["status"] == "landed"
    assert connector.was_airborne is False, "the next poll must earn its own completion"


# --------------------------------------------------- the number travels in every answer


async def test_every_phase_reports_the_datum_free_height():
    for landed_state, in_air, point in (
        ("ON_GROUND", False, LAUNCH),
        ("IN_AIR", True, HOSPITAL),
        ("LANDING", True, HOSPITAL),
    ):
        connector = _Connector(_Telemetry(landed_state, in_air, 33.0, point[0], point[1], abs_alt=LAUNCH_AMSL + 28.9))
        connector.was_airborne = True
        result = await monitor_flight(_ctx(connector))
        assert "height_above_launch_m" in result, result
        assert result["height_above_launch_m"] == pytest.approx(28.9, abs=0.05)
        assert result["altitude_m"] == 33.0, "the raw reading is still reported alongside it"
