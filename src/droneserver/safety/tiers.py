"""Criticality tiers - THE review artifact.

Every registered tool has exactly one entry in :data:`TOOL_TIERS`. A tool with
no entry is treated as CRITICAL (fail-safe: a newly added tool cannot slip in
unclassified), and the test suite asserts the table covers the whole registry.

Tiers
-----
``READ_ONLY``  Cannot change vehicle state. Allowed for telemetry-scope clients.
``NORMAL``     Changes vehicle state; requires control scope; validated + fenced.
``CRITICAL``   Can end the flight, destroy data, or disable safety. Requires
               control scope AND a confirmation-token round-trip.
``EMERGENCY``  Deliberately NOT token-gated - a confirmation round-trip in an
               emergency is a safety hazard. Requires control scope, is always
               audited, and is exempt from rate limiting.

Conditional escalation
----------------------
Some tools are only dangerous in a particular vehicle state (disarming on the
ground is routine; disarming in the air is a crash). :data:`ESCALATIONS`
promotes NORMAL -> CRITICAL when its predicate holds. Both tables are read
together by :func:`effective_tier`.
"""

from collections.abc import Callable
from enum import Enum


class Tier(str, Enum):
    READ_ONLY = "read_only"
    NORMAL = "normal"
    CRITICAL = "critical"
    EMERGENCY = "emergency"


R, N, C, E = Tier.READ_ONLY, Tier.NORMAL, Tier.CRITICAL, Tier.EMERGENCY

#: Base tier for every registered tool. Reviewed by a human before hardware use.
TOOL_TIERS: dict[str, Tier] = {
    # ---------------- emergency ----------------
    "emergency_stop": E,
    # ---------------- critical (always) ----------------
    "kill_motors": C,  # stops motors instantly - vehicle falls
    "vehicle_power": C,  # reboot / shutdown / flight termination
    "autopilot_shell": C,  # arbitrary autopilot console access
    "inject_failure": C,  # deliberately breaks a sensor/system
    "set_actuator": C,  # raw actuator override
    "offboard_set_actuator_control": C,  # raw actuator override (offboard)
    # ---------------- conditionally critical (see ESCALATIONS) ----------------
    "disarm_drone": N,  # routine on ground, catastrophic in air
    "arm_drone": N,  # force=True escalates (bypasses prearm checks)
    "clear_geofence": N,  # removing containment while flying escalates
    "flight_logs": N,  # action="erase_all" escalates (destroys evidence)
    "camera_storage": N,  # action="format" escalates (destroys media)
    "set_parameter": N,  # safety-relevant parameter names escalate
    "land": N,  # landing away from a destination is gated by the tool itself
    # ---------------- normal (state-changing, validated) ----------------
    "takeoff": N,
    "return_to_launch": N,
    "go_to_location": N,
    "move_to_relative": N,
    "reposition": N,
    "set_yaw": N,
    "hold_position": N,
    "set_max_speed": N,
    "set_flight_mode": N,
    "monitor_flight": N,
    "do_orbit": N,
    "flight_altitudes": N,
    "vtol_transition": N,
    "initiate_mission": N,
    "upload_mission": N,
    "clear_mission": N,
    "pause_mission": N,
    "hold_mission_position": N,
    "resume_mission": N,
    "set_current_waypoint": N,
    "rtl_after_mission": N,
    "cancel_mission_transfer": N,
    "import_qgc_mission": N,
    "rally_points": N,
    "raw_geofence_transfer": N,
    "raw_mission_control": N,
    "upload_geofence": N,
    "offboard_control": N,
    "offboard_set_position_ned": N,
    "offboard_set_position_global": N,
    "offboard_set_velocity_ned": N,
    "offboard_set_velocity_body": N,
    "offboard_set_attitude": N,
    "offboard_set_acceleration_ned": N,
    "camera_capture": N,
    "camera_settings": N,
    "camera_zoom_focus": N,
    "camera_tracking": N,
    "gimbal_control": N,
    "gimbal_point": N,
    "calibrate": N,
    "cancel_calibration": N,
    "manual_control": N,
    "follow_me": N,
    "payload_mechanism": N,
    "play_tune": N,
    "send_mocap": N,
    "send_rtcm": N,
    "send_status_text": N,
    "set_mavlink_timeout": N,
    "set_telemetry_rate": N,
    "param_select_component": N,
    "autopilot_files": N,  # FTP writes; shell is separate and CRITICAL
    # ---------------- read-only ----------------
    "get_position": R,
    "get_battery": R,
    "get_health": R,
    "get_health_all_ok": R,
    "get_home_position": R,
    "get_speed": R,
    "get_attitude": R,
    "get_gps_info": R,
    "get_in_air": R,
    "get_armed": R,
    "get_landed_state": R,
    "get_rc_status": R,
    "get_heading": R,
    "get_odometry": R,
    "get_imu": R,
    "get_flight_mode": R,
    "print_status_text": R,
    "print_mission_progress": R,
    "is_mission_finished": R,
    "check_arrival": R,
    "download_mission": R,
    "get_parameter": R,
    "list_parameters": R,
    "get_telemetry_extended": R,
    "list_cameras": R,
    "list_gimbals": R,
    "system_info": R,
    "read_transponder": R,
}

