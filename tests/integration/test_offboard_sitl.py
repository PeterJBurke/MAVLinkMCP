"""SITL integration tests for the v2 offboard tools (ArduCopter docker SITL).

Tests in this module share one flight on one fresh SITL, in file order:
validation/error paths first (on the ground), then a single flight exercising
velocity control, position hold, the remaining setpoint kinds, the
stale-setpoint auto-brake, and stop.
"""

import time

import pytest

from tests.integration.conftest import SITL_HOME
from tests.integration.flight import arm_and_takeoff, land_and_wait_disarm

pytestmark = pytest.mark.sitl


# ------------------------------------------------------------ error paths


def test_offboard_motion_blocked_on_the_ground(drone_tools):
    """With the safety layer active, offboard motion tools are stopped by the
    navigation precondition before the tool's own validation runs. Argument
    validation is exercised in flight (test_argument_validation_in_flight)."""
    for tool, kwargs in (
        ("offboard_control", dict(action="start")),
        ("offboard_set_velocity_ned", dict(north_m_s=1.0, east_m_s=0.0)),
        ("offboard_set_attitude", dict(roll=0, pitch=0, yaw=0, thrust=0.5)),
    ):
        result = drone_tools.call(tool, **kwargs)
        assert result["status"] == "rejected", (tool, result)
        assert result["rule"] == "precondition.navigation_requires_airborne", (tool, result)


def test_offboard_status_and_stop_work_on_the_ground(drone_tools):
    """Querying and stopping must NOT require being airborne - they are how a
    caller recovers from a bad state."""
    status = drone_tools.call("offboard_control", action="status")
    assert status["status"] == "success", status
    assert status["offboard_active"] is False

    bad = drone_tools.call("offboard_control", action="hover")
    assert bad["status"] == "failed", bad  # the tool's own action validation


# ------------------------------------------------------------ flight


def test_velocity_ned_flight(drone_tools):
    arm_and_takeoff(drone_tools, altitude_m=15.0)

    result = drone_tools.call("offboard_set_velocity_ned", north_m_s=2.0, east_m_s=0.0, yaw_deg=0.0, stale_timeout_s=45)
    assert result["status"] == "success", result

    result = drone_tools.call("offboard_control", action="start")
    assert result["status"] == "success", result

    status = drone_tools.call("offboard_control", action="status")
    assert status["offboard_active"] is True
    assert status["setpoint"]["last_setpoint"] == "velocity_ned"

    time.sleep(5)
    speed = drone_tools.call("get_speed")
    assert speed["status"] == "success"
    assert speed["velocity"]["ground_speed_m_s"] >= 1.0, speed


def test_position_ned_holds_altitude(drone_tools):
    result = drone_tools.call("offboard_set_position_ned", north_m=0.0, east_m=0.0, down_m=-15.0, yaw_deg=0.0)
    assert result["status"] == "success", result
    # Position setpoints clear the stale-watchdog (self-terminating).
    status = drone_tools.call("offboard_control", action="status")
    assert status["setpoint"]["stale_timeout_s"] is None

    time.sleep(8)
    pos = drone_tools.call("get_position")
    assert pos["status"] == "success"
    assert 13.0 <= pos["position"]["relative_altitude_m"] <= 17.0, pos


def test_remaining_setpoint_kinds_accepted(drone_tools):
    """Acceptance-level checks on ArduCopter; quantitative behavior per kind is
    recorded in docs/firmware_notes.csv (some are PX4-only in effect)."""
    calls = [
        (
            "offboard_set_position_global",
            dict(
                latitude_deg=SITL_HOME["lat"] + 0.0003,
                longitude_deg=SITL_HOME["lon"],
                altitude_m=15.0,
                altitude_type="rel_home",
            ),
        ),
        ("offboard_set_velocity_body", dict(forward_m_s=1.0, yawspeed_deg_s=5.0, stale_timeout_s=45)),
        ("offboard_set_attitude", dict(roll=0, pitch=-5, yaw=0, thrust=0.55, mode="angle", stale_timeout_s=45)),
        ("offboard_set_attitude", dict(roll=0, pitch=0, yaw=5, thrust=0.55, mode="rate", stale_timeout_s=45)),
        ("offboard_set_acceleration_ned", dict(north_m_s2=0.5, east_m_s2=0.0, stale_timeout_s=45)),
        ("offboard_set_actuator_control", dict(groups=[[0.0] * 8], stale_timeout_s=45)),
        (
            "offboard_set_position_ned",
            dict(
                north_m=0.0,
                east_m=0.0,
                down_m=-15.0,
                yaw_deg=0.0,
                velocity={"north_m_s": 0.5},
                acceleration={"north_m_s2": 0.1},
            ),
        ),
    ]
    for tool, kwargs in calls:
        result = drone_tools.call(tool, **kwargs)
        # offboard_set_actuator_control is tier CRITICAL (raw actuator
        # override), so it needs the confirmation round-trip first.
        if result.get("status") == "confirmation_required":
            result = drone_tools.call(tool, **kwargs, confirm_token=result["confirm_token"])
        assert result["status"] == "success", (tool, result)
        time.sleep(1)


def test_argument_validation_in_flight(drone_tools):
    """The tools' own argument validation, reached now that we are airborne."""
    over = drone_tools.call("offboard_set_velocity_ned", north_m_s=50.0, east_m_s=0.0)
    assert over["status"] == "rejected", over  # safety bounds catch it first
    assert over["rule"] == "bounds.max_speed"

    thrust = drone_tools.call("offboard_set_attitude", roll=0, pitch=0, yaw=0, thrust=1.5)
    assert thrust["status"] == "failed", thrust
    assert "thrust" in thrust["error"]

    stale = drone_tools.call("offboard_set_velocity_ned", north_m_s=1.0, east_m_s=0.0, stale_timeout_s=600)
    assert stale["status"] == "failed", stale
    assert "stale_timeout_s" in stale["error"]


def test_stale_setpoint_auto_brakes(drone_tools):
    result = drone_tools.call("offboard_set_velocity_ned", north_m_s=3.0, east_m_s=0.0, yaw_deg=0.0, stale_timeout_s=3)
    assert result["status"] == "success", result

    time.sleep(8)  # timeout (3s) + braking distance
    status = drone_tools.call("offboard_control", action="status")
    assert status["setpoint"]["auto_braked"] is True, status

    speed = drone_tools.call("get_speed")
    assert speed["velocity"]["ground_speed_m_s"] <= 0.7, speed


def test_stop_and_land(drone_tools):
    result = drone_tools.call("offboard_control", action="stop")
    assert result["status"] == "success", result

    status = drone_tools.call("offboard_control", action="status")
    assert status["offboard_active"] is False

    land_and_wait_disarm(drone_tools)


# ------------------------------------------------------------ PX4-only


@pytest.mark.px4
def test_actuator_control_has_effect():
    """SET_ACTUATOR_CONTROL_TARGET behavior verification - PX4-only.

    ArduPilot result (Copter 4.5.7 SITL): RPC accepted but inert; ArduPilot
    has no handler for this message. See docs/firmware_notes.csv."""
    raise NotImplementedError("requires PX4 SITL (llmuavpx4)")
