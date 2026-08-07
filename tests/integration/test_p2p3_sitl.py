"""SITL integration tests for the v1-completion + P2/P3 tools (ArduCopter SITL).

Covers what the plain (peripheral-enabled) docker SITL can actually exercise:
system info, extended telemetry, parameter custom path, transponder (ADS-B),
rangefinder, winch status, calibration/failure/follow-me/ftp/shell honest
error surfacing, status text, tune, mocap/rtk send paths. Behavior that needs
PX4 or hardware is @pytest.mark.px4 with the ArduPilot result documented.
"""

import pytest

pytestmark = pytest.mark.sitl


# ---------------------------------------------------------------- system / info


def test_system_info_version(drone_tools):
    result = drone_tools.call("system_info", topic="version", timeout=30)
    assert result["status"] == "success", result
    assert result["info"]["flight_sw_major"] == 4


def test_system_info_flight_information_unsupported_on_ardupilot(drone_tools):
    result = drone_tools.call("system_info", topic="flight_information")
    assert result["status"] == "failed"  # INFORMATION_NOT_RECEIVED_YET


def test_send_status_text(drone_tools):
    result = drone_tools.call("send_status_text", text="droneserver test", severity="info")
    assert result["status"] == "success", result


def test_set_mavlink_timeout(drone_tools):
    result = drone_tools.call("set_mavlink_timeout", timeout_s=3.0)
    assert result["status"] == "success", result


# ---------------------------------------------------------------- telemetry v2


def test_extended_telemetry_published_topic(drone_tools):
    result = drone_tools.call("get_telemetry_extended", topic="raw_gps", timeout_s=10)
    assert result["status"] == "success", result
    assert "latitude_deg" in result["data"]


def test_extended_telemetry_unpublished_topic_times_out(drone_tools):
    # ArduPilot does not publish altitude - honest timeout, not a hang
    result = drone_tools.call("get_telemetry_extended", topic="altitude", timeout_s=6, timeout=30)
    assert result["status"] == "failed"
    assert "No altitude" in result["error"]


def test_extended_telemetry_rangefinder(drone_tools):
    result = drone_tools.call("get_telemetry_extended", topic="distance_sensor", timeout_s=10)
    assert result["status"] == "success", result


def test_set_telemetry_rate(drone_tools):
    result = drone_tools.call("set_telemetry_rate", topic="position", rate_hz=5.0)
    assert result["status"] == "success", result


def test_set_telemetry_rate_denied_topic(drone_tools):
    # ArduPilot denies odometry rate
    result = drone_tools.call("set_telemetry_rate", topic="odometry", rate_hz=5.0)
    assert result["status"] == "failed"


# ---------------------------------------------------------------- param v2


def test_param_custom_unsupported_on_ardupilot(drone_tools):
    result = drone_tools.call("get_parameter", name="SYSID_THISMAV", param_type="custom", timeout=30)
    assert result["status"] == "failed"  # PARAM_EXT is PX4-only


# ---------------------------------------------------------------- peripherals


def test_read_transponder_adsb(drone_tools):
    # The transponder plugin works; simulated ADS-B traffic appears
    # intermittently (aircraft are randomly placed and move), so accept either
    # a decoded vehicle or the honest "no traffic" note. Retry a few times to
    # give the sim a chance to surface a report.
    import time

    vehicle = None
    for _ in range(3):
        result = drone_tools.call("read_transponder", rate_hz=2.0, timeout_s=25, timeout=40)
        assert result["status"] == "success", result
        assert "vehicle" in result
        if result["vehicle"] is not None:
            vehicle = result["vehicle"]
            break
        time.sleep(3)
    if vehicle is not None:
        assert "icao_address" in vehicle


def test_payload_mechanism_no_backend(drone_tools):
    # gripper/winch are not enabled on the test SITL (WINCH_TYPE blocks arming);
    # the tool surfaces an honest timeout rather than hanging. Coverage of the
    # payload methods is verified at the code-path level (coverage_overrides).
    result = drone_tools.call("payload_mechanism", device="gripper", action="grab", timeout=30)
    assert result["status"] == "failed"  # no simulated gripper backend

    result = drone_tools.call("payload_mechanism", device="winch", action="status", timeout=20)
    assert result["status"] == "failed"  # no winch configured

    # validation path still works synchronously
    result = drone_tools.call("payload_mechanism", device="hoist", action="grab")
    assert result["status"] == "failed"
    assert "gripper" in result["error"]


def test_play_tune(drone_tools):
    result = drone_tools.call("play_tune", notes="c d e f g", tempo=120)
    assert result["status"] == "success", result


def test_send_mocap_and_rtk_accepted(drone_tools):
    m = drone_tools.call("send_mocap", kind="vision_position", x_m=0.0, y_m=0.0, z_m=0.0)
    assert m["status"] == "success", m
    r = drone_tools.call("send_rtcm", rtcm_base64="AAECAw==")
    assert r["status"] == "success", r


def test_follow_me_get_config(drone_tools):
    result = drone_tools.call("follow_me", action="get_config")
    assert result["status"] == "success", result
    assert "follow_height_m" in result["config"]


# ---------------------------------------------------------------- honest failures


