"""Telemetry read-out MCP tools (MavSDK ``telemetry`` plugin).

v2 rewrite of the v1 module: every getter now reads its stream with a bounded
timeout (v1 could hang forever, or fall off the end returning null, when a
topic never emitted - e.g. ``odometry`` on ArduPilot). Result schemas are
unchanged from v1. Two tools complete the plugin: ``get_telemetry_extended``
(niche topics, generic schema) and ``set_telemetry_rate``.
"""

import asyncio
import math

from mcp.server.fastmcp import Context

from droneserver.app import mcp
from droneserver.convert import to_jsonable
from droneserver.telemetry.flight_log import LogColors, log_tool_call, log_tool_output, logger
from droneserver.telemetry.home import read_home
from droneserver.tools._common import CONN_ERROR, first_stream_item, get_drone

READ_TIMEOUT_S = 10.0


def _no_data(topic: str) -> dict:
    return {
        "status": "failed",
        "error": f"No {topic} telemetry received within {READ_TIMEOUT_S:.0f}s "
        "(the autopilot may not publish this topic - see the coverage matrix firmware notes)",
    }


def _read_error(topic: str, e: Exception) -> dict:
    logger.error(f"{LogColors.ERROR}❌ TOOL ERROR - Failed to get {topic}: {e}{LogColors.RESET}")
    return {"status": "failed", "error": f"{topic} read failed: {e}"}


@mcp.tool()
async def get_position(ctx: Context) -> dict:
    """
    Get the position of the drone in latitude/longitude degrees and altitude in meters.
    The drone must be connected and have a global position estimate.

    Returns:
        dict: A dict with the position or error status.
    """
    log_tool_call("get_position")
    drone = await get_drone(ctx)
    if drone is None:
        return dict(CONN_ERROR)
    try:
        position = await first_stream_item(drone.telemetry.position(), READ_TIMEOUT_S)
    except TimeoutError:
        return _no_data("position")
    except Exception as e:
        return _read_error("position", e)
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


@mcp.tool()
async def print_status_text(ctx: Context) -> dict:
    """Return the next status text message from the drone (waits up to 10s)."""
    log_tool_call("print_status_text")
    drone = await get_drone(ctx)
    if drone is None:
        return dict(CONN_ERROR)
    try:
        status_text = await first_stream_item(drone.telemetry.status_text(), READ_TIMEOUT_S)
    except TimeoutError:
        return _no_data("status_text")
    except Exception as e:
        return _read_error("status_text", e)
    logger.info(f"Status: {status_text.type}: {status_text.text}")
    return {"status": "success", "type": str(status_text.type), "text": status_text.text}


@mcp.tool()
async def get_imu(ctx: Context, n: int = 1) -> dict:
    """Fetch the first n IMU data points from the drone.

    Args:
        ctx (Context): The context of the request.
        n (int): The number of IMU data points to fetch (1-100). Default 1.

    Returns:
        dict: A dict with status and list of IMU data points.
    """
    log_tool_call("get_imu", n=n)
    if not 1 <= int(n) <= 100:
        return {"status": "failed", "error": f"n must be between 1 and 100, got {n}"}
    drone = await get_drone(ctx)
    if drone is None:
        return dict(CONN_ERROR)
    telemetry = drone.telemetry
    try:
        await telemetry.set_rate_imu(200.0)

        async def collect():
            imu_data = []
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
                if len(imu_data) >= int(n):
                    return imu_data
            return imu_data

        imu_data = await asyncio.wait_for(collect(), timeout=max(READ_TIMEOUT_S, int(n) * 0.1 + 5))
    except (TimeoutError, asyncio.TimeoutError):
        return _no_data("imu")
    except Exception as e:
        return _read_error("imu", e)
    return {"status": "success", "imu_data": imu_data, "count": len(imu_data)}


@mcp.tool()
async def get_flight_mode(ctx: Context) -> dict:
    """
    Get the current flight mode of the drone.

    Returns:
        dict: The current flight mode of the drone or error status.
    """
    log_tool_call("get_flight_mode")
    drone = await get_drone(ctx)
    if drone is None:
        return dict(CONN_ERROR)
    try:
        flight_mode = await first_stream_item(drone.telemetry.flight_mode(), READ_TIMEOUT_S)
    except TimeoutError:
        return _no_data("flight_mode")
    except Exception as e:
        return _read_error("flight_mode", e)
    logger.info(f"FlightMode: {flight_mode}")
    return {"status": "success", "flight_mode": str(flight_mode)}


