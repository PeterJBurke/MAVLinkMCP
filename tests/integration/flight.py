"""Shared flight helpers for SITL integration test modules."""

import time

from tests.integration.mcp_client import MCPToolClient, ToolCallError


def arm_and_takeoff(drone_tools: MCPToolClient, altitude_m: float = 15.0) -> None:
    """Arm (with prearm-settling retries) and take off, blocking until at altitude."""
    deadline = time.monotonic() + 90
    result: dict = {}
    while time.monotonic() < deadline:
        try:
            result = drone_tools.call("arm_drone", timeout=60)
        except ToolCallError as e:
            result = {"status": "failed", "error": str(e)}
        if result.get("status") == "success":
            break
        time.sleep(3)
    assert result.get("status") == "success", f"arm failed: {result}"

    result = drone_tools.call("takeoff", takeoff_altitude=altitude_m, wait_for_altitude=True, timeout=180)
    assert result["status"] == "success", f"takeoff failed: {result}"
    assert result["altitude_reached_m"] >= altitude_m - 1.5


def land_and_wait_disarm(drone_tools: MCPToolClient, timeout_s: float = 180.0) -> None:
    result = drone_tools.call("land", force=True, timeout=60)
    # "no_action" is the honest answer for an aircraft that already landed and
    # disarmed itself (e.g. a flown mission plan ending in auto-land) - FIX 5.
    assert result["status"] in ("success", "no_action"), f"land failed: {result}"
    if result["status"] == "no_action":
        return
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        r = drone_tools.call("get_armed", timeout=30)
        if r.get("status") == "success" and r["armed"] is False:
            return
        time.sleep(3)
    raise AssertionError("drone did not disarm after landing")
