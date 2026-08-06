"""Flight-control and navigation MCP tools (MavSDK ``action`` plugin)."""

import asyncio
import math

from mcp.server.fastmcp import Context

from droneserver.app import mcp
from droneserver.geo import haversine_distance
from droneserver.mavlink.connection import ensure_connection
from droneserver.telemetry.flight_log import (
    LogColors,
    get_flight_logger,
    log_mavlink_cmd,
    log_tool_call,
    log_tool_output,
    logger,
)


# ARM
@mcp.tool()
async def arm_drone(ctx: Context) -> dict:
    """Arm the drone. Waits for drone connection if not yet ready."""
    log_tool_call("arm_drone")
    connector = ctx.request_context.lifespan_context

    # Wait for connection
    if not await ensure_connection(connector):
        return {"status": "failed", "error": "Drone connection timeout. Please wait and try again."}

    drone = connector.drone
    log_mavlink_cmd("drone.action.arm")
    await drone.action.arm()
    return {"status": "success", "message": "Drone armed"}


@mcp.tool()
async def move_to_relative(ctx: Context, north_m: float, east_m: float, down_m: float, yaw_deg: float = 0.0) -> dict:
    """
    Move the drone relative to the current position using ArduPilot's GUIDED mode.

    ArduPilot automatically enters GUIDED mode when receiving goto_location commands
    (as long as the drone is armed). No manual mode switching required.

    The drone must be armed and in the air. Waits for connection if not ready.

    Args:
        ctx (Context): the context.
        north_m (float): distance in meters to move north (negative for south).
        east_m (float): distance in meters to move east (negative for west).
        down_m (float): distance in meters to move down (negative for up). Note: negative values = climb.
        yaw_deg (float): target yaw angle in degrees. Default is 0.0 (no yaw change).

    Returns:
        dict: Status message with success or error.
    """
    log_tool_call("move_to_relative", north_m=north_m, east_m=east_m, down_m=down_m, yaw_deg=yaw_deg)
    connector = ctx.request_context.lifespan_context

    # Wait for connection
    if not await ensure_connection(connector):
        return {"status": "failed", "error": "Drone connection timeout. Please wait and try again."}

    drone = connector.drone

    try:
        # Get current position
        position = await drone.telemetry.position().__anext__()
        current_lat = position.latitude_deg
        current_lon = position.longitude_deg
        # IMPORTANT: goto_location() requires ABSOLUTE altitude (MSL), not relative!
        current_alt = position.absolute_altitude_m

        # Calculate target altitude (down is positive in NED, so negate)
        target_alt = current_alt - down_m

        # Convert NED offsets (meters) to lat/lon offsets (degrees)
        # Earth radius in meters (approximate)
        EARTH_RADIUS = 6371000.0

        # Latitude: 1 degree = ~111,320 meters (constant)
        # north_m positive = increase latitude
        lat_offset_deg = north_m / 111320.0

        # Longitude: varies with latitude
        # east_m positive = increase longitude
        lon_offset_deg = east_m / (111320.0 * math.cos(math.radians(current_lat)))

        # Calculate target position
        target_lat = current_lat + lat_offset_deg
        target_lon = current_lon + lon_offset_deg

        logger.info("Moving in GUIDED mode:")
        logger.info(f"  Current: {current_lat:.6f}°, {current_lon:.6f}°")
        logger.info(f"  Altitude: {position.relative_altitude_m:.1f}m AGL (relative) / {current_alt:.1f}m MSL")
        logger.info(f"  Offset: north={north_m:.1f}m, east={east_m:.1f}m, down={down_m:.1f}m")
        target_rel_alt = position.relative_altitude_m - down_m
        logger.info(
            f"  Target: {target_lat:.6f}°, {target_lon:.6f}°, {target_rel_alt:.1f}m AGL (relative) / {target_alt:.1f}m MSL"
        )

        # Use goto_location with calculated target coordinates
        log_mavlink_cmd(
            "drone.action.goto_location",
            lat=f"{target_lat:.6f}",
            lon=f"{target_lon:.6f}",
            alt=f"{target_alt:.1f}",
            yaw=f"{yaw_deg:.1f}" if not math.isnan(yaw_deg) else "nan",
        )
        await drone.action.goto_location(target_lat, target_lon, target_alt, yaw_deg)

        logger.info("✓ Movement command sent successfully")
        return {
            "status": "success",
            "message": f"Moving: north={north_m}m, east={east_m}m, altitude_change={-down_m}m",
            "target_position": {"latitude_deg": target_lat, "longitude_deg": target_lon, "altitude_m": target_alt},
        }

    except Exception as e:
        logger.error(f"{LogColors.ERROR}❌ TOOL ERROR - Failed to execute relative movement: {e}{LogColors.RESET}")
        return {"status": "failed", "error": f"Movement failed: {str(e)}"}


