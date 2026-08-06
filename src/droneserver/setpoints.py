"""Pure construction/validation helpers for geofence and offboard tool inputs.

No I/O and no RPCs here - every function either returns MavSDK value objects
or raises ``ValueError`` with an LLM-actionable message. Fully unit-testable
without a drone (see tests/test_setpoints.py).
"""

from mavsdk.geofence import Circle, FenceType, GeofenceData, Point, Polygon
from mavsdk.offboard import (
    AccelerationNed,
    ActuatorControl,
    ActuatorControlGroup,
    Attitude,
    AttitudeRate,
    PositionGlobalYaw,
    PositionNedYaw,
    VelocityBodyYawspeed,
    VelocityNedYaw,
)

# Sanity bounds (coarse guards only; the Phase 3 validation middleware will
# make these configurable per deployment).
MIN_STALE_TIMEOUT_S = 1.0
MAX_STALE_TIMEOUT_S = 120.0
MAX_SPEED_M_S = 20.0
MAX_RATE_DEG_S = 180.0
MAX_ACCEL_M_S2 = 10.0

FENCE_TYPES = {"inclusion": FenceType.INCLUSION, "exclusion": FenceType.EXCLUSION}
ALTITUDE_TYPES = {
    "amsl": PositionGlobalYaw.AltitudeType.AMSL,
    "rel_home": PositionGlobalYaw.AltitudeType.REL_HOME,
    "agl": PositionGlobalYaw.AltitudeType.AGL,
}


def _check_range(name: str, value: float, lo: float, hi: float) -> float:
    try:
        value = float(value)
    except (TypeError, ValueError):
        raise ValueError(f"{name} must be a number, got {value!r}") from None
    if not lo <= value <= hi:
        raise ValueError(f"{name} must be between {lo} and {hi}, got {value}")
    return value


def _check_lat_lon(latitude_deg: float, longitude_deg: float, where: str) -> tuple[float, float]:
    lat = _check_range(f"{where}: latitude_deg", latitude_deg, -90.0, 90.0)
    lon = _check_range(f"{where}: longitude_deg", longitude_deg, -180.0, 180.0)
    return lat, lon


def validate_stale_timeout(timeout_s: float) -> float:
    """Bound the stale-setpoint safety timeout for motion setpoints."""
    return _check_range("stale_timeout_s", timeout_s, MIN_STALE_TIMEOUT_S, MAX_STALE_TIMEOUT_S)


# ---------------------------------------------------------------- geofence


def _fence_type(value: str, where: str) -> FenceType:
    try:
        return FENCE_TYPES[str(value).lower()]
    except KeyError:
        raise ValueError(f"{where}: fence_type must be one of {sorted(FENCE_TYPES)}, got {value!r}") from None


def build_geofence_data(polygons: list | None, circles: list | None) -> GeofenceData:
    """Build a GeofenceData from JSON-ish input.

    polygons: [{"points": [{"latitude_deg": .., "longitude_deg": ..}, ...] (>= 3),
               "fence_type": "inclusion"|"exclusion"}]
    circles:  [{"latitude_deg": .., "longitude_deg": .., "radius_m": > 0,
               "fence_type": "inclusion"|"exclusion"}]
    """
    polygons = polygons or []
    circles = circles or []
    if not polygons and not circles:
        raise ValueError("geofence must contain at least one polygon or circle")

    built_polygons = []
    for i, poly in enumerate(polygons):
        where = f"polygons[{i}]"
        points = poly.get("points") or []
        if len(points) < 3:
            raise ValueError(f"{where}: a polygon needs at least 3 points, got {len(points)}")
        built_points = [
            Point(*_check_lat_lon(p.get("latitude_deg"), p.get("longitude_deg"), f"{where}.points[{j}]"))
            for j, p in enumerate(points)
        ]
        built_polygons.append(Polygon(built_points, _fence_type(poly.get("fence_type", "inclusion"), where)))

    built_circles = []
    for i, circ in enumerate(circles):
        where = f"circles[{i}]"
        lat, lon = _check_lat_lon(circ.get("latitude_deg"), circ.get("longitude_deg"), where)
        radius = _check_range(f"{where}: radius_m", circ.get("radius_m"), 1.0, 100_000.0)
        built_circles.append(Circle(Point(lat, lon), radius, _fence_type(circ.get("fence_type", "inclusion"), where)))

    return GeofenceData(built_polygons, built_circles)


# ---------------------------------------------------------------- offboard


def build_position_ned(
    north_m: float,
    east_m: float,
    down_m: float,
    yaw_deg: float,
    velocity: dict | None = None,
    acceleration: dict | None = None,
) -> tuple[PositionNedYaw, VelocityNedYaw | None, AccelerationNed | None]:
    """Position setpoint (NED, meters relative to origin) with optional
    velocity / acceleration feed-forward. Feed-forward acceleration requires
    feed-forward velocity (matches the MavSDK method set)."""
    pos = PositionNedYaw(
        _check_range("north_m", north_m, -100_000.0, 100_000.0),
        _check_range("east_m", east_m, -100_000.0, 100_000.0),
        _check_range("down_m", down_m, -10_000.0, 10_000.0),
        _check_range("yaw_deg", yaw_deg, -360.0, 360.0),
    )
    vel = build_velocity_ned(**velocity) if velocity else None
    if acceleration is not None and vel is None:
        raise ValueError("acceleration feed-forward requires velocity feed-forward as well")
    acc = build_acceleration_ned(**acceleration) if acceleration else None
    return pos, vel, acc