@mcp.tool()
async def get_battery(ctx: Context) -> dict:
    """
    Get the current battery status including voltage and remaining percentage.
    Critical for monitoring flight time and knowing when to land.

    Returns:
        dict: Battery voltage (V), remaining percentage (%), and status.
    """
    log_tool_call("get_battery")
    drone = await get_drone(ctx)
    if drone is None:
        return dict(CONN_ERROR)
    logger.info("Fetching battery status")
    try:
        battery = await first_stream_item(drone.telemetry.battery(), READ_TIMEOUT_S)
    except TimeoutError:
        return _no_data("battery")
    except Exception as e:
        return _read_error("battery", e)

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
        # Rough 4S LiPo estimate: 16.8V full, 14.8V nominal, 14.0V empty
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

    effective_percent = percent_raw if percent_raw > 0 else (battery_data.get("estimated_percent", 100) / 100)
    if effective_percent < 0.20:
        battery_data["warning"] = "⚠️  LOW BATTERY - Land soon!"
    elif effective_percent < 0.30:
        battery_data["warning"] = "Battery getting low - consider landing"

    logger.info(
        f"{LogColors.STATUS}Battery: {battery_data['voltage_v']}V, {battery_data['remaining_percent']}%{LogColors.RESET}"
    )
    return {"status": "success", "battery": battery_data}


@mcp.tool()
async def get_health(ctx: Context) -> dict:
    """
    Get comprehensive system health status for pre-flight checks.
    Returns status of GPS, accelerometer, gyro, magnetometer, and more.

    Returns:
        dict: Comprehensive health status of all drone subsystems.
    """
    log_tool_call("get_health")
    drone = await get_drone(ctx)
    if drone is None:
        return dict(CONN_ERROR)
    try:
        health = await first_stream_item(drone.telemetry.health(), READ_TIMEOUT_S)
    except TimeoutError:
        return _no_data("health")
    except Exception as e:
        return _read_error("health", e)

    health_data = {
        "is_gyrometer_calibrated": health.is_gyrometer_calibration_ok,
        "is_accelerometer_calibrated": health.is_accelerometer_calibration_ok,
        "is_magnetometer_calibrated": health.is_magnetometer_calibration_ok,
        "is_local_position_ok": health.is_local_position_ok,
        "is_global_position_ok": health.is_global_position_ok,
        "is_home_position_ok": health.is_home_position_ok,
        "is_armable": health.is_armable,
    }
    all_ok = all(health_data.values())
    health_data["overall_status"] = "HEALTHY" if all_ok else "ISSUES DETECTED"
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


@mcp.tool()
async def get_home_position(ctx: Context) -> dict:
    """
    Get the home position where Return to Launch (RTL) will return to.
    This is typically set at the launch location when the drone first arms.

    Returns:
        dict: Home position coordinates and altitude.
    """
    log_tool_call("get_home_position")
    drone = await get_drone(ctx)
    if drone is None:
        return dict(CONN_ERROR)
    try:
        # Not a plain subscription read: ArduPilot only emits HOME_POSITION on
        # request, so read_home asks for the stream before giving up. See
        # droneserver.telemetry.home.
        home = await read_home(drone, READ_TIMEOUT_S)
    except TimeoutError:
        return {
            "status": "failed",
            "error": "No home telemetry received, including after requesting the topic "
            f"(waited {READ_TIMEOUT_S:.0f}s) - the vehicle most likely has no home set yet "
            "(it is set on first arm, or on GPS lock)",
        }
    except Exception as e:
        return _read_error("home position", e)
    home_data = {
        "latitude_deg": home.latitude_deg,
        "longitude_deg": home.longitude_deg,
        "absolute_altitude_m": home.absolute_altitude_m,
    }
    logger.info(
        f"Home position: {home_data['latitude_deg']}, {home_data['longitude_deg']} at {home_data['absolute_altitude_m']}m"
    )
    return {"status": "success", "home": home_data}


