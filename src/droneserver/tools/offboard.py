"""Offboard (continuous setpoint) control MCP tools (MavSDK ``offboard`` plugin).

v2 design - see docs/tool_groups.md for the full rationale. Key semantics
every caller (i.e. the LLM) must understand:

1. An MCP tool call is one-shot, but offboard setpoints are *streams*: the
   server (mavsdk_server) re-sends the LAST setpoint continuously. One call
   therefore persists until it is replaced, offboard is stopped, or the
   stale-setpoint watchdog fires.
2. Order: set a setpoint FIRST, then ``offboard_control("start")``.
3. Safety: motion setpoints (velocity / attitude / acceleration / actuator)
   accept ``stale_timeout_s`` (default 15 s). If no new setpoint arrives in
   time, the server automatically brakes to a zero-velocity hover at the
   current heading. Position setpoints are self-terminating (the vehicle
   stops at the target) and clear the watchdog.
4. Firmware: works on PX4 (OFFBOARD mode) and ArduPilot (MavSDK maps offboard
   to GUIDED; verified on ArduCopter SITL). ``offboard_control("stop")``
   returns the vehicle to Hold/Loiter.
"""

from mcp.server.fastmcp import Context

from droneserver.app import mcp
from droneserver.mavlink.connection import MAVLinkConnector, ensure_connection
from droneserver.safety.offboard_watchdog import OffboardWatchdog
from droneserver.setpoints import (
    build_acceleration_ned,
    build_actuator_control,
    build_attitude,
    build_position_global,
    build_position_ned,
    build_velocity_body,
    build_velocity_ned,
    validate_stale_timeout,
)
from droneserver.telemetry.flight_log import log_mavlink_cmd, log_tool_call, log_tool_output, logger

DEFAULT_STALE_TIMEOUT_S = 15.0

_CONN_ERROR = {"status": "failed", "error": "Drone connection timeout. Please wait and try again."}

# Server-wide offboard state (one drone per server, like the global connector).
_watchdog = OffboardWatchdog()
_setpoint_set = False

_STREAMING_NOTE = "Setpoint streams continuously until replaced or offboard is stopped."


def _motion_note(timeout_s: float) -> str:
    return f"{_STREAMING_NOTE} Auto-brakes to zero-velocity hover if not refreshed within {timeout_s:.0f}s."


def _make_brake(connector: MAVLinkConnector):
    async def brake() -> None:
        drone = connector.drone
        yaw_deg = 0.0
        try:
            async for heading in drone.telemetry.heading():
                yaw_deg = heading.heading_deg
                break
        except Exception:
            logger.warning("offboard brake: could not read heading, using yaw=0")
        await drone.offboard.set_velocity_ned(build_velocity_ned(0.0, 0.0, 0.0, yaw_deg))

    return brake


async def _apply_setpoint(ctx: Context, kind: str, stale_timeout_s: float | None, send) -> dict:
    """Shared tail for every setpoint tool: connect, send, arm/clear watchdog."""
    global _setpoint_set
    connector = ctx.request_context.lifespan_context
    if not await ensure_connection(connector):
        return dict(_CONN_ERROR)
    drone = connector.drone

    try:
        await send(drone)
    except Exception as e:
        logger.error(f"offboard setpoint ({kind}) failed: {e}")
        result = {"status": "failed", "error": f"Setpoint rejected: {e}"}
        log_tool_output(result)
        return result

    _setpoint_set = True
    if stale_timeout_s is None:
        _watchdog.note_setpoint(kind, None, None)
        note = _STREAMING_NOTE + " Position setpoints are self-terminating (vehicle stops at target)."
    else:
        _watchdog.note_setpoint(kind, stale_timeout_s, _make_brake(connector))
        note = _motion_note(stale_timeout_s)

    result = {"status": "success", "setpoint": kind, "note": note}
    log_tool_output(result)
    return result


