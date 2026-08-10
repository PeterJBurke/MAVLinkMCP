"""Mission management MCP tools (MavSDK ``mission``/``mission_raw`` plugins)."""

import asyncio

from mavsdk.mission_raw import MissionItem
from mcp.server.fastmcp import Context

from droneserver.app import mcp
from droneserver.mavlink.connection import ensure_connection
from droneserver.telemetry.flight_log import LogColors, log_mavlink_cmd, log_tool_call, logger
from droneserver.tools._common import first_stream_item

#: How long any of these tools will wait for a ``mission_progress`` sample.
#:
#: MavSDK publishes mission progress when the vehicle crosses a waypoint, not on
#: a timer, so between two distant waypoints the stream is legitimately silent
#: for minutes. Every read of it must therefore be bounded: a tool that blocks
#: until the next transition holds an MCP call open for the whole leg, and the
#: client eventually kills it at *its* timeout with no answer at all - which is
#: strictly worse than answering "progress unknown" straight away.
MISSION_PROGRESS_TIMEOUT_S = 5.0


async def _mission_progress_or_unknown(drone) -> tuple[int, int]:
    """``(current, total)`` waypoints, or ``(0, 0)`` if the stream is silent.

    Never raises and never blocks longer than
    :data:`MISSION_PROGRESS_TIMEOUT_S`. A total of ``0`` means "we do not know",
    which is exactly what a silent progress stream tells us, and is what every
    caller here already treats as "no progress information".
    """
    try:
        progress = await first_stream_item(drone.mission.mission_progress(), MISSION_PROGRESS_TIMEOUT_S)
    except Exception:
        return 0, 0
    return progress.current, progress.total


@mcp.tool()
async def print_mission_progress(ctx: Context) -> dict:
    """
    Print and return the current mission progress of the drone. Waits for connection if not ready.

    Args:
        ctx (Context): The context of the request.

    Returns:
        dict: A dictionary containing the current and total mission progress or error status.
    """
    connector = ctx.request_context.lifespan_context

    # Wait for connection
    if not await ensure_connection(connector):
        return {"status": "failed", "error": "Drone connection timeout. Please wait and try again."}

    drone = connector.drone
    try:
        mission_progress = await first_stream_item(drone.mission.mission_progress(), 10.0)
    except TimeoutError:
        return {
            "status": "failed",
            "error": "No mission progress received within 10s "
            "(progress is emitted on waypoint transitions; there may be no active mission)",
        }
    except Exception as e:
        return {"status": "failed", "error": f"Mission progress read failed: {e}"}
    logger.info(f"Mission progress: {mission_progress.current}/{mission_progress.total}")
    return {"status": "success", "current": mission_progress.current, "total": mission_progress.total}


