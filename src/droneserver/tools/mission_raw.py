"""Raw mission protocol MCP tools (MavSDK ``mission_raw`` plugin).

v2 design - see docs/tool_groups.md. Complements the friendly v1 ``mission``
tools with: QGroundControl .plan import (reproducibility artifact), rally
points, raw fence transfer, and raw mission-protocol control.

ArduCopter 4.5.7 SITL findings baked in: MavSDK requires the FIRST item of a
raw transfer to have current=1 (rally uploads fail with CURRENT_INVALID
otherwise - handled by the builders); cancel actions only succeed while a
transfer is in progress.
"""

import json

from mcp.server.fastmcp import Context

from droneserver.app import mcp
from droneserver.convert import to_jsonable
from droneserver.mission_plans import MISSION_TYPE_FENCE, build_rally_items, build_raw_items, items_to_dicts
from droneserver.telemetry.flight_log import log_mavlink_cmd, log_tool_call, log_tool_output, logger
from droneserver.tools._common import CONN_ERROR, first_stream_item, get_drone


def _fail(error: str) -> dict:
    result = {"status": "failed", "error": error}
    log_tool_output(result)
    return result


def _validate_imported_mission(mission_items) -> dict | None:
    """Check imported mission items against the SERVER-side geofence.

    Returns a rejection dict, or None when every item is inside the fence.
    """
    from droneserver.safety.config import get_safety_settings
    from droneserver.safety.geofence import Geofence, check_mission, parse_polygon
    from droneserver.safety.middleware import LAYER

    safety = get_safety_settings()
    if not safety.enabled or not safety.geofence_enabled:
        return None
    try:
        polygon = parse_polygon(safety.geofence_polygon)
    except ValueError:
        polygon = ()
    fence = Geofence(
        polygon=polygon,
        max_altitude_m=safety.geofence_max_altitude_m,
        max_radius_m=safety.geofence_max_radius_m,
        home=LAYER.state_tracker.state.home,
    )
    if not fence.active:
        return None

    # A radius fence is INERT until home is known: check_position skips the
    # radius branch when fence.home is None, so every waypoint on Earth is
    # inside it. The tool path refuses instead (geofence.home_unknown, review
    # item S8) and so does the managed-mission runner; this path - the only one
    # that also uploads the plan's OWN geofence and rally points - did not.
    if fence.max_radius_m > 0 and fence.home is None:
        return {
            "status": "rejected",
            "error": (
                "a radius geofence is configured but the drone's home position has not been read "
                "yet, so the imported plan cannot be checked against it"
            ),
            "rule": "geofence.home_unknown.imported_plan",
            "remedy": (
                "Wait for a GPS/home fix (get_home_position) and import again. Nothing was uploaded "
                "to the drone; the plan was refused rather than flown unfenced."
            ),
            "safety_layer": "droneserver.safety",
        }

    # Altitude frames again: a QGC plan mixes them. seq 0 is the HOME
    # placeholder (its AMSL altitude is not a commanded target), frames 0/5 are
    # AMSL, frames 3/6 are relative to home, 10/11 are terrain-relative.
    # Comparing an AMSL value against a relative ceiling rejects every valid
    # plan - which is exactly what the first version of this check did.
    AMSL_FRAMES = (0, 5)
    RELATIVE_FRAMES = (3, 6, 10, 11)
    POSITIONAL_COMMANDS = (16, 21, 22, 82)
    # Which ground elevation the AMSL items are converted against: the session's
    # launch point if the state tracker has it, because the autopilot's home
    # moves to wherever the aircraft last armed and would enforce the plan's
    # ceiling metres away from where the operator set it (FIX 12). The home is
    # the fallback, and no elevation at all still means "horizontal only".
    state = LAYER.state_tracker.state
    ground_amsl = state.session_launch_amsl_m if state.session_launch_amsl_m is not None else state.home_altitude_m

    waypoints = []
    for item in mission_items:
        if item.seq == 0:  # HOME placeholder, not a commanded target
            continue
        if item.command not in POSITIONAL_COMMANDS or not (item.x or item.y):
            continue
        altitude = None
        if item.frame in RELATIVE_FRAMES:
            altitude = item.z
        elif item.frame in AMSL_FRAMES and ground_amsl is not None:
            altitude = item.z - float(ground_amsl)
        # else: AMSL with no ground elevation - horizontal position is still checked
        waypoints.append(
            {
                "latitude_deg": item.x / 1e7,
                "longitude_deg": item.y / 1e7,
                "altitude_m": altitude,
            }
        )

    violations = check_mission(fence, waypoints)
    if not violations:
        return None
    idx, first = violations[0]
    return {
        "status": "rejected",
        "error": (
            f"imported plan item {idx} violates the server geofence: {first.detail} "
            f"({len(violations)} of {len(waypoints)} positional items violate it)"
        ),
        "rule": f"{first.rule}.imported_plan",
        "remedy": (
            "Edit the plan so every waypoint is inside the geofence and import again. "
            "Nothing was uploaded to the drone."
        ),
        "safety_layer": "droneserver.safety",
    }