@mcp.tool()
async def offboard_control(ctx: Context, action: str) -> dict:
    """Start, stop, or query offboard (continuous setpoint) control.

    Offboard mode makes the drone continuously follow the last setpoint sent
    with the offboard_set_* tools.

    WORKFLOW: 1) send a setpoint (e.g. offboard_set_velocity_ned),
    2) offboard_control("start"), 3) update setpoints as needed,
    4) offboard_control("stop") - the vehicle then holds position
    (Hold/Loiter mode). The drone must be armed and flying (see takeoff).

    Args:
        action (str): "start" (requires a setpoint to have been set first),
            "stop" (exit offboard, hold position), or "status" (read-only:
            whether offboard is active + stale-watchdog state).

    Returns:
        dict: status; for "status" also offboard_active and setpoint info.
    """
    log_tool_call("offboard_control", action=action)
    action = str(action).lower()
    if action not in ("start", "stop", "status"):
        return {"status": "failed", "error": f'action must be "start", "stop" or "status", got {action!r}'}

    connector = ctx.request_context.lifespan_context
    if not await ensure_connection(connector):
        return dict(_CONN_ERROR)
    drone = connector.drone

    try:
        if action == "status":
            active = await drone.offboard.is_active()
            result = {
                "status": "success",
                "offboard_active": active,
                "setpoint": _watchdog.status(),
            }
        elif action == "start":
            if not _setpoint_set:
                result = {
                    "status": "failed",
                    "error": "No setpoint has been set yet. Call an offboard_set_* tool first, then start.",
                }
                log_tool_output(result)
                return result
            log_mavlink_cmd("drone.offboard.start")
            await drone.offboard.start()
            result = {
                "status": "success",
                "message": "Offboard control active - drone is following the last setpoint",
            }
        else:  # stop
            _watchdog.cancel()
            log_mavlink_cmd("drone.offboard.stop")
            await drone.offboard.stop()
            result = {"status": "success", "message": "Offboard control stopped - drone is holding position"}
    except Exception as e:
        logger.error(f"offboard_control({action}) failed: {e}")
        result = {"status": "failed", "error": f"offboard {action} failed: {e}"}

    log_tool_output(result)
    return result


@mcp.tool()
async def offboard_set_position_ned(
    ctx: Context,
    north_m: float,
    east_m: float,
    down_m: float,
    yaw_deg: float = 0.0,
    velocity: dict | None = None,
    acceleration: dict | None = None,
) -> dict:
    """Set an offboard position setpoint in local NED frame (meters from the
    offboard origin; down is positive, so -15 means 15 m above it), with
    optional velocity/acceleration feed-forward.

    Self-terminating: the drone flies to the target and stops there, so no
    stale-timeout applies. Requires offboard_control("start") to take effect.

    Args:
        north_m (float): target north offset in meters.
        east_m (float): target east offset in meters.
        down_m (float): target down offset in meters (negative = up).
        yaw_deg (float): target heading in degrees (0 = north).
        velocity (dict, optional): feed-forward {"north_m_s", "east_m_s",
            "down_m_s", "yaw_deg"}.
        acceleration (dict, optional): feed-forward {"north_m_s2", "east_m_s2",
            "down_m_s2"}; requires velocity too.

    Returns:
        dict: status.
    """
    log_tool_call(
        "offboard_set_position_ned",
        north_m=north_m,
        east_m=east_m,
        down_m=down_m,
        yaw_deg=yaw_deg,
        velocity=velocity,
        acceleration=acceleration,
    )
    try:
        pos, vel, acc = build_position_ned(north_m, east_m, down_m, yaw_deg, velocity, acceleration)
    except (ValueError, TypeError) as e:
        return {"status": "failed", "error": str(e)}

    async def send(drone):
        if acc is not None:
            log_mavlink_cmd("drone.offboard.set_position_velocity_acceleration_ned")
            await drone.offboard.set_position_velocity_acceleration_ned(pos, vel, acc)
        elif vel is not None:
            log_mavlink_cmd("drone.offboard.set_position_velocity_ned")
            await drone.offboard.set_position_velocity_ned(pos, vel)
        else:
            log_mavlink_cmd("drone.offboard.set_position_ned")
            await drone.offboard.set_position_ned(pos)

    return await _apply_setpoint(ctx, "position_ned", None, send)


@mcp.tool()
async def offboard_set_position_global(
    ctx: Context,
    latitude_deg: float,
    longitude_deg: float,
    altitude_m: float,
    yaw_deg: float = 0.0,
    altitude_type: str = "rel_home",
) -> dict:
    """Set an offboard position setpoint as a global (GPS) coordinate.

    Self-terminating: the drone flies to the target and stops there.
    Requires offboard_control("start") to take effect.

    Args:
        latitude_deg (float): target latitude.
        longitude_deg (float): target longitude.
        altitude_m (float): target altitude, interpreted per altitude_type.
        yaw_deg (float): target heading in degrees (0 = north).
        altitude_type (str): "amsl" (above mean sea level), "rel_home"
            (relative to home, default) or "agl" (above ground level).

    Returns:
        dict: status.
    """
    log_tool_call(
        "offboard_set_position_global",
        latitude_deg=latitude_deg,
        longitude_deg=longitude_deg,
        altitude_m=altitude_m,
        yaw_deg=yaw_deg,
        altitude_type=altitude_type,
    )
    try:
        target = build_position_global(latitude_deg, longitude_deg, altitude_m, yaw_deg, altitude_type)
    except (ValueError, TypeError) as e:
        return {"status": "failed", "error": str(e)}

    async def send(drone):
        log_mavlink_cmd("drone.offboard.set_position_global")
        await drone.offboard.set_position_global(target)

    return await _apply_setpoint(ctx, "position_global", None, send)