@mcp.tool()
async def initiate_mission(ctx: Context, mission_points: list, return_to_launch: bool = True) -> dict:
    """
    Initiate a mission with a list of mission points. The drone must be armed. Waits for connection if not ready.

    Args:
        ctx (Context): The context of the request.
        mission_points (list): A list of dictionaries representing mission points. Each dictionary must include:
            - latitude_deg (float): Latitude in degrees (range: -90 to +90).
            - longitude_deg (float): Longitude in degrees (range: -180 to +180).
            - relative_altitude_m (float): Altitude relative to the takeoff altitude in meters.
            - speed_m_s (float): Speed in meters per second.
            - is_fly_through (bool): Whether to fly through the point or stop.
            - gimbal_pitch_deg (float): Gimbal pitch angle in degrees (optional).
            - gimbal_yaw_deg (float): Gimbal yaw angle in degrees (optional).
            - camera_action (MissionItem.CameraAction): Camera action at the point (optional).
            - loiter_time_s (float): Loiter time in seconds (optional).
            - camera_photo_interval_s (float): Camera photo interval in seconds (optional).
            - acceptance_radius_m (float): Acceptance radius in meters (optional).
            - yaw_deg (float): Yaw angle in degrees (optional).
            - camera_photo_distance_m (float): Camera photo distance in meters (optional).
            - vehicle_action (MissionItem.VehicleAction): Vehicle action at the point (optional).
        return_to_launch (bool): Whether to return to launch after completing the mission. Default is True.

    Returns:
        dict: Status message with success or error.
    """
    log_tool_call("initiate_mission", waypoint_count=len(mission_points), return_to_launch=return_to_launch)
    connector = ctx.request_context.lifespan_context

    # Wait for connection
    if not await ensure_connection(connector):
        return {"status": "failed", "error": "Drone connection timeout. Please wait and try again."}

    drone = connector.drone

    # Validate and construct mission items using mission_raw (ArduPilot-compatible)
    mission_items = []
    for i, point in enumerate(mission_points):
        try:
            # Validate latitude and longitude ranges
            if not (-90 <= point["latitude_deg"] <= 90):
                return {
                    "status": "failed",
                    "error": f"Invalid latitude_deg: {point['latitude_deg']}. Must be between -90 and 90.",
                }
            if not (-180 <= point["longitude_deg"] <= 180):
                return {
                    "status": "failed",
                    "error": f"Invalid longitude_deg: {point['longitude_deg']}. Must be between -180 and 180.",
                }

            # Use mission_raw format (raw MAVLink protocol)
            mission_items.append(
                MissionItem(
                    seq=i,
                    frame=3,  # MAV_FRAME_GLOBAL_RELATIVE_ALT
                    command=16,  # MAV_CMD_NAV_WAYPOINT
                    current=1 if i == 0 else 0,
                    autocontinue=1,
                    param1=point.get("loiter_time_s", 0),  # Hold time
                    param2=point.get("acceptance_radius_m", 2.0),  # Acceptance radius
                    param3=0,  # Pass radius
                    param4=point.get("yaw_deg", float("nan")),  # Yaw angle
                    x=int(point["latitude_deg"] * 1e7),  # Latitude * 1e7
                    y=int(point["longitude_deg"] * 1e7),  # Longitude * 1e7
                    z=float(point["relative_altitude_m"]),  # Altitude
                    mission_type=0,  # MAV_MISSION_TYPE_MISSION
                )
            )
        except KeyError as e:
            return {"status": "failed", "error": f"Missing required field in mission point: {e}"}

    # Set return-to-launch behavior
    log_mavlink_cmd("drone.mission.set_return_to_launch_after_mission", return_to_launch=return_to_launch)
    await drone.mission.set_return_to_launch_after_mission(return_to_launch)

    log_mavlink_cmd("drone.mission_raw.upload_mission", waypoint_count=len(mission_items))
    logger.info("Uploading mission using mission_raw (ArduPilot-compatible)")
    await drone.mission_raw.upload_mission(mission_items)

    log_mavlink_cmd("drone.mission.start_mission")
    logger.info("⚠️  Mission starting - drone will switch to AUTO flight mode")
    await drone.mission.start_mission()

    return {
        "status": "success",
        "message": f"Mission started with {len(mission_items)} waypoints",
        "note": "Flight mode automatically changed to AUTO for mission execution",
    }


