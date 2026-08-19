"""Post-deploy validation of the T6 return fixes, against the local SITL.

Run explicitly (they are excluded from the default suite)::

    uv run pytest -m sitl tests/integration/test_return_honesty_sitl.py

What these prove that the unit tests cannot: the fixes hold against a real
autopilot, with real telemetry timing, through the whole safety pipeline.
Each test names the T6 audit mechanism (2026-08-19) it closes.

Nothing here flies outside the SITL's geofence, and every test lands and
disarms what it armed.
"""

import time

import pytest

from tests.integration.conftest import SITL_HOME
from tests.integration.flight import arm_and_takeoff, land_and_wait_disarm

pytestmark = pytest.mark.sitl

HOME_ALT = SITL_HOME["alt_amsl"]


def _wait_disarmed(client, timeout_s: float = 120.0) -> None:
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        reading = client.call("get_armed", timeout=30)
        if reading.get("status") == "success" and reading["armed"] is False:
            return
        time.sleep(3)
    raise AssertionError("the aircraft did not disarm")


def test_rtl_on_a_parked_disarmed_aircraft_is_refused(control_tools):
    """FIX 5, mechanism M1: the command that flew nothing and reported a return."""
    _wait_disarmed(control_tools)
    result = control_tools.call("return_to_launch", timeout=60)
    assert result["status"] == "rejected", result
    assert result["rule"] == "precondition.rtl_requires_airborne", result
    assert "disarmed" in result.get("reason", "")
    # And the same command through the other door.
    by_mode = control_tools.call("set_flight_mode", mode="RTL", timeout=60)
    assert by_mode["status"] == "rejected", by_mode
    assert by_mode["rule"] == "precondition.rtl_requires_airborne", by_mode


def test_land_on_a_parked_disarmed_aircraft_reports_no_action(control_tools):
    """FIX 5: honest, and never refused - land is the abort path."""
    _wait_disarmed(control_tools)
    result = control_tools.call("land", timeout=60)
    assert result["status"] == "no_action", result
    assert "already on the ground" in result["message"]


def test_home_and_the_session_launch_point_are_both_reported(control_tools):
    """FIX 8a, mechanism M3: home follows the last arming; the launch point does not."""
    result = control_tools.call("get_home_position", timeout=60)
    assert result["status"] == "success", result
    assert "session_launch_point" in result
    assert "home_matches_session_launch" in result
    if result["session_launch_point"] is not None:
        assert result["distance_between_m"] >= 0.0
        if not result["home_matches_session_launch"]:
            assert "RTL will fly to" in result["warning"]


def test_an_rtl_in_flight_is_observable_all_the_way_home(control_tools):
    """FIX 7, mechanism M2: the return that monitor_flight could not see.

    Fly out, command RTL from the air, and poll monitor_flight. Every answer
    must carry a live position and a distance, and the distance must close.
    """
    arm_and_takeoff(control_tools, altitude_m=20.0)
    out = control_tools.call(
        "go_to_location",
        latitude_deg=SITL_HOME["lat"] + 0.0025,
        longitude_deg=SITL_HOME["lon"],
        absolute_altitude_m=HOME_ALT + 30,
        timeout=60,
    )
    assert out["status"] == "success", out
    for _ in range(10):
        progress = control_tools.call("monitor_flight", arrival_threshold_m=20.0, auto_land=False, timeout=120)
        if progress.get("status") in ("arrived", "landed"):
            break

    rtl = control_tools.call("return_to_launch", timeout=60)
    assert rtl["status"] == "success", rtl
    assert rtl["destination"] is not None, "an RTL must name the coordinate it is flying to"

    distances = []
    for _ in range(12):
        poll = control_tools.call("monitor_flight", arrival_threshold_m=20.0, auto_land=False, timeout=120)
        assert poll.get("position") is not None, poll
        assert poll.get("altitude_m") is not None, poll
        assert poll.get("distance_to_target_m") is not None, poll
        distances.append(poll["distance_to_target_m"])
        if poll.get("mission_complete"):
            break
    assert min(distances) < distances[0], f"the return never appeared to progress: {distances}"

    _wait_disarmed(control_tools, timeout_s=180)


def test_a_landing_away_from_the_target_is_not_mission_complete(control_tools):
    """FIX 6, mechanism M1: on the ground is not the same as arrived.

    Fly out, land there deliberately, then poll. The aircraft is on the ground
    a long way from the destination it was last sent to, which must read as
    ``landed_away_from_target`` and never as a completed mission.
    """
    arm_and_takeoff(control_tools, altitude_m=20.0)
    away = control_tools.call(
        "go_to_location",
        latitude_deg=SITL_HOME["lat"] + 0.0025,
        longitude_deg=SITL_HOME["lon"],
        absolute_altitude_m=HOME_ALT + 30,
        timeout=60,
    )
    assert away["status"] == "success", away
    time.sleep(10)
    # Land short of it, on purpose, with the gate overridden.
    land_and_wait_disarm(control_tools)

    poll = control_tools.call("monitor_flight", timeout=120)
    assert poll["mission_complete"] is False, poll
    assert poll["status"] in ("landed_away_from_target", "not_started"), poll
    assert "MISSION COMPLETE" not in poll["DISPLAY_TO_USER"]
    if poll["status"] == "landed_away_from_target":
        assert poll["landed_away_from_target"]["distance_m"] > 20.0