@mcp.tool()
async def offboard_set_velocity_ned(
    ctx: Context,
    north_m_s: float,
    east_m_s: float,
    down_m_s: float = 0.0,
    yaw_deg: float = 0.0,
    stale_timeout_s: float = DEFAULT_STALE_TIMEOUT_S,
) -> dict:
    """Set an offboard velocity setpoint in NED frame (m/s; down positive).

    MOTION SETPOINT - the drone keeps moving at this velocity until you send
    a new setpoint, stop offboard, or the stale-timeout auto-brake fires.
    Requires offboard_control("start") to take effect.

    Args:
        north_m_s (float): velocity north (m/s, max 20).
        east_m_s (float): velocity east (m/s).
        down_m_s (float): velocity down (m/s, negative = climb).
        yaw_deg (float): heading to hold in degrees (0 = north).
        stale_timeout_s (float): auto-brake to hover if no new setpoint within
            this many seconds (1-120, default 15).

    Returns:
        dict: status.
    """
    log_tool_call(
        "offboard_set_velocity_ned",
        north_m_s=north_m_s,
        east_m_s=east_m_s,
        down_m_s=down_m_s,
        yaw_deg=yaw_deg,
        stale_timeout_s=stale_timeout_s,
    )
    try:
        sp = build_velocity_ned(north_m_s, east_m_s, down_m_s, yaw_deg)
        timeout = validate_stale_timeout(stale_timeout_s)
    except (ValueError, TypeError) as e:
        return {"status": "failed", "error": str(e)}

    async def send(drone):
        log_mavlink_cmd("drone.offboard.set_velocity_ned", n=north_m_s, e=east_m_s, d=down_m_s)
        await drone.offboard.set_velocity_ned(sp)

    return await _apply_setpoint(ctx, "velocity_ned", timeout, send)


@mcp.tool()
async def offboard_set_velocity_body(
    ctx: Context,
    forward_m_s: float,
    right_m_s: float = 0.0,
    down_m_s: float = 0.0,
    yawspeed_deg_s: float = 0.0,
    stale_timeout_s: float = DEFAULT_STALE_TIMEOUT_S,
) -> dict:
    """Set an offboard velocity setpoint in the drone's BODY frame
    (forward/right/down, m/s) with a yaw rate - useful for "fly forward",
    orbit-like arcs, or camera-relative motion.

    MOTION SETPOINT - persists until replaced/stopped; auto-brakes after
    stale_timeout_s. Requires offboard_control("start") to take effect.

    Args:
        forward_m_s (float): velocity forward along heading (m/s, max 20).
        right_m_s (float): velocity to the right (m/s).
        down_m_s (float): velocity down (m/s, negative = climb).
        yawspeed_deg_s (float): yaw rate clockwise (deg/s, max 180).
        stale_timeout_s (float): auto-brake window in seconds (1-120).

    Returns:
        dict: status.
    """
    log_tool_call(
        "offboard_set_velocity_body",
        forward_m_s=forward_m_s,
        right_m_s=right_m_s,
        down_m_s=down_m_s,
        yawspeed_deg_s=yawspeed_deg_s,
        stale_timeout_s=stale_timeout_s,
    )
    try:
        sp = build_velocity_body(forward_m_s, right_m_s, down_m_s, yawspeed_deg_s)
        timeout = validate_stale_timeout(stale_timeout_s)
    except (ValueError, TypeError) as e:
        return {"status": "failed", "error": str(e)}

    async def send(drone):
        log_mavlink_cmd("drone.offboard.set_velocity_body", fwd=forward_m_s, right=right_m_s)
        await drone.offboard.set_velocity_body(sp)

    return await _apply_setpoint(ctx, "velocity_body", timeout, send)


