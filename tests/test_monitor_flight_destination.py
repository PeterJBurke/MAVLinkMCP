"""FIX 6 and FIX 7: monitor_flight must say where the aircraft is, and must not
call a flight that never happened complete.

Two halves of the same T6 failure (audit 2026-08-19).

**FIX 6, mechanism M1.** ``mission_complete`` was derived from being on the
ground and nothing else. Eight trials whose aircraft had auto-landed at a
hospital commanded RTL (which flew nothing), polled monitor_flight, and were
answered::

    {"DISPLAY_TO_USER": "✅ MISSION COMPLETE - Drone has landed safely!",
     "status": "landed", "altitude_m": -12.2, "mission_complete": true}

while parked 1.2-1.5 km from the launch point. Completion now needs a flight
(the was_airborne latch, e5eb448) AND the aircraft to be where it was sent.

**FIX 7, mechanism M2.** During a genuine return, monitor_flight answered
``🛬 LANDING | Alt: 50.0m | Descending...`` with no position and no distance,
for as many as 24 consecutive polls, while the aircraft climbed from 34.5 m to
50 m and cruised home. Three models concluded the return had stalled and
force-landed a kilometre short. Every answer now carries live position,
altitude, vertical speed and distances, and the phase word is read from
telemetry rather than assumed.
"""

from __future__ import annotations

import asyncio
import types

import pytest

from droneserver.tools import action

#: The launch field and Foothill Regional Medical Center, from the T6 campaign.
LAUNCH = (33.7434897, -117.8328829)
HOSPITAL = (33.7302219, -117.8284659)


class _LandedState:
    def __init__(self, name):
        self.name = name

    def __str__(self):
        return f"LandedState.{self.name}"


class _Telemetry:
    def __init__(self, landed_state, in_air, alt, lat, lon, abs_alt=41.3, north=0.0, east=0.0, down=0.0, mode="GUIDED"):
        self._landed_state, self._in_air, self._alt = landed_state, in_air, alt
        self._lat, self._lon, self._abs = lat, lon, abs_alt
        self._ned, self._mode = (north, east, down), mode

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
        north, east, down = self._ned
        yield types.SimpleNamespace(north_m_s=north, east_m_s=east, down_m_s=down)

    async def flight_mode(self):
        yield self._mode


class _Action:
    def __init__(self):
        self.landings = 0

    async def land(self):
        self.landings += 1


class _Connector:
    def __init__(self, telemetry):
        self.drone = types.SimpleNamespace(telemetry=telemetry, action=_Action())
        self.pending_destination = None
        self.landing_in_progress = False
        self.was_airborne = False
        self.last_movement = None
        self.session_launch = {
            "latitude_deg": LAUNCH[0],
            "longitude_deg": LAUNCH[1],
            "absolute_altitude_m": 41.3,
            "source": "autopilot home when the link came up",
        }


def _ctx(connector):
    return types.SimpleNamespace(request_context=types.SimpleNamespace(lifespan_context=connector))


def _rtl_to_launch(connector):
    """What return_to_launch records when it commands a real return home."""
    target = {"latitude": LAUNCH[0], "longitude": LAUNCH[1], "label": "the autopilot's home"}
    connector.last_movement = {"tool": "return_to_launch", "target": target, "commanded_at": 0.0}


monitor_flight = action.monitor_flight.__wrapped__


@pytest.fixture(autouse=True)
def _always_connected(monkeypatch):
    async def ok(_connector, timeout=30.0):
        return True

    monkeypatch.setattr(action, "ensure_connection", ok)


# ------------------------------------------------------------------ FIX 6


async def test_the_phantom_return_is_not_mission_complete():
    """The captured T6 defect, in full: parked at the hospital after an RTL."""
    connector = _Connector(_Telemetry("ON_GROUND", False, -12.2, HOSPITAL[0], HOSPITAL[1], abs_alt=29.16))
    connector.was_airborne = True  # it really did fly, outbound
    _rtl_to_launch(connector)

    result = await monitor_flight(_ctx(connector))

    assert result["mission_complete"] is False
    assert result["status"] == "landed_away_from_target"
    assert "MISSION COMPLETE" not in result["DISPLAY_TO_USER"]
    away = result["landed_away_from_target"]
    assert 1400 < away["distance_m"] < 1600, away
    assert away["commanded_by"] == "return_to_launch"
    assert "did not happen or did not finish" in result["action_required"]