def _ok(**payload) -> dict:
    result = {"status": "success", **payload}
    log_tool_output(result)
    return result


@mcp.tool()
async def import_qgc_mission(
    ctx: Context,
    plan_json: str = "",
    plan_path: str = "",
    upload: bool = True,
) -> dict:
    """Import a QGroundControl .plan (JSON) mission and optionally upload it
    to the drone - mission, geofence, and rally points included.

    Provide the plan either inline (plan_json - the full .plan file content
    as a string) or as a server-local file path (plan_path). Use
    initiate_mission/raw_mission_control to fly it afterwards.

    Args:
        plan_json (str): full QGC .plan JSON content (mutually exclusive with
            plan_path).
        plan_path (str): server-local path to a .plan file.
        upload (bool): if True (default), upload the imported items to the
            drone; if False, only parse and report counts.

    Returns:
        dict: status + item counts (mission/geofence/rally) and whether each
        was uploaded.
    """
    log_tool_call(
        "import_qgc_mission",
        plan_path=plan_path,
        upload=upload,
        plan_json=f"<{len(plan_json)} chars>" if plan_json else "",
    )
    if bool(plan_json) == bool(plan_path):
        return _fail("provide exactly one of plan_json or plan_path")
    if plan_json:
        try:
            json.loads(plan_json)
        except json.JSONDecodeError as e:
            return _fail(f"plan_json is not valid JSON: {e}")

    drone = await get_drone(ctx)
    if drone is None:
        return dict(CONN_ERROR)
    raw = drone.mission_raw
    try:
        if plan_json:
            log_mavlink_cmd("drone.mission_raw.import_qgroundcontrol_mission_from_string")
            imported = await raw.import_qgroundcontrol_mission_from_string(plan_json)
        else:
            log_mavlink_cmd("drone.mission_raw.import_qgroundcontrol_mission", path=plan_path)
            imported = await raw.import_qgroundcontrol_mission(plan_path)
    except Exception as e:
        logger.error(f"QGC plan import failed: {e}")
        return _fail(f"Plan import failed: {e}")

    counts = {
        "mission_items": len(imported.mission_items),
        "geofence_items": len(imported.geofence_items),
        "rally_items": len(imported.rally_items),
    }

    # B5: the imported plan used to go straight to the drone unvalidated - the
    # only tool that could fly an LLM-supplied route past the server-side
    # geofence. Validate every item here, on the same fence the managed-mission
    # path uses, BEFORE anything is uploaded.
    violation = _validate_imported_mission(imported.mission_items)
    if violation is not None:
        log_tool_output(violation)
        return violation
    uploaded: dict = {}
    if upload:
        if imported.geofence_items:
            logger.warning(
                "QGC plan carries %d geofence item(s): uploading it REPLACES the drone's "
                "firmware fence. The server-side fence is unaffected.",
                len(imported.geofence_items),
            )
        try:
            if imported.mission_items:
                log_mavlink_cmd("drone.mission_raw.upload_mission", items=counts["mission_items"])
                await raw.upload_mission(imported.mission_items)
                uploaded["mission"] = True
            if imported.geofence_items:
                log_mavlink_cmd("drone.mission_raw.upload_geofence", items=counts["geofence_items"])
                await raw.upload_geofence(imported.geofence_items)
                uploaded["geofence"] = True
            if imported.rally_items:
                log_mavlink_cmd("drone.mission_raw.upload_rally_points", items=counts["rally_items"])
                await raw.upload_rally_points(imported.rally_items)
                uploaded["rally"] = True
        except Exception as e:
            logger.error(f"Plan upload failed: {e}")
            return _fail(f"Plan imported ({counts}) but upload failed: {e}")
    result = _ok(imported=counts, uploaded=uploaded or "not requested")
    if imported.geofence_items and upload:
        result["warning"] = (
            f"This plan replaced the drone's firmware geofence with {len(imported.geofence_items)} "
            "item(s). The independent server-side geofence still applies and was not changed."
        )
    return result


@mcp.tool()
async def rally_points(ctx: Context, action: str, points: list | None = None) -> dict:
    """Upload or download rally points (safe alternative landing/loiter
    locations used by RTL logic on supported firmwares).

    Args:
        action (str): "upload" (replaces all rally points) or "download".
        points (list): for upload: [{"latitude_deg": float, "longitude_deg":
            float, "altitude_m": float (relative to home, default 0)}].

    Returns:
        dict: status; download returns the rally items.
    """
    log_tool_call("rally_points", action=action, points=points)
    action = str(action).lower()
    if action not in ("upload", "download"):
        return _fail(f'action must be "upload" or "download", got {action!r}')
    if action == "upload":
        try:
            items = build_rally_items(points or [])
        except ValueError as e:
            return _fail(str(e))

    drone = await get_drone(ctx)
    if drone is None:
        return dict(CONN_ERROR)
    try:
        if action == "upload":
            log_mavlink_cmd("drone.mission_raw.upload_rally_points", count=len(items))
            await drone.mission_raw.upload_rally_points(items)
            return _ok(message=f"{len(items)} rally point(s) uploaded")
        log_mavlink_cmd("drone.mission_raw.download_rallypoints")
        downloaded = await drone.mission_raw.download_rallypoints()
        return _ok(rally_points=items_to_dicts(downloaded))
    except Exception as e:
        logger.error(f"rally_points({action}) failed: {e}")
        return _fail(f"rally {action} failed: {e}")


