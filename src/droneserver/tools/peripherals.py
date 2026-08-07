"""Payload/peripheral MCP tools: manual control, follow-me, gripper/winch,
transponder (ADS-B), tune, mocap, RTK (P2/P3).
"""

from mcp.server.fastmcp import Context

from droneserver.app import mcp
from droneserver.convert import to_jsonable
from droneserver.telemetry.flight_log import log_tool_call, log_tool_output, logger
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
async def manual_control(
    ctx: Context, action: str, x: float = 0.0, y: float = 0.0, z: float = 0.5, r: float = 0.0
) -> dict:
    """Simulated manual (joystick) control.

    Call action="position" or "altitude" once to enter the assisted mode,
    then action="input" repeatedly to stream stick values.

    Args:
        action (str): "position" (start position-assisted control),
            "altitude" (start altitude-assisted control), or "input" (send one
            stick sample).
        x (float): pitch stick, -1..1 (forward positive).
        y (float): roll stick, -1..1 (right positive).
        z (float): throttle stick, 0..1 (0.5 = hover for altitude mode).
        r (float): yaw stick, -1..1 (clockwise positive).

    Returns:
        dict: status.
    """
    log_tool_call("manual_control", action=action, x=x, y=y, z=z, r=r)
    action = str(action).lower()
    if action not in ("position", "altitude", "input"):
        return _fail(f'action must be "position", "altitude" or "input", got {action!r}')
    if action == "input":
        if not (-1 <= x <= 1 and -1 <= y <= 1 and 0 <= z <= 1 and -1 <= r <= 1):
            return _fail("x/y/r must be in [-1,1] and z in [0,1]")

    drone = await get_drone(ctx)
    if drone is None:
        return dict(CONN_ERROR)
    try:
        if action == "position":
            await drone.manual_control.start_position_control()
            return _ok(message="position-assisted manual control started")
        if action == "altitude":
            await drone.manual_control.start_altitude_control()
            return _ok(message="altitude-assisted manual control started")
        await drone.manual_control.set_manual_control_input(float(x), float(y), float(z), float(r))
        return _ok(message="manual control input sent")
    except Exception as e:
        logger.error(f"manual_control({action}) failed: {e}")
        return _fail(f"manual control {action} failed: {e}")


@mcp.tool()
async def follow_me(
    ctx: Context,
    action: str,
    latitude_deg: float = 0.0,
    longitude_deg: float = 0.0,
    absolute_altitude_m: float = 0.0,
    follow_height_m: float = 8.0,
    follow_distance_m: float = 8.0,
    follow_angle_deg: float = 180.0,
    responsiveness: float = 0.5,
) -> dict:
    """Follow-me mode: the drone follows a moving target (e.g. the operator's
    phone GPS).

    FIRMWARE NOTE: this is a PX4 flight mode. ArduCopter has no equivalent -
    set_config/start return failures there (observed). Workflow on PX4:
    action="config" (optional) -> "start" -> repeated "target" updates ->
    "stop".

    Args:
        action (str): "config", "start", "stop", "target", "status", or
            "last_target".
        latitude_deg, longitude_deg, absolute_altitude_m (float): target
            position for action="target".
        follow_height_m, follow_distance_m, follow_angle_deg, responsiveness
            (float): parameters for action="config".

    Returns:
        dict: status (+ data for status/last_target).
    """
    from mavsdk.follow_me import Config, TargetLocation

    log_tool_call("follow_me", action=action)
    action = str(action).lower()
    if action not in ("config", "get_config", "start", "stop", "target", "status", "last_target"):
        return _fail(f"invalid action {action!r}")

    drone = await get_drone(ctx)
    if drone is None:
        return dict(CONN_ERROR)
    try:
        if action == "config":
            cfg = Config(
                float(follow_height_m),
                float(follow_distance_m),
                float(responsiveness),
                Config.FollowAltitudeMode.CONSTANT,
                8.0,
                float(follow_angle_deg),
            )
            await drone.follow_me.set_config(cfg)
            return _ok(message="follow-me config set")
        if action == "get_config":
            cfg = await drone.follow_me.get_config()
            return _ok(config=to_jsonable(cfg))
        if action == "start":
            await drone.follow_me.start()
            return _ok(message="follow-me started")
        if action == "stop":
            await drone.follow_me.stop()
            return _ok(message="follow-me stopped")
        if action == "target":
            await drone.follow_me.set_target_location(
                TargetLocation(float(latitude_deg), float(longitude_deg), float(absolute_altitude_m), 0.0, 0.0, 0.0)
            )
            return _ok(message="target location updated")
        if action == "status":
            active = await drone.follow_me.is_active()
            return _ok(active=active)
        last = await drone.follow_me.get_last_location()
        return _ok(last_target=to_jsonable(last))
    except Exception as e:
        logger.error(f"follow_me({action}) failed: {e}")
        return _fail(f"follow-me {action} failed: {e} (PX4-only flight mode)")


