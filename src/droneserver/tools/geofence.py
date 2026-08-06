"""Geofence MCP tools (MavSDK ``geofence`` plugin). v2 - see docs/tool_groups.md."""

from mcp.server.fastmcp import Context

from droneserver.app import mcp
from droneserver.mavlink.connection import ensure_connection
from droneserver.setpoints import build_geofence_data
from droneserver.telemetry.flight_log import log_mavlink_cmd, log_tool_call, log_tool_output, logger

_CONN_ERROR = {"status": "failed", "error": "Drone connection timeout. Please wait and try again."}


@mcp.tool()
async def upload_geofence(ctx: Context, polygons: list | None = None, circles: list | None = None) -> dict:
    """Upload a geofence (polygons and/or circles) to the drone.

    The fence is stored on the autopilot. NOTE for ArduPilot: uploading does
    not by itself enforce the fence - set the FENCE_ENABLE parameter to 1
    (see set_parameter) and configure FENCE_ACTION for the desired response.

    Args:
        polygons (list): list of {"points": [{"latitude_deg": float,
            "longitude_deg": float}, ...] (at least 3), "fence_type":
            "inclusion" | "exclusion"}. Inclusion = stay inside; exclusion =
            keep out.
        circles (list): list of {"latitude_deg": float, "longitude_deg": float,
            "radius_m": float, "fence_type": "inclusion" | "exclusion"}.

    Returns:
        dict: status, plus counts of uploaded polygons/circles.
    """
    log_tool_call("upload_geofence", polygons=polygons, circles=circles)
    try:
        data = build_geofence_data(polygons, circles)
    except ValueError as e:
        result = {"status": "failed", "error": str(e)}
        log_tool_output(result)
        return result

    connector = ctx.request_context.lifespan_context
    if not await ensure_connection(connector):
        return dict(_CONN_ERROR)
    drone = connector.drone

    try:
        log_mavlink_cmd(
            "drone.geofence.upload_geofence",
            polygons=len(data.polygons),
            circles=len(data.circles),
        )
        await drone.geofence.upload_geofence(data)
    except Exception as e:
        logger.error(f"Geofence upload failed: {e}")
        result = {"status": "failed", "error": f"Geofence upload failed: {e}"}
        log_tool_output(result)
        return result

    result = {
        "status": "success",
        "message": f"Geofence uploaded ({len(data.polygons)} polygon(s), {len(data.circles)} circle(s))",
        "note": "On ArduPilot, enforcement also requires FENCE_ENABLE=1 (see set_parameter).",
    }
    log_tool_output(result)
    return result


@mcp.tool()
async def clear_geofence(ctx: Context) -> dict:
    """Remove all geofence polygons/circles stored on the drone.

    Safety note: after clearing, the vehicle has no fence-based containment.

    Returns:
        dict: status.
    """
    log_tool_call("clear_geofence")
    connector = ctx.request_context.lifespan_context
    if not await ensure_connection(connector):
        return dict(_CONN_ERROR)
    drone = connector.drone

    try:
        log_mavlink_cmd("drone.geofence.clear_geofence")
        await drone.geofence.clear_geofence()
    except Exception as e:
        logger.error(f"Geofence clear failed: {e}")
        result = {"status": "failed", "error": f"Geofence clear failed: {e}"}
        log_tool_output(result)
        return result

    result = {"status": "success", "message": "Geofence cleared - no fence containment is active"}
    log_tool_output(result)
    return result
