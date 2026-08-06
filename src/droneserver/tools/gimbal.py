"""Gimbal MCP tools (MavSDK ``gimbal`` plugin, MAVLink gimbal protocol v2).

v2 design - see docs/tool_groups.md. 10 SDK methods grouped into 3 tools
(discovery, control ownership, pointing). Verified working end-to-end on
ArduCopter 4.5.7 SITL with a simulated mount (MNT1_TYPE=1, baked into the
test SITL image).
"""

from mavsdk.gimbal import ControlMode, GimbalMode, SendMode
from mcp.server.fastmcp import Context

from droneserver.app import mcp
from droneserver.convert import to_jsonable
from droneserver.telemetry.flight_log import log_mavlink_cmd, log_tool_call, log_tool_output, logger
from droneserver.tools._common import CONN_ERROR, first_stream_item, get_drone

_GIMBAL_MODES = {"yaw_follow": GimbalMode.YAW_FOLLOW, "yaw_lock": GimbalMode.YAW_LOCK}


def _fail(error: str) -> dict:
    result = {"status": "failed", "error": error}
    log_tool_output(result)
    return result


def _ok(**payload) -> dict:
    result = {"status": "success", **payload}
    log_tool_output(result)
    return result


@mcp.tool()
async def list_gimbals(ctx: Context) -> dict:
    """List gimbals detected on the drone.

    Returns each gimbal's gimbal_id (needed by the other gimbal tools) and
    vendor/model. An empty list means no gimbal/mount is configured.

    Returns:
        dict: status + gimbals list.
    """
    log_tool_call("list_gimbals")
    drone = await get_drone(ctx)
    if drone is None:
        return dict(CONN_ERROR)
    try:
        gimbal_list = await first_stream_item(drone.gimbal.gimbal_list(), timeout_s=10.0)
        return _ok(gimbals=to_jsonable(gimbal_list.gimbals))
    except TimeoutError:
        return _ok(gimbals=[], note="No gimbal announced itself within 10s")
    except Exception as e:
        logger.error(f"list_gimbals failed: {e}")
        return _fail(f"Gimbal discovery failed: {e}")


@mcp.tool()
async def gimbal_control(ctx: Context, gimbal_id: int, action: str) -> dict:
    """Take/release control of a gimbal, or query who controls it.

    Take control before pointing the gimbal; release when done so other
    components (e.g. the RC) can use it.

    Args:
        gimbal_id (int): gimbal id from list_gimbals.
        action (str): "take" (become primary controller), "release", or
            "status" (read-only: current controllers).

    Returns:
        dict: status (+ control_status for "status").
    """
    log_tool_call("gimbal_control", gimbal_id=gimbal_id, action=action)
    action = str(action).lower()
    if action not in ("take", "release", "status"):
        return _fail(f'action must be "take", "release" or "status", got {action!r}')

    drone = await get_drone(ctx)
    if drone is None:
        return dict(CONN_ERROR)
    try:
        log_mavlink_cmd(f"drone.gimbal.{action}_control", gimbal_id=gimbal_id)
        if action == "take":
            await drone.gimbal.take_control(gimbal_id, ControlMode.PRIMARY)
            return _ok(message=f"took primary control of gimbal {gimbal_id}")
        if action == "release":
            await drone.gimbal.release_control(gimbal_id)
            return _ok(message=f"released control of gimbal {gimbal_id}")
        status = await drone.gimbal.get_control_status(gimbal_id)
        return _ok(control_status=to_jsonable(status))
    except Exception as e:
        logger.error(f"gimbal_control({action}) failed: {e}")
        return _fail(f"gimbal {action} failed: {e}")


@mcp.tool()
async def gimbal_point(
    ctx: Context,
    gimbal_id: int,
    action: str,
    roll: float = 0.0,
    pitch: float = 0.0,
    yaw: float = 0.0,
    latitude_deg: float = 0.0,
    longitude_deg: float = 0.0,
    altitude_m: float = 0.0,
    gimbal_mode: str = "yaw_follow",
) -> dict:
    """Point a gimbal: set angles, set angular rates, track a ground location
    (ROI), or read the current attitude.

    Take control first (gimbal_control "take"). Pitch -90 looks straight
    down; 0 is level.

    Args:
        gimbal_id (int): gimbal id from list_gimbals.
        action (str): "set_angles" (roll/pitch/yaw in deg),
            "set_rates" (roll/pitch/yaw in deg/s),
            "roi_location" (latitude_deg/longitude_deg/altitude_m AMSL -
            gimbal keeps pointing at that spot), or
            "get_attitude" (read-only).
        roll, pitch, yaw (float): angles (deg) or rates (deg/s) per action.
        latitude_deg, longitude_deg, altitude_m (float): for roi_location.
        gimbal_mode (str): "yaw_follow" (yaw relative to vehicle heading,
            default) or "yaw_lock" (yaw fixed in earth frame).

    Returns:
        dict: status (+ attitude for "get_attitude").
    """
    log_tool_call(
        "gimbal_point",
        gimbal_id=gimbal_id,
        action=action,
        roll=roll,
        pitch=pitch,
        yaw=yaw,
        latitude_deg=latitude_deg,
        longitude_deg=longitude_deg,
        altitude_m=altitude_m,
        gimbal_mode=gimbal_mode,
    )
    action = str(action).lower()
    if action not in ("set_angles", "set_rates", "roi_location", "get_attitude"):
        return _fail(f'action must be "set_angles", "set_rates", "roi_location" or "get_attitude", got {action!r}')
    mode = _GIMBAL_MODES.get(str(gimbal_mode).lower())
    if mode is None:
        return _fail(f'gimbal_mode must be "yaw_follow" or "yaw_lock", got {gimbal_mode!r}')
    if action == "set_angles" and not (-180 <= roll <= 180 and -120 <= pitch <= 120 and -360 <= yaw <= 360):
        return _fail(f"angles out of range: roll={roll} (±180), pitch={pitch} (±120), yaw={yaw} (±360)")
    if action == "roi_location" and not (-90 <= latitude_deg <= 90 and -180 <= longitude_deg <= 180):
        return _fail(f"latitude/longitude out of range ({latitude_deg}, {longitude_deg})")

    drone = await get_drone(ctx)
    if drone is None:
        return dict(CONN_ERROR)
    try:
        log_mavlink_cmd(f"drone.gimbal.{action}", gimbal_id=gimbal_id)
        if action == "set_angles":
            await drone.gimbal.set_angles(gimbal_id, float(roll), float(pitch), float(yaw), mode, SendMode.ONCE)
            return _ok(message=f"gimbal angles set (roll={roll}, pitch={pitch}, yaw={yaw})")
        if action == "set_rates":
            await drone.gimbal.set_angular_rates(gimbal_id, float(roll), float(pitch), float(yaw), mode, SendMode.ONCE)
            return _ok(message=f"gimbal rates set (roll={roll}, pitch={pitch}, yaw={yaw} deg/s)")
        if action == "roi_location":
            await drone.gimbal.set_roi_location(gimbal_id, float(latitude_deg), float(longitude_deg), float(altitude_m))
            return _ok(message=f"gimbal tracking ROI ({latitude_deg}, {longitude_deg}, {altitude_m} m AMSL)")
        attitude = await drone.gimbal.get_attitude(gimbal_id)
        return _ok(attitude=to_jsonable(attitude))
    except Exception as e:
        logger.error(f"gimbal_point({action}) failed: {e}")
        return _fail(f"gimbal {action} failed: {e}")