@mcp.tool()
async def pause_mission(ctx: Context, mode: str = "guided_hold") -> dict:
    """
    Pause the running mission. v2 replaces the v1 deprecated stub with a real,
    SAFE implementation.

    mode="guided_hold" (DEFAULT, RECOMMENDED): switches to GUIDED and holds
    the current position/altitude - the fix for the documented v1 crash
    (LOITER descends without RC throttle input; see
    LOITER_MODE_CRASH_REPORT.md). Same behavior as hold_mission_position.

    mode="native_hold": firmware-native pause (MavSDK mission pause ->
    Hold/Loiter). ⚠️ On ArduPilot, LOITER altitude depends on RC throttle;
    on a real drone WITHOUT an RC transmitter at mid-throttle this can
    descend. Only use with RC present or on PX4.

    To continue the mission afterwards use resume_mission (optionally
    set_current_waypoint first).

    Args:
        mode (str): "guided_hold" (default, safe) or "native_hold".

    Returns:
        dict: status + position/waypoint info.
    """
    log_tool_call("pause_mission", mode=mode)
    mode = str(mode).lower()
    if mode not in ("guided_hold", "native_hold"):
        return {"status": "failed", "error": f'mode must be "guided_hold" or "native_hold", got {mode!r}'}

    connector = ctx.request_context.lifespan_context
    if not await ensure_connection(connector):
        return {"status": "failed", "error": "Drone connection timeout. Please wait and try again."}
    drone = connector.drone

    if mode == "native_hold":
        try:
            log_mavlink_cmd("drone.mission.pause_mission")
            await drone.mission.pause_mission()
        except Exception as e:
            logger.error(f"native mission pause failed: {e}")
            return {"status": "failed", "error": f"Mission pause failed: {e}"}
        return {
            "status": "success",
            "message": "Mission paused (firmware Hold/Loiter mode)",
            "warning": "⚠️ On ArduPilot, LOITER altitude tracks RC throttle - without an RC "
            "at mid-throttle the drone can descend. Prefer mode=guided_hold without RC.",
            "note": "Use resume_mission to continue.",
        }

    # guided_hold: goto current position in GUIDED (altitude-safe, no RC needed)
    try:
        current_wp = 0
        total_wp = 0
        try:
            progress = await first_stream_item(drone.mission.mission_progress(), 3.0)
            current_wp, total_wp = progress.current, progress.total
        except TimeoutError:
            pass  # no active mission info - still safe to hold position
        position = await first_stream_item(drone.telemetry.position(), 10.0)
        current_lat = position.latitude_deg
        current_lon = position.longitude_deg
        current_alt = position.absolute_altitude_m
        log_mavlink_cmd(f"drone.action.goto_location(lat={current_lat}, lon={current_lon}, alt={current_alt})")
        logger.info(f"Pausing mission via GUIDED hold - was at waypoint {current_wp}/{total_wp}")
        await drone.action.goto_location(current_lat, current_lon, current_alt, float("nan"))
    except Exception as e:
        logger.error(f"{LogColors.ERROR}❌ TOOL ERROR - Failed to pause mission: {e}{LogColors.RESET}")
        return {"status": "failed", "error": f"Mission pause failed: {e}"}
    return {
        "status": "success",
        "message": f"Mission paused - holding position in GUIDED mode (was at waypoint {current_wp}/{total_wp})",
        "was_at_waypoint": current_wp,
        "total_waypoints": total_wp,
        "position": {"latitude": current_lat, "longitude": current_lon, "altitude": current_alt},
        "flight_mode": "GUIDED",
        "note": "Use set_current_waypoint + resume_mission to continue the mission.",
    }


@mcp.tool()
async def hold_mission_position(ctx: Context) -> dict:
    """
    Alternative to pause_mission that holds position in GUIDED mode instead of LOITER.
    This interrupts the current mission and switches to GUIDED mode to hold the current position.
    Unlike pause_mission, this does NOT enter LOITER mode.

    NOTE: This stops the mission entirely. To continue the mission after using this,
    you must use set_current_waypoint() to jump back to a waypoint and resume_mission(),
    or upload/initiate a new mission.

    Use this when you want to:
    - Pause flight without entering LOITER mode
    - Maintain altitude stability (GUIDED mode)
    - Temporarily interrupt a mission for manual control

    Waits for connection if not ready.

    Args:
        ctx (Context): The context of the request.

    Returns:
        dict: Status message with current position and waypoint info.
    """
    connector = ctx.request_context.lifespan_context

    # Wait for connection
    if not await ensure_connection(connector):
        return {"status": "failed", "error": "Drone connection timeout. Please wait and try again."}

    drone = connector.drone
    log_tool_call("hold_mission_position")

    try:
        # Get current mission progress before holding (bounded - see
        # _mission_progress_or_unknown: this stream is silent between waypoints)
        current_wp, total_wp = await _mission_progress_or_unknown(drone)

        # Get current position
        async for position in drone.telemetry.position():
            current_lat = position.latitude_deg
            current_lon = position.longitude_deg
            current_alt = position.absolute_altitude_m
            break

        # Use hold_position to stay in GUIDED mode
        # This will call goto_location with current position
        log_mavlink_cmd(f"drone.action.goto_location(lat={current_lat}, lon={current_lon}, alt={current_alt})")
        logger.info(
            f"⚠️  Holding mission position in GUIDED mode (not LOITER) - was at waypoint {current_wp}/{total_wp}"
        )
        await drone.action.goto_location(current_lat, current_lon, current_alt, float("nan"))

        return {
            "status": "success",
            "message": f"Holding position in GUIDED mode - mission interrupted at waypoint {current_wp}/{total_wp}",
            "was_at_waypoint": current_wp,
            "total_waypoints": total_wp,
            "position": {"latitude": current_lat, "longitude": current_lon, "altitude": current_alt},
            "flight_mode": "GUIDED",
            "note": "Mission stopped. To continue: use set_current_waypoint() then resume_mission(), or start a new mission.",
        }
    except Exception as e:
        logger.error(f"{LogColors.ERROR}❌ TOOL ERROR - Failed to hold mission position: {e}{LogColors.RESET}")
        return {"status": "failed", "error": f"Hold mission position failed: {str(e)}"}


