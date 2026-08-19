"""Server-side geofence enforcement - independent of the firmware fence.

Why two layers (the argument for the paper's safety section):

1. **Different failure modes.** The firmware fence protects against the
   vehicle straying; the server fence protects against the *command* ever
   being sent. An LLM that hallucinates a waypoint 40 km away is stopped here,
   before MAVLink, with an explanation it can act on - rather than the vehicle
   flying to the fence edge and triggering a firmware failsafe mid-mission.
2. **Different trust domains.** The firmware fence lives on the vehicle and
   can be disabled by a parameter write (``FENCE_ENABLE=0``) - which is itself
   a tool call an LLM could make. The server fence is enforced in the
   operator's trust domain and cannot be turned off from the LLM side at all.
3. **Coverage of things the firmware fence does not see.** Mission uploads are
   validated item-by-item *before* the mission is ever on the vehicle, and
   offboard setpoints are checked per setpoint.

Pure geometry lives here (no I/O), so every rule is unit-testable.
"""

import math
from dataclasses import dataclass

EARTH_RADIUS_M = 6371000.0


@dataclass(frozen=True)
class FenceViolation:
    rule: str
    detail: str


@dataclass(frozen=True)
class Geofence:
    """An immutable fence: optional polygon, altitude ceiling, home radius."""

    polygon: tuple[tuple[float, float], ...] = ()
    max_altitude_m: float = 120.0
    max_radius_m: float = 0.0  # 0 = no radius constraint
    home: tuple[float, float] | None = None

    @property
    def active(self) -> bool:
        return bool(self.polygon) or self.max_altitude_m > 0 or self.max_radius_m > 0


def parse_polygon(spec: str) -> tuple[tuple[float, float], ...]:
    """Parse ``"lat,lon;lat,lon;..."`` into vertices. Empty -> ()."""
    spec = (spec or "").strip()
    if not spec:
        return ()
    vertices = []
    for i, part in enumerate(p for p in spec.split(";") if p.strip()):
        bits = part.split(",")
        if len(bits) != 2:
            raise ValueError(f"geofence_polygon vertex {i} must be 'lat,lon', got {part!r}")
        try:
            lat, lon = float(bits[0]), float(bits[1])
        except ValueError:
            raise ValueError(f"geofence_polygon vertex {i} is not numeric: {part!r}") from None
        if not (-90 <= lat <= 90 and -180 <= lon <= 180):
            raise ValueError(f"geofence_polygon vertex {i} out of range: {lat},{lon}")
        vertices.append((lat, lon))
    if len(vertices) < 3:
        raise ValueError(f"geofence_polygon needs at least 3 vertices, got {len(vertices)}")
    return tuple(vertices)


def haversine_m(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlam = math.radians(lon2 - lon1)
    a = math.sin(dphi / 2) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(dlam / 2) ** 2
    return EARTH_RADIUS_M * 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))


def point_in_polygon(lat: float, lon: float, polygon: tuple[tuple[float, float], ...]) -> bool:
    """Ray-casting test. Vertices are (lat, lon); treated as a planar polygon,
    which is accurate at the scale a fence operates on (<10 km)."""
    inside = False
    n = len(polygon)
    for i in range(n):
        lat_i, lon_i = polygon[i]
        lat_j, lon_j = polygon[(i - 1) % n]
        # does the edge straddle the test latitude, and is the crossing east of us?
        if (lat_i > lat) != (lat_j > lat):
            x = (lon_j - lon_i) * (lat - lat_i) / (lat_j - lat_i) + lon_i
            if lon < x:
                inside = not inside
    return inside


def check_position(
    fence: Geofence, lat: float | None, lon: float | None, altitude_m: float | None
) -> FenceViolation | None:
    """Check one target position against the fence. Returns the first
    violation, or None if the target is allowed. ``None`` coordinates are
    skipped (e.g. an altitude-only check).

    ``altitude_m`` is frame-agnostic here - this function compares whatever it
    is given against the ceiling - so the FRAME is the caller's contract, and
    the contract is: height above the launch point. Callers must not pass the
    autopilot's raw ``relative_altitude_m``, whose datum moves to wherever the
    aircraft last armed; see :mod:`droneserver.telemetry.ground` and the
    callers in ``safety.validation`` and ``missions.runner``."""
    if not fence.active:
        return None

    if altitude_m is not None and fence.max_altitude_m > 0 and altitude_m > fence.max_altitude_m:
        return FenceViolation(
            "geofence.altitude_ceiling",
            f"target altitude {altitude_m:.1f} m exceeds the geofence ceiling of {fence.max_altitude_m:.1f} m",
        )

    if lat is None or lon is None:
        return None

    if fence.polygon and not point_in_polygon(lat, lon, fence.polygon):
        return FenceViolation(
            "geofence.polygon",
            f"target ({lat:.6f}, {lon:.6f}) is outside the configured geofence polygon",
        )

    if fence.max_radius_m > 0 and fence.home is not None:
        distance = haversine_m(fence.home[0], fence.home[1], lat, lon)
        if distance > fence.max_radius_m:
            # The centre is named, not just the distance. The server caches the
            # home it first read and keeps that centre for the life of the
            # process, which is the safe behaviour - a fence that followed the
            # vehicle could be walked outwards indefinitely, a flight at a time.
            # But it means the centre can be somewhere nobody currently expects,
            # and a message that only says "1046 m from home" gives the reader
            # no way to notice. Naming the point turns a campaign-long mystery
            # into one line. (Halted N=5 campaign, 2026-08-10: the aircraft had
            # drifted ~990 m from a fence centred where the simulator started
            # three days earlier, and every horizontal command was refused.)
            return FenceViolation(
                "geofence.radius",
                f"target is {distance:.0f} m from home ({fence.home[0]:.6f}, {fence.home[1]:.6f}), "
                f"beyond the geofence radius of {fence.max_radius_m:.0f} m",
            )
    return None


def clip_altitude(fence: Geofence, altitude_m: float) -> float:
    """Clamp an altitude to the ceiling. Only altitude is ever clipped -
    horizontal targets are rejected instead, because silently moving a
    waypoint would fly the vehicle somewhere the operator did not ask for."""
    if fence.max_altitude_m > 0:
        return min(altitude_m, fence.max_altitude_m)
    return altitude_m


def check_mission(fence: Geofence, waypoints: list[dict]) -> list[tuple[int, FenceViolation]]:
    """Validate an entire mission before it is uploaded.

    Each waypoint may use ``latitude_deg``/``longitude_deg`` (or ``lat``/
    ``lon``) plus one of ``altitude_m``/``relative_altitude_m``/``alt``.
    Returns ``[(index, violation), ...]`` for every offending item.
    """
    violations: list[tuple[int, FenceViolation]] = []
    for i, wp in enumerate(waypoints or []):
        if not isinstance(wp, dict):
            continue
        lat = wp.get("latitude_deg", wp.get("lat"))
        lon = wp.get("longitude_deg", wp.get("lon"))
        # Not a telemetry reading and not a datum question: this is the
        # caller's REQUESTED waypoint altitude, checked before anything is
        # uploaded, and mission waypoints are flown in the relative-to-home
        # frame. It is already expressed in the frame the ceiling uses.
        alt = wp.get("altitude_m", wp.get("relative_altitude_m", wp.get("alt")))
        try:
            lat = float(lat) if lat is not None else None
            lon = float(lon) if lon is not None else None
            alt = float(alt) if alt is not None else None
        except (TypeError, ValueError):
            continue  # malformed items are the parameter validator's business
        violation = check_position(fence, lat, lon, alt)
        if violation is not None:
            violations.append((i, violation))
    return violations
