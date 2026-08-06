"""SITL integration tests for the v2 camera and log tools.

Plain ArduCopter SITL has no camera backend, and MavSDK's log_files plugin
misreads ArduPilot's 1-based log ids (see docs/firmware_notes.csv). These
tests pin the OBSERVED behavior - honest error surfacing, not fake success.
Behavior-level tests are px4-marked until a PX4(+gazebo camera) SITL exists.
"""

import pytest

pytestmark = pytest.mark.sitl


# ------------------------------------------------------------ camera


def test_list_cameras_reports_none_on_plain_sitl(drone_tools):
    result = drone_tools.call("list_cameras", timeout=40)
    assert result["status"] == "success", result
    assert result["cameras"] == []  # no camera backend in plain SITL


def test_take_photo_surfaces_honest_error(drone_tools):
    result = drone_tools.call("camera_capture", component_id=100, action="take_photo", timeout=90)
    assert result["status"] == "failed"
    assert "failed" in result["error"].lower() or "TIMEOUT" in result["error"]


def test_camera_validation_paths(drone_tools):
    result = drone_tools.call("camera_capture", component_id=100, action="selfie")
    assert result["status"] == "failed"

    result = drone_tools.call("camera_settings", component_id=100, action="set_mode", mode="night")
    assert result["status"] == "failed"

    result = drone_tools.call("camera_settings", component_id=100, action="set_setting", setting_id="")
    assert result["status"] == "failed"

    result = drone_tools.call("camera_zoom_focus", component_id=100, control="iris", action="stop")
    assert result["status"] == "failed"

    result = drone_tools.call("camera_tracking", component_id=100, action="track_point", point_x=1.5)
    assert result["status"] == "failed"
    assert "0..1" in result["error"]


# ------------------------------------------------------------ logs


def test_flight_logs_list_documents_mavsdk_ardupilot_gap(drone_tools):
    """ArduPilot answers LOG_REQUEST_LIST correctly (verified via pymavlink),
    but MavSDK 3.0.1 reports NO_LOGFILES (expects PX4 0-based ids)."""
    result = drone_tools.call("flight_logs", action="list", timeout=130)
    assert result["status"] == "failed"
    assert "NO_LOGFILES" in result["error"] or "failed" in result["error"].lower()


def test_flight_logs_validation(drone_tools):
    result = drone_tools.call("flight_logs", action="shred")
    assert result["status"] == "failed"

    result = drone_tools.call("flight_logs", action="download")
    assert result["status"] == "failed"
    assert "log_id" in result["error"]


# ------------------------------------------------------------ PX4-only


@pytest.mark.px4
def test_camera_capture_behavior():
    """Photo/video/stream behavior needs a real camera backend (PX4 + gazebo
    or a hardware camera). ArduPilot plain-SITL result: discovery empty,
    commands TIMEOUT (docs/firmware_notes.csv)."""
    raise NotImplementedError("requires PX4 SITL with camera")


@pytest.mark.px4
def test_flight_logs_roundtrip_behavior():
    """List->download->verify roundtrip needs PX4 (MavSDK log_files is
    incompatible with ArduPilot's 1-based log ids; see firmware notes)."""
    raise NotImplementedError("requires PX4 SITL")