@mcp.tool()
async def resume_mission(ctx: Context) -> dict:
    """
    Resume a previously paused mission.
    The drone will continue from where it was paused.
    Waits for connection if not ready.

    Args:
        ctx (Context): The context of the request.

    Returns:
        dict: Status message with success or error including current waypoint.
    """
    connector = ctx.request_context.lifespan_context

    # Wait for connection
    if not await ensure_connection(connector):
        return {"status": "failed", "error": "Drone connection timeout. Please wait and try again."}

    drone = connector.drone
    log_tool_call("resume_mission")

    try:
        # Get current mission progress before resuming (bounded - see
        # _mission_progress_or_unknown: this stream is silent between waypoints)
        current_wp, total_wp = await _mission_progress_or_unknown(drone)

        log_mavlink_cmd("drone.mission.start_mission")
        logger.info(
            f"⚠️  Resuming mission from waypoint {current_wp}/{total_wp} - drone will switch to AUTO flight mode"
        )
        await drone.mission.start_mission()

        # Give the autopilot a moment to process the command
        await asyncio.sleep(0.5)

        # Verify flight mode changed to AUTO
        try:
            flight_mode = await drone.telemetry.flight_mode().__anext__()
            logger.info(f"Flight mode after resume: {flight_mode}")
            mode_ok = "AUTO" in str(flight_mode) or "MISSION" in str(flight_mode)
        except Exception:
            mode_ok = False
            flight_mode = "UNKNOWN"

        return {
            "status": "success",
            "message": f"Mission resumed from waypoint {current_wp}/{total_wp}",
            "current_waypoint": current_wp,
            "total_waypoints": total_wp,
            "flight_mode": str(flight_mode),
            "mode_transition_ok": mode_ok,
            "note": "Flight mode should have changed to AUTO/MISSION for mission execution",
        }
    except Exception as e:
        logger.error(f"{LogColors.ERROR}❌ TOOL ERROR - Failed to resume mission: {e}{LogColors.RESET}")
        return {"status": "failed", "error": f"Mission resume failed: {str(e)}"}


@mcp.tool()
async def clear_mission(ctx: Context) -> dict:
    """
    Clear the current mission from the drone.
    Removes all uploaded waypoints.
    Waits for connection if not ready.

    Args:
        ctx (Context): The context of the request.

    Returns:
        dict: Status message with success or error.
    """
    connector = ctx.request_context.lifespan_context

    # Wait for connection
    if not await ensure_connection(connector):
        return {"status": "failed", "error": "Drone connection timeout. Please wait and try again."}

    drone = connector.drone
    logger.info("Clearing mission")

    try:
        log_mavlink_cmd("drone.mission.clear_mission")
        await drone.mission.clear_mission()
        return {"status": "success", "message": "Mission cleared - all waypoints removed"}
    except Exception as e:
        logger.error(f"{LogColors.ERROR}❌ TOOL ERROR - Failed to clear mission: {e}{LogColors.RESET}")
        return {"status": "failed", "error": f"Mission clear failed: {str(e)}"}