async def test_no_answer_echoes_the_harness_verdict_token():
    """The tool emitted the exact phrase the model is told to close with.

    The system prompt asks the model to begin its final message with
    "MISSION COMPLETE". The server printing that same string at a hospital, as
    a tool result, is what four of gpt-5.2's five failures quoted back.
    """
    states = [
        ("ON_GROUND", False, 0.0, HOSPITAL),
        ("ON_GROUND", False, 0.0, LAUNCH),
        ("LANDING", True, 20.0, LAUNCH),
        ("IN_AIR", True, 30.0, HOSPITAL),
    ]
    for landed_state, in_air, alt, point in states:
        connector = _Connector(_Telemetry(landed_state, in_air, alt, point[0], point[1]))
        connector.was_airborne = True
        _rtl_to_launch(connector)
        result = await monitor_flight(_ctx(connector))
        assert "MISSION COMPLETE" not in result["DISPLAY_TO_USER"], result


async def test_the_landing_that_did_reach_the_target_is_complete():
    """The legitimate return must still finish, or every model loops forever."""
    connector = _Connector(_Telemetry("ON_GROUND", False, 0.1, LAUNCH[0] + 0.00005, LAUNCH[1]))
    connector.was_airborne = True
    _rtl_to_launch(connector)

    result = await monitor_flight(_ctx(connector))

    assert result["mission_complete"] is True
    assert result["status"] == "landed"
    assert result["distance_to_target_m"] < 20.0


async def test_a_landing_with_no_commanded_destination_still_completes():
    """Nothing was commanded, so there is nothing to be away from."""
    connector = _Connector(_Telemetry("ON_GROUND", False, 0.0, LAUNCH[0], LAUNCH[1]))
    connector.was_airborne = True

    result = await monitor_flight(_ctx(connector))

    assert result["mission_complete"] is True
    assert result["status"] == "landed"


async def test_completion_still_requires_a_flight():
    """The e5eb448 latch is not weakened by any of this."""
    connector = _Connector(_Telemetry("ON_GROUND", False, 0.0, LAUNCH[0], LAUNCH[1]))
    result = await monitor_flight(_ctx(connector))
    assert result["mission_complete"] is False
    assert result["status"] == "not_started"


async def test_a_reported_landing_does_not_leave_the_latch_set():
    """Exhibit A: the SECOND poll is where the phantom completion was issued."""
    connector = _Connector(_Telemetry("ON_GROUND", False, 0.0, LAUNCH[0], LAUNCH[1]))
    connector.was_airborne = True
    assert (await monitor_flight(_ctx(connector)))["mission_complete"] is True
    second = await monitor_flight(_ctx(connector))
    assert second["mission_complete"] is False
    assert second["status"] == "not_started"


async def test_the_arrival_landing_clears_the_latch_too(monkeypatch):
    """The auto-land completion is what left the latch set in the real trials.

    Arrive hovering over the destination, let monitor_flight's own auto-land
    put the aircraft down, and check what it says and what it leaves behind.
    """
    real_sleep = asyncio.sleep

    async def fast(_seconds):
        await real_sleep(0)

    monkeypatch.setattr(action.asyncio, "sleep", fast)

    class _Arriving(_Telemetry):
        """Hovering at the destination for two reads, then on the ground."""

        def __init__(self):
            super().__init__("IN_AIR", True, 18.7, HOSPITAL[0], HOSPITAL[1], abs_alt=47.9)
            self.reads = 0

        async def landed_state(self):
            self.reads += 1
            if self.reads > 2:
                self._landed_state, self._in_air, self._alt, self._abs = "ON_GROUND", False, 0.5, 29.2
            async for item in super().landed_state():
                yield item

    connector = _Connector(_Arriving())
    connector.was_airborne = True
    connector.pending_destination = {
        "latitude": HOSPITAL[0],
        "longitude": HOSPITAL[1],
        "label": "the commanded destination",
        "altitude_msl": 80.0,
        "initial_distance": 1500.0,
        "start_time": 0.0,
        "source": "go_to_location",
    }
    connector.last_movement = {
        "tool": "go_to_location",
        "target": {"latitude": HOSPITAL[0], "longitude": HOSPITAL[1], "label": "the commanded destination"},
        "commanded_at": 0.0,
    }

    result = await monitor_flight(_ctx(connector), auto_land=True)

    assert result["mission_complete"] is True
    assert "MISSION COMPLETE" not in result["DISPLAY_TO_USER"]
    assert "LANDED AT THE COMMANDED DESTINATION" in result["DISPLAY_TO_USER"]
    assert connector.was_airborne is False, "the next poll must earn its own completion"
    assert result["distance_from_launch_point_m"] > 1000, "the answer names how far from the launch point it is"