@mcp.tool()
async def get_speed(ctx: Context) -> dict:
    """
    Get the current ground speed (velocity over ground).
    Returns velocity in North, East, Down directions.

    Returns:
        dict: Current velocity in NED frame and total ground speed.
    """
    log_tool_call("get_speed")
    drone = await get_drone(ctx)
    if drone is None:
        return dict(CONN_ERROR)
    try:
        velocity = await first_stream_item(drone.telemetry.velocity_ned(), READ_TIMEOUT_S)
    except TimeoutError:
        return _no_data("velocity_ned")
    except Exception as e:
        return _read_error("speed", e)
    ground_speed_m_s = math.sqrt(velocity.north_m_s**2 + velocity.east_m_s**2)
    speed_data = {
        "north_m_s": velocity.north_m_s,
        "east_m_s": velocity.east_m_s,
        "down_m_s": velocity.down_m_s,
        "ground_speed_m_s": round(ground_speed_m_s, 2),
        "ground_speed_kmh": round(ground_speed_m_s * 3.6, 2),
    }
    logger.info(f"Ground speed: {speed_data['ground_speed_m_s']} m/s")
    return {"status": "success", "velocity": speed_data}


@mcp.tool()
async def get_attitude(ctx: Context) -> dict:
    """
    Get the current attitude (orientation) of the drone.
    Returns roll, pitch, and yaw angles in degrees.

    Returns:
        dict: Roll, pitch, yaw angles in degrees.
    """
    log_tool_call("get_attitude")
    drone = await get_drone(ctx)
    if drone is None:
        return dict(CONN_ERROR)
    try:
        attitude = await first_stream_item(drone.telemetry.attitude_euler(), READ_TIMEOUT_S)
    except TimeoutError:
        return _no_data("attitude_euler")
    except Exception as e:
        return _read_error("attitude", e)
    attitude_data = {
        "roll_deg": round(attitude.roll_deg, 2),
        "pitch_deg": round(attitude.pitch_deg, 2),
        "yaw_deg": round(attitude.yaw_deg, 2),
    }
    logger.info(
        f"Attitude: roll={attitude_data['roll_deg']}°, pitch={attitude_data['pitch_deg']}°, yaw={attitude_data['yaw_deg']}°"
    )
    return {"status": "success", "attitude": attitude_data}


@mcp.tool()
async def get_gps_info(ctx: Context) -> dict:
    """
    Get detailed GPS information including number of satellites and fix type.
    Important for assessing navigation quality.

    Returns:
        dict: GPS satellite count, fix type, and quality metrics.
    """
    log_tool_call("get_gps_info")
    drone = await get_drone(ctx)
    if drone is None:
        return dict(CONN_ERROR)
    try:
        gps_info = await first_stream_item(drone.telemetry.gps_info(), READ_TIMEOUT_S)
    except TimeoutError:
        return _no_data("gps_info")
    except Exception as e:
        return _read_error("GPS info", e)
    gps_data = {
        "num_satellites": gps_info.num_satellites,
        "fix_type": str(gps_info.fix_type),
    }
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


@mcp.tool()
async def get_in_air(ctx: Context) -> dict:
    """
    Check if the drone is currently in the air (flying) or on the ground.

    Returns:
        dict: Boolean indicating if drone is airborne.
    """
    log_tool_call("get_in_air")
    drone = await get_drone(ctx)
    if drone is None:
        return dict(CONN_ERROR)
    try:
        in_air = await first_stream_item(drone.telemetry.in_air(), READ_TIMEOUT_S)
    except TimeoutError:
        return _no_data("in_air")
    except Exception as e:
        return _read_error("in_air", e)
    status_text = "IN AIR (flying)" if in_air else "ON GROUND"
    logger.info(f"{LogColors.STATUS}Drone status: {status_text}{LogColors.RESET}")
    return {"status": "success", "in_air": in_air, "status_text": status_text}


@mcp.tool()
async def get_armed(ctx: Context) -> dict:
    """
    Check if the drone is currently armed (motors can spin).

    Returns:
        dict: Boolean indicating if drone is armed.
    """
    log_tool_call("get_armed")
    drone = await get_drone(ctx)
    if drone is None:
        return dict(CONN_ERROR)
    try:
        armed = await first_stream_item(drone.telemetry.armed(), READ_TIMEOUT_S)
    except TimeoutError:
        return _no_data("armed")
    except Exception as e:
        return _read_error("armed", e)
    status_text = "ARMED (motors ready)" if armed else "DISARMED (motors off)"
    logger.info(f"{LogColors.STATUS}Drone status: {status_text}{LogColors.RESET}")
    return {"status": "success", "armed": armed, "status_text": status_text}


