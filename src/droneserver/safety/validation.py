"""Command validation: parameter bounds, state preconditions, geofence, rate limits.

Every rule is a pure function of (tool, args, state, settings) so it can be
unit-tested without a drone. Rejections carry a stable ``rule`` id (used by the
audit log and the adversarial results table) and an LLM-actionable
``remedy`` - the message tells the model why it was stopped and what to do
instead, rather than just "denied".
"""

import time
from collections import defaultdict, deque
from dataclasses import dataclass, field

from droneserver.safety.config import SafetySettings
from droneserver.safety.geofence import Geofence, check_mission, check_position


@dataclass(frozen=True)
class Rejection:
    rule: str
    reason: str
    remedy: str

    def as_result(self) -> dict:
        """The dict handed back to the LLM as the tool result."""
        return {
            "status": "rejected",
            "error": self.reason,
            "rule": self.rule,
            "remedy": self.remedy,
            "safety_layer": "droneserver.safety",
        }


# --------------------------------------------------------------- altitude frames
#
# CAREFUL: tools do not agree on an altitude frame. Bounds and the geofence
# ceiling are expressed as height ABOVE HOME (AGL-ish), so AMSL arguments must
# be converted using the home altitude before they can be compared. If the home
# altitude is not known yet, AMSL altitudes are NOT range-checked (checking
# them against the wrong datum would reject legitimate commands); the
# horizontal fence still applies. This is called out in docs/safety_review.md.

REL = "relative"  # metres above home / takeoff point
AMSL = "amsl"  # metres above mean sea level
NED_DOWN = "ned_down"  # metres DOWN from the offboard origin (negative = up)
DYNAMIC = "dynamic"  # frame given by another argument

#: Tools whose arguments name an absolute target position:
#: tool -> (lat_arg, lon_arg, alt_arg|None, alt_frame)
_POSITION_ARGS: dict[str, tuple[str, str, str | None, str]] = {
    "go_to_location": ("latitude_deg", "longitude_deg", "absolute_altitude_m", AMSL),
    "reposition": ("latitude_deg", "longitude_deg", "altitude_m", AMSL),
    "do_orbit": ("latitude_deg", "longitude_deg", "absolute_altitude_m", AMSL),
    "offboard_set_position_global": ("latitude_deg", "longitude_deg", "altitude_m", DYNAMIC),
}

#: Tools carrying lat/lon that are NOT flight targets - sanity-checked but
#: never fenced. Pointing a camera at something outside the fence is not a
#: containment breach, and these tools default lat/lon to 0.0 when the caller
#: is doing something else entirely (gimbal_point action="set_angles"), which
#: made a configured polygon reject every gimbal command.
_LOOK_AT_ARGS: dict[str, tuple[str, str]] = {
    "gimbal_point": ("latitude_deg", "longitude_deg"),
}

#: Tools whose target is an OFFSET from the vehicle's current position. To
#: fence these we need to resolve the offset against live position.
_RELATIVE_TARGET_TOOLS = frozenset({"move_to_relative", "offboard_set_position_ned"})

#: Velocity tools: fenced by projecting the commanded velocity forward over the
#: stale-setpoint window and checking the PREDICTED position.
_VELOCITY_TOOLS = frozenset({"offboard_set_velocity_ned", "offboard_set_velocity_body"})

#: Tools that command a follow-me / external target position.
_TARGET_LOCATION_TOOLS = frozenset({"follow_me"})

#: Metres per degree of latitude (good to <1% anywhere; used to convert small
#: local offsets to lat/lon for fence checks).
_M_PER_DEG_LAT = 111320.0

#: Tools that command an altitude without a horizontal target:
#: tool -> (alt_arg, frame)
_ALTITUDE_ONLY_ARGS: dict[str, tuple[str, str]] = {
    "takeoff": ("takeoff_altitude", REL),
    "flight_altitudes": ("altitude_m", REL),
    "offboard_set_position_ned": ("down_m", NED_DOWN),
    # S7: the managed-mission takeoff altitude was accepted unchecked.
    "start_managed_mission": ("takeoff_altitude_m", REL),
}


