"""Telemetry read-out MCP tools (MavSDK ``telemetry`` plugin)."""

import asyncio
import math

from mcp.server.fastmcp import Context

from droneserver.app import mcp
from droneserver.mavlink.connection import ensure_connection
from droneserver.telemetry.flight_log import LogColors, log_tool_call, log_tool_output, logger


# Get Position
@mcp.tool()
async def get_position(ctx: Context) -> dict:
    """
    Get the position of the drone in latitude/longitude degrees and altitude in meters.
    The drone must be connected and have a global position estimate.
    This tool will wait up to 30 seconds for the drone to be ready.

    Args:
        ctx (Context): The context of the request.

    Returns:
        dict: A dict with the position or error status.
    """
    log_tool_call("get_position")
    connector = ctx.request_context.lifespan_context

    # Wait for connection
    if not await ensure_connection(connector):
        return {"status": "failed", "error": "Drone connection timeout. Please wait and try again."}

    drone = connector.drone
    logger.info("Fetching drone position")

    try:
        async for position in drone.telemetry.position():
            result = {
                "status": "success",
                "position": {
                    "latitude_deg": position.latitude_deg,
                    "longitude_deg": position.longitude_deg,
                    "absolute_altitude_m": position.absolute_altitude_m,
                    "relative_altitude_m": position.relative_altitude_m,
                },
            }
            log_tool_output(result)
            return result
    except Exception as e:
        logger.error(f"{LogColors.ERROR}❌ TOOL ERROR - Failed to retrieve position: {e}{LogColors.RESET}")
        return {"status": "failed", "error": str(e)}


@mcp.tool()
async def print_status_text(ctx: Context) -> dict:
    """Print and return status text from the drone. Waits for connection if not ready."""
    connector = ctx.request_context.lifespan_context

    # Wait for connection
    if not await ensure_connection(connector):
        return {"status": "failed", "error": "Drone connection timeout. Please wait and try again."}

    drone = connector.drone
    try:
        async for status_text in drone.telemetry.status_text():
            logger.info(f"Status: {status_text.type}: {status_text.text}")
            return {"status": "success", "type": status_text.type, "text": status_text.text}
    except asyncio.CancelledError:
        return {"status": "failed", "error": "Failed to retrieve status text"}


@mcp.tool()
async def get_imu(ctx: Context, n: int = 1) -> dict:
    """Fetch the first n IMU data points from the drone. Waits for connection if not ready.

    Args:
        ctx (Context): The context of the request.
        n (int): The number of IMU data points to fetch. Default is 1.

    Returns:
        dict: A dict with status and list of IMU data points.
    """
    connector = ctx.request_context.lifespan_context

    # Wait for connection
    if not await ensure_connection(connector):
        return {"status": "failed", "error": "Drone connection timeout. Please wait and try again."}

    drone = connector.drone
    telemetry = drone.telemetry

    # Set the rate at which IMU data is updated (in Hz)
    await telemetry.set_rate_imu(200.0)

    imu_data = []
    count = 0

    async for imu in telemetry.imu():
        imu_data.append(
            {
                "timestamp_us": imu.timestamp_us,
                "acceleration": {
                    "x": imu.acceleration_frd.forward_m_s2,
                    "y": imu.acceleration_frd.right_m_s2,
                    "z": imu.acceleration_frd.down_m_s2,
                },
                "angular_velocity": {
                    "x": imu.angular_velocity_frd.forward_rad_s,
                    "y": imu.angular_velocity_frd.right_rad_s,
                    "z": imu.angular_velocity_frd.down_rad_s,
                },
                "magnetic_field": {
                    "x": imu.magnetic_field_frd.forward_gauss,
                    "y": imu.magnetic_field_frd.right_gauss,
                    "z": imu.magnetic_field_frd.down_gauss,
                },
                "temperature_degc": imu.temperature_degc,
            }
        )
        count += 1
        if count >= n:
            break

    return {"status": "success", "imu_data": imu_data, "count": len(imu_data)}