@mcp.tool()
async def raw_geofence_transfer(ctx: Context, action: str, items: list | None = None) -> dict:
    """EXPERT: raw MAVLink fence transfer. Prefer upload_geofence /
    clear_geofence for normal fence management; this tool gives raw item
    access (e.g. to verify what is actually stored on the autopilot).

    Args:
        action (str): "download" (returns the fence items stored on the
            drone) or "upload" (raw items).
        items (list): for upload: raw mission-item dicts {"frame", "command",
            "x" (lat*1e7 int), "y" (lon*1e7 int), "z", "param1".."param4"}.

    Returns:
        dict: status; download returns the fence items.
    """
    log_tool_call("raw_geofence_transfer", action=action, items=items)
    action = str(action).lower()
    if action not in ("upload", "download"):
        return _fail(f'action must be "upload" or "download", got {action!r}')
    if action == "upload":
        try:
            built = build_raw_items(items or [], MISSION_TYPE_FENCE)
        except ValueError as e:
            return _fail(str(e))

    drone = await get_drone(ctx)
    if drone is None:
        return dict(CONN_ERROR)
    try:
        if action == "upload":
            log_mavlink_cmd("drone.mission_raw.upload_geofence", count=len(built))
            await drone.mission_raw.upload_geofence(built)
            return _ok(message=f"{len(built)} raw fence item(s) uploaded")
        log_mavlink_cmd("drone.mission_raw.download_geofence")
        downloaded = await drone.mission_raw.download_geofence()
        return _ok(geofence_items=items_to_dicts(downloaded))
    except Exception as e:
        logger.error(f"raw_geofence_transfer({action}) failed: {e}")
        return _fail(f"raw fence {action} failed: {e}")


@mcp.tool()
async def raw_mission_control(ctx: Context, action: str, index: int = 0, timeout_s: float = 10.0) -> dict:
    """EXPERT: raw mission-protocol control. Prefer the mission tools
    (initiate_mission, resume_mission, ...) for normal flying; this tool
    exposes the raw plugin for full protocol access.

    Args:
        action (str): "start" (drone must be armed), "pause", "clear",
            "set_current" (jump to item `index`), "progress" (read one
            current/total progress report), "cancel_upload" or
            "cancel_download" (only succeed while a transfer is running).
        index (int): item index for "set_current".
        timeout_s (float): for "progress": how long to wait for a report
            (1-120, default 10). Progress is emitted on CHANGE - a fresh
            read may wait until the next waypoint transition, so use a
            window longer than a mission leg when polling mid-flight.

    Returns:
        dict: status (+ progress for "progress").
    """
    log_tool_call("raw_mission_control", action=action, index=index, timeout_s=timeout_s)
    if not 1.0 <= float(timeout_s) <= 120.0:
        return _fail(f"timeout_s must be between 1 and 120, got {timeout_s}")
    action = str(action).lower()
    actions = ("start", "pause", "clear", "set_current", "progress", "cancel_upload", "cancel_download")
    if action not in actions:
        return _fail(f"action must be one of {actions}, got {action!r}")

    drone = await get_drone(ctx)
    if drone is None:
        return dict(CONN_ERROR)
    raw = drone.mission_raw
    try:
        log_mavlink_cmd(f"drone.mission_raw.{action}", index=index if action == "set_current" else None)
        if action == "start":
            await raw.start_mission()
            return _ok(message="mission started")
        if action == "pause":
            await raw.pause_mission()
            return _ok(message="mission paused - vehicle holds position")
        if action == "clear":
            await raw.clear_mission()
            return _ok(message="mission cleared")
        if action == "set_current":
            await raw.set_current_mission_item(int(index))
            return _ok(message=f"current mission item set to {index}")
        if action == "progress":
            progress = await first_stream_item(raw.mission_progress(), timeout_s=float(timeout_s))
            return _ok(progress=to_jsonable(progress))
        if action == "cancel_upload":
            await raw.cancel_mission_upload()
        else:
            await raw.cancel_mission_download()
        return _ok(message=f"{action} ok")
    except TimeoutError:
        return _fail(
            f"No mission progress received within {timeout_s}s "
            "(progress is emitted on waypoint transitions - try a longer timeout_s)"
        )
    except Exception as e:
        logger.error(f"raw_mission_control({action}) failed: {e}")
        return _fail(f"raw mission {action} failed: {e}")
