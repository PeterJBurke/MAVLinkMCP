"""Flight log MCP tools (MavSDK ``log_files`` plugin, MAVLink log download).

v2 design - see docs/tool_groups.md. One tool: list/download/erase are one
"onboard flight logs" intent with tiny per-action args.

Firmware reality (observed): ArduCopter 4.5.7 SITL answers the raw
LOG_REQUEST_LIST protocol correctly (verified with pymavlink, 1-based log
ids), but MavSDK 3.0.1 reports NO_LOGFILES - its log_files plugin expects
PX4's 0-based ids. Recorded in docs/firmware_notes.csv; effective support is
PX4-only until fixed upstream.
"""

from pathlib import Path

from mcp.server.fastmcp import Context

from droneserver.app import mcp
from droneserver.config import get_settings
from droneserver.convert import to_jsonable
from droneserver.telemetry.flight_log import log_mavlink_cmd, log_tool_call, log_tool_output, logger
from droneserver.tools._common import CONN_ERROR, get_drone


def _fail(error: str) -> dict:
    result = {"status": "failed", "error": error}
    log_tool_output(result)
    return result


def _ok(**payload) -> dict:
    result = {"status": "success", **payload}
    log_tool_output(result)
    return result


@mcp.tool()
async def flight_logs(ctx: Context, action: str, log_id: int = -1) -> dict:
    """List, download, or erase flight logs stored on the drone
    (autopilot dataflash logs, downloaded over MAVLink).

    Downloads are saved server-side (FLIGHT_LOG_DIR/downloads/) and the path
    is returned - log binaries are far too large to inline in a chat.

    NOTE: with the current MavSDK version this works against PX4; ArduPilot
    responds correctly at the MAVLink level but MavSDK misreads its log ids
    (see docs/firmware_notes.csv).

    Args:
        action (str): "list" (entries with id/date/size), "download"
            (requires log_id from "list"), or "erase_all" (DESTRUCTIVE:
            deletes all logs on the drone).
        log_id (int): id of the log entry to download.

    Returns:
        dict: status; list returns entries; download returns the saved path
        and size.
    """
    log_tool_call("flight_logs", action=action, log_id=log_id)
    action = str(action).lower()
    if action not in ("list", "download", "erase_all"):
        return _fail(f'action must be "list", "download" or "erase_all", got {action!r}')
    if action == "download" and log_id < 0:
        return _fail('download requires log_id (use action="list" first)')

    drone = await get_drone(ctx)
    if drone is None:
        return dict(CONN_ERROR)
    logs = drone.log_files
    try:
        if action == "list":
            log_mavlink_cmd("drone.log_files.get_entries")
            entries = await logs.get_entries()
            return _ok(entries=to_jsonable(entries))
        if action == "erase_all":
            log_mavlink_cmd("drone.log_files.erase_all_log_files")
            await logs.erase_all_log_files()
            return _ok(message="all onboard logs erased")

        # download
        log_mavlink_cmd("drone.log_files.get_entries")
        entries = await logs.get_entries()
        entry = next((e for e in entries if e.id == log_id), None)
        if entry is None:
            return _fail(f"no log entry with id {log_id}; available: {[e.id for e in entries]}")
        download_dir = Path(get_settings().flight_log_dir) / "downloads"
        download_dir.mkdir(parents=True, exist_ok=True)
        path = download_dir / f"log_{entry.id}.bin"
        log_mavlink_cmd("drone.log_files.download_log_file", id=entry.id, size=entry.size_bytes)
        async for _progress in logs.download_log_file(entry, str(path)):
            pass
        return _ok(
            message=f"log {entry.id} downloaded",
            path=str(path),
            size_bytes=path.stat().st_size if path.exists() else None,
        )
    except Exception as e:
        logger.error(f"flight_logs({action}) failed: {e}")
        return _fail(f"flight logs {action} failed: {e}")