@mcp.tool()
async def upload_mission(ctx: Context, waypoints: list) -> dict:
    """
    Upload a mission to the drone WITHOUT starting it.
    Allows preparing missions in advance.
    Waits for connection if not ready.

    Args:
        ctx (Context): The context of the request.
        waypoints (list): List of waypoint dictionaries with keys:
                         - latitude_deg (float): Waypoint latitude
                         - longitude_deg (float): Waypoint longitude
                         - relative_altitude_m (float): Altitude above home
                         - speed_m_s (float, optional): Speed to waypoint

    Returns:
        dict: Status message with mission summary.

    Examples:
        waypoints = [
            {"latitude_deg": 33.645, "longitude_deg": -117.842, "relative_altitude_m": 10},
            {"latitude_deg": 33.646, "longitude_deg": -117.843, "relative_altitude_m": 15},
            {"latitude_deg": 33.647, "longitude_deg": -117.844, "relative_altitude_m": 20}
        ]
        upload_mission(waypoints)

    Note:
        - Mission is uploaded but NOT started automatically
        - Use initiate_mission or start_mission to begin execution
        - Clears any existing mission first

    Important:
        Pass waypoints as a properly formatted list of dictionaries.
        Each waypoint MUST have: latitude_deg, longitude_deg, relative_altitude_m
    """
    connector = ctx.request_context.lifespan_context

    # Wait for connection
    if not await ensure_connection(connector):
        return {"status": "failed", "error": "Drone connection timeout. Please wait and try again."}

    # Validate waypoints input
    if not waypoints:
        return {
            "status": "failed",
            "error": "No waypoints provided",
            "example": [
                {"latitude_deg": 33.645, "longitude_deg": -117.842, "relative_altitude_m": 10},
                {"latitude_deg": 33.646, "longitude_deg": -117.843, "relative_altitude_m": 15},
            ],
        }

    if not isinstance(waypoints, list):
        return {
            "status": "failed",
            "error": f"Waypoints must be a list, got {type(waypoints).__name__}",
            "hint": "Pass waypoints as: [{'latitude_deg': 33.645, 'longitude_deg': -117.842, 'relative_altitude_m': 10}]",
        }

    drone = connector.drone
    logger.info(f"Uploading mission with {len(waypoints)} waypoints")

    try:
        # Validate and create mission items
        mission_items = []
        for i, wp in enumerate(waypoints):
            # Type check
            if not isinstance(wp, dict):
                return {
                    "status": "failed",
                    "error": f"Waypoint {i} must be a dictionary, got {type(wp).__name__}",
                    "hint": "Each waypoint needs: latitude_deg, longitude_deg, relative_altitude_m",
                }

            # Required field check
            required_fields = ["latitude_deg", "longitude_deg", "relative_altitude_m"]
            missing = [f for f in required_fields if f not in wp]
            if missing:
                return {
                    "status": "failed",
                    "error": f"Waypoint {i} missing required fields: {', '.join(missing)}",
                    "received": list(wp.keys()),
                    "required": required_fields,
                }

            # Validate coordinates
            if not (-90 <= wp["latitude_deg"] <= 90):
                return {
                    "status": "failed",
                    "error": f"Waypoint {i}: invalid latitude {wp['latitude_deg']} (must be -90 to 90)",
                }
            if not (-180 <= wp["longitude_deg"] <= 180):
                return {
                    "status": "failed",
                    "error": f"Waypoint {i}: invalid longitude {wp['longitude_deg']} (must be -180 to 180)",
                }
            if wp["relative_altitude_m"] < 0:
                return {"status": "failed", "error": f"Waypoint {i}: altitude cannot be negative"}

            # Use mission_raw format (ArduPilot-compatible)
            # MAVLink uses lat/lon * 1e7 as integers
            mission_item = MissionItem(
                seq=i,  # Sequence number
                frame=3,  # MAV_FRAME_GLOBAL_RELATIVE_ALT
                command=16,  # MAV_CMD_NAV_WAYPOINT
                current=1 if i == 0 else 0,  # First waypoint is current
                autocontinue=1,  # Auto-continue to next waypoint
                param1=0,  # Hold time (seconds)
                param2=2.0,  # Acceptance radius (meters)
                param3=0,  # Pass radius (meters)
                param4=float("nan"),  # Yaw angle (NaN = don't change)
                x=int(wp["latitude_deg"] * 1e7),  # Latitude * 1e7
                y=int(wp["longitude_deg"] * 1e7),  # Longitude * 1e7
                z=float(wp["relative_altitude_m"]),  # Altitude (meters)
                mission_type=0,  # MAV_MISSION_TYPE_MISSION
            )
            mission_items.append(mission_item)

        # Upload mission using mission_raw (ArduPilot-compatible)
        log_mavlink_cmd("drone.mission_raw.upload_mission", waypoint_count=len(waypoints))
        await drone.mission_raw.upload_mission(mission_items)

        logger.info(f"{LogColors.SUCCESS}✓ Mission uploaded successfully: {len(waypoints)} waypoints{LogColors.RESET}")

        return {
            "status": "success",
            "message": f"Mission uploaded with {len(waypoints)} waypoints",
            "waypoint_count": len(waypoints),
            "waypoints_summary": [
                f"WP{i}: ({wp['latitude_deg']:.5f}, {wp['longitude_deg']:.5f}) @ {wp['relative_altitude_m']}m"
                for i, wp in enumerate(waypoints)
            ],
            "note": "Mission uploaded but NOT started. Use initiate_mission to start.",
        }
    except Exception as e:
        logger.error(f"Mission upload failed: {e}{LogColors.RESET}")
        return {
            "status": "failed",
            "error": f"Mission upload failed: {str(e)}",
            "troubleshooting": "Ensure waypoints are formatted correctly as list of dictionaries",
        }


