"""Calibration and failure-injection MCP tools (P2).

- ``calibrate`` wraps the ``calibration`` plugin's streaming calibrations.
- ``inject_failure`` wraps the ``failure`` plugin - the sensor/system failure
  injection used for the paper's safety experiments (Phase 3). It requires the
  autopilot's SIM_* failure hooks; on ArduPilot SITL MavSDK reports
  UNSUPPORTED (failures are injected via SIM_* parameters instead - see
  docs/firmware_notes.csv), so the tool also documents that path.
"""

import asyncio

from mcp.server.fastmcp import Context

from droneserver.app import mcp
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


_CALIBRATIONS = ("gyro", "accelerometer", "magnetometer", "level_horizon", "gimbal_accelerometer")


@mcp.tool()
async def calibrate(ctx: Context, sensor: str, timeout_s: float = 60.0) -> dict:
    """Run a sensor calibration and report the progress log.

    ⚠️ Do this on the ground, disarmed. Some calibrations (accelerometer,
    magnetometer) normally require physically rotating the vehicle and will
    not complete in SITL.

    Args:
        sensor (str): "gyro", "accelerometer", "magnetometer",
            "level_horizon", or "gimbal_accelerometer".
        timeout_s (float): max time to wait for completion (5-300, default 60).

    Returns:
        dict: status + the sequence of progress/instruction messages.
    """
    log_tool_call("calibrate", sensor=sensor, timeout_s=timeout_s)
    sensor = str(sensor).lower()
    if sensor not in _CALIBRATIONS:
        return _fail(f"sensor must be one of {_CALIBRATIONS}, got {sensor!r}")
    if not 5.0 <= float(timeout_s) <= 300.0:
        return _fail(f"timeout_s must be between 5 and 300, got {timeout_s}")

    drone = await get_drone(ctx)
    if drone is None:
        return dict(CONN_ERROR)
    method = {
        "gyro": drone.calibration.calibrate_gyro,
        "accelerometer": drone.calibration.calibrate_accelerometer,
        "magnetometer": drone.calibration.calibrate_magnetometer,
        "level_horizon": drone.calibration.calibrate_level_horizon,
        "gimbal_accelerometer": drone.calibration.calibrate_gimbal_accelerometer,
    }[sensor]

    progress_log: list[dict] = []

    async def run():
        async for data in method():
            entry = {}
            if getattr(data, "has_progress", False):
                entry["progress"] = round(data.progress, 3)
            if getattr(data, "has_status_text", False):
                entry["status_text"] = data.status_text
            if entry:
                progress_log.append(entry)

    try:
        log_mavlink_cmd(f"drone.calibration.calibrate_{sensor}")
        await asyncio.wait_for(run(), timeout=float(timeout_s))
    except (TimeoutError, asyncio.TimeoutError):
        return {
            "status": "failed",
            "error": f"{sensor} calibration did not complete within {timeout_s:.0f}s "
            "(accelerometer/magnetometer need physical motion - not possible in SITL)",
            "progress_log": progress_log,
        }
    except Exception as e:
        logger.error(f"calibrate({sensor}) failed: {e}")
        return {"status": "failed", "error": f"{sensor} calibration failed: {e}", "progress_log": progress_log}
    return _ok(sensor=sensor, progress_log=progress_log, message=f"{sensor} calibration complete")


@mcp.tool()
async def cancel_calibration(ctx: Context) -> dict:
    """Cancel an in-progress calibration."""
    log_tool_call("cancel_calibration")
    drone = await get_drone(ctx)
    if drone is None:
        return dict(CONN_ERROR)
    try:
        await drone.calibration.cancel()
    except Exception as e:
        logger.error(f"cancel_calibration failed: {e}")
        return _fail(f"cancel calibration failed: {e}")
    return _ok(message="calibration cancelled")


_FAILURE_UNITS = (
    "gyro",
    "accel",
    "mag",
    "baro",
    "gps",
    "optical_flow",
    "vio",
    "distance_sensor",
    "airspeed",
    "battery",
    "motor",
    "servo",
    "avoidance",
    "rc_signal",
    "mavlink_signal",
)
_FAILURE_TYPES = ("ok", "off", "stuck", "garbage", "wrong", "slow", "delayed", "intermittent")


