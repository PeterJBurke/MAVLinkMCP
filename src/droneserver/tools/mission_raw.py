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
    uploaded: dict = {}
    if upload:
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
    return _ok(imported=counts, uploaded=uploaded or "not requested")


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