@mcp.tool()
async def download_mission(ctx: Context) -> dict:
    """
    Download the current mission from the drone.
    Retrieves all waypoints stored on the drone.
    Waits for connection if not ready.

    Args:
        ctx (Context): The context of the request.

    Returns:
        dict: Mission data with all waypoints.

    Use Cases:
        - Backup current mission
        - Verify uploaded mission
        - Check drone's planned route
        - Mission debugging

    Note:
        Mission download may occasionally fail with "UNSUPPORTED" immediately after upload
        due to ArduPilot mission state synchronization. If this happens, wait a moment and
        try again, or use mission_progress() to verify the mission exists.
    """
    connector = ctx.request_context.lifespan_context

    # Wait for connection
    if not await ensure_connection(connector):
        return {"status": "failed", "error": "Drone connection timeout. Please wait and try again."}

    drone = connector.drone
    logger.info("Downloading mission from drone")

    # v2 fix: v1 pre-checked drone.mission.mission_progress() here, which HANGS
    # forever when the mission was uploaded via the raw path (the stream never
    # emits). The raw download below reports "no mission" errors on its own.
    # Try to download mission with proper retry logic
    max_retries = 5  # Increased retries
    retry_delay = 0.3  # Shorter, more frequent retries

    for attempt in range(max_retries):
        try:
            if attempt > 0:
                logger.info(f"Retry attempt {attempt + 1}/{max_retries} after {retry_delay}s delay...")
                await asyncio.sleep(retry_delay)

            log_mavlink_cmd("drone.mission_raw.download_mission")
            mission_items = await drone.mission_raw.download_mission()

            # Convert raw mission items to dict format
            # Filter for waypoint commands only (command 16 = MAV_CMD_NAV_WAYPOINT)
            waypoints = []
            for item in mission_items:
                if item.command == 16:  # MAV_CMD_NAV_WAYPOINT
                    waypoints.append(
                        {
                            "seq": item.seq,
                            "latitude_deg": item.x / 1e7,  # Convert from int * 1e7 to float
                            "longitude_deg": item.y / 1e7,  # Convert from int * 1e7 to float
                            "relative_altitude_m": item.z,
                            "frame": item.frame,
                            "command": item.command,
                        }
                    )

            logger.info(
                f"{LogColors.SUCCESS}✓ Downloaded mission with {len(waypoints)} waypoints (from {len(mission_items)} total items){LogColors.RESET}"
            )

            return {
                "status": "success",
                "waypoint_count": len(waypoints),
                "waypoints": waypoints,
                "note": f"Downloaded on attempt {attempt + 1}" if attempt > 0 else None,
            }

        except Exception as e:
            error_str = str(e)

            # If UNSUPPORTED and not last attempt, retry
            if "UNSUPPORTED" in error_str.upper() and attempt < max_retries - 1:
                logger.warning(f"Mission download attempt {attempt + 1} failed (UNSUPPORTED), retrying...")
                continue

            # Last attempt or different error - report it
            logger.error(f"Mission download failed after {attempt + 1} attempts: {e}{LogColors.RESET}")

            # Provide helpful error message
            if "UNSUPPORTED" in error_str.upper():
                logger.error(
                    f"{LogColors.ERROR}Mission download failed - ArduPilot may need mission state refresh{LogColors.RESET}"
                )
                return {
                    "status": "failed",
                    "error": "Mission download UNSUPPORTED by current autopilot state",
                    "hint": "Try waiting a moment after upload, or use mission_progress() to verify mission exists",
                    "mission_exists": "Mission was successfully uploaded and verified via mission_progress",
                    "attempts": attempt + 1,
                    "technical_error": error_str,
                    "workaround": "Use is_mission_finished() to monitor mission execution even without download",
                }
            else:
                return {
                    "status": "failed",
                    "error": f"Mission download failed: {error_str}",
                    "hint": "Ensure a mission has been uploaded to the drone",
                    "attempts": attempt + 1,
                }

    # All retries exhausted with UNSUPPORTED (retry path continued past the loop)
    return {
        "status": "failed",
        "error": f"Mission download UNSUPPORTED after {max_retries} attempts",
        "hint": "Wait a moment after upload and retry, or use is_mission_finished() to monitor.",
    }