@mcp.tool()
async def get_flight_mode(ctx: Context) -> dict:
    """
    Get the current flight mode of the drone. Waits for connection if not ready.

    Args:
        ctx (Context): The context of the request.

    Returns:
        dict: The current flight mode of the drone or error status.
    """
    connector = ctx.request_context.lifespan_context

    # Wait for connection
    if not await ensure_connection(connector):
        return {"status": "failed", "error": "Drone connection timeout. Please wait and try again."}

    drone = connector.drone
    try:
        flight_mode = await drone.telemetry.flight_mode().__anext__()
        logger.info(f"FlightMode: {flight_mode}")
        return {"status": "success", "flight_mode": str(flight_mode)}
    except StopAsyncIteration:
        logger.error(f"{LogColors.ERROR}❌ TOOL ERROR - Failed to retrieve flight mode{LogColors.RESET}")
        return {"status": "failed", "error": "Failed to retrieve flight mode"}


@mcp.tool()
async def get_battery(ctx: Context) -> dict:
    """
    Get the current battery status including voltage and remaining percentage.
    Critical for monitoring flight time and knowing when to land.
    Waits for connection if not ready.

    Args:
        ctx (Context): The context of the request.

    Returns:
        dict: Battery voltage (V), remaining percentage (%), and status.
    """
    log_tool_call("get_battery")
    connector = ctx.request_context.lifespan_context

    # Wait for connection
    if not await ensure_connection(connector):
        return {"status": "failed", "error": "Drone connection timeout. Please wait and try again."}

    drone = connector.drone
    logger.info("Fetching battery status")

    try:
        async for battery in drone.telemetry.battery():
            voltage = battery.voltage_v
            percent_raw = battery.remaining_percent

            battery_data = {
                "voltage_v": round(voltage, 2),
                "remaining_percent": round(percent_raw * 100, 1),  # Convert to percentage
            }

            # Handle case where percentage is unavailable/uncalibrated (0% with good voltage)
            if percent_raw == 0.0 and voltage > 10.0:
                battery_data["note"] = "⚠️  Battery percentage unavailable - using voltage estimate"
                battery_data["calibration_status"] = "Uncalibrated or not supported by autopilot"

                # Rough LiPo estimate: 4.2V = 100%, 3.7V = 50%, 3.5V = 0% per cell
                # Assume 4S LiPo (most common for drones): 16.8V full, 14.8V nominal, 14.0V empty
                if voltage >= 16.0:
                    estimated_percent = 90
                elif voltage >= 15.2:
                    estimated_percent = 75
                elif voltage >= 14.8:
                    estimated_percent = 50
                elif voltage >= 14.0:
                    estimated_percent = 25
                else:
                    estimated_percent = 10

                battery_data["estimated_percent"] = estimated_percent
                battery_data["hint"] = "Set battery capacity parameter (BATT_CAPACITY) for accurate readings"

            # Add warning if battery is low (use estimated if percentage unavailable)
            effective_percent = percent_raw if percent_raw > 0 else (battery_data.get("estimated_percent", 100) / 100)

            if effective_percent < 0.20:
                battery_data["warning"] = "⚠️  LOW BATTERY - Land soon!"
            elif effective_percent < 0.30:
                battery_data["warning"] = "Battery getting low - consider landing"

            logger.info(
                f"{LogColors.STATUS}Battery: {battery_data['voltage_v']}V, {battery_data['remaining_percent']}% "
                f"{'(estimated: ' + str(battery_data.get('estimated_percent', '')) + '%)' if 'estimated_percent' in battery_data else ''}{LogColors.RESET}"
            )
            return {"status": "success", "battery": battery_data}
    except Exception as e:
        logger.error(f"{LogColors.ERROR}❌ TOOL ERROR - Failed to get battery status: {e}{LogColors.RESET}")
        return {"status": "failed", "error": f"Battery read failed: {str(e)}"}