@mcp.tool()
async def inject_failure(ctx: Context, unit: str, failure_type: str, instance: int = 0) -> dict:
    """SAFETY-EXPERIMENT tool: inject a simulated sensor/system failure.

    Enables controlled failure experiments (GPS loss, motor failure, ...) for
    the paper's safety evaluation. SIMULATION ONLY.

    FIRMWARE NOTE: requires the autopilot's MAVSDK failure-injection hook. On
    ArduPilot SITL this returns UNSUPPORTED (observed); inject failures there
    via SIM_* parameters instead, e.g. set_parameter("SIM_GPS_DISABLE", 1) or
    "SIM_ENGINE_FAIL". PX4 SITL supports this directly (needs
    SYS_FAILURE_EN=1).

    Args:
        unit (str): sensor/system to fail - one of gyro, accel, mag, baro,
            gps, optical_flow, vio, distance_sensor, airspeed, battery, motor,
            servo, avoidance, rc_signal, mavlink_signal.
        failure_type (str): ok (clear), off, stuck, garbage, wrong, slow,
            delayed, intermittent.
        instance (int): sensor instance (0 = all instances of that unit).

    Returns:
        dict: status.
    """
    from mavsdk.failure import FailureType, FailureUnit

    log_tool_call("inject_failure", unit=unit, failure_type=failure_type, instance=instance)
    unit_map = {
        "gyro": FailureUnit.SENSOR_GYRO,
        "accel": FailureUnit.SENSOR_ACCEL,
        "mag": FailureUnit.SENSOR_MAG,
        "baro": FailureUnit.SENSOR_BARO,
        "gps": FailureUnit.SENSOR_GPS,
        "optical_flow": FailureUnit.SENSOR_OPTICAL_FLOW,
        "vio": FailureUnit.SENSOR_VIO,
        "distance_sensor": FailureUnit.SENSOR_DISTANCE_SENSOR,
        "airspeed": FailureUnit.SENSOR_AIRSPEED,
        "battery": FailureUnit.SYSTEM_BATTERY,
        "motor": FailureUnit.SYSTEM_MOTOR,
        "servo": FailureUnit.SYSTEM_SERVO,
        "avoidance": FailureUnit.SYSTEM_AVOIDANCE,
        "rc_signal": FailureUnit.SYSTEM_RC_SIGNAL,
        "mavlink_signal": FailureUnit.SYSTEM_MAVLINK_SIGNAL,
    }
    type_map = {
        "ok": FailureType.OK,
        "off": FailureType.OFF,
        "stuck": FailureType.STUCK,
        "garbage": FailureType.GARBAGE,
        "wrong": FailureType.WRONG,
        "slow": FailureType.SLOW,
        "delayed": FailureType.DELAYED,
        "intermittent": FailureType.INTERMITTENT,
    }
    unit_val = unit_map.get(str(unit).lower())
    type_val = type_map.get(str(failure_type).lower())
    if unit_val is None:
        return _fail(f"unit must be one of {_FAILURE_UNITS}, got {unit!r}")
    if type_val is None:
        return _fail(f"failure_type must be one of {_FAILURE_TYPES}, got {failure_type!r}")

    drone = await get_drone(ctx)
    if drone is None:
        return dict(CONN_ERROR)
    try:
        log_mavlink_cmd("drone.failure.inject", unit=unit, type=failure_type)
        await drone.failure.inject(unit_val, type_val, int(instance))
    except Exception as e:
        logger.error(f"inject_failure failed: {e}")
        return {
            "status": "failed",
            "error": f"Failure injection failed: {e}",
            "hint": "ArduPilot: use set_parameter with SIM_* (e.g. SIM_GPS_DISABLE=1). "
            "PX4: set SYS_FAILURE_EN=1 first.",
        }
    return _ok(message=f"injected {failure_type} on {unit} (instance {instance})")