@mcp.tool()
async def get_health_all_ok(ctx: Context) -> dict:
    """
    Quick health check - returns True if ALL systems are OK for flight.
    Use this for quick pre-flight go/no-go decisions; for the detailed
    breakdown use get_health().

    Returns:
        dict: Boolean indicating if all systems pass health checks.
    """
    log_tool_call("get_health_all_ok")
    drone = await get_drone(ctx)
    if drone is None:
        return dict(CONN_ERROR)
    try:
        health_all_ok = await first_stream_item(drone.telemetry.health_all_ok(), READ_TIMEOUT_S)
    except TimeoutError:
        return _no_data("health_all_ok")
    except Exception as e:
        return _read_error("health_all_ok", e)
    status_text = "ALL SYSTEMS GO ✓" if health_all_ok else "SYSTEMS NOT READY ✗"
    logger.info(f"{LogColors.STATUS}Health check: {status_text}{LogColors.RESET}")
    result = {
        "status": "success",
        "health_all_ok": health_all_ok,
        "status_text": status_text,
        "recommendation": "Ready for flight" if health_all_ok else "Run get_health() for details on what's not ready",
    }
    log_tool_output(result)
    return result


@mcp.tool()
async def get_landed_state(ctx: Context) -> dict:
    """
    Get detailed landed state of the drone.
    Returns one of: ON_GROUND, TAKING_OFF, IN_AIR, LANDING, or UNKNOWN.

    Returns:
        dict: Landed state enum value and descriptive text.
    """
    log_tool_call("get_landed_state")
    drone = await get_drone(ctx)
    if drone is None:
        return dict(CONN_ERROR)
    try:
        landed_state = await first_stream_item(drone.telemetry.landed_state(), READ_TIMEOUT_S)
    except TimeoutError:
        return _no_data("landed_state")
    except Exception as e:
        return _read_error("landed state", e)
    state_str = str(landed_state)
    state_descriptions = {
        "UNKNOWN": "State cannot be determined",
        "ON_GROUND": "Drone is on the ground, not moving",
        "IN_AIR": "Drone is flying/airborne",
        "TAKING_OFF": "Drone is in the process of taking off",
        "LANDING": "Drone is in the process of landing",
    }
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


@mcp.tool()
async def get_rc_status(ctx: Context) -> dict:
    """
    Get RC (Remote Control) controller connection status and signal strength.

    Returns:
        dict: RC availability status and signal strength percentage.
    """
    log_tool_call("get_rc_status")
    drone = await get_drone(ctx)
    if drone is None:
        return dict(CONN_ERROR)
    try:
        rc_status = await first_stream_item(drone.telemetry.rc_status(), READ_TIMEOUT_S)
    except TimeoutError:
        return _no_data("rc_status")
    except Exception as e:
        return _read_error("RC status", e)
    is_available = rc_status.is_available
    signal_strength = rc_status.signal_strength_percent
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
    status_text = f"RC {'Available' if is_available else 'Not Available'} - Signal: {signal_strength:.0f}% ({quality})"
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


@mcp.tool()
async def get_heading(ctx: Context) -> dict:
    """
    Get the current compass heading of the drone in degrees
    (0 = North, 90 = East, 180 = South, 270 = West).

    Returns:
        dict: Heading in degrees and cardinal direction.
    """
    log_tool_call("get_heading")
    drone = await get_drone(ctx)
    if drone is None:
        return dict(CONN_ERROR)
    try:
        heading = await first_stream_item(drone.telemetry.heading(), READ_TIMEOUT_S)
    except TimeoutError:
        return _no_data("heading")
    except Exception as e:
        return _read_error("heading", e)
    heading_deg = heading.heading_deg
    heading_normalized = heading_deg % 360
    if heading_normalized < 0:
        heading_normalized += 360
    compass = [
        (22.5, "N", "North"),
        (67.5, "NE", "Northeast"),
        (112.5, "E", "East"),
        (157.5, "SE", "Southeast"),
        (202.5, "S", "South"),
        (247.5, "SW", "Southwest"),
        (292.5, "W", "West"),
        (337.5, "NW", "Northwest"),
        (360.1, "N", "North"),
    ]
    cardinal, direction = "N", "North"
    for limit, c, d in compass:
        if heading_normalized < limit:
            cardinal, direction = c, d
            break
    logger.info(f"{LogColors.STATUS}Heading: {heading_normalized:.1f}° ({direction}){LogColors.RESET}")
    result = {
        "status": "success",
        "heading_deg": round(heading_normalized, 1),
        "cardinal_direction": cardinal,
        "direction_name": direction,
    }
    log_tool_output(result)
    return result


