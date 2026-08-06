"""Pure builders/validators for raw mission items (rally points, raw fence).

No I/O - unit-testable without a drone. See tests/test_mission_plans.py.

Gotcha discovered on ArduCopter 4.5.7 SITL: MavSDK validates raw uploads and
requires the FIRST item of any transfer to have ``current == 1`` - rally
uploads fail with CURRENT_INVALID otherwise. The builders here take care of
that automatically.
"""

import math

from mavsdk.mission_raw import MissionItem

MAV_CMD_NAV_RALLY_POINT = 5100
MAV_FRAME_GLOBAL_RELATIVE_ALT = 3
MISSION_TYPE_MISSION = 0
MISSION_TYPE_FENCE = 1
MISSION_TYPE_RALLY = 2

_ITEM_FIELDS = (
    "seq",
    "frame",
    "command",
    "current",
    "autocontinue",
    "param1",
    "param2",
    "param3",
    "param4",
    "x",
    "y",
    "z",
    "mission_type",
)


def _check_lat_lon(lat, lon, where: str) -> tuple[float, float]:
    try:
        lat, lon = float(lat), float(lon)
    except (TypeError, ValueError):
        raise ValueError(f"{where}: latitude_deg/longitude_deg must be numbers") from None
    if not (-90.0 <= lat <= 90.0 and -180.0 <= lon <= 180.0):
        raise ValueError(f"{where}: latitude/longitude out of range ({lat}, {lon})")
    return lat, lon


def build_rally_items(points: list) -> list[MissionItem]:
    """points: [{"latitude_deg": .., "longitude_deg": .., "altitude_m": ..=0}]"""
    if not points:
        raise ValueError("at least one rally point is required")
    items = []
    for i, p in enumerate(points):
        lat, lon = _check_lat_lon(p.get("latitude_deg"), p.get("longitude_deg"), f"points[{i}]")
        alt = float(p.get("altitude_m", 0.0))
        items.append(
            MissionItem(
                i,
                MAV_FRAME_GLOBAL_RELATIVE_ALT,
                MAV_CMD_NAV_RALLY_POINT,
                1 if i == 0 else 0,  # first item must be current=1 (MavSDK validation)
                1,
                0.0,
                0.0,
                0.0,
                0.0,
                int(round(lat * 1e7)),
                int(round(lon * 1e7)),
                alt,
                MISSION_TYPE_RALLY,
            )
        )
    return items


def build_raw_items(dicts: list, mission_type: int) -> list[MissionItem]:
    """Expert path: full raw MAVLink mission items from dicts.

    Each dict: {"seq", "frame", "command", "current", "autocontinue",
    "param1".."param4", "x" (lat*1e7 int), "y" (lon*1e7 int), "z"}.
    ``mission_type`` is forced to the given transfer type; ``current`` of the
    first item is forced to 1 (MavSDK transfer validation).
    """
    if not dicts:
        raise ValueError("at least one mission item is required")
    items = []
    for i, d in enumerate(dicts):
        missing = [f for f in ("frame", "command", "x", "y", "z") if f not in d]
        if missing:
            raise ValueError(f"items[{i}]: missing required fields {missing}")
        try:
            items.append(
                MissionItem(
                    int(d.get("seq", i)),
                    int(d["frame"]),
                    int(d["command"]),
                    1 if i == 0 else int(d.get("current", 0)),
                    int(d.get("autocontinue", 1)),
                    _param(d, "param1"),
                    _param(d, "param2"),
                    _param(d, "param3"),
                    _param(d, "param4"),
                    int(d["x"]),
                    int(d["y"]),
                    float(d["z"]),
                    mission_type,
                )
            )
        except (TypeError, ValueError) as e:
            raise ValueError(f"items[{i}]: {e}") from None
    return items


def _param(d: dict, key: str) -> float:
    v = d.get(key, 0.0)
    if v is None:
        return float("nan")
    return float(v)


def items_to_dicts(items: list) -> list[dict]:
    """MissionItem list -> JSON-able dicts (NaN params become None)."""
    out = []
    for item in items:
        d = {}
        for f in _ITEM_FIELDS:
            v = getattr(item, f)
            if isinstance(v, float) and math.isnan(v):
                v = None
            d[f] = v
        d["latitude_deg"] = d["x"] / 1e7 if d["x"] is not None else None
        d["longitude_deg"] = d["y"] / 1e7 if d["y"] is not None else None
        out.append(d)
    return out
