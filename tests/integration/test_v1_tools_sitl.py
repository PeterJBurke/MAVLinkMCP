"""End-to-end SITL integration tests for a handful of v1 tools.

The point is proving the harness (docker SITL -> droneserver -> MCP client),
not tool coverage - Phase 2 adds per-tool tests on top of these fixtures.
"""

import time

import pytest

from droneserver.geo import haversine_distance
from tests.integration.conftest import SITL_HOME
from tests.integration.mcp_client import ToolCallError

pytestmark = pytest.mark.sitl


def test_tool_inventory_over_the_wire(drone_tools):
    assert len(drone_tools.list_tools()) == 45


def test_get_position_reports_sitl_home(drone_tools):
    result = drone_tools.call("get_position")
    assert result["status"] == "success"
    pos = result["position"]
    assert haversine_distance(pos["latitude_deg"], pos["longitude_deg"], SITL_HOME["lat"], SITL_HOME["lon"]) < 200.0
    assert abs(pos["absolute_altitude_m"] - SITL_HOME["alt_amsl"]) < 50.0


def test_get_battery_reports_voltage(drone_tools):
    result = drone_tools.call("get_battery")
    assert result["status"] == "success"
    assert result["battery"]["voltage_v"] > 0


def test_flight_cycle_arm_takeoff_land_disarm(drone_tools):
    # Arm - retry briefly in case prearm checks are still settling.
    deadline = time.monotonic() + 90
    result = None
    while time.monotonic() < deadline:
        try:
            result = drone_tools.call("arm_drone", timeout=60)
        except ToolCallError as e:
            result = {"status": "failed", "error": str(e)}
        if isinstance(result, dict) and result.get("status") == "success":
            break
        time.sleep(3)
    assert isinstance(result, dict) and result.get("status") == "success", f"arm failed: {result}"

    # Takeoff to 10 m; the tool itself waits until the altitude is reached.
    result = drone_tools.call("takeoff", takeoff_altitude=10.0, wait_for_altitude=True, timeout=180)
    assert result["status"] == "success", f"takeoff failed: {result}"
    assert result["altitude_reached_m"] >= 9.0

    # Independent telemetry cross-check of the altitude.
    pos = drone_tools.call("get_position")
    assert pos["status"] == "success"
    assert pos["position"]["relative_altitude_m"] >= 8.0

    # Land (tool initiates and returns; Copter auto-disarms after touchdown).
    result = drone_tools.call("land", timeout=60)
    assert result["status"] == "success", f"land failed: {result}"

    deadline = time.monotonic() + 180
    armed = True
    while time.monotonic() < deadline:
        r = drone_tools.call("get_armed", timeout=30)
        if r.get("status") == "success":
            armed = r["armed"]
            if not armed:
                break
        time.sleep(3)
    assert armed is False, "drone did not disarm after landing"