def _relative_altitude(tool: str, args: dict, state: dict) -> float | None:
    """Target altitude expressed as metres above home, or None if it cannot be
    determined (unknown frame or missing home altitude)."""
    frame: str | None = None
    raw: float | None = None
    if tool in _ALTITUDE_ONLY_ARGS:
        key, frame = _ALTITUDE_ONLY_ARGS[tool]
        raw = _num(args, key)
    elif tool in _POSITION_ARGS:
        _, _, alt_key, frame = _POSITION_ARGS[tool]
        raw = _num(args, alt_key) if alt_key else None
    if raw is None or frame is None:
        return None

    if frame == DYNAMIC:  # offboard_set_position_global
        kind = str(args.get("altitude_type", "rel_home")).lower()
        frame = AMSL if kind == "amsl" else REL
    if frame == REL:
        return raw
    if frame == NED_DOWN:
        return -raw  # down is negative-up
    # AMSL: needs the home altitude to become comparable
    home_amsl = state.get("home_altitude_m")
    if home_amsl is None:
        return None
    return raw - float(home_amsl)


#: Tools that command a speed.
_SPEED_ARGS: dict[str, tuple[str, ...]] = {
    "set_max_speed": ("speed_m_s",),
    "do_orbit": ("velocity_m_s",),
    "offboard_set_velocity_ned": ("north_m_s", "east_m_s", "down_m_s"),
    "offboard_set_velocity_body": ("forward_m_s", "right_m_s", "down_m_s"),
}

#: Tools that move the vehicle and therefore require it to be airborne.
NAVIGATION_TOOLS = frozenset(
    {
        "go_to_location",
        "move_to_relative",
        "reposition",
        "set_yaw",
        "do_orbit",
        "offboard_set_position_ned",
        "offboard_set_position_global",
        "offboard_set_velocity_ned",
        "offboard_set_velocity_body",
        "offboard_set_attitude",
        "offboard_set_acceleration_ned",
        "offboard_set_actuator_control",
        "offboard_control",
    }
)

#: Tools that start a mission (require a mission to be on board).
MISSION_START_TOOLS = frozenset({"initiate_mission", "resume_mission"})

#: Tools that upload a mission (whole-mission fence validation).
MISSION_UPLOAD_TOOLS = frozenset({"upload_mission", "initiate_mission"})


def _num(args: dict, key: str):
    value = args.get(key)
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


# --------------------------------------------------------------- rules


