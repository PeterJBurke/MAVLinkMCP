"""Smoke tests: the refactored package must expose exactly the 45 v1 tools.

No drone/SITL connection is made - only server-object introspection.
"""

import droneserver.tools  # noqa: F401  - registers all tools on import
from droneserver.app import mcp

# The v1 tool inventory (see docs/coverage_summary.md)
V1_TOOLS = {
    "arm_drone",
    "check_arrival",
    "clear_mission",
    "disarm_drone",
    "download_mission",
    "get_armed",
    "get_attitude",
    "get_battery",
    "get_flight_mode",
    "get_gps_info",
    "get_heading",
    "get_health",
    "get_health_all_ok",
    "get_home_position",
    "get_imu",
    "get_in_air",
    "get_landed_state",
    "get_odometry",
    "get_parameter",
    "get_position",
    "get_rc_status",
    "get_speed",
    "go_to_location",
    "hold_mission_position",
    "hold_position",
    "initiate_mission",
    "is_mission_finished",
    "kill_motors",
    "land",
    "list_parameters",
    "monitor_flight",
    "move_to_relative",
    "pause_mission",
    "print_mission_progress",
    "print_status_text",
    "reposition",
    "resume_mission",
    "return_to_launch",
    "set_current_waypoint",
    "set_flight_mode",
    "set_max_speed",
    "set_parameter",
    "set_yaw",
    "takeoff",
    "upload_mission",
}


async def test_all_45_v1_tools_registered():
    tools = await mcp.list_tools()
    names = {t.name for t in tools}
    assert names == V1_TOOLS
    assert len(tools) == 45


async def test_tools_have_descriptions():
    for tool in await mcp.list_tools():
        assert tool.description, f"tool {tool.name} has no description"