@mcp.tool()
async def get_health(ctx: Context) -> dict:
    """
    Get comprehensive system health status for pre-flight checks.
    Returns status of GPS, accelerometer, gyro, magnetometer, and more.
    Waits for connection if not ready.

    Args:
        ctx (Context): The context of the request.

    Returns:
        dict: Comprehensive health status of all drone subsystems.
    """
    log_tool_call("get_health")
    connector = ctx.request_context.lifespan_context

    # Wait for connection
    if not await ensure_connection(connector):
        return {"status": "failed", "error": "Drone connection timeout. Please wait and try again."}

    drone = connector.drone
    logger.info("Fetching system health")

    try:
        async for health in drone.telemetry.health():
            health_data = {
                "is_gyrometer_calibrated": health.is_gyrometer_calibration_ok,
                "is_accelerometer_calibrated": health.is_accelerometer_calibration_ok,
                "is_magnetometer_calibrated": health.is_magnetometer_calibration_ok,
                "is_local_position_ok": health.is_local_position_ok,
                "is_global_position_ok": health.is_global_position_ok,
                "is_home_position_ok": health.is_home_position_ok,
                "is_armable": health.is_armable,
            }

            # Add overall health assessment
            all_ok = all(health_data.values())
            health_data["overall_status"] = "HEALTHY" if all_ok else "ISSUES DETECTED"

            # Add warnings for critical issues
            warnings = []
            if not health.is_global_position_ok:
                warnings.append("⚠️  No GPS lock - cannot fly safely!")
            if not health.is_armable:
                warnings.append("⚠️  Drone is not armable - check for errors")
            if not health.is_gyrometer_calibration_ok:
                warnings.append("Gyroscope needs calibration")
            if not health.is_accelerometer_calibration_ok:
                warnings.append("Accelerometer needs calibration")
            if not health.is_magnetometer_calibration_ok:
                warnings.append("Magnetometer/compass needs calibration")

            if warnings:
                health_data["warnings"] = warnings

            logger.info(f"{LogColors.STATUS}System health: {health_data['overall_status']}{LogColors.RESET}")
            return {"status": "success", "health": health_data}
    except Exception as e:
        logger.error(f"{LogColors.ERROR}❌ TOOL ERROR - Failed to get health status: {e}{LogColors.RESET}")
        return {"status": "failed", "error": f"Health check failed: {str(e)}"}


@mcp.tool()
async def get_home_position(ctx: Context) -> dict:
    """
    Get the home position where Return to Launch (RTL) will return to.
    This is typically set at the launch location when the drone first arms.
    Waits for connection if not ready.

    Args:
        ctx (Context): The context of the request.

    Returns:
        dict: Home position coordinates and altitude.
    """
    connector = ctx.request_context.lifespan_context

    # Wait for connection
    if not await ensure_connection(connector):
        return {"status": "failed", "error": "Drone connection timeout. Please wait and try again."}

    drone = connector.drone
    logger.info("Fetching home position")

    try:
        async for home in drone.telemetry.home():
            home_data = {
                "latitude_deg": home.latitude_deg,
                "longitude_deg": home.longitude_deg,
                "absolute_altitude_m": home.absolute_altitude_m,
            }
            logger.info(
                f"Home position: {home_data['latitude_deg']}, {home_data['longitude_deg']} at {home_data['absolute_altitude_m']}m"
            )
            return {"status": "success", "home": home_data}
    except Exception as e:
        logger.error(f"{LogColors.ERROR}❌ TOOL ERROR - Failed to get home position: {e}{LogColors.RESET}")
        return {"status": "failed", "error": f"Home position read failed: {str(e)}"}


@mcp.tool()
async def get_speed(ctx: Context) -> dict:
    """
    Get the current ground speed (velocity over ground).
    Returns velocity in North, East, Down directions.
    Waits for connection if not ready.

    Args:
        ctx (Context): The context of the request.

    Returns:
        dict: Current velocity in NED frame and total ground speed.
    """
    connector = ctx.request_context.lifespan_context

    # Wait for connection
    if not await ensure_connection(connector):
        return {"status": "failed", "error": "Drone connection timeout. Please wait and try again."}

    drone = connector.drone
    logger.info("Fetching ground speed")

    try:
        async for velocity in drone.telemetry.velocity_ned():
            # Calculate total ground speed (horizontal speed only)
            ground_speed_m_s = math.sqrt(velocity.north_m_s**2 + velocity.east_m_s**2)

            speed_data = {
                "north_m_s": velocity.north_m_s,
                "east_m_s": velocity.east_m_s,
                "down_m_s": velocity.down_m_s,
                "ground_speed_m_s": round(ground_speed_m_s, 2),
                "ground_speed_kmh": round(ground_speed_m_s * 3.6, 2),
            }

            logger.info(f"Ground speed: {speed_data['ground_speed_m_s']} m/s ({speed_data['ground_speed_kmh']} km/h)")
            return {"status": "success", "velocity": speed_data}
    except Exception as e:
        logger.error(f"{LogColors.ERROR}❌ TOOL ERROR - Failed to get speed: {e}{LogColors.RESET}")
        return {"status": "failed", "error": f"Speed read failed: {str(e)}"}