def check_parameter_bounds(tool: str, args: dict, s: SafetySettings, state: dict | None = None) -> Rejection | None:
    """Altitude / speed / coordinate sanity, independent of the geofence."""
    state = state or {}
    altitude = _relative_altitude(tool, args, state)

    # B3: offset commands (move_to_relative, offboard_set_position_ned) got no
    # bounds at all. Bound the offset magnitude - which works even with no
    # position fix - and resolve the commanded altitude against live position.
    if tool in _RELATIVE_TARGET_TOOLS:
        north, east = _num(args, "north_m") or 0.0, _num(args, "east_m") or 0.0
        offset_m = (north**2 + east**2) ** 0.5
        if offset_m > s.max_distance_from_home_m:
            return Rejection(
                "bounds.max_offset",
                f"requested move of {offset_m:.0f} m exceeds the configured maximum "
                f"single-command distance of {s.max_distance_from_home_m:.0f} m",
                f"Break the move into steps of at most {s.max_distance_from_home_m:.0f} m.",
            )
        position = state.get("position") or {}
        down = _num(args, "down_m")
        if altitude is None and down is not None and position.get("relative_altitude_m") is not None:
            altitude = position["relative_altitude_m"] - down
    if altitude is not None and altitude > s.max_altitude_m:
        return Rejection(
            "bounds.max_altitude",
            f"requested altitude {altitude:.1f} m exceeds the configured maximum of {s.max_altitude_m:.1f} m",
            f"Request an altitude of {s.max_altitude_m:.0f} m or less.",
        )
    if altitude is not None and altitude < s.min_altitude_m:
        return Rejection(
            "bounds.min_altitude",
            f"requested altitude {altitude:.1f} m is below the configured minimum of {s.min_altitude_m:.1f} m",
            f"Request an altitude of at least {s.min_altitude_m:.0f} m.",
        )

    # speed
    for key in _SPEED_ARGS.get(tool, ()):
        speed = _num(args, key)
        if speed is not None and abs(speed) > s.max_speed_m_s:
            return Rejection(
                "bounds.max_speed",
                f"requested {key}={speed} m/s exceeds the configured maximum speed of {s.max_speed_m_s} m/s",
                f"Use a speed magnitude of {s.max_speed_m_s} m/s or less.",
            )

    # coordinate sanity
    if tool in _POSITION_ARGS:
        lat_key, lon_key, _, _ = _POSITION_ARGS[tool]
        lat, lon = _num(args, lat_key), _num(args, lon_key)
        if lat is not None and not -90 <= lat <= 90:
            return Rejection(
                "bounds.latitude",
                f"latitude {lat} is not a valid coordinate",
                "Provide a latitude between -90 and 90 degrees.",
            )
        if lon is not None and not -180 <= lon <= 180:
            return Rejection(
                "bounds.longitude",
                f"longitude {lon} is not a valid coordinate",
                "Provide a longitude between -180 and 180 degrees.",
            )

    # mission size
    if tool in MISSION_UPLOAD_TOOLS:
        items = args.get("waypoints") or args.get("mission_points") or []
        if isinstance(items, list) and len(items) > s.max_mission_items:
            return Rejection(
                "bounds.mission_size",
                f"mission has {len(items)} items, more than the configured maximum of {s.max_mission_items}",
                f"Split the mission into segments of at most {s.max_mission_items} items.",
            )
    return None


#: Tools that must not run while the vehicle is airborne.
GROUND_ONLY_TOOLS = frozenset({"calibrate", "cancel_calibration"})

#: Every tool whose safety depends on vehicle state - the full set the
#: fail-closed policy applies to (S9: it previously covered navigation only).
STATE_DEPENDENT_RULES = NAVIGATION_TOOLS | MISSION_START_TOOLS | GROUND_ONLY_TOOLS | {"takeoff"}


def check_preconditions(tool: str, args: dict, state: dict, s: SafetySettings) -> Rejection | None:
    """Vehicle-state preconditions, including the takeoff-then-crash fix."""
    # Evaluated before the unknown-state early return: calibrating in flight is
    # a crash, so "we cannot tell whether we are flying" must block it
    # regardless of the fail-open/fail-closed policy.
    if tool in GROUND_ONLY_TOOLS and (state.get("in_air") or state.get("unknown")):
        return Rejection(
            "precondition.ground_only",
            f"{tool} was commanded while the drone is airborne (or its state is unknown)",
            "Land and disarm first. Calibration must never run in flight.",
        )

    if state.get("unknown"):
        # Telemetry unreadable. Default is fail-open (documented) so a
        # telemetry hiccup cannot strand an airborne vehicle. When configured
        # to fail closed this now covers EVERY state-dependent rule, not just
        # navigation.
        if s.preconditions_fail_closed and tool in STATE_DEPENDENT_RULES:
            return Rejection(
                "precondition.state_unknown",
                "vehicle state could not be read and the safety layer is configured to fail closed",
                "Wait for telemetry to recover, then retry. Use get_health to check the link.",
            )
        return None

    armed = bool(state.get("armed"))
    in_air = bool(state.get("in_air"))

    if tool == "takeoff" and s.require_armed_for_takeoff and not armed:
        return Rejection(
            "precondition.takeoff_requires_armed",
            "takeoff was commanded while the drone is disarmed",
            "Call arm_drone first, then takeoff.",
        )

    # offboard_control("stop"/"status") must work on the ground: stopping and
    # querying are how a caller recovers from a bad state, and refusing them
    # would be a usability trap. Only "start" actually commands motion.
    navigation = tool in NAVIGATION_TOOLS
    if tool == "offboard_control" and str(args.get("action", "")).lower() != "start":
        navigation = False

    if navigation and s.require_in_air_for_navigation:
        if not in_air:
            return Rejection(
                "precondition.navigation_requires_airborne",
                f"{tool} was commanded while the drone is on the ground",
                "Call takeoff first and wait for it to report the target altitude, then navigate.",
            )
        # The takeoff-then-crash timing fix, formalized: a navigation command
        # issued in the first moments after takeoff can be executed while the
        # vehicle is still climbing, which historically caused a crash.
        since_takeoff = state.get("seconds_since_takeoff")
        if since_takeoff is not None and since_takeoff < s.takeoff_settle_s:
            return Rejection(
                "precondition.takeoff_settling",
                f"{tool} was commanded {since_takeoff:.1f} s after takeoff, inside the "
                f"{s.takeoff_settle_s:.0f} s settling window",
                "Wait until takeoff reports its target altitude (or poll get_position) before navigating.",
            )

    if tool in MISSION_START_TOOLS and state.get("mission_uploaded") is False:
        return Rejection(
            "precondition.mission_required",
            f"{tool} was called but no mission has been uploaded in this session",
            "Upload a mission first (upload_mission or import_qgc_mission), then start it.",
        )
    return None


