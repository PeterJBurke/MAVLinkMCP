"""Camera MCP tools (MavSDK ``camera`` plugin, MAVLink camera protocol v2).

v2 design - see docs/tool_groups.md. 36 SDK methods are grouped into 6 tools
by sub-domain (discovery, capture, settings, storage, zoom/focus, tracking).

Firmware reality: a camera must announce itself via the MAVLink camera
protocol. Plain ArduPilot SITL has no camera backend, so on it discovery
returns an empty list and commands time out - recorded honestly in
docs/firmware_notes.csv. All tools take the camera's ``component_id`` as
returned by ``list_cameras`` (MAVLink camera components are 100-105).
"""

from mavsdk.camera import Mode, Option, PhotosRange, Setting
from mcp.server.fastmcp import Context

from droneserver.app import mcp
from droneserver.convert import to_jsonable
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
async def list_cameras(ctx: Context) -> dict:
    """List cameras detected on the drone (MAVLink camera protocol).

    Returns each camera's component_id (needed by all other camera tools),
    vendor/model, and resolution. An empty list means no camera announced
    itself (plain SITL has no camera backend).

    Returns:
        dict: status + cameras list.
    """
    log_tool_call("list_cameras")
    drone = await get_drone(ctx)
    if drone is None:
        return dict(CONN_ERROR)
    try:
        camera_list = await first_stream_item(drone.camera.camera_list(), timeout_s=8.0)
        return _ok(cameras=to_jsonable(camera_list.cameras))
    except TimeoutError:
        return _ok(cameras=[], note="No camera announced itself within 8s")
    except Exception as e:
        logger.error(f"list_cameras failed: {e}")
        return _fail(f"Camera discovery failed: {e}")


@mcp.tool()
async def camera_capture(
    ctx: Context,
    component_id: int,
    action: str,
    interval_s: float = 1.0,
    stream_id: int = 0,
) -> dict:
    """Photo/video capture and video streaming control for a camera.

    Args:
        component_id (int): camera component id from list_cameras.
        action (str): one of "take_photo", "start_photo_interval",
            "stop_photo_interval", "start_video", "stop_video",
            "start_video_streaming", "stop_video_streaming",
            "video_stream_info", "last_capture_info".
        interval_s (float): seconds between photos for start_photo_interval.
        stream_id (int): video stream id for the streaming actions.

    Returns:
        dict: status (+ info payloads for the info actions).
    """
    log_tool_call(
        "camera_capture", component_id=component_id, action=action, interval_s=interval_s, stream_id=stream_id
    )
    action = str(action).lower()
    actions = (
        "take_photo",
        "start_photo_interval",
        "stop_photo_interval",
        "start_video",
        "stop_video",
        "start_video_streaming",
        "stop_video_streaming",
        "video_stream_info",
        "last_capture_info",
    )
    if action not in actions:
        return _fail(f"action must be one of {actions}, got {action!r}")
    if action == "start_photo_interval" and not 0.1 <= float(interval_s) <= 3600:
        return _fail(f"interval_s must be between 0.1 and 3600, got {interval_s}")

    drone = await get_drone(ctx)
    if drone is None:
        return dict(CONN_ERROR)
    cam = drone.camera
    try:
        log_mavlink_cmd(f"drone.camera.{action}", component_id=component_id)
        if action == "take_photo":
            await cam.take_photo(component_id)
        elif action == "start_photo_interval":
            await cam.start_photo_interval(component_id, float(interval_s))
        elif action == "stop_photo_interval":
            await cam.stop_photo_interval(component_id)
        elif action == "start_video":
            await cam.start_video(component_id)
        elif action == "stop_video":
            await cam.stop_video(component_id)
        elif action == "start_video_streaming":
            await cam.start_video_streaming(component_id, int(stream_id))
        elif action == "stop_video_streaming":
            await cam.stop_video_streaming(component_id, int(stream_id))
        elif action == "video_stream_info":
            info = await cam.get_video_stream_info(component_id)
            return _ok(video_stream_info=to_jsonable(info))
        else:  # last_capture_info
            info = await first_stream_item(cam.capture_info(), timeout_s=8.0)
            return _ok(capture_info=to_jsonable(info))
        return _ok(message=f"{action} ok")
    except TimeoutError:
        return _fail("No capture info received within 8s")
    except Exception as e:
        logger.error(f"camera_capture({action}) failed: {e}")
        return _fail(f"camera {action} failed: {e}")