@mcp.tool()
async def get_attitude(ctx: Context) -> dict:
    """
    Get the current attitude (orientation) of the drone.
    Returns roll, pitch, and yaw angles in degrees.
    Waits for connection if not ready.

    Args:
        ctx (Context): The context of the request.

    Returns:
        dict: Roll, pitch, yaw angles in degrees.
    """
    connector = ctx.request_context.lifespan_context

    # Wait for connection
    if not await ensure_connection(connector):
        return {"status": "failed", "error": "Drone connection timeout. Please wait and try again."}

    drone = connector.drone
    logger.info("Fetching attitude")

    try:
        async for attitude in drone.telemetry.attitude_euler():
            attitude_data = {
                "roll_deg": round(attitude.roll_deg, 2),
                "pitch_deg": round(attitude.pitch_deg, 2),
                "yaw_deg": round(attitude.yaw_deg, 2),
            }

            logger.info(
                f"Attitude: roll={attitude_data['roll_deg']}°, pitch={attitude_data['pitch_deg']}°, yaw={attitude_data['yaw_deg']}°"
            )
            return {"status": "success", "attitude": attitude_data}
    except Exception as e:
        logger.error(f"{LogColors.ERROR}❌ TOOL ERROR - Failed to get attitude: {e}{LogColors.RESET}")
        return {"status": "failed", "error": f"Attitude read failed: {str(e)}"}


@mcp.tool()
async def get_gps_info(ctx: Context) -> dict:
    """
    Get detailed GPS information including number of satellites and fix type.
    Important for assessing navigation quality.
    Waits for connection if not ready.

    Args:
        ctx (Context): The context of the request.

    Returns:
        dict: GPS satellite count, fix type, and quality metrics.
    """
    connector = ctx.request_context.lifespan_context

    # Wait for connection
    if not await ensure_connection(connector):
        return {"status": "failed", "error": "Drone connection timeout. Please wait and try again."}

    drone = connector.drone
    logger.info("Fetching GPS info")

    try:
        async for gps_info in drone.telemetry.gps_info():
            gps_data = {
                "num_satellites": gps_info.num_satellites,
                "fix_type": str(gps_info.fix_type),
            }

            # Add quality assessment
            if gps_info.num_satellites >= 10:
                gps_data["quality"] = "Excellent"
            elif gps_info.num_satellites >= 6:
                gps_data["quality"] = "Good"
            elif gps_info.num_satellites >= 4:
                gps_data["quality"] = "Marginal"
            else:
                gps_data["quality"] = "Poor"
                gps_data["warning"] = "⚠️  Insufficient satellites for reliable navigation!"

            logger.info(f"GPS: {gps_data['num_satellites']} satellites, {gps_data['fix_type']}, {gps_data['quality']}")
            return {"status": "success", "gps": gps_data}
    except Exception as e:
        logger.error(f"{LogColors.ERROR}❌ TOOL ERROR - Failed to get GPS info: {e}{LogColors.RESET}")
        return {"status": "failed", "error": f"GPS info read failed: {str(e)}"}


@mcp.tool()
async def get_in_air(ctx: Context) -> dict:
    """
    Check if the drone is currently in the air (flying) or on the ground.
    Waits for connection if not ready.

    Args:
        ctx (Context): The context of the request.

    Returns:
        dict: Boolean indicating if drone is airborne.
    """
    connector = ctx.request_context.lifespan_context

    # Wait for connection
    if not await ensure_connection(connector):
        return {"status": "failed", "error": "Drone connection timeout. Please wait and try again."}

    drone = connector.drone
    logger.info("Checking if drone is in air")

    try:
        async for in_air in drone.telemetry.in_air():
            status_text = "IN AIR (flying)" if in_air else "ON GROUND"
            logger.info(f"{LogColors.STATUS}Drone status: {status_text}{LogColors.RESET}")
            return {"status": "success", "in_air": in_air, "status_text": status_text}
    except Exception as e:
        logger.error(f"{LogColors.ERROR}❌ TOOL ERROR - Failed to check in_air status: {e}{LogColors.RESET}")
        return {"status": "failed", "error": f"In-air check failed: {str(e)}"}