@mcp.tool()
async def get_odometry(ctx: Context) -> dict:
    """
    Get combined odometry data: position, velocity, and orientation.

    NOTE: requires the autopilot to publish the ODOMETRY message. ArduPilot
    does not by default (this returns a timeout error there); position/
    velocity/attitude have dedicated tools that work everywhere.

    Returns:
        dict: Combined position, velocity, and orientation data.
    """
    log_tool_call("get_odometry")
    drone = await get_drone(ctx)
    if drone is None:
        return dict(CONN_ERROR)
    try:
        odometry = await first_stream_item(drone.telemetry.odometry(), READ_TIMEOUT_S)
    except TimeoutError:
        return _no_data("odometry")
    except Exception as e:
        return _read_error("odometry", e)

    position = {
        "north_m": round(odometry.position_body.x_m, 3),
        "east_m": round(odometry.position_body.y_m, 3),
        "down_m": round(odometry.position_body.z_m, 3),
    }
    velocity = {
        "forward_m_s": round(odometry.velocity_body.x_m_s, 3),
        "right_m_s": round(odometry.velocity_body.y_m_s, 3),
        "down_m_s": round(odometry.velocity_body.z_m_s, 3),
    }
    quaternion = {
        "w": round(odometry.q.w, 4),
        "x": round(odometry.q.x, 4),
        "y": round(odometry.q.y, 4),
        "z": round(odometry.q.z, 4),
    }
    w, x, y, z = odometry.q.w, odometry.q.x, odometry.q.y, odometry.q.z
    sinr_cosp = 2 * (w * x + y * z)
    cosr_cosp = 1 - 2 * (x * x + y * y)
    roll_rad = math.atan2(sinr_cosp, cosr_cosp)
    sinp = 2 * (w * y - z * x)
    if abs(sinp) >= 1:
        pitch_rad = math.copysign(math.pi / 2, sinp)
    else:
        pitch_rad = math.asin(sinp)
    siny_cosp = 2 * (w * z + x * y)
    cosy_cosp = 1 - 2 * (y * y + z * z)
    yaw_rad = math.atan2(siny_cosp, cosy_cosp)
    euler_angles = {
        "roll_deg": round(math.degrees(roll_rad), 2),
        "pitch_deg": round(math.degrees(pitch_rad), 2),
        "yaw_deg": round(math.degrees(yaw_rad), 2),
    }
    ground_speed = math.sqrt(velocity["forward_m_s"] ** 2 + velocity["right_m_s"] ** 2)
    total_speed = math.sqrt(ground_speed**2 + velocity["down_m_s"] ** 2)
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
        "altitude_m": round(-position["down_m"], 2),
    }
    log_tool_output(result)
    return result


# ---------------------------------------------------------------- v2 additions

_EXTENDED_TOPICS = (
    "altitude",
    "attitude_quaternion",
    "attitude_angular_velocity_body",
    "raw_gps",
    "scaled_pressure",
    "position_velocity_ned",
    "unix_epoch_time",
    "distance_sensor",
    "vtol_state",
    "ground_truth",
    "fixedwing_metrics",
    "actuator_control_target",
    "actuator_output_status",
    "scaled_imu",
    "raw_imu",
    "gps_global_origin",
)


@mcp.tool()
async def get_telemetry_extended(ctx: Context, topic: str, timeout_s: float = 10.0) -> dict:
    """Read one sample of a less-common telemetry topic (generic schema).

    Dedicated tools exist for the common topics (get_position, get_battery,
    ...); this covers the long tail. Whether a topic emits depends on the
    autopilot - e.g. ArduPilot publishes raw_gps/scaled_pressure/
    distance_sensor but not odometry/altitude/vtol_state (see coverage
    matrix firmware notes). A timeout is an honest "not published here".

    Args:
        topic (str): one of altitude, attitude_quaternion,
            attitude_angular_velocity_body, raw_gps, scaled_pressure,
            position_velocity_ned, unix_epoch_time, distance_sensor,
            vtol_state, ground_truth, fixedwing_metrics,
            actuator_control_target, actuator_output_status, scaled_imu,
            raw_imu, gps_global_origin.
        timeout_s (float): how long to wait for a sample (1-60, default 10).

    Returns:
        dict: status + the sample converted to JSON (field names follow
        MavSDK).
    """
    log_tool_call("get_telemetry_extended", topic=topic, timeout_s=timeout_s)
    topic = str(topic).lower()
    if topic not in _EXTENDED_TOPICS:
        return {"status": "failed", "error": f"topic must be one of {_EXTENDED_TOPICS}, got {topic!r}"}
    if not 1.0 <= float(timeout_s) <= 60.0:
        return {"status": "failed", "error": f"timeout_s must be between 1 and 60, got {timeout_s}"}

    drone = await get_drone(ctx)
    if drone is None:
        return dict(CONN_ERROR)
    try:
        if topic == "gps_global_origin":
            sample = await asyncio.wait_for(drone.telemetry.get_gps_global_origin(), timeout=float(timeout_s))
        else:
            sample = await first_stream_item(getattr(drone.telemetry, topic)(), float(timeout_s))
    except (TimeoutError, asyncio.TimeoutError):
        return _no_data(topic)
    except Exception as e:
        return _read_error(topic, e)
    result = {"status": "success", "topic": topic, "data": to_jsonable(sample)}
    log_tool_output(result)
    return result


