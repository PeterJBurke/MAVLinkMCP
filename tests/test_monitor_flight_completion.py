"""``monitor_flight`` must not call a drone that never flew "mission complete".

The v1 tool answered "landed" from ``landed_state == ON_GROUND``, which is also
what a drone that has not taken off reports. Captured, S-Local arm, 2026-08-16,
``20260816T071008Z_local-google_gemma-4-e4b`` T4 trial 1: ``initiate_mission``
returned "Mission started with 3 waypoints", the very next ``monitor_flight``
returned ``{"DISPLAY_TO_USER": "MISSION COMPLETE - Drone has landed safely!",
"mission_complete": true}``, and the trial's own evidence records
``ever_armed: false``, ``max_altitude_m: 0.0``,
``max_distance_from_home_m: 0.0``. The model closed with "the drone executed
all waypoints flawlessly".

Same failure class as the PX4 managed-mission defect (a completion signal that
is already true at zero progress), different code path and different firmware -
this one is ArduPilot, and it is in the v1 tool, not the mission runner.
"""

import types

import pytest

from droneserver.tools import action


class _LandedState:
    def __init__(self, name):
        self.name = name

    def __str__(self):
        return f"LandedState.{self.name}"


class _Telemetry:
    def __init__(self, landed_state, in_air, alt):
        self._landed_state, self._in_air, self._alt = landed_state, in_air, alt

    async def landed_state(self):
        yield _LandedState(self._landed_state)

    async def in_air(self):
        yield self._in_air

    async def position(self):
        yield types.SimpleNamespace(
            latitude_deg=33.6402947,
            longitude_deg=-117.8444507,
            relative_altitude_m=self._alt,
            absolute_altitude_m=25.0 + self._alt,
        )


class _Connector:
    def __init__(self, landed_state="ON_GROUND", in_air=False, alt=0.0):
        self.drone = types.SimpleNamespace(telemetry=_Telemetry(landed_state, in_air, alt))
        self.pending_destination = None
        self.landing_in_progress = False
        self.was_airborne = False


def _ctx(connector):
    return types.SimpleNamespace(request_context=types.SimpleNamespace(lifespan_context=connector))


#: The tool as registered is wrapped by the safety layer (scope check, rate
#: limit, audit). This file exercises the completion logic underneath it; the
#: safety layer has its own suite, and going through it here would make these
#: assertions depend on a process-wide rate-limit budget.
monitor_flight = action.monitor_flight.__wrapped__


@pytest.fixture(autouse=True)
def _always_connected(monkeypatch):
    async def ok(_connector, timeout=30.0):
        return True

    monkeypatch.setattr(action, "ensure_connection", ok)


async def test_never_airborne_is_not_mission_complete():
    """The captured gemma-4-e4b regression."""
    connector = _Connector()
    result = await monitor_flight(_ctx(connector))
    assert result["mission_complete"] is False
    assert result["status"] == "not_started"
    assert "MISSION COMPLETE" not in result["DISPLAY_TO_USER"]


async def test_landing_after_a_real_flight_is_mission_complete():
    """The legitimate case must keep working: airborne first, then on ground."""
    connector = _Connector(landed_state="IN_AIR", in_air=True, alt=20.0)
    flying = await monitor_flight(_ctx(connector))
    assert flying["mission_complete"] is False
    assert connector.was_airborne is True

    connector.drone.telemetry = _Telemetry("ON_GROUND", False, 0.0)
    landed = await monitor_flight(_ctx(connector))
    assert landed["mission_complete"] is True
    assert landed["status"] == "landed"


async def test_the_latch_is_cleared_once_the_landing_is_reported():
    """So a second flight in the same session has to earn its own completion."""
    connector = _Connector(landed_state="IN_AIR", in_air=True, alt=20.0)
    await monitor_flight(_ctx(connector))
    connector.drone.telemetry = _Telemetry("ON_GROUND", False, 0.0)
    assert (await monitor_flight(_ctx(connector)))["mission_complete"] is True
    assert connector.was_airborne is False
    assert (await monitor_flight(_ctx(connector)))["mission_complete"] is False


async def test_low_hover_still_counts_as_airborne():
    """in_air is the signal; a 1.2 m hover is a flight, not a parked drone."""
    connector = _Connector(landed_state="IN_AIR", in_air=True, alt=1.2)
    await monitor_flight(_ctx(connector))
    assert connector.was_airborne is True