@mcp.tool()
async def set_current_waypoint(ctx: Context, waypoint_index: int) -> dict:
    """
    Jump to a specific waypoint in the current mission.
    Allows skipping ahead or going back in a mission.
    Waits for connection if not ready.

    Args:
        ctx (Context): The context of the request.
        waypoint_index (int): Waypoint number to jump to (0-based index).

    Returns:
        dict: Status message with new current waypoint.

    Examples:
        - set_current_waypoint(0) - Jump to first waypoint (restart mission)
        - set_current_waypoint(5) - Skip to waypoint 5
        - set_current_waypoint(3) - Go back to waypoint 3

    Use Cases:
        - Skip completed waypoints
        - Restart mission from beginning
        - Re-survey specific area
        - Mission recovery after interruption
    """
    connector = ctx.request_context.lifespan_context

    # Wait for connection
    if not await ensure_connection(connector):
        return {"status": "failed", "error": "Drone connection timeout. Please wait and try again."}

    if waypoint_index < 0:
        return {"status": "failed", "error": f"Invalid waypoint index: {waypoint_index}. Must be 0 or greater."}

    drone = connector.drone
    logger.info(f"Setting current mission waypoint to index {waypoint_index}")

    try:
        log_mavlink_cmd("drone.mission.set_current_mission_item", waypoint_index=waypoint_index)
        await drone.mission.set_current_mission_item(waypoint_index)

        logger.info(f"{LogColors.SUCCESS}✓ Current waypoint set to index {waypoint_index}{LogColors.RESET}")

        return {
            "status": "success",
            "message": f"Current waypoint set to index {waypoint_index}",
            "waypoint_index": waypoint_index,
            "note": "Mission will continue from this waypoint",
        }
    except Exception as e:
        logger.error(f"Set current waypoint failed: {e}{LogColors.RESET}")
        return {"status": "failed", "error": f"Set waypoint failed: {str(e)}"}