def build_position_global(
    latitude_deg: float,
    longitude_deg: float,
    altitude_m: float,
    yaw_deg: float,
    altitude_type: str,
) -> PositionGlobalYaw:
    lat, lon = _check_lat_lon(latitude_deg, longitude_deg, "position")
    alt_type = ALTITUDE_TYPES.get(str(altitude_type).lower())
    if alt_type is None:
        raise ValueError(f"altitude_type must be one of {sorted(ALTITUDE_TYPES)}, got {altitude_type!r}")
    return PositionGlobalYaw(
        lat,
        lon,
        _check_range("altitude_m", altitude_m, -500.0, 10_000.0),
        _check_range("yaw_deg", yaw_deg, -360.0, 360.0),
        alt_type,
    )


def build_velocity_ned(
    north_m_s: float = 0.0, east_m_s: float = 0.0, down_m_s: float = 0.0, yaw_deg: float = 0.0
) -> VelocityNedYaw:
    return VelocityNedYaw(
        _check_range("north_m_s", north_m_s, -MAX_SPEED_M_S, MAX_SPEED_M_S),
        _check_range("east_m_s", east_m_s, -MAX_SPEED_M_S, MAX_SPEED_M_S),
        _check_range("down_m_s", down_m_s, -MAX_SPEED_M_S, MAX_SPEED_M_S),
        _check_range("yaw_deg", yaw_deg, -360.0, 360.0),
    )


def build_velocity_body(
    forward_m_s: float = 0.0,
    right_m_s: float = 0.0,
    down_m_s: float = 0.0,
    yawspeed_deg_s: float = 0.0,
) -> VelocityBodyYawspeed:
    return VelocityBodyYawspeed(
        _check_range("forward_m_s", forward_m_s, -MAX_SPEED_M_S, MAX_SPEED_M_S),
        _check_range("right_m_s", right_m_s, -MAX_SPEED_M_S, MAX_SPEED_M_S),
        _check_range("down_m_s", down_m_s, -MAX_SPEED_M_S, MAX_SPEED_M_S),
        _check_range("yawspeed_deg_s", yawspeed_deg_s, -MAX_RATE_DEG_S, MAX_RATE_DEG_S),
    )


def build_acceleration_ned(north_m_s2: float = 0.0, east_m_s2: float = 0.0, down_m_s2: float = 0.0) -> AccelerationNed:
    return AccelerationNed(
        _check_range("north_m_s2", north_m_s2, -MAX_ACCEL_M_S2, MAX_ACCEL_M_S2),
        _check_range("east_m_s2", east_m_s2, -MAX_ACCEL_M_S2, MAX_ACCEL_M_S2),
        _check_range("down_m_s2", down_m_s2, -MAX_ACCEL_M_S2, MAX_ACCEL_M_S2),
    )


def build_attitude(mode: str, roll: float, pitch: float, yaw: float, thrust: float) -> Attitude | AttitudeRate:
    """mode="angle": roll/pitch/yaw are angles in deg; mode="rate": angular
    rates in deg/s. thrust is normalized 0..1 in both modes."""
    thrust = _check_range("thrust", thrust, 0.0, 1.0)
    mode = str(mode).lower()
    if mode == "angle":
        return Attitude(
            _check_range("roll", roll, -180.0, 180.0),
            _check_range("pitch", pitch, -90.0, 90.0),
            _check_range("yaw", yaw, -360.0, 360.0),
            thrust,
        )
    if mode == "rate":
        return AttitudeRate(
            _check_range("roll", roll, -MAX_RATE_DEG_S, MAX_RATE_DEG_S),
            _check_range("pitch", pitch, -MAX_RATE_DEG_S, MAX_RATE_DEG_S),
            _check_range("yaw", yaw, -MAX_RATE_DEG_S, MAX_RATE_DEG_S),
            thrust,
        )
    raise ValueError(f'mode must be "angle" or "rate", got {mode!r}')


def build_actuator_control(groups: list) -> ActuatorControl:
    """groups: up to 2 lists of up to 8 control values in [-1, 1]."""
    if not groups or len(groups) > 2:
        raise ValueError(f"groups must contain 1 or 2 control groups, got {len(groups or [])}")
    built = []
    for gi, controls in enumerate(groups):
        if not controls or len(controls) > 8:
            raise ValueError(f"groups[{gi}] must contain 1..8 control values, got {len(controls or [])}")
        built.append(
            ActuatorControlGroup([_check_range(f"groups[{gi}][{ci}]", c, -1.0, 1.0) for ci, c in enumerate(controls)])
        )
    return ActuatorControl(built)