@mcp.tool()
async def camera_settings(
    ctx: Context,
    component_id: int,
    action: str,
    mode: str = "",
    setting_id: str = "",
    option_id: str = "",
) -> dict:
    """Get/set camera mode and settings.

    Args:
        component_id (int): camera component id from list_cameras.
        action (str): "get_mode", "set_mode", "list_settings" (current values),
            "list_options" (possible settings + options), "get_setting",
            "set_setting", or "reset" (factory reset).
        mode (str): for set_mode: "photo" or "video".
        setting_id (str): for get_setting/set_setting.
        option_id (str): for set_setting.

    Returns:
        dict: status + requested data.
    """
    log_tool_call(
        "camera_settings",
        component_id=component_id,
        action=action,
        mode=mode,
        setting_id=setting_id,
        option_id=option_id,
    )
    action = str(action).lower()
    actions = ("get_mode", "set_mode", "list_settings", "list_options", "get_setting", "set_setting", "reset")
    if action not in actions:
        return _fail(f"action must be one of {actions}, got {action!r}")
    if action == "set_mode" and str(mode).lower() not in ("photo", "video"):
        return _fail(f'mode must be "photo" or "video", got {mode!r}')
    if action in ("get_setting", "set_setting") and not setting_id:
        return _fail(f"{action} requires setting_id")
    if action == "set_setting" and not option_id:
        return _fail("set_setting requires option_id")

    drone = await get_drone(ctx)
    if drone is None:
        return dict(CONN_ERROR)
    cam = drone.camera
    try:
        log_mavlink_cmd(f"drone.camera.{action}", component_id=component_id)
        if action == "get_mode":
            result = await cam.get_mode(component_id)
            return _ok(mode=to_jsonable(result))
        if action == "set_mode":
            await cam.set_mode(component_id, Mode.PHOTO if mode.lower() == "photo" else Mode.VIDEO)
            return _ok(message=f"mode set to {mode.lower()}")
        if action == "list_settings":
            settings = await cam.get_current_settings(component_id)
            return _ok(settings=to_jsonable(settings))
        if action == "list_options":
            options = await cam.get_possible_setting_options(component_id)
            return _ok(setting_options=to_jsonable(options))
        if action == "get_setting":
            setting = await cam.get_setting(component_id, Setting(setting_id, "", Option("", ""), False))
            return _ok(setting=to_jsonable(setting))
        if action == "set_setting":
            await cam.set_setting(component_id, Setting(setting_id, "", Option(option_id, ""), False))
            return _ok(message=f"setting {setting_id} set to {option_id}")
        await cam.reset_settings(component_id)
        return _ok(message="camera settings reset to factory defaults")
    except Exception as e:
        logger.error(f"camera_settings({action}) failed: {e}")
        return _fail(f"camera {action} failed: {e}")


@mcp.tool()
async def camera_storage(
    ctx: Context,
    component_id: int,
    action: str,
    storage_id: int = 0,
    photos_range: str = "since_connection",
) -> dict:
    """Camera storage status, formatting, and photo listing.

    Args:
        component_id (int): camera component id from list_cameras.
        action (str): "status", "format" (DESTRUCTIVE - erases the storage),
            or "list_photos".
        storage_id (int): storage id for "format".
        photos_range (str): "all" or "since_connection" for "list_photos".

    Returns:
        dict: status + requested data.
    """
    log_tool_call(
        "camera_storage", component_id=component_id, action=action, storage_id=storage_id, photos_range=photos_range
    )
    action = str(action).lower()
    if action not in ("status", "format", "list_photos"):
        return _fail(f'action must be "status", "format" or "list_photos", got {action!r}')
    if action == "list_photos" and str(photos_range).lower() not in ("all", "since_connection"):
        return _fail(f'photos_range must be "all" or "since_connection", got {photos_range!r}')

    drone = await get_drone(ctx)
    if drone is None:
        return dict(CONN_ERROR)
    try:
        log_mavlink_cmd(f"drone.camera.storage.{action}", component_id=component_id)
        if action == "status":
            storage = await drone.camera.get_storage(component_id)
            return _ok(storage=to_jsonable(storage))
        if action == "format":
            await drone.camera.format_storage(component_id, int(storage_id))
            return _ok(message=f"storage {storage_id} formatted")
        rng = PhotosRange.ALL if str(photos_range).lower() == "all" else PhotosRange.SINCE_CONNECTION
        photos = await drone.camera.list_photos(component_id, rng)
        return _ok(photos=to_jsonable(photos))
    except Exception as e:
        logger.error(f"camera_storage({action}) failed: {e}")
        return _fail(f"camera storage {action} failed: {e}")


