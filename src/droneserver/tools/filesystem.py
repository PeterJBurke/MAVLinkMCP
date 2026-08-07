"""Autopilot filesystem (MAVLink FTP) and raw shell MCP tools.

⚠️ SECURITY: ``autopilot_shell`` runs arbitrary commands on the autopilot's
NuttX/console. It is TIER-CRITICAL and must be gated by the Phase 3 safety
layer (auth + confirmation). See docs/tool_groups.md.
"""

from mcp.server.fastmcp import Context

from droneserver.app import mcp
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
async def autopilot_files(ctx: Context, action: str, path: str = "", to_path: str = "", local_dir: str = "") -> dict:
    """Browse and manage files on the autopilot over MAVLink FTP.

    FIRMWARE NOTE: PX4 exposes a filesystem over MAVLink FTP; ArduPilot's
    support is limited and MavSDK often returns PROTOCOL_ERROR (observed on
    4.5.7 SITL). Errors are surfaced honestly.

    Args:
        action (str): "list" (path), "mkdir" (path), "rmdir" (path),
            "remove" (path = file), "rename" (path -> to_path), "download"
            (remote path -> local_dir), "upload" (local file `path` ->
            remote dir `to_path`), "compare" (are local `path` and remote
            `to_path` identical?), or "set_component" (path = numeric MAVLink
            component id).
        path (str): remote path (or local path for upload/compare, or
            component id for set_component).
        to_path (str): destination remote path for "rename"/"upload"/"compare".
        local_dir (str): local destination directory for "download".

    Returns:
        dict: status (+ listing for "list", identical flag for "compare").
    """
    log_tool_call("autopilot_files", action=action, path=path, to_path=to_path, local_dir=local_dir)
    action = str(action).lower()
    valid = ("list", "mkdir", "rmdir", "remove", "rename", "download", "upload", "compare", "set_component")
    if action not in valid:
        return _fail(f"action must be one of {valid}, got {action!r}")

    drone = await get_drone(ctx)
    if drone is None:
        return dict(CONN_ERROR)
    ftp = drone.ftp
    try:
        if action == "list":
            log_mavlink_cmd("drone.ftp.list_directory", path=path)
            listing = await ftp.list_directory(path)
            return _ok(path=path, dirs=list(listing.dirs), files=to_jsonable(listing.files))
        if action == "mkdir":
            await ftp.create_directory(path)
            return _ok(message=f"created {path}")
        if action == "rmdir":
            await ftp.remove_directory(path)
            return _ok(message=f"removed directory {path}")
        if action == "remove":
            await ftp.remove_file(path)
            return _ok(message=f"removed file {path}")
        if action == "rename":
            await ftp.rename(path, to_path)
            return _ok(message=f"renamed {path} -> {to_path}")
        if action == "set_component":
            await ftp.set_target_compid(int(path))
            return _ok(message=f"FTP target component set to {path}")
        if action == "compare":
            identical = await ftp.are_files_identical(path, to_path)
            return _ok(identical=identical, local=path, remote=to_path)
        if action == "upload":
            async for _p in ftp.upload(path, to_path):
                pass
            return _ok(message=f"uploaded {path} to {to_path}")
        # download
        async for _p in ftp.download(path, local_dir, False):
            pass
        return _ok(message=f"downloaded {path} to {local_dir}")
    except Exception as e:
        logger.error(f"autopilot_files({action}) failed: {e}")
        return _fail(f"FTP {action} failed: {e} (ArduPilot MAVLink-FTP support is limited)")


@mcp.tool()
async def autopilot_shell(ctx: Context, command: str, confirm: bool = False, read_timeout_s: float = 3.0) -> dict:
    """⚠️ TIER-CRITICAL: run a command on the autopilot's system shell and
    return its output.

    This is powerful and dangerous (arbitrary autopilot console access) and
    requires confirm=True. Intended for the PX4 NSH shell; ArduPilot does not
    expose a MAVLink shell, so ``receive`` times out there (observed).

    Args:
        command (str): shell command line (a trailing newline is added).
        confirm (bool): must be True to execute.
        read_timeout_s (float): how long to collect output (0.5-15).

    Returns:
        dict: status + captured output lines.
    """
    log_tool_call("autopilot_shell", command=command, confirm=confirm)
    if not confirm:
        return _fail("autopilot_shell refused: pass confirm=true (this runs arbitrary commands on the autopilot)")
    if not command:
        return _fail("command must not be empty")
    if not 0.5 <= float(read_timeout_s) <= 15.0:
        return _fail(f"read_timeout_s must be between 0.5 and 15, got {read_timeout_s}")

    drone = await get_drone(ctx)
    if drone is None:
        return dict(CONN_ERROR)
    try:
        log_mavlink_cmd("drone.shell.send", cmd=command)
        await drone.shell.send(command if command.endswith("\n") else command + "\n")
        output = []
        import asyncio

        async def collect():
            async for line in drone.shell.receive():
                output.append(line)

        try:
            await asyncio.wait_for(collect(), timeout=float(read_timeout_s))
        except (TimeoutError, asyncio.TimeoutError):
            pass  # timeout just ends collection
    except Exception as e:
        logger.error(f"autopilot_shell failed: {e}")
        return _fail(f"shell command failed: {e} (ArduPilot has no MAVLink shell)")
    if not output:
        return _ok(output=[], note="command sent; no shell output received (ArduPilot has no MAVLink shell)")
    return _ok(output=output)
