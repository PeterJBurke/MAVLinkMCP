"""System info / identification / status-text MCP tools.

Covers the ``info`` plugin, ``core.set_mavlink_timeout``, and
``server_utility.send_status_text``.
"""

import asyncio

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
async def system_info(ctx: Context, topic: str = "version") -> dict:
    """Get autopilot/system information.

    Args:
        topic (str): "version" (firmware/OS versions), "identification"
            (hardware UID), "product" (vendor/product names),
            "flight_information" (time boot / flight uid - PX4; ArduPilot
            reports INFORMATION_NOT_RECEIVED_YET), or "speed_factor" (sim
            speed multiplier; PX4).

    Returns:
        dict: status + the requested info.
    """
    log_tool_call("system_info", topic=topic)
    topic = str(topic).lower()
    topics = ("version", "identification", "product", "flight_information", "speed_factor")
    if topic not in topics:
        return _fail(f"topic must be one of {topics}, got {topic!r}")

    drone = await get_drone(ctx)
    if drone is None:
        return dict(CONN_ERROR)
    try:
        if topic == "version":
            data = await drone.info.get_version()
        elif topic == "identification":
            data = await drone.info.get_identification()
        elif topic == "product":
            data = await drone.info.get_product()
        elif topic == "flight_information":
            data = await drone.info.get_flight_information()
        else:
            data = await drone.info.get_speed_factor()
    except Exception as e:
        logger.error(f"system_info({topic}) failed: {e}")
        return _fail(f"system {topic} unavailable: {e}")
    return _ok(topic=topic, info=to_jsonable(data))


@mcp.tool()
async def send_status_text(ctx: Context, text: str, severity: str = "info") -> dict:
    """Send a STATUSTEXT message onto the MAVLink network (e.g. visible in a
    GCS log). Useful for annotating flight logs from the LLM.

    Args:
        text (str): message text (<= 50 chars recommended).
        severity (str): "emergency", "alert", "critical", "error", "warning",
            "notice", "info" (default), or "debug".

    Returns:
        dict: status.
    """
    from mavsdk.server_utility import StatusTextType

    log_tool_call("send_status_text", text=text, severity=severity)
    levels = {
        "emergency": StatusTextType.EMERGENCY,
        "alert": StatusTextType.ALERT,
        "critical": StatusTextType.CRITICAL,
        "error": StatusTextType.ERROR,
        "warning": StatusTextType.WARNING,
        "notice": StatusTextType.NOTICE,
        "info": StatusTextType.INFO,
        "debug": StatusTextType.DEBUG,
    }
    level = levels.get(str(severity).lower())
    if level is None:
        return _fail(f"severity must be one of {sorted(levels)}, got {severity!r}")
    if not text:
        return _fail("text must not be empty")

    drone = await get_drone(ctx)
    if drone is None:
        return dict(CONN_ERROR)
    try:
        log_mavlink_cmd("drone.server_utility.send_status_text", severity=severity)
        await drone.server_utility.send_status_text(level, str(text))
    except Exception as e:
        logger.error(f"send_status_text failed: {e}")
        return _fail(f"send_status_text failed: {e}")
    return _ok(message="status text sent")


@mcp.tool()
async def set_mavlink_timeout(ctx: Context, timeout_s: float) -> dict:
    """Set the MAVLink heartbeat timeout used to detect a lost connection.

    Args:
        timeout_s (float): timeout in seconds (0.1-30).

    Returns:
        dict: status.
    """
    log_tool_call("set_mavlink_timeout", timeout_s=timeout_s)
    if not 0.1 <= float(timeout_s) <= 30.0:
        return _fail(f"timeout_s must be between 0.1 and 30, got {timeout_s}")

    drone = await get_drone(ctx)
    if drone is None:
        return dict(CONN_ERROR)
    try:
        await asyncio.wait_for(drone.core.set_mavlink_timeout(float(timeout_s)), timeout=10)
    except Exception as e:
        logger.error(f"set_mavlink_timeout failed: {e}")
        return _fail(f"set_mavlink_timeout failed: {e}")
    return _ok(message=f"MAVLink timeout set to {timeout_s}s")