@mcp.tool()
async def payload_mechanism(
    ctx: Context, device: str, action: str, instance: int = 0, length_m: float = 0.0, rate_m_s: float = 0.0
) -> dict:
    """Control a payload gripper or winch.

    FIRMWARE NOTE: requires the mechanism wired to a servo/PWM output on the
    autopilot. On the plain SITL these commands time out (no physical device);
    winch STATUS telemetry is available. Verified reachable, effect untestable
    in SITL - see docs/firmware_notes.csv.

    Args:
        device (str): "gripper" or "winch".
        action (str): gripper -> "grab" | "release"; winch -> "relax" |
            "retract" | "deliver" | "hold" | "lock" | "load" | "load_payload"
            | "abandon" | "status" | "rate" (needs rate_m_s) | "length" (needs
            length_m, rate_m_s).
        instance (int): device instance.
        length_m (float): winch line length for action="length".
        rate_m_s (float): winch line rate for action="rate"/"length".

    Returns:
        dict: status (+ winch status for action="status").
    """
    log_tool_call("payload_mechanism", device=device, action=action, instance=instance)
    device = str(device).lower()
    action = str(action).lower()
    if device not in ("gripper", "winch"):
        return _fail(f'device must be "gripper" or "winch", got {device!r}')

    drone = await get_drone(ctx)
    if drone is None:
        return dict(CONN_ERROR)
    try:
        if device == "gripper":
            if action == "grab":
                await drone.gripper.grab(int(instance))
            elif action == "release":
                await drone.gripper.release(int(instance))
            else:
                return _fail(f'gripper action must be "grab" or "release", got {action!r}')
            return _ok(message=f"gripper {action} (instance {instance})")

        w = drone.winch
        simple = {
            "relax": w.relax,
            "retract": w.retract,
            "deliver": w.deliver,
            "hold": w.hold,
            "lock": w.lock,
            "load": w.load_line,
            "abandon": w.abandon_line,
            "load_payload": w.load_payload,
        }
        if action in simple:
            await simple[action](int(instance))
            return _ok(message=f"winch {action} (instance {instance})")
        if action == "status":
            status = await first_stream_item(w.status(), 8.0)
            return _ok(winch_status=to_jsonable(status))
        if action == "rate":
            await w.rate_control(int(instance), float(rate_m_s))
            return _ok(message=f"winch rate {rate_m_s} m/s")
        if action == "length":
            await w.relative_length_control(int(instance), float(length_m), float(rate_m_s))
            return _ok(message=f"winch length {length_m} m at {rate_m_s} m/s")
        return _fail(f"invalid winch action {action!r}")
    except TimeoutError:
        return _fail("No winch status received within 8s")
    except Exception as e:
        logger.error(f"payload_mechanism({device},{action}) failed: {e}")
        return _fail(f"{device} {action} failed: {e} (device may not be present on this vehicle)")


@mcp.tool()
async def read_transponder(ctx: Context, rate_hz: float = 1.0, timeout_s: float = 20.0) -> dict:
    """Read one ADS-B transponder report (nearby aircraft), setting the update
    rate first.

    Verified on ArduCopter SITL with simulated ADS-B traffic
    (SIM_ADSB_COUNT); returns the ICAO address, position and velocity of a
    nearby aircraft.

    Args:
        rate_hz (float): transponder update rate to request (0.1-10).
        timeout_s (float): how long to wait for a report (1-60).

    Returns:
        dict: status + one ADS-B vehicle report (or a note if no traffic).
    """
    log_tool_call("read_transponder", rate_hz=rate_hz, timeout_s=timeout_s)
    if not 0.1 <= float(rate_hz) <= 10.0:
        return _fail(f"rate_hz must be between 0.1 and 10, got {rate_hz}")
    if not 1.0 <= float(timeout_s) <= 60.0:
        return _fail(f"timeout_s must be between 1 and 60, got {timeout_s}")

    drone = await get_drone(ctx)
    if drone is None:
        return dict(CONN_ERROR)
    try:
        await drone.transponder.set_rate_transponder(float(rate_hz))
        report = await first_stream_item(drone.transponder.transponder(), float(timeout_s))
    except TimeoutError:
        return _ok(vehicle=None, note="No ADS-B traffic received (no nearby transponder-equipped aircraft)")
    except Exception as e:
        logger.error(f"read_transponder failed: {e}")
        return _fail(f"transponder read failed: {e}")
    return _ok(vehicle=to_jsonable(report))