@mcp.tool()
async def set_telemetry_rate(ctx: Context, topic: str, rate_hz: float) -> dict:
    """Ask the autopilot to publish a telemetry topic at a given rate.

    Support is firmware-dependent: the autopilot may deny rates for topics it
    does not publish (e.g. ArduPilot denies odometry). Denials are surfaced
    as errors.

    Args:
        topic (str): one of position, home, in_air, landed_state, vtol_state,
            attitude_quaternion, attitude_euler, velocity_ned, gps_info,
            battery, rc_status, actuator_control_target,
            actuator_output_status, odometry, position_velocity_ned,
            ground_truth, fixedwing_metrics, imu, scaled_imu, raw_imu,
            unix_epoch_time, distance_sensor, altitude.
        rate_hz (float): messages per second (0.1-200).

    Returns:
        dict: status.
    """
    log_tool_call("set_telemetry_rate", topic=topic, rate_hz=rate_hz)
    topic = str(topic).lower()
    if not 0.1 <= float(rate_hz) <= 200.0:
        return {"status": "failed", "error": f"rate_hz must be between 0.1 and 200, got {rate_hz}"}
    rate = float(rate_hz)

    drone = await get_drone(ctx)
    if drone is None:
        return dict(CONN_ERROR)
    t = drone.telemetry
    try:
        if topic == "position":
            await t.set_rate_position(rate)
        elif topic == "home":
            await t.set_rate_home(rate)
        elif topic == "in_air":
            await t.set_rate_in_air(rate)
        elif topic == "landed_state":
            await t.set_rate_landed_state(rate)
        elif topic == "vtol_state":
            await t.set_rate_vtol_state(rate)
        elif topic == "attitude_quaternion":
            await t.set_rate_attitude_quaternion(rate)
        elif topic == "attitude_euler":
            await t.set_rate_attitude_euler(rate)
        elif topic == "velocity_ned":
            await t.set_rate_velocity_ned(rate)
        elif topic == "gps_info":
            await t.set_rate_gps_info(rate)
        elif topic == "battery":
            await t.set_rate_battery(rate)
        elif topic == "rc_status":
            await t.set_rate_rc_status(rate)
        elif topic == "actuator_control_target":
            await t.set_rate_actuator_control_target(rate)
        elif topic == "actuator_output_status":
            await t.set_rate_actuator_output_status(rate)
        elif topic == "odometry":
            await t.set_rate_odometry(rate)
        elif topic == "position_velocity_ned":
            await t.set_rate_position_velocity_ned(rate)
        elif topic == "ground_truth":
            await t.set_rate_ground_truth(rate)
        elif topic == "fixedwing_metrics":
            await t.set_rate_fixedwing_metrics(rate)
        elif topic == "imu":
            await t.set_rate_imu(rate)
        elif topic == "scaled_imu":
            await t.set_rate_scaled_imu(rate)
        elif topic == "raw_imu":
            await t.set_rate_raw_imu(rate)
        elif topic == "unix_epoch_time":
            await t.set_rate_unix_epoch_time(rate)
        elif topic == "distance_sensor":
            await t.set_rate_distance_sensor(rate)
        elif topic == "altitude":
            await t.set_rate_altitude(rate)
        else:
            return {"status": "failed", "error": f"unknown rate topic {topic!r} (see docstring for the list)"}
    except Exception as e:
        logger.error(f"set_telemetry_rate({topic}) failed: {e}")
        return {"status": "failed", "error": f"set rate for {topic} failed: {e}"}
    result = {"status": "success", "message": f"{topic} rate set to {rate} Hz"}
    log_tool_output(result)
    return result