@mcp.tool()
async def get_armed(ctx: Context) -> dict:
    """
    Check if the drone is currently armed (motors can spin).
    Waits for connection if not ready.

    Args:
        ctx (Context): The context of the request.

    Returns:
        dict: Boolean indicating if drone is armed.
    """
    connector = ctx.request_context.lifespan_context

    # Wait for connection
    if not await ensure_connection(connector):
        return {"status": "failed", "error": "Drone connection timeout. Please wait and try again."}

    drone = connector.drone
    logger.info("Checking if drone is armed")

    try:
        async for armed in drone.telemetry.armed():
            status_text = "ARMED (motors ready)" if armed else "DISARMED (motors off)"
            logger.info(f"{LogColors.STATUS}Drone status: {status_text}{LogColors.RESET}")
            return {"status": "success", "armed": armed, "status_text": status_text}
    except Exception as e:
        logger.error(f"{LogColors.ERROR}❌ TOOL ERROR - Failed to check armed status: {e}{LogColors.RESET}")
        return {"status": "failed", "error": f"Armed check failed: {str(e)}"}


@mcp.tool()
async def get_health_all_ok(ctx: Context) -> dict:
    """
    Quick health check - returns True if ALL systems are OK for flight.
    This is a simplified check that returns a single boolean rather than
    detailed health status per subsystem.

    Use this for quick pre-flight go/no-go decisions.
    For detailed health breakdown, use get_health() instead.

    Args:
        ctx (Context): The context of the request.

    Returns:
        dict: Boolean indicating if all systems pass health checks.
    """
    log_tool_call("get_health_all_ok")
    connector = ctx.request_context.lifespan_context

    # Wait for connection
    if not await ensure_connection(connector):
        return {"status": "failed", "error": "Drone connection timeout. Please wait and try again."}

    drone = connector.drone
    logger.info("Checking if all systems are healthy")

    try:
        async for health_all_ok in drone.telemetry.health_all_ok():
            status_text = "ALL SYSTEMS GO ✓" if health_all_ok else "SYSTEMS NOT READY ✗"
            logger.info(f"{LogColors.STATUS}Health check: {status_text}{LogColors.RESET}")

            result = {
                "status": "success",
                "health_all_ok": health_all_ok,
                "status_text": status_text,
                "recommendation": "Ready for flight"
                if health_all_ok
                else "Run get_health() for details on what's not ready",
            }
            log_tool_output(result)
            return result
    except Exception as e:
        logger.error(f"{LogColors.ERROR}❌ TOOL ERROR - Failed to check health_all_ok: {e}{LogColors.RESET}")
        return {"status": "failed", "error": f"Health check failed: {str(e)}"}


@mcp.tool()
async def get_landed_state(ctx: Context) -> dict:
    """
    Get detailed landed state of the drone.
    Returns one of: ON_GROUND, TAKING_OFF, IN_AIR, LANDING, or UNKNOWN.

    More detailed than get_in_air() which only tells you if drone is airborne.
    This tells you the transition states (taking off, landing) as well.

    Args:
        ctx (Context): The context of the request.

    Returns:
        dict: Landed state enum value and descriptive text.
    """
    log_tool_call("get_landed_state")
    connector = ctx.request_context.lifespan_context

    # Wait for connection
    if not await ensure_connection(connector):
        return {"status": "failed", "error": "Drone connection timeout. Please wait and try again."}

    drone = connector.drone
    logger.info("Checking landed state")

    try:
        async for landed_state in drone.telemetry.landed_state():
            state_str = str(landed_state)

            # Map enum to human-readable description
            state_descriptions = {
                "UNKNOWN": "State cannot be determined",
                "ON_GROUND": "Drone is on the ground, not moving",
                "IN_AIR": "Drone is flying/airborne",
                "TAKING_OFF": "Drone is in the process of taking off",
                "LANDING": "Drone is in the process of landing",
            }

            # Extract enum name from string representation
            state_name = state_str.split(".")[-1] if "." in state_str else state_str
            description = state_descriptions.get(state_name, state_str)

            logger.info(f"{LogColors.STATUS}Landed state: {state_name} - {description}{LogColors.RESET}")

            result = {
                "status": "success",
                "landed_state": state_name,
                "description": description,
                "is_on_ground": state_name == "ON_GROUND",
                "is_in_air": state_name == "IN_AIR",
                "is_transitioning": state_name in ["TAKING_OFF", "LANDING"],
            }
            log_tool_output(result)
            return result
    except Exception as e:
        logger.error(f"{LogColors.ERROR}❌ TOOL ERROR - Failed to get landed state: {e}{LogColors.RESET}")
        return {"status": "failed", "error": f"Landed state read failed: {str(e)}"}