@mcp.tool()
async def is_mission_finished(ctx: Context) -> dict:
    """
    Check if the current mission has completed.
    Returns true if all waypoints have been reached.
    Waits for connection if not ready.

    Args:
        ctx (Context): The context of the request.

    Returns:
        dict: Boolean indicating if mission is finished.

    Use Cases:
        - Monitor mission completion
        - Trigger post-mission actions
        - Mission automation
        - Status monitoring
    """
    connector = ctx.request_context.lifespan_context

    # Wait for connection
    if not await ensure_connection(connector):
        return {"status": "failed", "error": "Drone connection timeout. Please wait and try again."}

    drone = connector.drone
    logger.info("Checking if mission is finished")

    try:
        # Check mission finished status
        log_mavlink_cmd("drone.mission.is_mission_finished")
        finished = await drone.mission.is_mission_finished()

        # Get current waypoint progress.
        #
        # ``mission_progress()`` publishes on waypoint TRANSITIONS, not on a
        # timer, so a mission flying a long leg (or one that never started)
        # emits nothing and an unbounded read here never returns. That is not
        # theoretical: in the halted N=5 campaign this call hung until the
        # client's 300 s timeout, after which the model gave up on the mission
        # tools and polled get_position 46 times instead. Progress is the
        # optional part of this answer - "is it finished" is the answer - so a
        # silent stream degrades to "unknown", never to a hang.
        current_wp, total_wp = await _mission_progress_or_unknown(drone)

        # Get current flight mode
        try:
            flight_mode = await drone.telemetry.flight_mode().__anext__()
        except Exception:
            flight_mode = "UNKNOWN"

        status_text = "FINISHED" if finished else "IN PROGRESS"
        logger.info(f"Mission status: {status_text} - Waypoint {current_wp}/{total_wp} - Mode: {flight_mode}")

        return {
            "status": "success",
            "mission_finished": finished,
            "status_text": status_text,
            "current_waypoint": current_wp,
            "total_waypoints": total_wp,
            "flight_mode": str(flight_mode),
            "progress_percentage": round((current_wp / total_wp * 100) if total_wp > 0 else 0, 1),
        }
    except Exception as e:
        logger.error(f"Check mission finished failed: {e}{LogColors.RESET}")
        return {"status": "failed", "error": f"Mission status check failed: {str(e)}"}


# ============================================================================
# v2: remaining mission-plugin coverage
# ============================================================================


@mcp.tool()
async def rtl_after_mission(ctx: Context, action: str, enabled: bool = True) -> dict:
    """Get or set whether the drone returns to launch automatically after the
    last mission item.

    Args:
        action (str): "get" or "set".
        enabled (bool): for "set": True = RTL after mission (default).

    Returns:
        dict: status (+ enabled for "get").
    """
    log_tool_call("rtl_after_mission", action=action, enabled=enabled)
    action = str(action).lower()
    if action not in ("get", "set"):
        return {"status": "failed", "error": f'action must be "get" or "set", got {action!r}'}

    connector = ctx.request_context.lifespan_context
    if not await ensure_connection(connector):
        return {"status": "failed", "error": "Drone connection timeout. Please wait and try again."}
    drone = connector.drone
    try:
        if action == "get":
            log_mavlink_cmd("drone.mission.get_return_to_launch_after_mission")
            value = await drone.mission.get_return_to_launch_after_mission()
            return {"status": "success", "enabled": value}
        log_mavlink_cmd("drone.mission.set_return_to_launch_after_mission", enabled=enabled)
        await drone.mission.set_return_to_launch_after_mission(bool(enabled))
        return {"status": "success", "message": f"RTL-after-mission set to {bool(enabled)}"}
    except Exception as e:
        logger.error(f"rtl_after_mission({action}) failed: {e}")
        return {"status": "failed", "error": f"rtl_after_mission {action} failed: {e}"}


@mcp.tool()
async def cancel_mission_transfer(ctx: Context, direction: str) -> dict:
    """Cancel an in-progress mission upload or download (mission plugin).

    Only meaningful while a transfer is running; errors otherwise.

    Args:
        direction (str): "upload" or "download".

    Returns:
        dict: status.
    """
    log_tool_call("cancel_mission_transfer", direction=direction)
    direction = str(direction).lower()
    if direction not in ("upload", "download"):
        return {"status": "failed", "error": f'direction must be "upload" or "download", got {direction!r}'}

    connector = ctx.request_context.lifespan_context
    if not await ensure_connection(connector):
        return {"status": "failed", "error": "Drone connection timeout. Please wait and try again."}
    drone = connector.drone
    try:
        if direction == "upload":
            log_mavlink_cmd("drone.mission.cancel_mission_upload")
            await drone.mission.cancel_mission_upload()
        else:
            log_mavlink_cmd("drone.mission.cancel_mission_download")
            await drone.mission.cancel_mission_download()
    except Exception as e:
        logger.error(f"cancel_mission_transfer({direction}) failed: {e}")
        return {"status": "failed", "error": f"cancel {direction} failed: {e}"}
    return {"status": "success", "message": f"mission {direction} cancelled"}