@mcp.tool()
async def play_tune(ctx: Context, notes: str, tempo: int = 120) -> dict:
    """Play a tune/buzzer melody on the autopilot.

    Args:
        notes (str): space-separated note letters a-g (optionally with an
            octave prefix), e.g. "c d e f g". Rests: "p".
        tempo (int): beats per minute (40-300).

    Returns:
        dict: status.
    """
    from mavsdk.tune import SongElement, TuneDescription

    log_tool_call("play_tune", notes=notes, tempo=tempo)
    if not 40 <= int(tempo) <= 300:
        return _fail(f"tempo must be between 40 and 300, got {tempo}")
    note_map = {
        "a": SongElement.NOTE_A,
        "b": SongElement.NOTE_B,
        "c": SongElement.NOTE_C,
        "d": SongElement.NOTE_D,
        "e": SongElement.NOTE_E,
        "f": SongElement.NOTE_F,
        "g": SongElement.NOTE_G,
        "p": SongElement.NOTE_PAUSE,
    }
    elements = [SongElement.DURATION_4]
    for token in str(notes).lower().split():
        el = note_map.get(token[-1])
        if el is None:
            return _fail(f"unrecognized note {token!r}; use letters a-g (or p for pause)")
        elements.append(el)
    if len(elements) == 1:
        return _fail("no playable notes provided")

    drone = await get_drone(ctx)
    if drone is None:
        return dict(CONN_ERROR)
    try:
        await drone.tune.play_tune(TuneDescription(elements, int(tempo)))
    except Exception as e:
        logger.error(f"play_tune failed: {e}")
        return _fail(f"play_tune failed: {e}")
    return _ok(message="tune played")


@mcp.tool()
async def send_mocap(
    ctx: Context,
    kind: str,
    x_m: float,
    y_m: float,
    z_m: float,
    roll_rad: float = 0.0,
    pitch_rad: float = 0.0,
    yaw_rad: float = 0.0,
) -> dict:
    """Feed an external motion-capture / visual position estimate to the
    autopilot (for indoor/GPS-denied flight).

    Args:
        kind (str): "vision_position" (position + attitude estimate),
            "mocap_pose" (motion-capture attitude+position), or "odometry"
            (position + attitude, in the local NED frame).
        x_m, y_m, z_m (float): position in the local NED frame (meters).
        roll_rad, pitch_rad, yaw_rad (float): attitude in radians.

    Returns:
        dict: status.
    """
    import math

    from mavsdk.mocap import (
        AngleBody,
        AngularVelocityBody,
        AttitudePositionMocap,
        Covariance,
        Odometry,
        PositionBody,
        Quaternion,
        SpeedBody,
        VisionPositionEstimate,
    )

    log_tool_call("send_mocap", kind=kind, x_m=x_m, y_m=y_m, z_m=z_m)
    kind = str(kind).lower()
    if kind not in ("vision_position", "mocap_pose", "odometry"):
        return _fail(f'kind must be "vision_position", "mocap_pose" or "odometry", got {kind!r}')
    nan_cov = Covariance([float("nan")])
    pos = PositionBody(float(x_m), float(y_m), float(z_m))

    def quat():
        cy, sy = math.cos(yaw_rad * 0.5), math.sin(yaw_rad * 0.5)
        cp, sp = math.cos(pitch_rad * 0.5), math.sin(pitch_rad * 0.5)
        cr, sr = math.cos(roll_rad * 0.5), math.sin(roll_rad * 0.5)
        return Quaternion(
            cr * cp * cy + sr * sp * sy,
            sr * cp * cy - cr * sp * sy,
            cr * sp * cy + sr * cp * sy,
            cr * cp * sy - sr * sp * cy,
        )

    drone = await get_drone(ctx)
    if drone is None:
        return dict(CONN_ERROR)
    try:
        if kind == "vision_position":
            vpe = VisionPositionEstimate(0, pos, AngleBody(float(roll_rad), float(pitch_rad), float(yaw_rad)), nan_cov)
            await drone.mocap.set_vision_position_estimate(vpe)
        elif kind == "mocap_pose":
            await drone.mocap.set_attitude_position_mocap(AttitudePositionMocap(0, quat(), pos, nan_cov))
        else:
            odom = Odometry(
                0,
                Odometry.MavFrame.LOCAL_NED,
                pos,
                quat(),
                SpeedBody(0.0, 0.0, 0.0),
                AngularVelocityBody(0.0, 0.0, 0.0),
                nan_cov,
                nan_cov,
            )
            await drone.mocap.set_odometry(odom)
    except Exception as e:
        logger.error(f"send_mocap({kind}) failed: {e}")
        return _fail(f"send_mocap {kind} failed: {e}")
    return _ok(message=f"{kind} estimate sent")


@mcp.tool()
async def send_rtcm(ctx: Context, rtcm_base64: str) -> dict:
    """Forward an RTCM differential-GPS correction frame (base64-encoded) to
    the autopilot for RTK positioning.

    Args:
        rtcm_base64 (str): base64-encoded RTCM data (typically relayed from an
            NTRIP caster or RTK base station).

    Returns:
        dict: status.
    """
    from mavsdk.rtk import RtcmData

    log_tool_call("send_rtcm", rtcm_base64=f"<{len(rtcm_base64)} chars>")
    if not rtcm_base64:
        return _fail("rtcm_base64 must not be empty")

    drone = await get_drone(ctx)
    if drone is None:
        return dict(CONN_ERROR)
    try:
        await drone.rtk.send_rtcm_data(RtcmData(str(rtcm_base64)))
    except Exception as e:
        logger.error(f"send_rtcm failed: {e}")
        return _fail(f"send_rtcm failed: {e}")
    return _ok(message="RTCM correction sent")
