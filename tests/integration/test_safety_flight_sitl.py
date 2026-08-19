"""A full flight under an active geofence, with a deliberate violation attempt.

This is the end-to-end proof that the safety layer permits normal operations
and blocks the dangerous ones *in flight*, not just on the bench.
"""

import time

import pytest

from tests.integration.conftest import SITL_HOME
from tests.integration.mcp_client import ToolCallError

pytestmark = pytest.mark.sitl

HOME_ALT = SITL_HOME["alt_amsl"]


def _arm(client):
    deadline = time.monotonic() + 90
    result = {}
    while time.monotonic() < deadline:
        try:
            result = client.call("arm_drone", timeout=60)
        except ToolCallError as e:
            result = {"status": "failed", "error": str(e)}
        if result.get("status") == "success":
            return result
        time.sleep(3)
    pytest.fail(f"arm failed: {result}")


def test_flight_under_active_geofence(control_tools, audit_path):
    """arm -> takeoff -> legal move -> ILLEGAL move (blocked) -> land."""
    # 1. Navigation is refused before takeoff (precondition).
    early = control_tools.call(
        "go_to_location",
        latitude_deg=SITL_HOME["lat"],
        longitude_deg=SITL_HOME["lon"],
        absolute_altitude_m=HOME_ALT + 20,
    )
    assert early["status"] == "rejected", early
    assert early["rule"] == "precondition.navigation_requires_airborne"

    # 2. Arm and take off within the configured limits (max 60 m).
    _arm(control_tools)
    takeoff = control_tools.call("takeoff", takeoff_altitude=20.0, wait_for_altitude=True, timeout=180)
    assert takeoff["status"] == "success", takeoff

    # 3. A legal move inside the fence is ALLOWED - the guard must not be a
    #    blanket "no".
    legal = control_tools.call(
        "go_to_location",
        latitude_deg=SITL_HOME["lat"] + 0.0005,
        longitude_deg=SITL_HOME["lon"],
        absolute_altitude_m=HOME_ALT + 20,
        timeout=60,
    )
    assert legal["status"] == "success", legal
    time.sleep(5)

    # 4. The deliberate violation: a waypoint far outside the fence, in flight.
    illegal = control_tools.call(
        "go_to_location",
        latitude_deg=SITL_HOME["lat"] + 0.5,  # ~55 km away
        longitude_deg=SITL_HOME["lon"],
        absolute_altitude_m=HOME_ALT + 20,
        timeout=60,
    )
    assert illegal["status"] == "rejected", illegal
    assert illegal["rule"].startswith("geofence."), illegal

    # 5. ...and an altitude above the ceiling, in flight.
    too_high = control_tools.call(
        "go_to_location",
        latitude_deg=SITL_HOME["lat"],
        longitude_deg=SITL_HOME["lon"],
        absolute_altitude_m=HOME_ALT + 500,
        timeout=60,
    )
    assert too_high["status"] == "rejected", too_high
    assert too_high["rule"] in ("bounds.max_altitude", "geofence.altitude_ceiling")

    # 6. The drone is still flying normally after the rejections.
    position = control_tools.call("get_position")
    assert position["status"] == "success"
    assert position["position"]["relative_altitude_m"] > 10

    # 7. Land and confirm disarm.
    landed = control_tools.call("land", force=True, timeout=60)
    assert landed["status"] == "success", landed
    deadline = time.monotonic() + 180
    while time.monotonic() < deadline:
        armed = control_tools.call("get_armed", timeout=30)
        if armed.get("status") == "success" and armed["armed"] is False:
            break
        time.sleep(3)
    else:
        pytest.fail("drone did not disarm after landing")

    # 8. The whole flight, including the two rejections, is in the audit log
    #    with latencies - the paper's instrumentation.
    import json

    records = [json.loads(line) for line in audit_path.read_text().splitlines() if line.strip()]
    gotos = [r for r in records if r["tool"] == "go_to_location"]
    assert any(r["verdict"] == "allowed" for r in gotos)
    assert any(r["verdict"] == "rejected" and r["rule"].startswith("geofence.") for r in gotos)
    assert all(r["latency_ms"] >= 0 for r in records)
    assert any(r["tool"] == "takeoff" and r["verdict"] == "allowed" for r in records)


def test_emergency_stop_needs_no_confirmation(control_tools):
    """The e-stop must work first time - a token round-trip in an emergency
    would be a hazard. (Drone is on the ground here; land is a no-op.)"""
    result = control_tools.call("emergency_stop", mode="land", timeout=60)
    assert result["status"] in ("success", "failed"), result
    assert result.get("status") != "confirmation_required"


def test_emergency_stop_rejects_bad_mode(control_tools):
    result = control_tools.call("emergency_stop", mode="explode")
    assert result["status"] == "failed"


def test_emergency_stop_kill_requires_a_confirmation_token(control_tools):
    """FIX 4 (safety review 2026-08-19, option (b)): mode="kill" is gated.

    Cutting the motors is the same physical effect as kill_motors, which has
    always been token-gated. So emergency_stop kill mode now escalates to
    CRITICAL and takes the same two-call handshake; the recovery modes (land,
    rtl) stay ungated. Exercised on the GROUND and disarmed, where killing the
    motors is a no-op, so the test proves the gate without risking a crash.
    """
    armed = control_tools.call("get_armed", timeout=30)
    assert armed.get("status") == "success" and armed["armed"] is False, (
        f"this test must run disarmed on the ground; state was {armed}"
    )

    # First call, no token: refused with a confirmation demand, nothing run.
    issued = control_tools.call("emergency_stop", mode="kill", timeout=60)
    assert issued["status"] == "confirmation_required", (
        "kill mode must now require a confirmation token, exactly like kill_motors"
    )
    assert issued["confirm_token"], issued

    still_disarmed = control_tools.call("get_armed", timeout=30)
    assert still_disarmed["armed"] is False

    # Second call quoting the token executes.
    result = control_tools.call(
        "emergency_stop", mode="kill", confirm_token=issued["confirm_token"], timeout=60
    )
    assert result["status"] in ("success", "failed"), result
    if result["status"] == "success":
        assert result["mode"] == "kill"
        assert any("KILL" in a.upper() for a in result["actions_taken"]), result

    still_disarmed = control_tools.call("get_armed", timeout=30)
    assert still_disarmed["armed"] is False


def test_emergency_stop_recovery_modes_still_need_no_token(control_tools):
    """The recovery modes stay ungated - a handshake in an emergency is itself
    a hazard, so land and rtl must never demand a token."""
    for mode in ("land", "rtl"):
        result = control_tools.call("emergency_stop", mode=mode, timeout=60)
        assert result.get("status") != "confirmation_required", (
            f"emergency_stop(mode={mode!r}) is a safe recovery and must never require a token"
        )
        assert result["status"] in ("success", "failed"), result


def test_emergency_stop_is_exempt_from_rate_limiting(control_tools):
    """B2 coverage: the exemption is deliberate; pin it so it cannot regress
    silently into a throttled path."""
    last = None
    for _ in range(8):
        last = control_tools.call("emergency_stop", mode="land", timeout=60)
        assert not (isinstance(last, dict) and str(last.get("rule", "")).startswith("rate_limit")), last
    assert isinstance(last, dict)