@mcp.tool()
async def offboard_set_attitude(
    ctx: Context,
    roll: float,
    pitch: float,
    yaw: float,
    thrust: float,
    mode: str = "angle",
    stale_timeout_s: float = DEFAULT_STALE_TIMEOUT_S,
) -> dict:
    """Set a low-level offboard attitude setpoint (EXPERT - prefer velocity or
    position setpoints for navigation).

    mode="angle": roll/pitch/yaw are attitude angles in degrees.
    mode="rate": roll/pitch/yaw are body angular rates in deg/s.
    thrust is normalized 0..1 (≈0.5 hovers a typical multicopter).

    MOTION SETPOINT - persists until replaced/stopped; auto-brakes after
    stale_timeout_s. Requires offboard_control("start") to take effect.

    Args:
        roll (float): roll angle (deg) or rate (deg/s per mode).
        pitch (float): pitch angle (deg) or rate (deg/s). Negative pitch
            moves forward in "angle" mode.
        yaw (float): yaw angle (deg) or rate (deg/s).
        thrust (float): normalized collective thrust, 0..1.
        mode (str): "angle" (default) or "rate".
        stale_timeout_s (float): auto-brake window in seconds (1-120).

    Returns:
        dict: status.
    """
    log_tool_call(
        "offboard_set_attitude",
        roll=roll,
        pitch=pitch,
        yaw=yaw,
        thrust=thrust,
        mode=mode,
        stale_timeout_s=stale_timeout_s,
    )
    try:
        sp = build_attitude(mode, roll, pitch, yaw, thrust)
        timeout = validate_stale_timeout(stale_timeout_s)
    except (ValueError, TypeError) as e:
        return {"status": "failed", "error": str(e)}

    kind = f"attitude_{str(mode).lower()}"

    async def send(drone):
        if kind == "attitude_rate":
            log_mavlink_cmd("drone.offboard.set_attitude_rate")
            await drone.offboard.set_attitude_rate(sp)
        else:
            log_mavlink_cmd("drone.offboard.set_attitude")
            await drone.offboard.set_attitude(sp)

    return await _apply_setpoint(ctx, kind, timeout, send)


@mcp.tool()
async def offboard_set_acceleration_ned(
    ctx: Context,
    north_m_s2: float,
    east_m_s2: float,
    down_m_s2: float = 0.0,
    stale_timeout_s: float = DEFAULT_STALE_TIMEOUT_S,
) -> dict:
    """Set an offboard acceleration setpoint in NED frame (m/s^2, max 10;
    down positive). EXPERT - prefer velocity or position setpoints.

    MOTION SETPOINT - persists until replaced/stopped; auto-brakes after
    stale_timeout_s. Requires offboard_control("start") to take effect.

    Returns:
        dict: status.
    """
    log_tool_call(
        "offboard_set_acceleration_ned",
        north_m_s2=north_m_s2,
        east_m_s2=east_m_s2,
        down_m_s2=down_m_s2,
        stale_timeout_s=stale_timeout_s,
    )
    try:
        sp = build_acceleration_ned(north_m_s2, east_m_s2, down_m_s2)
        timeout = validate_stale_timeout(stale_timeout_s)
    except (ValueError, TypeError) as e:
        return {"status": "failed", "error": str(e)}

    async def send(drone):
        log_mavlink_cmd("drone.offboard.set_acceleration_ned")
        await drone.offboard.set_acceleration_ned(sp)

    return await _apply_setpoint(ctx, "acceleration_ned", timeout, send)


@mcp.tool()
async def offboard_set_actuator_control(
    ctx: Context,
    groups: list,
    stale_timeout_s: float = DEFAULT_STALE_TIMEOUT_S,
) -> dict:
    """Directly command actuator control groups (EXPERT, PX4-only in
    practice: ArduPilot accepts the message but does not act on it -
    SET_ACTUATOR_CONTROL_TARGET has no handler there).

    Args:
        groups (list): 1-2 control groups, each a list of up to 8 normalized
            control values in [-1, 1] (PX4 mixer group semantics).
        stale_timeout_s (float): auto-brake window in seconds (1-120).

    Returns:
        dict: status.
    """
    log_tool_call("offboard_set_actuator_control", groups=groups, stale_timeout_s=stale_timeout_s)
    try:
        sp = build_actuator_control(groups)
        timeout = validate_stale_timeout(stale_timeout_s)
    except (ValueError, TypeError) as e:
        return {"status": "failed", "error": str(e)}

    async def send(drone):
        log_mavlink_cmd("drone.offboard.set_actuator_control")
        await drone.offboard.set_actuator_control(sp)

    return await _apply_setpoint(ctx, "actuator_control", timeout, send)
