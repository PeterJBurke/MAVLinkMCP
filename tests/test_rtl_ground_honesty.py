"""FIX 5: a movement command to a parked, disarmed aircraft must not report a flight.

The T6 hospital campaign's largest single failure mechanism (audit
2026-08-19, mechanism M1, 8 of 27 failures) was one tool result:

    CALL return_to_launch {}
    RES  {"status": "success", "message": "Return to Launch initiated - drone returning home"}

issued to an aircraft that was standing, disarmed, at the hospital 1507 m from
the launch field. Nothing flew. The models were following their instruction to
treat tool results as the only source of truth, and reported a completed
return.

What each movement tool does now, and why:

- ``return_to_launch`` (and ``set_flight_mode("RTL")``, the same command
  through another door): REFUSED with ``precondition.rtl_requires_airborne``
  when the aircraft is known to be disarmed on the ground. An ARMED aircraft on
  the ground is left alone - ArduPilot's RTL genuinely climbs and flies from
  there, which is what the T6 models that re-armed at the hospital were doing.
- ``land``: answered with ``status: "no_action"`` rather than "Landing
  initiated". Never refused: land is the abort path.
- ``go_to_location``, ``move_to_relative``, ``reposition`` and the rest of
  NAVIGATION_TOOLS: already refused on the ground by
  ``precondition.navigation_requires_airborne``. Pinned here so the coverage is
  visible in one place.

Neither rule can fire on a state the safety layer could not read: unknown state
returns before them, and both tools stay available.
"""

from __future__ import annotations

import types

import pytest

from droneserver.safety.config import SafetySettings
from droneserver.safety.middleware import _STATE_DEPENDENT
from droneserver.safety.validation import check_preconditions, commands_return_home
from droneserver.tools import action

AIRBORNE = {"armed": True, "in_air": True, "unknown": False, "seconds_since_takeoff": 60.0}
GROUNDED_DISARMED = {"armed": False, "in_air": False, "unknown": False, "seconds_since_takeoff": None}
GROUNDED_ARMED = {"armed": True, "in_air": False, "unknown": False, "seconds_since_takeoff": None}


@pytest.fixture
def s():
    return SafetySettings(_env_file=None, max_altitude_m=120.0, max_speed_m_s=20.0)


# --------------------------------------------------------------- the rule


def test_disarmed_ground_rtl_is_refused(s):
    """The captured T6 defect: RTL to a parked, disarmed aircraft."""
    r = check_preconditions("return_to_launch", {}, GROUNDED_DISARMED, s)
    assert r is not None and r.rule == "precondition.rtl_requires_airborne"
    assert "disarmed" in r.reason
    assert "arm_drone" in r.remedy and "takeoff" in r.remedy


def test_armed_on_the_ground_rtl_proceeds(s):
    """The real T6 flow: models re-armed at the hospital and RTL genuinely flew."""
    assert check_preconditions("return_to_launch", {}, GROUNDED_ARMED, s) is None


def test_airborne_rtl_proceeds(s):
    assert check_preconditions("return_to_launch", {}, AIRBORNE, s) is None


def test_unknown_state_still_allows_rtl(s):
    """An unreadable link must never block the return path."""
    assert check_preconditions("return_to_launch", {}, {"unknown": True}, s) is None


@pytest.mark.parametrize("mode", ["RTL", "rtl", "Return_To_Launch"])
def test_set_flight_mode_rtl_is_the_same_command(s, mode):
    r = check_preconditions("set_flight_mode", {"mode": mode}, GROUNDED_DISARMED, s)
    assert r is not None and r.rule == "precondition.rtl_requires_airborne"


@pytest.mark.parametrize("mode", ["LAND", "HOLD", "GUIDED", "AUTO"])
def test_other_flight_modes_are_untouched(s, mode):
    assert check_preconditions("set_flight_mode", {"mode": mode}, GROUNDED_DISARMED, s) is None


def test_emergency_stop_is_not_gated(s):
    """EMERGENCY tier is ungated by design; an abort path must not be refusable."""
    assert commands_return_home("emergency_stop", {"mode": "rtl"}) is False
    assert check_preconditions("emergency_stop", {"mode": "rtl"}, GROUNDED_DISARMED, s) is None


def test_movement_tools_are_already_refused_on_the_ground(s):
    for tool in ("go_to_location", "move_to_relative", "reposition", "do_orbit"):
        r = check_preconditions(tool, {}, GROUNDED_DISARMED, s)
        assert r is not None and r.rule == "precondition.navigation_requires_airborne", tool


def test_rtl_state_is_refreshed_not_read_stale():
    """The rule can only fire on state that was actually read."""
    assert "return_to_launch" in _STATE_DEPENDENT
    assert "set_flight_mode" in _STATE_DEPENDENT


# ------------------------------------------------------- land: no_action


class _Telemetry:
    def __init__(self, armed: bool, in_air: bool):
        self._armed, self._in_air = armed, in_air

    async def armed(self):
        yield self._armed

    async def in_air(self):
        yield self._in_air

    async def position(self):
        yield types.SimpleNamespace(
            latitude_deg=33.7433185,
            longitude_deg=-117.8328833,
            relative_altitude_m=0.0,
            absolute_altitude_m=41.3,
        )


class _Action:
    def __init__(self):
        self.landings = 0

    async def land(self):
        self.landings += 1


class _Connector:
    def __init__(self, armed=False, in_air=False):
        self.drone = types.SimpleNamespace(telemetry=_Telemetry(armed, in_air), action=_Action())
        self.pending_destination = None
        self.landing_in_progress = False
        self.was_airborne = False
        self.last_movement = None
        self.session_launch = None


def _ctx(connector):
    return types.SimpleNamespace(request_context=types.SimpleNamespace(lifespan_context=connector))


land = action.land.__wrapped__


@pytest.fixture(autouse=True)
def _always_connected(monkeypatch):
    async def ok(_connector, timeout=30.0):
        return True

    monkeypatch.setattr(action, "ensure_connection", ok)


async def test_land_on_a_parked_disarmed_aircraft_lands_nothing():
    connector = _Connector(armed=False, in_air=False)
    result = await land(_ctx(connector))
    assert result["status"] == "no_action"
    assert "already on the ground" in result["message"]
    assert connector.drone.action.landings == 0
    assert "Landing initiated" not in str(result)


async def test_land_still_works_on_an_armed_aircraft_on_the_ground():
    connector = _Connector(armed=True, in_air=False)
    result = await land(_ctx(connector))
    assert result["status"] == "success"
    assert connector.drone.action.landings == 1


async def test_land_still_works_in_the_air():
    connector = _Connector(armed=True, in_air=True)
    result = await land(_ctx(connector))
    assert result["status"] == "success"
    assert connector.drone.action.landings == 1


async def test_land_is_commanded_when_the_state_cannot_be_read():
    """Unreadable telemetry must not turn the abort path into a no-op."""

    class _Blind(_Telemetry):
        async def armed(self):
            raise RuntimeError("no telemetry")
            yield  # pragma: no cover

        async def in_air(self):
            raise RuntimeError("no telemetry")
            yield  # pragma: no cover

    connector = _Connector()
    connector.drone.telemetry = _Blind(False, False)
    result = await land(_ctx(connector))
    assert result["status"] == "success"
    assert connector.drone.action.landings == 1
