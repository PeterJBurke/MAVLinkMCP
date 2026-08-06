"""SITL integration tests for the v2 gimbal tools.

The test SITL image simulates a servo mount (MNT1_TYPE=1 in sitl.parm), so
the full MAVLink gimbal protocol v2 is exercised for real on ArduCopter.
"""

import pytest

from tests.integration.conftest import SITL_HOME

pytestmark = pytest.mark.sitl


def test_list_gimbals_finds_simulated_mount(drone_tools):
    result = drone_tools.call("list_gimbals", timeout=40)
    assert result["status"] == "success", result
    assert len(result["gimbals"]) == 1
    assert result["gimbals"][0]["gimbal_id"] == 1


def test_take_control_and_status(drone_tools):
    result = drone_tools.call("gimbal_control", gimbal_id=1, action="take")
    assert result["status"] == "success", result

    status = drone_tools.call("gimbal_control", gimbal_id=1, action="status")
    assert status["status"] == "success"
    assert status["control_status"]["control_mode"] == "PRIMARY"


def test_set_angles_verified_by_attitude(drone_tools):
    result = drone_tools.call("gimbal_point", gimbal_id=1, action="set_angles", pitch=-30.0)
    assert result["status"] == "success", result

    import time

    time.sleep(3)
    attitude = drone_tools.call("gimbal_point", gimbal_id=1, action="get_attitude")
    assert attitude["status"] == "success"
    pitch = attitude["attitude"]["euler_angle_forward"]["pitch_deg"]
    assert pitch == pytest.approx(-30.0, abs=3.0)


def test_roi_location_and_rates_accepted(drone_tools):
    roi = drone_tools.call(
        "gimbal_point",
        gimbal_id=1,
        action="roi_location",
        latitude_deg=SITL_HOME["lat"] + 0.001,
        longitude_deg=SITL_HOME["lon"],
        altitude_m=600.0,
    )
    assert roi["status"] == "success", roi

    rates = drone_tools.call("gimbal_point", gimbal_id=1, action="set_rates", pitch=5.0)
    assert rates["status"] == "success", rates


def test_release_control(drone_tools):
    result = drone_tools.call("gimbal_control", gimbal_id=1, action="release")
    assert result["status"] == "success", result


def test_error_paths(drone_tools):
    result = drone_tools.call("gimbal_control", gimbal_id=1, action="grab")
    assert result["status"] == "failed"

    result = drone_tools.call("gimbal_point", gimbal_id=1, action="set_angles", pitch=-200.0)
    assert result["status"] == "failed"
    assert "out of range" in result["error"]

    result = drone_tools.call("gimbal_point", gimbal_id=9, action="get_attitude")
    assert result["status"] == "failed"  # INVALID_ARGUMENT from MavSDK, surfaced honestly
