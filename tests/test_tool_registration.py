"""Smoke tests: the package must expose exactly the expected tool inventory
(45 v1 tools + the v2 additions).

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

# v2 additions (see docs/tool_groups.md)
V2_TOOLS = {
    # P0
    "upload_geofence",
    "clear_geofence",
    "offboard_control",
    "offboard_set_position_ned",
    "offboard_set_position_global",
    "offboard_set_velocity_ned",
    "offboard_set_velocity_body",
    "offboard_set_attitude",
    "offboard_set_acceleration_ned",
    "offboard_set_actuator_control",
    # P1
    "list_cameras",
    "camera_capture",
    "camera_settings",
    "camera_storage",
    "camera_zoom_focus",
    "camera_tracking",
    "list_gimbals",
    "gimbal_control",
    "gimbal_point",
    "import_qgc_mission",
    "rally_points",
    "raw_geofence_transfer",
    "raw_mission_control",
    "flight_logs",
    # v1-plugin completion (action / telemetry / mission / param)
    "do_orbit",
    "vehicle_power",
    "set_actuator",
    "flight_altitudes",
    "vtol_transition",
    "get_telemetry_extended",
    "set_telemetry_rate",
    "rtl_after_mission",
    "cancel_mission_transfer",
    "param_select_component",
    # P2 / P3
    "system_info",
    "send_status_text",
    "set_mavlink_timeout",
    "calibrate",
    "cancel_calibration",
    "inject_failure",
    "manual_control",
    "follow_me",
    "payload_mechanism",
    "read_transponder",
    "play_tune",
    "send_mocap",
    "send_rtcm",
    "autopilot_files",
    "autopilot_shell",
}

EXPECTED_TOOLS = V1_TOOLS | V2_TOOLS


async def test_expected_tool_inventory_registered():
    tools = await mcp.list_tools()
    names = {t.name for t in tools}
    assert names == EXPECTED_TOOLS
    assert len(tools) == len(EXPECTED_TOOLS) == 94


async def test_tools_have_descriptions():
    for tool in await mcp.list_tools():
        assert tool.description, f"tool {tool.name} has no description"