@mcp.tool()
async def takeoff(ctx: Context, takeoff_altitude: float = 3.0, wait_for_altitude: bool = True) -> dict:
    """Command the drone to initiate takeoff and ascend to a specified altitude.
    The drone must be armed. Waits for connection if not ready.

    IMPORTANT: By default, this function waits until the drone reaches the target
    altitude before returning. This prevents unsafe conditions where subsequent
    navigation commands are sent while the drone is still climbing.

    Args:
        ctx (Context): The context of the request.
        takeoff_altitude (float): The altitude to ascend to after takeoff. Default is 3.0 meters.
        wait_for_altitude (bool): If True (default), waits until drone reaches target altitude.
                                  Set to False only if you need immediate return and will
                                  monitor altitude manually before sending navigation commands.

    Returns:
        dict: Status message with success or error, including final altitude reached.
    """
    log_tool_call("takeoff", takeoff_altitude=takeoff_altitude, wait_for_altitude=wait_for_altitude)
    connector = ctx.request_context.lifespan_context

    # Wait for connection
    if not await ensure_connection(connector):
        return {"status": "failed", "error": "Drone connection timeout. Please wait and try again."}

    drone = connector.drone
    logger.info(f"Taking off to {takeoff_altitude}m AGL (relative altitude)")
    log_mavlink_cmd("drone.action.set_takeoff_altitude", altitude=takeoff_altitude)
    await drone.action.set_takeoff_altitude(takeoff_altitude)
    log_mavlink_cmd("drone.action.takeoff")
    await drone.action.takeoff()

    if not wait_for_altitude:
        return {
            "status": "success",
            "message": f"Takeoff initiated to {takeoff_altitude}m AGL (relative)",
            "warning": "⚠️ Takeoff in progress - do NOT send navigation commands until altitude is reached!",
        }

    # Wait for drone to reach target altitude
    logger.info(f"Waiting for drone to reach {takeoff_altitude}m...")
    altitude_threshold = 0.5  # Consider arrived when within 0.5m of target
    max_wait_time = 60  # Maximum wait time in seconds
    check_interval = 1.0  # Check every second
    elapsed_time = 0

    while elapsed_time < max_wait_time:
        try:
            async for position in drone.telemetry.position():
                current_alt = position.relative_altitude_m
                logger.info(f"  Altitude: {current_alt:.1f}m / {takeoff_altitude}m")

                if current_alt >= (takeoff_altitude - altitude_threshold):
                    logger.info(f"{LogColors.SUCCESS}✅ Takeoff complete - reached {current_alt:.1f}m{LogColors.RESET}")
                    result = {
                        "status": "success",
                        "message": f"Takeoff complete - drone at {current_alt:.1f}m AGL",
                        "altitude_reached_m": round(current_alt, 1),
                        "target_altitude_m": takeoff_altitude,
                        "safe_to_navigate": True,
                    }
                    log_tool_output(result)
                    return result
                break  # Got one position reading, wait and try again
        except Exception as e:
            logger.warning(f"Error reading altitude: {e}")

        await asyncio.sleep(check_interval)
        elapsed_time += check_interval

    # Timeout - get final altitude
    try:
        async for position in drone.telemetry.position():
            current_alt = position.relative_altitude_m
            break
    except:
        current_alt = 0

    logger.warning(f"Takeoff timeout after {max_wait_time}s - current altitude: {current_alt:.1f}m")
    return {
        "status": "warning",
        "message": f"Takeoff timeout - drone at {current_alt:.1f}m (target was {takeoff_altitude}m)",
        "altitude_reached_m": round(current_alt, 1),
        "target_altitude_m": takeoff_altitude,
        "safe_to_navigate": current_alt >= (takeoff_altitude - altitude_threshold),
    }


@mcp.tool()
async def land(ctx: Context, force: bool = False) -> dict:
    """Command the drone to initiate landing at its current location.

    LANDING GATE SAFETY: If there's a pending navigation destination (from go_to_location),
    this will check if the drone has arrived before allowing landing. This prevents
    accidentally landing far from the intended destination.

    Use force=True to override the landing gate (emergency use only).

    Args:
        ctx (Context): The context of the request.
        force (bool): If True, bypass landing gate safety check (default: False).

    Returns:
        dict: Status message with success, blocked, or error.
    """
    log_tool_call("land", force=force)
    connector = ctx.request_context.lifespan_context

    # Wait for connection
    if not await ensure_connection(connector):
        return {"status": "failed", "error": "Drone connection timeout. Please wait and try again."}

    drone = connector.drone

    # LANDING GATE: Check if there's a pending destination
    if connector.pending_destination and not force:
        dest = connector.pending_destination
        dest_lat = dest["latitude"]
        dest_lon = dest["longitude"]

        # Get current position
        try:
            async for position in drone.telemetry.position():
                current_lat = position.latitude_deg
                current_lon = position.longitude_deg
                break

            distance = haversine_distance(current_lat, current_lon, dest_lat, dest_lon)

            # Landing gate threshold - block if more than 20m from destination
            landing_gate_threshold = 20.0

            if distance > landing_gate_threshold:
                logger.warning(
                    f"{LogColors.ERROR}🚫 LANDING BLOCKED - {distance:.0f}m from destination!{LogColors.RESET}"
                )

                result = {
                    "status": "blocked",
                    "message": f"Cannot land - drone is {distance:.0f}m from destination!",
                    "distance_to_destination_m": round(distance, 1),
                    "current_position": {"latitude": current_lat, "longitude": current_lon},
                    "destination": {"latitude": dest_lat, "longitude": dest_lon},
                    "recommendation": "Call monitor_flight() to check progress, or use land(force=True) for emergency landing",
                    "safe_to_land": False,
                }
                log_tool_output(result)
                return result
            else:
                # Close enough - clear destination and proceed with landing
                logger.info(
                    f"Landing gate passed - {distance:.1f}m from destination (within {landing_gate_threshold}m threshold)"
                )
                connector.pending_destination = None

        except Exception as e:
            logger.warning(f"Could not check position for landing gate: {e}")
            # Proceed with landing if we can't check position

    # Clear any pending destination since we're landing
    connector.pending_destination = None
    # Set landing flag so monitor_flight knows we're descending
    connector.landing_in_progress = True

    log_mavlink_cmd("drone.action.land")
    await drone.action.land()

    result = {
        "status": "success",
        "message": "Landing initiated",
        "next_step": "Call monitor_flight() until mission_complete is true",
    }
    log_tool_output(result)
    return result