def _offset_to_latlon(lat: float, lon: float, north_m: float, east_m: float) -> tuple[float, float]:
    """Apply a local NED offset (metres) to a lat/lon."""
    import math

    dlat = north_m / _M_PER_DEG_LAT
    dlon = east_m / (_M_PER_DEG_LAT * max(math.cos(math.radians(lat)), 1e-6))
    return lat + dlat, lon + dlon


def resolve_target(
    tool: str, args: dict, state: dict, s: SafetySettings
) -> tuple[float | None, float | None, float | None, str | None]:
    """Resolve a command's horizontal target to (lat, lon, alt_rel, error).

    Handles the three cases the fence previously missed entirely:

    - offsets from the current position (``move_to_relative``,
      ``offboard_set_position_ned``)
    - velocities, projected forward over the stale-setpoint window
    - follow-me target locations

    ``error`` is set when a target *should* be fenced but cannot be resolved
    (no live position). Callers reject in that case: refusing to move is the
    safe direction, and silently skipping the fence is what the independent
    review flagged.
    """
    position = state.get("position") or {}
    cur_lat, cur_lon = position.get("latitude_deg"), position.get("longitude_deg")
    cur_alt = position.get("relative_altitude_m")

    if tool in _TARGET_LOCATION_TOOLS:
        if str(args.get("action", "")).lower() != "target":
            return None, None, None, None
        return _num(args, "latitude_deg"), _num(args, "longitude_deg"), None, None

    if tool in _RELATIVE_TARGET_TOOLS:
        north, east = _num(args, "north_m"), _num(args, "east_m")
        down = _num(args, "down_m")
        if north is None and east is None and down is None:
            return None, None, None, None
        if cur_lat is None or cur_lon is None:
            return None, None, None, "current position unknown"
        lat, lon = _offset_to_latlon(cur_lat, cur_lon, north or 0.0, east or 0.0)
        alt = None
        if down is not None:
            # move_to_relative: down is relative to the CURRENT altitude.
            # offboard_set_position_ned: down is relative to the offboard
            # origin, which the server approximates as the current position.
            alt = (cur_alt if cur_alt is not None else 0.0) - down
        return lat, lon, alt, None

    if tool in _VELOCITY_TOOLS:
        horizon_s = _num(args, "stale_timeout_s") or 15.0
        if tool == "offboard_set_velocity_ned":
            north, east = _num(args, "north_m_s") or 0.0, _num(args, "east_m_s") or 0.0
        else:  # body frame: without heading, treat the magnitude as worst case
            forward = _num(args, "forward_m_s") or 0.0
            right = _num(args, "right_m_s") or 0.0
            magnitude = (forward**2 + right**2) ** 0.5
            north, east = magnitude, 0.0
        if north == 0.0 and east == 0.0:
            return None, None, None, None
        if cur_lat is None or cur_lon is None:
            return None, None, None, "current position unknown"
        lat, lon = _offset_to_latlon(cur_lat, cur_lon, north * horizon_s, east * horizon_s)
        return lat, lon, None, None

    return None, None, None, None


