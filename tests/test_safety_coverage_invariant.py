"""Coverage invariant: no state-changing tool may be silently unguarded.

This is the structural fix the independent review asked for. Every defect it
found in the rule tables (move_to_relative with no bounds and no fence,
offboard NED/velocity horizontally unfenced, follow_me targets unfenced) was
the same shape: a tool was added, and nobody noticed it appeared in no rule
table. A reviewer cannot be expected to re-derive that by eye each time.

So: every registered tool whose tier is NORMAL or CRITICAL must either

1. appear in at least one validation rule table, or
2. be listed in :data:`NO_SPATIAL_OR_STATE_ARGS` with a written reason.

Adding a tool that commands motion without touching either fails this test.
"""

import asyncio

import pytest

import droneserver.tools  # noqa: F401  - registers all tools
from droneserver.app import mcp
from droneserver.safety import validation as V
from droneserver.safety.tiers import TOOL_TIERS, Tier

#: Tools that change state but carry no position, altitude, speed or
#: vehicle-state-dependent argument, so no rule table applies. Each entry is a
#: deliberate, reviewed statement - not a default.
NO_SPATIAL_OR_STATE_ARGS: dict[str, str] = {
    # --- vehicle commands with no spatial argument ---
    "arm_drone": "arming has no spatial argument; force=True escalates to CRITICAL instead",
    "disarm_drone": "no spatial argument; escalates to CRITICAL in air",
    "land": "descends in place; the tool's own landing gate checks distance to destination",
    "return_to_launch": "flies to home, which is by definition inside any home-based fence",
    "kill_motors": "CRITICAL + confirmation token; a fence cannot make motor-kill safe",
    "emergency_stop": "EMERGENCY tier; deliberately unguarded so it always works",
    "hold_position": "holds the current position - cannot leave the fence",
    "hold_mission_position": "holds the current position - cannot leave the fence",
    "set_flight_mode": "mode change, no target; modes are firmware-validated",
    "vehicle_power": "CRITICAL + token; reboot/shutdown/terminate have no spatial argument",
    "vtol_transition": "airframe transition, no target",
    "set_actuator": "CRITICAL + token; raw actuator index/value, no spatial meaning",
    "offboard_set_actuator_control": "CRITICAL + token; raw actuator groups, no spatial meaning",
    "offboard_set_attitude": "attitude/rate setpoint; bounded by build_attitude, no position",
    "offboard_set_acceleration_ned": "acceleration setpoint; bounded, and the velocity it "
    "produces is fenced on the following setpoint",
    "manual_control": "normalised stick inputs, bounded by the tool; no absolute target",
    # --- mission plumbing (the mission itself is fence-validated on upload) ---
    "clear_mission": "removes a mission; cannot move the drone",
    "pause_mission": "holds position",
    "resume_mission": "resumes an already fence-validated mission",
    "set_current_waypoint": "selects an item of an already fence-validated mission",
    "rtl_after_mission": "sets a flag",
    "cancel_mission_transfer": "cancels a transfer",
    "raw_mission_control": "start/pause/clear of an already fence-validated mission",
    "control_managed_mission": "pause/resume/abort/clear of a running mission",
    "rally_points": "rally points are alternates the firmware chooses; not a commanded target",
    "upload_geofence": "defines the firmware fence; escalates to CRITICAL in air",
    "clear_geofence": "removes the firmware fence; escalates to CRITICAL in air",
    "raw_geofence_transfer": "writes raw fence items; escalates to CRITICAL in air",
    # --- payload / peripheral / config ---
    "camera_capture": "camera control, no vehicle motion",
    "camera_settings": "camera control, no vehicle motion",
    "camera_storage": "camera storage; format escalates to CRITICAL",
    "camera_zoom_focus": "optics only",
    "camera_tracking": "normalised image coordinates, bounded by the tool",
    "gimbal_control": "gimbal ownership, no vehicle motion",
    "gimbal_point": "points a camera; lat/lon are a look-at target, never a flight target",
    "payload_mechanism": "gripper/winch actuation",
    "play_tune": "buzzer",
    "send_mocap": "feeds an external position ESTIMATE; does not command motion",
    "send_rtcm": "GPS corrections",
    "send_status_text": "log annotation",
    "set_mavlink_timeout": "link timeout setting",
    "set_telemetry_rate": "telemetry rate; bounded by the tool",
    "param_select_component": "selects which component parameters address",
    "set_parameter": "arbitrary parameter write; safety-relevant names escalate to CRITICAL",
    "autopilot_files": "filesystem; destructive actions escalate to CRITICAL",
    "autopilot_shell": "CRITICAL + token; arbitrary console access cannot be range-checked",
    "inject_failure": "CRITICAL + token; simulation-only failure injection",
    "calibrate": "ground-only precondition applies (GROUND_ONLY_TOOLS)",
    "cancel_calibration": "ground-only precondition applies (GROUND_ONLY_TOOLS)",
    "flight_logs": "log management; erase_all escalates to CRITICAL",
    "import_qgc_mission": "imported plans are fence-validated inside the tool before upload",
    "monitor_flight": "read-mostly progress monitor; may auto-land in place",
    "follow_me": "target locations are fence-checked via resolve_target",
}