@mcp.tool()
async def get_rc_status(ctx: Context) -> dict:
    """
    Get RC (Remote Control) controller connection status and signal strength.
    Shows whether an RC transmitter is connected and the signal quality.

    Useful for monitoring RC link health during manual/assisted flight.

    Args:
        ctx (Context): The context of the request.

    Returns:
        dict: RC availability status and signal strength percentage.
    """
    log_tool_call("get_rc_status")
    connector = ctx.request_context.lifespan_context

    # Wait for connection
    if not await ensure_connection(connector):
        return {"status": "failed", "error": "Drone connection timeout. Please wait and try again."}

    drone = connector.drone
    logger.info("Checking RC controller status")

    try:
        async for rc_status in drone.telemetry.rc_status():
            is_available = rc_status.is_available
            signal_strength = rc_status.signal_strength_percent

            # Determine signal quality
            if not is_available:
                quality = "NO RC CONNECTED"
            elif signal_strength >= 80:
                quality = "Excellent"
            elif signal_strength >= 60:
                quality = "Good"
            elif signal_strength >= 40:
                quality = "Fair"
            elif signal_strength >= 20:
                quality = "Poor"
            else:
                quality = "Critical - Link may be lost"

            status_text = (
                f"RC {'Available' if is_available else 'Not Available'} - Signal: {signal_strength:.0f}% ({quality})"
            )
            logger.info(f"{LogColors.STATUS}RC Status: {status_text}{LogColors.RESET}")

            result = {
                "status": "success",
                "rc_available": is_available,
                "signal_strength_percent": round(signal_strength, 1) if is_available else 0,
                "signal_quality": quality,
                "status_text": status_text,
            }
            log_tool_output(result)
            return result
    except Exception as e:
        logger.error(f"{LogColors.ERROR}❌ TOOL ERROR - Failed to get RC status: {e}{LogColors.RESET}")
        return {"status": "failed", "error": f"RC status read failed: {str(e)}"}


@mcp.tool()
async def get_heading(ctx: Context) -> dict:
    """
    Get the current compass heading of the drone in degrees.
    Returns heading from 0 to 360 degrees where:
    - 0° = North
    - 90° = East
    - 180° = South
    - 270° = West

    Args:
        ctx (Context): The context of the request.

    Returns:
        dict: Heading in degrees and cardinal direction.
    """
    log_tool_call("get_heading")
    connector = ctx.request_context.lifespan_context

    # Wait for connection
    if not await ensure_connection(connector):
        return {"status": "failed", "error": "Drone connection timeout. Please wait and try again."}

    drone = connector.drone
    logger.info("Getting compass heading")

    try:
        async for heading in drone.telemetry.heading():
            heading_deg = heading.heading_deg

            # Normalize heading to 0-360
            heading_normalized = heading_deg % 360
            if heading_normalized < 0:
                heading_normalized += 360

            # Determine cardinal direction
            if heading_normalized >= 337.5 or heading_normalized < 22.5:
                cardinal = "N"
                direction = "North"
            elif heading_normalized < 67.5:
                cardinal = "NE"
                direction = "Northeast"
            elif heading_normalized < 112.5:
                cardinal = "E"
                direction = "East"
            elif heading_normalized < 157.5:
                cardinal = "SE"
                direction = "Southeast"
            elif heading_normalized < 202.5:
                cardinal = "S"
                direction = "South"
            elif heading_normalized < 247.5:
                cardinal = "SW"
                direction = "Southwest"
            elif heading_normalized < 292.5:
                cardinal = "W"
                direction = "West"
            else:
                cardinal = "NW"
                direction = "Northwest"

            logger.info(f"{LogColors.STATUS}Heading: {heading_normalized:.1f}° ({direction}){LogColors.RESET}")

            result = {
                "status": "success",
                "heading_deg": round(heading_normalized, 1),
                "cardinal_direction": cardinal,
                "direction_name": direction,
            }
            log_tool_output(result)
            return result
    except Exception as e:
        logger.error(f"{LogColors.ERROR}❌ TOOL ERROR - Failed to get heading: {e}{LogColors.RESET}")
        return {"status": "failed", "error": f"Heading read failed: {str(e)}"}


