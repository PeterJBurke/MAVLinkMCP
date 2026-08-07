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
    "gimbal_point": ("latitude_deg", "longitude_deg", None, REL),  # ROI target only
}

#: Tools that command an altitude without a horizontal target:
#: tool -> (alt_arg, frame)
_ALTITUDE_ONLY_ARGS: dict[str, tuple[str, str]] = {
    "takeoff": ("takeoff_altitude", REL),
    "flight_altitudes": ("altitude_m", REL),
    "offboard_set_position_ned": ("down_m", NED_DOWN),
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
    altitude = _relative_altitude(tool, args, state or {})
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


def check_preconditions(tool: str, args: dict, state: dict, s: SafetySettings) -> Rejection | None:
    """Vehicle-state preconditions, including the takeoff-then-crash fix."""
    if state.get("unknown"):
        # Telemetry unreadable. Default is fail-open (documented) so a
        # telemetry hiccup cannot strand an airborne vehicle.
        if s.preconditions_fail_closed and tool in NAVIGATION_TOOLS:
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

    if tool in NAVIGATION_TOOLS and s.require_in_air_for_navigation:
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


def check_geofence(
    tool: str, args: dict, fence: Geofence, s: SafetySettings, state: dict | None = None
) -> Rejection | None:
    """Server-side fence: single targets and whole missions."""
    if not fence.active:
        return None
    state = state or {}

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