#: Human-readable consequence shown in the confirmation prompt. Keyed by tool;
#: ESCALATIONS may supply a more specific one.
CONSEQUENCES: dict[str, str] = {
    "emergency_stop": "Cuts the flight short immediately.",
    "kill_motors": "Motors stop INSTANTLY. If airborne the drone will FALL and likely be destroyed.",
    "vehicle_power": "Reboots/shuts down/terminates the autopilot. In flight this means loss of control.",
    "autopilot_shell": "Runs an arbitrary command on the autopilot's console.",
    "inject_failure": "Deliberately disables or corrupts a sensor/system (simulation experiment).",
    "set_actuator": "Directly drives an actuator output, bypassing the flight controller's mixer.",
    "offboard_set_actuator_control": "Directly drives actuator outputs, bypassing normal control.",
    "disarm_drone": "Disarms the motors. IN AIR THIS CAUSES A CRASH.",
    "arm_drone": "Force-arms WITHOUT prearm safety checks (sensor/EKF/GPS checks bypassed).",
    "clear_geofence": "Removes the drone's geofence containment while it is flying.",
    "flight_logs": "PERMANENTLY ERASES all flight logs stored on the drone.",
    "camera_storage": "PERMANENTLY ERASES the camera storage (all photos/videos).",
    "set_parameter": "Changes a safety-critical autopilot parameter; wrong values can make the drone unflyable.",
}

#: Parameter names whose modification escalates ``set_parameter`` to CRITICAL.
SAFETY_CRITICAL_PARAM_PREFIXES = (
    "FENCE",
    "RTL",
    "BATT",
    "FS_",
    "ARMING",
    "SIM_",
    "GPS_TYPE",
    "EK3_ENABLE",
    "MOT_",
    "SERVO",
    "BRD_SAFETY",
    "THR_",
    "WPNAV_SPEED",
)

# predicate(args, state) -> (escalate, consequence_override|None)
Predicate = Callable[[dict, dict], tuple[bool, str | None]]


def _disarm_in_air(args: dict, state: dict) -> tuple[bool, str | None]:
    return bool(state.get("in_air")), None


def _force_arm(args: dict, state: dict) -> tuple[bool, str | None]:
    return bool(args.get("force")), None


def _clear_fence_in_air(args: dict, state: dict) -> tuple[bool, str | None]:
    return bool(state.get("in_air")), None


def _erase_logs(args: dict, state: dict) -> tuple[bool, str | None]:
    return str(args.get("action", "")).lower() == "erase_all", None


def _format_storage(args: dict, state: dict) -> tuple[bool, str | None]:
    return str(args.get("action", "")).lower() == "format", None


def _critical_param(args: dict, state: dict) -> tuple[bool, str | None]:
    name = str(args.get("name", "")).upper()
    if any(name.startswith(p) for p in SAFETY_CRITICAL_PARAM_PREFIXES):
        return True, f"Changes the safety-critical parameter {name}; wrong values can make the drone unflyable."
    return False, None


#: NORMAL -> CRITICAL escalations. Read together with TOOL_TIERS.
ESCALATIONS: dict[str, Predicate] = {
    "disarm_drone": _disarm_in_air,
    "arm_drone": _force_arm,
    "clear_geofence": _clear_fence_in_air,
    "flight_logs": _erase_logs,
    "camera_storage": _format_storage,
    "set_parameter": _critical_param,
}


def effective_tier(tool: str, args: dict, state: dict) -> tuple[Tier, str]:
    """Return the tier in force for this call plus its consequence statement.

    Unknown tools are CRITICAL by design - a tool added without a tier entry
    fails safe rather than silently running unguarded.
    """
    base = TOOL_TIERS.get(tool)
    if base is None:
        return Tier.CRITICAL, f"Tool '{tool}' has no criticality classification; treated as critical."
    consequence = CONSEQUENCES.get(tool, f"Executes {tool}.")
    if base is Tier.NORMAL and tool in ESCALATIONS:
        escalate, override = ESCALATIONS[tool](args, state)
        if escalate:
            return Tier.CRITICAL, override or consequence
    return base, consequence