@mcp.tool()
async def get_odometry(ctx: Context) -> dict:
    """
    Get combined odometry data: position, velocity, and orientation.
    Returns all motion-related telemetry in a single call.

    This is more efficient than calling get_position, get_velocity,
    and get_attitude separately when you need all three.

    Position is in NED (North-East-Down) frame relative to home.
    Velocity is in body frame (forward, right, down).
    Orientation is given as quaternion and can be converted to Euler angles.

    Args:
        ctx (Context): The context of the request.

    Returns:
        dict: Combined position, velocity, and orientation data.
    """
    log_tool_call("get_odometry")
    connector = ctx.request_context.lifespan_context

    # Wait for connection
    if not await ensure_connection(connector):
        return {"status": "failed", "error": "Drone connection timeout. Please wait and try again."}

    drone = connector.drone
    logger.info("Getting odometry data")

    try:
        async for odometry in drone.telemetry.odometry():
            # Extract position (NED frame)
            position = {
                "north_m": round(odometry.position_body.x_m, 3),
                "east_m": round(odometry.position_body.y_m, 3),
                "down_m": round(odometry.position_body.z_m, 3),
            }

            # Extract velocity (body frame)
            velocity = {
                "forward_m_s": round(odometry.velocity_body.x_m_s, 3),
                "right_m_s": round(odometry.velocity_body.y_m_s, 3),
                "down_m_s": round(odometry.velocity_body.z_m_s, 3),
            }

            # Extract orientation quaternion
            quaternion = {
                "w": round(odometry.q.w, 4),
                "x": round(odometry.q.x, 4),
                "y": round(odometry.q.y, 4),
                "z": round(odometry.q.z, 4),
            }

            # Convert quaternion to Euler angles for easier interpretation
            # Using standard aerospace convention (roll, pitch, yaw)
            w, x, y, z = odometry.q.w, odometry.q.x, odometry.q.y, odometry.q.z

            # Roll (rotation around x-axis)
            sinr_cosp = 2 * (w * x + y * z)
            cosr_cosp = 1 - 2 * (x * x + y * y)
            roll_rad = math.atan2(sinr_cosp, cosr_cosp)

            # Pitch (rotation around y-axis)
            sinp = 2 * (w * y - z * x)
            if abs(sinp) >= 1:
                pitch_rad = math.copysign(math.pi / 2, sinp)  # Use 90 degrees if out of range
            else:
                pitch_rad = math.asin(sinp)

            # Yaw (rotation around z-axis)
            siny_cosp = 2 * (w * z + x * y)
            cosy_cosp = 1 - 2 * (y * y + z * z)
            yaw_rad = math.atan2(siny_cosp, cosy_cosp)

            euler_angles = {
                "roll_deg": round(math.degrees(roll_rad), 2),
                "pitch_deg": round(math.degrees(pitch_rad), 2),
                "yaw_deg": round(math.degrees(yaw_rad), 2),
            }

            # Calculate derived values
            ground_speed = math.sqrt(velocity["forward_m_s"] ** 2 + velocity["right_m_s"] ** 2)
            total_speed = math.sqrt(ground_speed**2 + velocity["down_m_s"] ** 2)

            logger.info(
                f"{LogColors.STATUS}Odometry: Pos({position['north_m']:.1f}N, {position['east_m']:.1f}E, {-position['down_m']:.1f}Up) "
                f"Vel({ground_speed:.1f}m/s ground) Yaw({euler_angles['yaw_deg']:.0f}°){LogColors.RESET}"
            )

            result = {
                "status": "success",
                "frame_id": str(odometry.frame_id),
                "child_frame_id": str(odometry.child_frame_id),
                "position_ned_m": position,
                "velocity_body_m_s": velocity,
                "orientation_quaternion": quaternion,
                "euler_angles_deg": euler_angles,
                "ground_speed_m_s": round(ground_speed, 2),
                "total_speed_m_s": round(total_speed, 2),
                "altitude_m": round(-position["down_m"], 2),  # Convert down to up (altitude)
            }
            log_tool_output(result)
            return result
    except Exception as e:
        logger.error(f"{LogColors.ERROR}❌ TOOL ERROR - Failed to get odometry: {e}{LogColors.RESET}")
        return {"status": "failed", "error": f"Odometry read failed: {str(e)}"}
