"""Emergency stop (tier EMERGENCY - see docs/estop.md).

Deliberately NOT confirmation-gated and exempt from rate limiting: requiring a
token round-trip during an emergency would be a safety hazard. It still
requires control scope and is always audited.
"""

from mcp.server.fastmcp import Context

from droneserver.app import mcp
from droneserver.telemetry.flight_log import log_mavlink_cmd, log_tool_call, log_tool_output, logger
from droneserver.tools._common import CONN_ERROR, get_drone


@mcp.tool()
async def emergency_stop(ctx: Context, mode: str = "land") -> dict:
    """EMERGENCY: stop the current operation immediately.

    Choose the least destructive mode that resolves the situation:

    - ``land`` (DEFAULT, SAFEST): stop offboard control and land where the
      drone is now. Use for "stop!", "abort", "something is wrong".
    - ``rtl``: stop and fly home, then land. Use when the area below is
      unsafe to land in but the vehicle is still healthy.
    - ``kill``: CUT MOTORS INSTANTLY. **The drone falls out of the sky and
      will likely be destroyed.** Only for a genuine emergency where a
      falling drone is safer than a flying one (e.g. flyaway toward people).

    This tool is exempt from the confirmation round-trip so it always works in
    an emergency. It does NOT replace the out-of-band chain (RC takeover / GCS
    / kill switch) - see docs/estop.md.

    Args:
        mode (str): "land" (default), "rtl", or "kill".

    Returns:
        dict: status and the actions taken.
    """
    log_tool_call("emergency_stop", mode=mode)
    mode = str(mode).lower()
    if mode not in ("land", "rtl", "kill"):
        return {"status": "failed", "error": f'mode must be "land", "rtl" or "kill", got {mode!r}'}

    drone = await get_drone(ctx)
    if drone is None:
        return dict(CONN_ERROR)

    actions: list[str] = []

    # Always stop offboard streaming first - a live setpoint would otherwise
    # keep commanding the vehicle while we try to stop it.
    if mode in ("land", "rtl"):
        try:
            from droneserver.tools.offboard import _watchdog

            _watchdog.cancel()
            if await drone.offboard.is_active():
                await drone.offboard.stop()
                actions.append("offboard stopped")
        except Exception as e:
            logger.warning(f"emergency_stop: could not stop offboard: {e}")
            actions.append(f"offboard stop failed: {e}")

    try:
        if mode == "land":
            log_mavlink_cmd("drone.action.land")
            await drone.action.land()
            actions.append("landing at current position")
        elif mode == "rtl":
            log_mavlink_cmd("drone.action.return_to_launch")
            await drone.action.return_to_launch()
            actions.append("returning to launch")
        else:
            logger.error("emergency_stop: KILLING MOTORS")
            log_mavlink_cmd("drone.action.kill")
            await drone.action.kill()
            actions.append("MOTORS KILLED - vehicle is falling")
    except Exception as e:
        logger.error(f"emergency_stop({mode}) failed: {e}")
        result = {
            "status": "failed",
            "error": f"emergency stop ({mode}) failed: {e}",
            "actions_taken": actions,
            "escalate": "Use the out-of-band chain NOW: RC mode switch / GCS / kill switch. See docs/estop.md.",
        }
        log_tool_output(result)
        return result

    result = {
        "status": "success",
        "mode": mode,
        "actions_taken": actions,
        "next_step": "Monitor with get_landed_state / get_armed until the drone is down and disarmed.",
        "out_of_band": "Human override remains available at all times - see docs/estop.md.",
    }
    log_tool_output(result)
    return result