def _rule_tables() -> dict[str, frozenset]:
    """Every set/table a tool can appear in to be considered guarded."""
    return {
        "position_args": frozenset(V._POSITION_ARGS),
        "altitude_args": frozenset(V._ALTITUDE_ONLY_ARGS),
        "speed_args": frozenset(V._SPEED_ARGS),
        "relative_target": V._RELATIVE_TARGET_TOOLS,
        "velocity": V._VELOCITY_TOOLS,
        "target_location": V._TARGET_LOCATION_TOOLS,
        "navigation": V.NAVIGATION_TOOLS,
        "mission_upload": V.MISSION_UPLOAD_TOOLS,
        "mission_start": V.MISSION_START_TOOLS,
        "ground_only": V.GROUND_ONLY_TOOLS,
    }


def _registered_tools() -> set[str]:
    return {t.name for t in asyncio.run(mcp.list_tools())}


def _state_changing_tools() -> set[str]:
    return {
        name
        for name in _registered_tools()
        if TOOL_TIERS.get(name, Tier.CRITICAL) in (Tier.NORMAL, Tier.CRITICAL, Tier.EMERGENCY)
    }


def test_every_state_changing_tool_is_guarded_or_explicitly_exempt():
    tables = _rule_tables()
    guarded = set().union(*tables.values())
    unaccounted = sorted(_state_changing_tools() - guarded - set(NO_SPATIAL_OR_STATE_ARGS))
    assert not unaccounted, (
        "These tools change vehicle state but appear in no validation rule table and are "
        "not listed in NO_SPATIAL_OR_STATE_ARGS. Either add them to the right rule table "
        f"in droneserver.safety.validation, or document why they need none: {unaccounted}"
    )


def test_exemption_list_has_no_stale_entries():
    stale = sorted(set(NO_SPATIAL_OR_STATE_ARGS) - _registered_tools())
    assert not stale, f"NO_SPATIAL_OR_STATE_ARGS lists tools that no longer exist: {stale}"


def test_exemptions_are_not_read_only_tools():
    """Read-only tools do not need an exemption; listing one hides a mistake."""
    read_only = {n for n in _registered_tools() if TOOL_TIERS.get(n) is Tier.READ_ONLY}
    wrong = sorted(set(NO_SPATIAL_OR_STATE_ARGS) & read_only)
    assert not wrong, f"read-only tools should not be in the exemption list: {wrong}"


def test_every_exemption_has_a_reason():
    empty = sorted(k for k, v in NO_SPATIAL_OR_STATE_ARGS.items() if not v.strip())
    assert not empty, f"exemptions need a written reason: {empty}"


#: Tools that can move the aircraft or commit it to motion. The 2026-08-16
#: energy-direction policy must classify every one of them explicitly - a
#: motion tool that falls through to NEUTRAL would be flown blind when
#: telemetry is unreadable, which is the exact failure the policy exists to
#: prevent. Arg-dependent tools are probed with their motion-commanding action.
MOTION_TOOLS: dict[str, dict] = {
    **{tool: {} for tool in V.NAVIGATION_TOOLS - set(V.ENERGY_DIRECTION_BY_ARGS)},
    **{tool: {} for tool in V.MISSION_START_TOOLS},
    "takeoff": {},
    "arm_drone": {},
    "start_managed_mission": {},
    "set_max_speed": {},
    "manual_control": {},
    "vtol_transition": {},
    "offboard_control": {"action": "start"},
    "follow_me": {"action": "start"},
    "raw_mission_control": {"action": "start"},
    "control_managed_mission": {"action": "resume"},
    "set_flight_mode": {"mode": "AUTO"},
}

#: Tools that reduce energy or recover the aircraft. Classifying one of these
#: as anything but REDUCES would let unreadable telemetry block the abort path.
RECOVERY_TOOLS: dict[str, dict] = {
    "land": {},
    "return_to_launch": {},
    "hold_position": {},
    "hold_mission_position": {},
    "pause_mission": {},
    "emergency_stop": {"mode": "kill"},
    "kill_motors": {},
    "disarm_drone": {},
    "offboard_control": {"action": "stop"},
    "set_flight_mode": {"mode": "LAND"},
}


@pytest.mark.parametrize("tool,args", sorted(MOTION_TOOLS.items()))
def test_every_motion_tool_is_classified_energy_adding(tool, args):
    assert V.energy_direction(tool, args) is V.EnergyDirection.ADDS, (
        f"{tool} can commit the aircraft to motion but is not classified ADDS in "
        "droneserver.safety.validation.ENERGY_DIRECTION - on unknown telemetry it would "
        "be allowed through unchecked"
    )


@pytest.mark.parametrize("tool,args", sorted(RECOVERY_TOOLS.items()))
def test_every_recovery_tool_is_classified_energy_reducing(tool, args):
    assert V.energy_direction(tool, args) is V.EnergyDirection.REDUCES, (
        f"{tool} recovers or de-energises the aircraft and must stay available when "
        "telemetry is unreadable; classify it REDUCES"
    )


def test_energy_tables_only_reference_real_tools():
    registered = _registered_tools()
    stale = sorted((set(V.ENERGY_DIRECTION) | set(V.ENERGY_DIRECTION_BY_ARGS)) - registered)
    assert not stale, f"the energy-direction tables list tools that no longer exist: {stale}"


@pytest.mark.parametrize(
    "tool",
    [
        "go_to_location",
        "reposition",
        "do_orbit",
        "move_to_relative",
        "offboard_set_position_ned",
        "offboard_set_position_global",
        "offboard_set_velocity_ned",
        "offboard_set_velocity_body",
        "takeoff",
        "upload_mission",
        "initiate_mission",
        "start_managed_mission",
    ],
)
def test_known_motion_tools_are_in_a_rule_table(tool):
    """A direct regression net for the tools the review found unguarded."""
    guarded = set().union(*_rule_tables().values())
    assert tool in guarded, f"{tool} must be covered by a validation rule table"