@mcp.tool()
async def camera_zoom_focus(
    ctx: Context,
    component_id: int,
    control: str,
    action: str,
    value: float = 0.0,
) -> dict:
    """Continuous zoom/focus control for a camera.

    Args:
        component_id (int): camera component id from list_cameras.
        control (str): "zoom" or "focus".
        action (str): "in_start" (start moving in/tele), "out_start" (start
            moving out/wide), "stop", or "range" (jump to `value`: zoom factor
            for zoom, 0..100 % for focus).
        value (float): target for action="range".

    Returns:
        dict: status.
    """
    log_tool_call("camera_zoom_focus", component_id=component_id, control=control, action=action, value=value)
    control = str(control).lower()
    action = str(action).lower()
    if control not in ("zoom", "focus"):
        return _fail(f'control must be "zoom" or "focus", got {control!r}')
    if action not in ("in_start", "out_start", "stop", "range"):
        return _fail(f'action must be "in_start", "out_start", "stop" or "range", got {action!r}')

    drone = await get_drone(ctx)
    if drone is None:
        return dict(CONN_ERROR)
    cam = drone.camera
    try:
        log_mavlink_cmd(f"drone.camera.{control}_{action}", component_id=component_id)
        if control == "zoom":
            if action == "in_start":
                await cam.zoom_in_start(component_id)
            elif action == "out_start":
                await cam.zoom_out_start(component_id)
            elif action == "stop":
                await cam.zoom_stop(component_id)
            else:
                await cam.zoom_range(component_id, float(value))
        else:
            if action == "in_start":
                await cam.focus_in_start(component_id)
            elif action == "out_start":
                await cam.focus_out_start(component_id)
            elif action == "stop":
                await cam.focus_stop(component_id)
            else:
                await cam.focus_range(component_id, float(value))
        return _ok(message=f"{control} {action} ok")
    except Exception as e:
        logger.error(f"camera_zoom_focus({control},{action}) failed: {e}")
        return _fail(f"camera {control} {action} failed: {e}")


@mcp.tool()
async def camera_tracking(
    ctx: Context,
    component_id: int,
    action: str,
    point_x: float = 0.5,
    point_y: float = 0.5,
    radius: float = 0.1,
    top_left_x: float = 0.0,
    top_left_y: float = 0.0,
    bottom_right_x: float = 1.0,
    bottom_right_y: float = 1.0,
) -> dict:
    """Onboard visual tracking control (cameras supporting MAVLink tracking).

    Coordinates are normalized image coordinates: 0..1, origin top-left.

    Args:
        component_id (int): camera component id from list_cameras.
        action (str): "track_point", "track_rectangle", or "stop".
        point_x, point_y, radius (float): for track_point.
        top_left_x, top_left_y, bottom_right_x, bottom_right_y (float): for
            track_rectangle.

    Returns:
        dict: status.
    """
    log_tool_call("camera_tracking", component_id=component_id, action=action)
    action = str(action).lower()
    if action not in ("track_point", "track_rectangle", "stop"):
        return _fail(f'action must be "track_point", "track_rectangle" or "stop", got {action!r}')
    for name, v in (
        ("point_x", point_x),
        ("point_y", point_y),
        ("top_left_x", top_left_x),
        ("top_left_y", top_left_y),
        ("bottom_right_x", bottom_right_x),
        ("bottom_right_y", bottom_right_y),
    ):
        if not 0.0 <= float(v) <= 1.0:
            return _fail(f"{name} must be within 0..1, got {v}")

    drone = await get_drone(ctx)
    if drone is None:
        return dict(CONN_ERROR)
    try:
        log_mavlink_cmd(f"drone.camera.{action}", component_id=component_id)
        if action == "track_point":
            await drone.camera.track_point(component_id, float(point_x), float(point_y), float(radius))
        elif action == "track_rectangle":
            await drone.camera.track_rectangle(
                component_id,
                float(top_left_x),
                float(top_left_y),
                float(bottom_right_x),
                float(bottom_right_y),
            )
        else:
            await drone.camera.track_stop(component_id)
        return _ok(message=f"{action} ok")
    except Exception as e:
        logger.error(f"camera_tracking({action}) failed: {e}")
        return _fail(f"camera {action} failed: {e}")