def check_geofence(
    tool: str, args: dict, fence: Geofence, s: SafetySettings, state: dict | None = None
) -> Rejection | None:
    """Server-side fence: single targets, offsets, velocities and whole missions."""
    if not fence.active:
        return None
    state = state or {}

    # S8: a configured radius fence is inert until home is known. Surface that
    # instead of silently passing every target.
    if (
        fence.max_radius_m > 0
        and fence.home is None
        and (
            tool in _POSITION_ARGS
            or tool in _RELATIVE_TARGET_TOOLS
            or tool in _VELOCITY_TOOLS
            or tool in MISSION_UPLOAD_TOOLS
        )
    ):
        return Rejection(
            "geofence.home_unknown",
            "a radius geofence is configured but the drone's home position has not been "
            "read yet, so the fence cannot be enforced",
            "Wait for a GPS/home fix (get_home_position) and retry. The command was refused "
            "rather than flown unfenced.",
        )

    if tool in MISSION_UPLOAD_TOOLS:
        items = args.get("waypoints") or args.get("mission_points") or []
        if isinstance(items, list):
            violations = check_mission(fence, items)
            if violations:
                idx, first = violations[0]
                return Rejection(
                    f"{first.rule}.mission_item",
                    f"mission item {idx} violates the geofence: {first.detail} "
                    f"({len(violations)} of {len(items)} items violate it)",
                    "Move the offending waypoints inside the geofence and upload again. "
                    "The whole mission is rejected; nothing was sent to the drone.",
                )

    lat = lon = None
    if tool in _POSITION_ARGS:
        lat_key, lon_key, _, _ = _POSITION_ARGS[tool]
        lat, lon = _num(args, lat_key), _num(args, lon_key)
    alt = _relative_altitude(tool, args, state)

    resolved_lat, resolved_lon, resolved_alt, resolve_error = resolve_target(tool, args, state, s)
    if resolve_error is not None:
        return Rejection(
            "geofence.target_unresolvable",
            f"{tool} commands a target relative to the drone, but {resolve_error}, so the geofence cannot be checked",
            "Wait for position telemetry (get_position) and retry. The command was refused rather than flown unfenced.",
        )
    if resolved_lat is not None:
        lat, lon = resolved_lat, resolved_lon
    if resolved_alt is not None:
        alt = resolved_alt

    if lat is None and lon is None and alt is None:
        return None

    violation = check_position(fence, lat, lon, alt)
    if violation is None:
        return None
    return Rejection(
        violation.rule,
        violation.detail,
        "Choose a target inside the geofence. Use get_home_position and the configured fence "
        "to pick a valid point, or ask the operator to widen the fence.",
    )


# --------------------------------------------------------------- rate limiting


@dataclass
class RateLimiter:
    """Sliding-window limiter, per client and per tier class."""

    calls: dict[str, deque] = field(default_factory=lambda: defaultdict(deque))

    def check(self, client_id: str, critical: bool, s: SafetySettings, now: float | None = None) -> Rejection | None:
        now = time.monotonic() if now is None else now
        limit = s.rate_limit_critical_calls if critical else s.rate_limit_calls
        window = s.rate_limit_critical_window_s if critical else s.rate_limit_window_s
        key = f"{client_id}:{'critical' if critical else 'normal'}"
        bucket = self.calls[key]
        while bucket and now - bucket[0] > window:
            bucket.popleft()
        if len(bucket) >= limit:
            retry_in = window - (now - bucket[0])
            return Rejection(
                "rate_limit.critical" if critical else "rate_limit.normal",
                f"rate limit exceeded: more than {limit} {'critical ' if critical else ''}calls in {window:.0f}s",
                f"Wait about {max(retry_in, 1):.0f}s before retrying. If you are polling, "
                "poll less often or use a monitoring tool that blocks.",
            )
        bucket.append(now)
        return None

    def reset(self) -> None:
        self.calls.clear()