@mcp.tool()
async def set_flight_mode(ctx: Context, mode: str) -> dict:
    """
    Set the flight mode of the drone.

    Available modes:
    - HOLD: Hold current position (requires GPS)
    - RTL: Return to launch/home position
    - LAND: Land at current position
    - GUIDED: Manual waypoint control (used by go_to_location)

    Note: Some modes like AUTO require an active mission.
    For GUIDED mode navigation, use go_to_location instead.

    Args:
        ctx (Context): The context of the request.
        mode (str): The flight mode to set (HOLD, RTL, LAND, GUIDED).

    Returns:
        dict: Status message with the new flight mode or error.
    """
    log_tool_call("set_flight_mode", mode=mode)
    connector = ctx.request_context.lifespan_context

    # Wait for connection
    if not await ensure_connection(connector):
        return {"status": "failed", "error": "Drone connection timeout. Please wait and try again."}

    drone = connector.drone
    mode_upper = mode.upper().strip()

    # Map mode names to MAVSDK actions
    supported_modes = {
        "HOLD": "hold",
        "LOITER": "hold",  # LOITER maps to hold action
        "RTL": "return_to_launch",
        "RETURN_TO_LAUNCH": "return_to_launch",
        "LAND": "land",
        "GUIDED": "guided",
    }

    if mode_upper not in supported_modes:
        return {
            "status": "failed",
            "error": f"Unsupported mode: {mode}. Supported modes: HOLD, RTL, LAND, GUIDED",
            "hint": "For AUTO mode, use initiate_mission or resume_mission instead.",
        }

    try:
        action_name = supported_modes[mode_upper]

        if action_name == "hold":
            log_mavlink_cmd("drone.action.hold")
            await drone.action.hold()
            result_mode = "HOLD/LOITER"

        elif action_name == "return_to_launch":
            log_mavlink_cmd("drone.action.return_to_launch")
            await drone.action.return_to_launch()
            result_mode = "RTL"

        elif action_name == "land":
            log_mavlink_cmd("drone.action.land")
            await drone.action.land()
            result_mode = "LAND"

        elif action_name == "guided":
            # For GUIDED, we need to send a position command to enter GUIDED mode
            # Get current position and command drone to hold there
            position = await drone.telemetry.position().__anext__()
            log_mavlink_cmd(
                "drone.action.goto_location (GUIDED mode)",
                lat=f"{position.latitude_deg:.6f}",
                lon=f"{position.longitude_deg:.6f}",
                alt=f"{position.absolute_altitude_m:.1f}",
            )
            await drone.action.goto_location(
                position.latitude_deg,
                position.longitude_deg,
                position.absolute_altitude_m,
                float("nan"),  # Maintain current yaw
            )
            result_mode = "GUIDED"

        # Verify mode changed
        await asyncio.sleep(0.5)
        try:
            new_mode = await drone.telemetry.flight_mode().__anext__()
            actual_mode = str(new_mode)
        except:
            actual_mode = "UNKNOWN"

        logger.info(f"{LogColors.SUCCESS}✅ Flight mode set to {result_mode} (actual: {actual_mode}){LogColors.RESET}")

        return {
            "status": "success",
            "message": f"Flight mode changed to {result_mode}",
            "requested_mode": mode_upper,
            "actual_mode": actual_mode,
        }

    except Exception as e:
        logger.error(f"{LogColors.ERROR}❌ TOOL ERROR - Failed to set flight mode: {e}{LogColors.RESET}")
        return {"status": "failed", "error": f"Failed to set flight mode: {str(e)}"}


@mcp.tool()
async def disarm_drone(ctx: Context) -> dict:
    """
    Disarm the drone motors. This stops the motors from spinning.
    SAFETY: Only use when drone is on the ground!
    Waits for connection if not ready.

    Args:
        ctx (Context): The context of the request.

    Returns:
        dict: Status message with success or error.
    """
    log_tool_call("disarm_drone")
    connector = ctx.request_context.lifespan_context

    # Wait for connection
    if not await ensure_connection(connector):
        return {"status": "failed", "error": "Drone connection timeout. Please wait and try again."}

    drone = connector.drone
    logger.info("Disarming drone")

    try:
        log_mavlink_cmd("drone.action.disarm")
        await drone.action.disarm()
        return {"status": "success", "message": "Drone disarmed - motors stopped"}
    except Exception as e:
        logger.error(f"{LogColors.ERROR}❌ TOOL ERROR - Failed to disarm: {e}{LogColors.RESET}")
        return {"status": "failed", "error": f"Disarm failed: {str(e)}"}


@mcp.tool()
async def return_to_launch(ctx: Context) -> dict:
    """
    Command the drone to return to its launch/home position (RTL mode).
    This is the primary emergency/safety feature.
    The drone will fly back to home and land automatically.
    Waits for connection if not ready.

    Args:
        ctx (Context): The context of the request.

    Returns:
        dict: Status message with success or error.
    """
    log_tool_call("return_to_launch")
    connector = ctx.request_context.lifespan_context

    # Wait for connection
    if not await ensure_connection(connector):
        return {"status": "failed", "error": "Drone connection timeout. Please wait and try again."}

    drone = connector.drone
    logger.info("Initiating Return to Launch (RTL)")

    try:
        log_mavlink_cmd("drone.action.return_to_launch")
        await drone.action.return_to_launch()
        return {"status": "success", "message": "Return to Launch initiated - drone returning home"}
    except Exception as e:
        logger.error(f"{LogColors.ERROR}❌ TOOL ERROR - RTL failed: {e}{LogColors.RESET}")
        return {"status": "failed", "error": f"Return to Launch failed: {str(e)}"}