def test_inject_failure_unsupported_on_ardupilot(drone_tools):
    """inject_failure is tier CRITICAL - confirm first, then ArduPilot rejects
    the injection itself (UNSUPPORTED) and the tool points at the SIM_* route."""
    issued = drone_tools.call("inject_failure", unit="gps", failure_type="off")
    assert issued["status"] == "confirmation_required", issued
    result = drone_tools.call("inject_failure", unit="gps", failure_type="off", confirm_token=issued["confirm_token"])
    assert result["status"] == "failed", result
    assert "SIM_" in result.get("hint", "")  # points at the ArduPilot workaround


def test_ftp_protocol_error_on_ardupilot(drone_tools):
    result = drone_tools.call("autopilot_files", action="list", path="/", timeout=30)
    assert result["status"] == "failed"  # ArduPilot MAVLink-FTP limited


def test_shell_no_output_on_ardupilot(drone_tools):
    """autopilot_shell is tier CRITICAL, so the safety layer requires a
    confirmation round-trip before the tool ever runs (see docs/safety_review.md).
    After confirming: ArduPilot has no MAVLink shell, so no output comes back."""
    issued = drone_tools.call("autopilot_shell", command="help", confirm=True, timeout=20)
    assert issued["status"] == "confirmation_required", issued
    result = drone_tools.call(
        "autopilot_shell",
        command="help",
        confirm=True,
        confirm_token=issued["confirm_token"],
        timeout=30,
    )
    assert result["status"] == "success", result
    assert result.get("output") == []


def test_shell_refused_without_confirm(drone_tools):
    """Two independent gates for the shell: the safety layer's confirmation
    token (tier CRITICAL) and the tool's own confirm flag."""
    issued = drone_tools.call("autopilot_shell", command="help", confirm=False)
    assert issued["status"] == "confirmation_required", issued
    result = drone_tools.call("autopilot_shell", command="help", confirm=False, confirm_token=issued["confirm_token"])
    assert result["status"] == "failed", result
    assert "confirm" in result["error"]


# ---------------------------------------------------------------- action v2


def test_do_orbit_blocked_on_the_ground(drone_tools):
    """With the safety layer active, do_orbit on the ground is stopped by the
    navigation precondition before it reaches the autopilot. The firmware
    finding itself (ArduPilot has no DO_ORBIT handler -> UNSUPPORTED) is
    recorded in docs/firmware_notes.csv from a direct in-flight probe."""
    result = drone_tools.call(
        "do_orbit",
        radius_m=20.0,
        velocity_m_s=2.0,
        latitude_deg=-35.362,
        longitude_deg=149.165,
        absolute_altitude_m=604.0,
    )
    assert result["status"] == "rejected", result
    assert result["rule"] == "precondition.navigation_requires_airborne"


def test_flight_altitudes_takeoff_get(drone_tools):
    result = drone_tools.call("flight_altitudes", action="get_takeoff", timeout=20)
    assert result["status"] == "success", result
    assert "altitude_m" in result


def test_vehicle_power_requires_confirm(drone_tools):
    """Two independent gates: the safety layer's confirmation token (tier
    CRITICAL) and the tool's own confirm flag."""
    issued = drone_tools.call("vehicle_power", action="reboot", confirm=False)
    assert issued["status"] == "confirmation_required", issued
    assert "reboot" in issued["consequence"].lower() or "autopilot" in issued["consequence"].lower()
    # Even WITH a valid token, the tool's own confirm flag still applies.
    result = drone_tools.call("vehicle_power", action="reboot", confirm=False, confirm_token=issued["confirm_token"])
    assert result["status"] == "failed"
    assert "confirm" in result["error"]


# ---------------------------------------------------------------- v1 rewrite regressions


def test_pause_mission_guided_hold_is_default_and_safe(drone_tools):
    """v1 pause_mission was a deprecated error stub; v2 default is guided_hold.
    On the ground (no mission) it still returns a structured result."""
    result = drone_tools.call("pause_mission", timeout=30)
    # Either success (held position) or a clean failure - never the old stub
    assert result["status"] in ("success", "failed")
    assert "DEPRECATED" not in str(result)


def test_set_max_speed_works_now(drone_tools):
    """v1 called a non-existent MavSDK method and always errored; v2 uses
    set_current_speed / WPNAV_SPEED fallback."""
    result = drone_tools.call("set_max_speed", speed_m_s=5.0, timeout=30)
    assert result["status"] == "success", result


# ---------------------------------------------------------------- PX4-only


@pytest.mark.px4
def test_inject_failure_behavior():
    """Failure injection actually takes effect - PX4 only (SYS_FAILURE_EN=1).
    ArduPilot result: UNSUPPORTED (docs/firmware_notes.csv)."""
    raise NotImplementedError("requires PX4 SITL")


@pytest.mark.px4
def test_ftp_roundtrip_behavior():
    """MAVLink-FTP upload/list/download roundtrip - PX4 only. ArduPilot result:
    PROTOCOL_ERROR (docs/firmware_notes.csv)."""
    raise NotImplementedError("requires PX4 SITL")


@pytest.mark.px4
def test_shell_command_output():
    """NSH shell command output - PX4 only. ArduPilot has no MAVLink shell."""
    raise NotImplementedError("requires PX4 SITL")