# ------------------------------------------------------------------ FIX 7


async def test_the_landing_phase_carries_position_and_distance():
    """The frozen answer that cost three trials their return."""
    connector = _Connector(
        _Telemetry("LANDING", True, 50.0, 33.7350, -117.8300, abs_alt=91.37, north=12.0, east=1.0, down=-2.0)
    )
    connector.was_airborne = True
    _rtl_to_launch(connector)

    result = await monitor_flight(_ctx(connector))

    assert result["status"] == "landing"
    assert result["mission_complete"] is False
    assert result["position"] == {"latitude_deg": 33.7350, "longitude_deg": -117.8300}
    assert result["distance_to_target_m"] > 0
    assert result["distance_from_launch_point_m"] > 0
    assert result["altitude_m"] == 50.0
    assert result["ground_speed_m_s"] == pytest.approx(12.0, abs=0.1)
    assert result["flight_mode"] == "GUIDED"
    display = result["DISPLAY_TO_USER"]
    assert "from the autopilot's home" in display
    assert "33.735000,-117.830000" in display


async def test_a_climbing_aircraft_is_not_described_as_descending():
    """It said "Descending" sixteen times while the aircraft climbed to 50 m."""
    connector = _Connector(_Telemetry("LANDING", True, 44.0, 33.7350, -117.8300, down=-2.5))
    connector.was_airborne = True
    result = await monitor_flight(_ctx(connector))
    assert "CLIMBING" in result["DISPLAY_TO_USER"]
    assert "Descending" not in result["DISPLAY_TO_USER"]
    assert result["vertical_speed_m_s"] == pytest.approx(2.5)


async def test_a_descending_aircraft_says_so():
    connector = _Connector(_Telemetry("LANDING", True, 12.0, LAUNCH[0], LAUNCH[1], down=1.5))
    connector.was_airborne = True
    result = await monitor_flight(_ctx(connector))
    assert "Descending" in result["DISPLAY_TO_USER"]
    assert result["vertical_speed_m_s"] == pytest.approx(-1.5)


async def test_the_landing_text_changes_as_the_aircraft_moves():
    """A cached string cannot show a return progressing; these must differ."""
    connector = _Connector(_Telemetry("LANDING", True, 50.0, 33.7350, -117.8300, down=-2.0))
    connector.was_airborne = True
    _rtl_to_launch(connector)
    first = await monitor_flight(_ctx(connector))

    connector.drone.telemetry = _Telemetry("LANDING", True, 30.0, 33.7420, -117.8320, down=1.0)
    second = await monitor_flight(_ctx(connector))

    assert first["DISPLAY_TO_USER"] != second["DISPLAY_TO_USER"]
    assert second["distance_to_target_m"] < first["distance_to_target_m"]


async def test_the_latched_landing_branch_reports_the_same_detail():
    """The branch the T6 trials actually hit: landing_in_progress, no destination."""
    connector = _Connector(_Telemetry("IN_AIR", True, 48.7, 33.7340, -117.8300, down=-1.0))
    connector.was_airborne = True
    connector.landing_in_progress = True
    _rtl_to_launch(connector)

    result = await monitor_flight(_ctx(connector))

    assert result["status"] == "landing"
    assert result["position"] is not None
    assert result["distance_to_target_m"] is not None
    assert "CLIMBING" in result["DISPLAY_TO_USER"]


async def test_hovering_reports_position_too():
    connector = _Connector(_Telemetry("IN_AIR", True, 30.0, 33.7350, -117.8300))
    connector.was_airborne = True
    result = await monitor_flight(_ctx(connector))
    assert result["status"] == "hovering"
    assert result["position"] is not None
    assert result["distance_from_launch_point_m"] > 0