@mcp.tool()
async def kill_motors(ctx: Context) -> dict:
    """
    EMERGENCY ONLY: Immediately cut power to all motors.
    ⚠️  WARNING: This will cause the drone to fall from the sky!
    ⚠️  Only use in critical emergencies (fire, collision imminent, etc.)
    ⚠️  Drone may be damaged from the fall!
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
    logger.warning(f"{LogColors.YELLOW}⚠️  EMERGENCY MOTOR KILL ACTIVATED ⚠️{LogColors.RESET}")

    try:
        log_mavlink_cmd("drone.action.kill")
        await drone.action.kill()
        return {
            "status": "success",
            "message": "EMERGENCY: Motors killed - drone will fall!",
            "warning": "This is an emergency action. Drone may be damaged.",
        }
    except Exception as e:
        logger.error(f"{LogColors.ERROR}❌ TOOL ERROR - Motor kill failed: {e}{LogColors.RESET}")
        return {"status": "failed", "error": f"Motor kill failed: {str(e)}"}


@mcp.tool()
async def hold_position(ctx: Context) -> dict:
    """
    Command the drone to hold its current position while staying in GUIDED mode.
    Useful for pausing during flight to assess situation or wait.
    Waits for connection if not ready.

    Note: This uses goto_location with current position instead of hold() to avoid
          switching to LOITER mode which can cause unwanted altitude changes.

    Args:
        ctx (Context): The context of the request.

    Returns:
        dict: Status message with success or error including current position.
    """
    connector = ctx.request_context.lifespan_context

    # Wait for connection
    if not await ensure_connection(connector):
        return {"status": "failed", "error": "Drone connection timeout. Please wait and try again."}

    drone = connector.drone
    log_tool_call("hold_position")
    logger.info("Commanding drone to hold position (staying in GUIDED mode)")

    try:
        # Get current position
        position = await drone.telemetry.position().__anext__()
        current_lat = position.latitude_deg
        current_lon = position.longitude_deg
        current_alt = position.absolute_altitude_m

        # Send goto_location with current position - keeps drone in GUIDED mode
        # This prevents the altitude drop that occurs when switching to LOITER mode
        log_mavlink_cmd(
            "drone.action.goto_location", lat=f"{current_lat:.6f}", lon=f"{current_lon:.6f}", alt=f"{current_alt:.1f}"
        )
        await drone.action.goto_location(
            current_lat,
            current_lon,
            current_alt,
            float("nan"),  # Maintain current heading
        )

        logger.info(
            f"{LogColors.SUCCESS}✓ Holding position at ({current_lat:.6f}, {current_lon:.6f}) @ {position.relative_altitude_m:.1f}m AGL (relative) / {current_alt:.1f}m MSL{LogColors.RESET}"
        )

        return {
            "status": "success",
            "message": "Drone holding position in GUIDED mode",
            "position": {
                "latitude_deg": current_lat,
                "longitude_deg": current_lon,
                "altitude_m": position.relative_altitude_m,
                "altitude_rel": position.relative_altitude_m,
            },
            "note": "Using GUIDED mode instead of LOITER to prevent altitude drift",
        }
    except Exception as e:
        logger.error(f"{LogColors.ERROR}❌ TOOL ERROR - Hold position failed: {e}{LogColors.RESET}")
        return {"status": "failed", "error": f"Hold position failed: {str(e)}"}


@mcp.tool()
async def go_to_location(
    ctx: Context, latitude_deg: float, longitude_deg: float, absolute_altitude_m: float, yaw_deg: float = float("nan")
) -> dict:
    """
    Fly to an absolute GPS location. Returns immediately - drone flies autonomously.

    AFTER CALLING THIS, YOU MUST:
    1. Call monitor_flight() repeatedly
    2. PRINT the DISPLAY_TO_USER value to the user after each monitor_flight() call
    3. When status is "arrived", call land()
    4. Continue calling monitor_flight() until mission_complete is true

    This gives the user real-time updates on flight progress (distance, speed, ETA).

    Args:
        ctx (Context): The context of the request.
        latitude_deg (float): Target latitude in degrees (-90 to +90).
        longitude_deg (float): Target longitude in degrees (-180 to +180).
        absolute_altitude_m (float): Target altitude in meters MSL.
        yaw_deg (float): Target heading in degrees (optional).

    Returns:
        dict: Navigation started. Next: call monitor_flight() and show updates to user.
    """
    log_tool_call(
        "go_to_location",
        latitude_deg=latitude_deg,
        longitude_deg=longitude_deg,
        absolute_altitude_m=absolute_altitude_m,
    )
    connector = ctx.request_context.lifespan_context

    # Wait for connection
    if not await ensure_connection(connector):
        return {"status": "failed", "error": "Drone connection timeout. Please wait and try again."}

    # Validate coordinates
    if not (-90 <= latitude_deg <= 90):
        return {"status": "failed", "error": f"Invalid latitude: {latitude_deg}. Must be between -90 and 90."}
    if not (-180 <= longitude_deg <= 180):
        return {"status": "failed", "error": f"Invalid longitude: {longitude_deg}. Must be between -180 and 180."}

    drone = connector.drone

    try:
        # Get current position to calculate relative altitude and initial distance
        position = await drone.telemetry.position().__anext__()
        home_alt = position.absolute_altitude_m - position.relative_altitude_m
        relative_alt = absolute_altitude_m - home_alt
        initial_distance = haversine_distance(
            position.latitude_deg, position.longitude_deg, latitude_deg, longitude_deg
        )

        # Get current speed to estimate flight time
        try:
            async for velocity in drone.telemetry.velocity_ned():
                ground_speed = math.sqrt(velocity.north_m_s**2 + velocity.east_m_s**2)
                break
        except:
            ground_speed = 10.0  # Default assumption

        # Estimate flight time (assuming ~10-15 m/s cruise speed for copter)
        estimated_speed = max(ground_speed, 10.0)  # At least 10 m/s for ETA
        eta_seconds = initial_distance / estimated_speed

        logger.info(
            f"Flying to GPS location: {latitude_deg}, {longitude_deg} at {relative_alt:.1f}m AGL / {absolute_altitude_m:.1f}m MSL"
        )
        logger.info(f"Distance to target: {initial_distance:.1f}m, ETA: {eta_seconds:.0f}s")

        log_mavlink_cmd(
            "drone.action.goto_location",
            lat=f"{latitude_deg:.6f}",
            lon=f"{longitude_deg:.6f}",
            alt=f"{absolute_altitude_m:.1f}",
            yaw=f"{yaw_deg:.1f}" if not math.isnan(yaw_deg) else "nan",
        )
        await drone.action.goto_location(latitude_deg, longitude_deg, absolute_altitude_m, yaw_deg)

        # Register pending destination for landing gate safety
        connector.pending_destination = {
            "latitude": latitude_deg,
            "longitude": longitude_deg,
            "altitude_msl": absolute_altitude_m,
            "initial_distance": initial_distance,
            "start_time": asyncio.get_event_loop().time(),
        }

        result = {
            "status": "success",
            "message": "Navigation started. Call monitor_flight() to track progress.",
            "initial_distance_m": round(initial_distance, 1),
            "estimated_flight_time_seconds": round(eta_seconds, 0),
            "target": {
                "latitude": latitude_deg,
                "longitude": longitude_deg,
                "altitude_msl": absolute_altitude_m,
                "altitude_agl": round(relative_alt, 1),
                "yaw": yaw_deg if not math.isnan(yaw_deg) else "maintain current",
            },
            "next_step": "Call monitor_flight() repeatedly until mission_complete is true",
        }
        log_tool_output(result)
        return result

    except Exception as e:
        logger.error(f"Go to location failed: {e}{LogColors.RESET}")
        return {"status": "failed", "error": f"Navigation failed: {str(e)}"}


@mcp.tool()
async def check_arrival(ctx: Context, latitude_deg: float, longitude_deg: float, threshold_m: float = 10.0) -> dict:
    """
    Check if the drone has arrived at a target GPS location (instant, non-blocking).

    IMPORTANT: Call this AFTER go_to_location or reposition commands.
    This returns immediately with current distance - it does NOT wait.

    If status is "in_progress", call this again after a few seconds.
    If status is "arrived", the drone is within threshold of target - safe to land.

    Args:
        ctx (Context): The context of the request.
        latitude_deg (float): Target latitude in degrees (-90 to +90).
        longitude_deg (float): Target longitude in degrees (-180 to +180).
        threshold_m (float): Distance threshold in meters to consider "arrived" (default: 10.0m).

    Returns:
        dict: Status with "arrived" (within threshold) or "in_progress" (still flying).
    """
    log_tool_call("check_arrival", latitude_deg=latitude_deg, longitude_deg=longitude_deg, threshold_m=threshold_m)
    connector = ctx.request_context.lifespan_context

    # Wait for connection
    if not await ensure_connection(connector):
        return {"status": "failed", "error": "Drone connection timeout. Please wait and try again."}

    # Validate coordinates
    if not (-90 <= latitude_deg <= 90):
        return {"status": "failed", "error": f"Invalid latitude: {latitude_deg}. Must be between -90 and 90."}
    if not (-180 <= longitude_deg <= 180):
        return {"status": "failed", "error": f"Invalid longitude: {longitude_deg}. Must be between -180 and 180."}

    drone = connector.drone

    try:
        # Get current position (instant - no waiting)
        async for position in drone.telemetry.position():
            current_lat = position.latitude_deg
            current_lon = position.longitude_deg
            current_alt = position.relative_altitude_m
            break

        # Calculate distance to target
        distance = haversine_distance(current_lat, current_lon, latitude_deg, longitude_deg)

        logger.info(f"📍 Distance to target: {distance:.1f}m (threshold: {threshold_m}m)")

        # Check if arrived
        if distance <= threshold_m:
            logger.info(f"{LogColors.SUCCESS}✅ ARRIVED at target! Distance: {distance:.1f}m{LogColors.RESET}")
            get_flight_logger().log_entry("ARRIVED", f"Distance: {distance:.1f}m")

            result = {
                "status": "arrived",
                "message": f"Drone has arrived at target location! Distance: {distance:.1f}m",
                "distance_m": round(distance, 1),
                "current_position": {"latitude": current_lat, "longitude": current_lon, "altitude_m": current_alt},
                "target": {"latitude": latitude_deg, "longitude": longitude_deg},
            }
            log_tool_output(result)
            return result
        else:
            result = {
                "status": "in_progress",
                "message": f"Still {distance:.1f}m from target. Call check_arrival again in a few seconds.",
                "distance_m": round(distance, 1),
                "current_position": {"latitude": current_lat, "longitude": current_lon, "altitude_m": current_alt},
                "target": {"latitude": latitude_deg, "longitude": longitude_deg},
            }
            log_tool_output(result)
            return result

    except Exception as e:
        logger.error(f"{LogColors.ERROR}❌ Check arrival failed: {e}{LogColors.RESET}")
        return {"status": "failed", "error": f"Check failed: {str(e)}"}


@mcp.tool()
async def monitor_flight(ctx: Context, arrival_threshold_m: float = 20.0, auto_land: bool = True) -> dict:
    """
    Monitor flight progress. YOU MUST CALL THIS IN A LOOP UNTIL mission_complete IS TRUE.

    ⚠️ CRITICAL: If mission_complete is false, you MUST call monitor_flight() again!
    Stopping early leaves the drone flying unattended - DANGEROUS!

    REQUIRED LOOP:
    while True:
        result = monitor_flight()
        print(result["DISPLAY_TO_USER"])  # Show user the progress
        if result["mission_complete"]:
            break  # Only stop when mission_complete is true

    Landing is automatic when the drone arrives (auto_land=True by default).

    Args:
        ctx (Context): The context of the request.
        arrival_threshold_m (float): Distance to consider "arrived" (default: 20m).
        auto_land (bool): Automatically land when arrived (default: True).

    Returns:
        dict: DISPLAY_TO_USER (print this!), status, mission_complete (ONLY stop when true).
    """
    # Fixed 30-second update interval (not configurable to prevent LLM from overriding)
    wait_seconds = 30.0

    log_tool_call("monitor_flight", arrival_threshold_m=arrival_threshold_m, auto_land=auto_land)
    connector = ctx.request_context.lifespan_context

    # Wait for connection
    if not await ensure_connection(connector):
        return {"status": "failed", "error": "Drone connection timeout. Please wait and try again."}

    drone = connector.drone

    try:
        # First, check if drone is on the ground (mission complete)
        async for landed_state in drone.telemetry.landed_state():
            landed_state_str = str(landed_state).split(".")[-1]
            break

        async for in_air in drone.telemetry.in_air():
            is_in_air = in_air
            break

        # Get current position
        async for position in drone.telemetry.position():
            current_lat = position.latitude_deg
            current_lon = position.longitude_deg
            current_alt = position.relative_altitude_m
            break

        # Check if landed (mission complete!)
        if landed_state_str == "ON_GROUND" or (not is_in_air and current_alt < 1.0):
            logger.info(f"{LogColors.SUCCESS}✅ MISSION COMPLETE - Drone has landed!{LogColors.RESET}")
            get_flight_logger().log_entry("LANDED", "Mission complete")

            # Clear all tracking state
            connector.pending_destination = None
            connector.landing_in_progress = False

            result = {
                "DISPLAY_TO_USER": "✅ MISSION COMPLETE - Drone has landed safely!",
                "status": "landed",
                "altitude_m": round(current_alt, 1),
                "action_required": None,
                "mission_complete": True,
            }
            log_tool_output(result)
            return result

        # Check if landing in progress
        if landed_state_str == "LANDING":
            logger.info(f"🛬 Landing in progress... altitude: {current_alt:.1f}m")

            result = {
                "DISPLAY_TO_USER": f"🛬 LANDING | Alt: {current_alt:.1f}m | Descending...",
                "status": "landing",
                "altitude_m": round(current_alt, 1),
                "action_required": "SHOW the DISPLAY_TO_USER to user, then CALL monitor_flight() AGAIN",
                "mission_complete": False,
            }
            log_tool_output(result)
            return result

        # Check if there's a pending destination (still navigating)
        if not connector.pending_destination:
            # Check if we initiated landing (auto_land or manual land call)
            if connector.landing_in_progress:
                logger.info(f"🛬 Landing in progress (flag set)... altitude: {current_alt:.1f}m")
                result = {
                    "DISPLAY_TO_USER": f"🛬 LANDING | Alt: {current_alt:.1f}m | Descending...",
                    "status": "landing",
                    "altitude_m": round(current_alt, 1),
                    "action_required": "call monitor_flight again",
                    "mission_complete": False,
                }
                log_tool_output(result)
                return result

            # No destination and not landing - drone is just hovering
            result = {
                "DISPLAY_TO_USER": f"🚁 HOVERING | Alt: {current_alt:.1f}m | No destination set",
                "status": "hovering",
                "altitude_m": round(current_alt, 1),
                "action_required": "Call go_to_location() to set destination, or land() to land here",
                "mission_complete": False,
            }
            log_tool_output(result)
            return result

        # Get destination from pending navigation
        dest = connector.pending_destination
        dest_lat = dest["latitude"]
        dest_lon = dest["longitude"]
        initial_distance = dest["initial_distance"]
        start_time = dest.get("start_time", asyncio.get_event_loop().time())

        logger.info(f"Monitoring flight for {wait_seconds}s...")

        check_interval = 1.0  # Check every second for arrival detection
        elapsed_in_monitor = 0

        while elapsed_in_monitor < wait_seconds:
            # Get current position
            async for position in drone.telemetry.position():
                current_lat = position.latitude_deg
                current_lon = position.longitude_deg
                current_alt = position.relative_altitude_m
                break

            # Calculate distance to destination
            distance = haversine_distance(current_lat, current_lon, dest_lat, dest_lon)

            # Calculate progress percentage
            if initial_distance > 0:
                progress = ((initial_distance - distance) / initial_distance) * 100
                progress = max(0, min(100, progress))
            else:
                progress = 100 if distance <= arrival_threshold_m else 0

            # Get speed for ETA calculation
            try:
                async for velocity in drone.telemetry.velocity_ned():
                    ground_speed = math.sqrt(velocity.north_m_s**2 + velocity.east_m_s**2)
                    break
            except:
                ground_speed = 0

            # Calculate ETA
            if ground_speed > 0.5:
                eta_seconds = distance / ground_speed
            else:
                eta_seconds = None

            logger.info(
                f"  📍 Distance: {distance:.1f}m ({progress:.0f}%), Speed: {ground_speed:.1f}m/s, Alt: {current_alt:.1f}m"
            )

            # Check if arrived at destination
            if distance <= arrival_threshold_m:
                logger.info(f"{LogColors.SUCCESS}✅ ARRIVED at destination! Distance: {distance:.1f}m{LogColors.RESET}")
                get_flight_logger().log_entry("ARRIVED", f"Distance: {distance:.1f}m")

                # Clear pending destination
                connector.pending_destination = None

                total_flight_time = asyncio.get_event_loop().time() - start_time

                if auto_land:
                    # Automatically initiate landing and WAIT for it to complete
                    logger.info(
                        f"{LogColors.MAVLINK}🛬 Auto-landing initiated - waiting for touchdown{LogColors.RESET}"
                    )
                    get_flight_logger().log_entry("AUTO_LAND", "Landing initiated automatically")
                    connector.landing_in_progress = True
                    await drone.action.land()

                    # Wait for landing to complete (up to 120 seconds)
                    landing_timeout = 120
                    landing_start = asyncio.get_event_loop().time()

                    while (asyncio.get_event_loop().time() - landing_start) < landing_timeout:
                        # Check landed state
                        async for state in drone.telemetry.landed_state():
                            landed_state = state
                            break

                        async for position in drone.telemetry.position():
                            current_alt = position.relative_altitude_m
                            break

                        async for in_air in drone.telemetry.in_air():
                            is_in_air = in_air
                            break

                        landed_state_str = str(landed_state).split(".")[-1]

                        # Only consider landed when PX4 reports ON_GROUND AND not in air AND altitude < 2m
                        if landed_state_str == "ON_GROUND" and not is_in_air and current_alt < 2.0:
                            # Wait 3 more seconds to confirm stable on ground
                            logger.info("🛬 Touchdown detected, confirming stable...")
                            await asyncio.sleep(3)

                            # Re-check to confirm
                            async for state in drone.telemetry.landed_state():
                                landed_state = state
                                break
                            async for in_air in drone.telemetry.in_air():
                                is_in_air = in_air
                                break

                            landed_state_str = str(landed_state).split(".")[-1]
                            if landed_state_str == "ON_GROUND" and not is_in_air:
                                # Confirmed landed!
                                connector.landing_in_progress = False
                                total_flight_time = asyncio.get_event_loop().time() - start_time

                                logger.info(f"{LogColors.SUCCESS}✅ LANDED! Flight complete.{LogColors.RESET}")
                                get_flight_logger().log_entry("LANDED", "Mission complete")

                                result = {
                                    "DISPLAY_TO_USER": f"✅ MISSION COMPLETE | Landed safely | Flight time: {total_flight_time:.0f}s",
                                    "status": "landed",
                                    "flight_time_seconds": round(total_flight_time, 0),
                                    "mission_complete": True,
                                }
                                log_tool_output(result)
                                return result

                        logger.info(
                            f"🛬 Landing... altitude: {current_alt:.1f}m, state: {landed_state_str}, in_air: {is_in_air}"
                        )
                        await asyncio.sleep(2)  # Check every 2 seconds

                    # Timeout - return landing status
                    result = {
                        "DISPLAY_TO_USER": f"⚠️ LANDING TIMEOUT | Alt: {current_alt:.1f}m | Check drone status",
                        "status": "landing_timeout",
                        "altitude_m": round(current_alt, 1),
                        "mission_complete": False,
                    }
                    log_tool_output(result)
                    return result
                else:
                    # Manual landing required (auto_land=False)
                    result = {
                        "DISPLAY_TO_USER": f"✅ ARRIVED | Distance: {distance:.1f}m | Alt: {current_alt:.1f}m | Call land() to land",
                        "status": "arrived",
                        "distance_m": round(distance, 1),
                        "altitude_m": round(current_alt, 1),
                        "mission_complete": False,
                    }
                    log_tool_output(result)
                    return result

            # Wait before next check
            await asyncio.sleep(check_interval)
            elapsed_in_monitor += check_interval

        # Monitoring period ended, still in progress
        total_elapsed = asyncio.get_event_loop().time() - start_time

        # Format ETA nicely
        if eta_seconds:
            if eta_seconds > 60:
                eta_str = f"{int(eta_seconds // 60)}m {int(eta_seconds % 60)}s"
            else:
                eta_str = f"{int(eta_seconds)}s"
        else:
            eta_str = "calculating..."

        result = {
            "DISPLAY_TO_USER": f"🚁 FLYING | Dist: {distance:.0f}m | Alt: {current_alt:.1f}m | Speed: {ground_speed:.1f}m/s | ETA: {eta_str} | {progress:.0f}%",
            "status": "in_progress",
            "distance_m": round(distance, 1),
            "progress_percent": round(progress, 0),
            "action_required": "call monitor_flight again",
            "mission_complete": False,
        }
        log_tool_output(result)
        return result

    except Exception as e:
        logger.error(f"{LogColors.ERROR}❌ Monitor flight failed: {e}{LogColors.RESET}")
        return {"status": "failed", "error": f"Monitoring failed: {str(e)}"}


@mcp.tool()
async def set_max_speed(ctx: Context, speed_m_s: float) -> dict:
    """
    Set the maximum speed limit for the drone.
    Useful for safety or when flying in confined areas.
    Waits for connection if not ready.

    Args:
        ctx (Context): The context of the request.
        speed_m_s (float): Maximum speed in meters per second. Typical range: 1-20 m/s.

    Returns:
        dict: Status message with success or error.
    """
    connector = ctx.request_context.lifespan_context

    # Wait for connection
    if not await ensure_connection(connector):
        return {"status": "failed", "error": "Drone connection timeout. Please wait and try again."}

    # Validate speed
    if speed_m_s <= 0:
        return {"status": "failed", "error": f"Invalid speed: {speed_m_s}. Must be positive."}
    if speed_m_s > 30:
        return {"status": "failed", "error": f"Speed too high: {speed_m_s} m/s. Maximum is 30 m/s for safety."}

    drone = connector.drone
    logger.info(f"Setting maximum speed to {speed_m_s} m/s")

    try:
        log_mavlink_cmd("drone.action.set_maximum_speed", speed_m_s=speed_m_s)
        await drone.action.set_maximum_speed(speed_m_s)
        return {
            "status": "success",
            "message": f"Maximum speed set to {speed_m_s} m/s",
            "speed_kmh": round(speed_m_s * 3.6, 1),  # Also provide in km/h
        }
    except Exception as e:
        logger.error(f"{LogColors.ERROR}❌ TOOL ERROR - Failed to set max speed: {e}{LogColors.RESET}")
        return {"status": "failed", "error": f"Set max speed failed: {str(e)}"}


# ============================================================================
# v1.2.0: ADVANCED NAVIGATION
# ============================================================================
@mcp.tool()
async def set_yaw(ctx: Context, yaw_deg: float, yaw_rate_deg_s: float = 30.0) -> dict:
    """
    Set the drone's heading (yaw) without changing position.
    Rotates the drone to face a specific direction.
    Waits for connection if not ready.

    Args:
        ctx (Context): The context of the request.
        yaw_deg (float): Target heading in degrees (0-360, where 0/360 is North).
        yaw_rate_deg_s (float): Rotation speed in degrees per second (default: 30).

    Returns:
        dict: Status message with target heading.

    Examples:
        - set_yaw(0) - Face North
        - set_yaw(90) - Face East
        - set_yaw(180) - Face South
        - set_yaw(270) - Face West
        - set_yaw(45, 15) - Face Northeast at 15 deg/s rotation speed

    Note:
        - 0° = North, 90° = East, 180° = South, 270° = West
        - Drone will rotate in place to face the specified direction
        - Implementation: Uses goto_location with current position + new yaw
          (MAVSDK doesn't have a dedicated "yaw only" command)
    """
    log_tool_call("set_yaw", yaw_deg=yaw_deg, yaw_rate_deg_s=yaw_rate_deg_s)
    connector = ctx.request_context.lifespan_context

    # Wait for connection
    if not await ensure_connection(connector):
        return {"status": "failed", "error": "Drone connection timeout. Please wait and try again."}

    # Normalize yaw to 0-360
    yaw_normalized = yaw_deg % 360

    # Validate yaw rate
    if yaw_rate_deg_s <= 0:
        return {"status": "failed", "error": f"Invalid yaw rate: {yaw_rate_deg_s}. Must be positive."}

    drone = connector.drone
    logger.info(f"Setting yaw to {yaw_normalized}° at {yaw_rate_deg_s}°/s")

    try:
        # WORKAROUND: MAVSDK doesn't have a "set yaw only" command
        # We use goto_location with current position + new yaw
        # This tells the drone to "fly to where you already are, but face this direction"
        async for position in drone.telemetry.position():
            current_lat = position.latitude_deg
            current_lon = position.longitude_deg
            current_alt = position.absolute_altitude_m
            current_rel_alt = position.relative_altitude_m

            logger.info(
                f"Reading current position: ({current_lat:.6f}, {current_lon:.6f}) @ {current_rel_alt:.1f}m AGL"
            )
            logger.info(f"Commanding: same position, new yaw = {yaw_normalized}°")

            # Use goto_location with current position but new yaw
            # This is the standard MAVSDK workaround for yaw-only control
            log_mavlink_cmd(
                "drone.action.goto_location",
                lat=f"{current_lat:.6f}",
                lon=f"{current_lon:.6f}",
                alt=f"{current_alt:.1f}",
                yaw=f"{yaw_normalized:.1f}",
            )
            await drone.action.goto_location(current_lat, current_lon, current_alt, yaw_normalized)

            # Convert heading to cardinal direction
            directions = ["N", "NE", "E", "SE", "S", "SW", "W", "NW"]
            direction_index = int((yaw_normalized + 22.5) / 45) % 8
            cardinal = directions[direction_index]

            logger.info(f"{LogColors.SUCCESS}✓ Yaw set to {yaw_normalized}° ({cardinal}){LogColors.RESET}")

            return {
                "status": "success",
                "message": f"Rotating to heading {yaw_normalized}°",
                "yaw_degrees": yaw_normalized,
                "cardinal_direction": cardinal,
                "yaw_rate_deg_s": yaw_rate_deg_s,
            }
    except Exception as e:
        logger.error(f"Set yaw failed: {e}{LogColors.RESET}")
        return {"status": "failed", "error": f"Yaw control failed: {str(e)}"}


@mcp.tool()
async def reposition(ctx: Context, latitude_deg: float, longitude_deg: float, altitude_m: float) -> dict:
    """
    Move to a new location and loiter (hover) there.
    Combination of goto_location and hold_position.
    Waits for connection if not ready.

    Args:
        ctx (Context): The context of the request.
        latitude_deg (float): Target latitude in degrees.
        longitude_deg (float): Target longitude in degrees.
        altitude_m (float): Target altitude above sea level in meters.

    Returns:
        dict: Status message with target position.

    Examples:
        - reposition(33.645, -117.842, 50) - Move to coordinates and hover at 50m
        - reposition(33.646, -117.843, 100) - Reposition to new survey point

    Use Cases:
        - Adjusting survey position
        - Moving to better vantage point
        - Relocating between tasks
    """
    connector = ctx.request_context.lifespan_context

    # Wait for connection
    if not await ensure_connection(connector):
        return {"status": "failed", "error": "Drone connection timeout. Please wait and try again."}

    # Validate coordinates
    if not (-90 <= latitude_deg <= 90):
        return {"status": "failed", "error": f"Invalid latitude: {latitude_deg}. Must be between -90 and 90."}
    if not (-180 <= longitude_deg <= 180):
        return {"status": "failed", "error": f"Invalid longitude: {longitude_deg}. Must be between -180 and 180."}

    drone = connector.drone

    try:
        # Get current position to calculate relative altitude for display
        position = await drone.telemetry.position().__anext__()
        home_alt = position.absolute_altitude_m - position.relative_altitude_m
        relative_alt = altitude_m - home_alt

        logger.info(
            f"Repositioning to ({latitude_deg}, {longitude_deg}) at {relative_alt:.1f}m AGL (relative) / {altitude_m:.1f}m MSL"
        )

        # Move to new location (will loiter automatically in GUIDED mode)
        log_mavlink_cmd(
            "drone.action.goto_location",
            lat=f"{latitude_deg:.6f}",
            lon=f"{longitude_deg:.6f}",
            alt=f"{altitude_m:.1f}",
            yaw="nan",
        )
        await drone.action.goto_location(
            latitude_deg,
            longitude_deg,
            altitude_m,
            float("nan"),  # Maintain current heading
        )

        return {
            "status": "success",
            "message": "Repositioning to new location",
            "target": {"latitude": latitude_deg, "longitude": longitude_deg, "altitude_msl": altitude_m},
            "note": "Drone will fly to location and loiter (hover) there",
        }
    except Exception as e:
        logger.error(f"Reposition failed: {e}{LogColors.RESET}")
        return {"status": "failed", "error": f"Reposition failed: {str(e)}"}
