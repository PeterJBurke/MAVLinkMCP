"""Managed (server-executed) mission tools - the long-mission path.

See docs/tool_groups.md. The point of these three tools is that the LLM does
NOT have to stay connected: submit the mission, disconnect, reattach later and
poll status. The server flies and monitors it, and auto-actions fire
server-side with no model in the loop.
"""

from mcp.server.fastmcp import Context

from droneserver.app import mcp
from droneserver.missions.config import get_mission_settings
from droneserver.missions.runner import RUNNER
from droneserver.missions.state import Phase
from droneserver.telemetry.flight_log import log_tool_call, log_tool_output, logger
from droneserver.tools._common import CONN_ERROR, get_drone


def _fail(error: str, **extra) -> dict:
    result = {"status": "failed", "error": error, **extra}
    log_tool_output(result)
    return result


def _status_payload(include_events: bool, event_limit: int) -> dict:
    record = RUNNER.record
    if record is None:
        return {"status": "success", "mission": None, "message": "no mission has been submitted"}
    s = get_mission_settings()
    payload = {
        "status": "success",
        "mission": {
            "mission_id": record.mission_id,
            "phase": record.phase,
            "active": record.active,
            "progress_percent": record.progress_percent(),
            "current_item": record.current_item,
            "total_items": record.total_items,
            "elapsed_s": record.elapsed_s(),
            "created_at": record.created_at,
            "updated_at": record.updated_at,
            "position": record.last_position,
            "battery": record.last_battery,
            "flight_mode": record.last_flight_mode,
            "armed": record.last_armed,
            "error": record.error,
            "auto_actions_fired": record.auto_actions_fired,
            "resumed_after_restart": record.resumed_after_restart,
            "event_count": len(record.events),
        },
        "auto_actions_configured": {
            "enabled": s.auto_actions_enabled,
            "low_battery": f"{s.low_battery_action} below {s.low_battery_threshold * 100:.0f}%",
            "critical_battery": f"land below {s.critical_battery_threshold * 100:.0f}%",
            "geofence_breach": s.geofence_breach_action,
            "link_loss": s.link_loss_action,
        },
    }
    if include_events:
        limit = max(1, min(int(event_limit), 200))
        payload["events"] = record.events[-limit:]
    return payload


@mcp.tool()
async def start_managed_mission(
    ctx: Context,
    waypoints: list,
    takeoff_altitude_m: float = 20.0,
    return_to_launch: bool = True,
) -> dict:
    """Submit a mission for the SERVER to fly and monitor autonomously.

    Returns immediately with a mission_id - it does NOT block for the flight.
    **You may disconnect entirely and reconnect later**; the mission keeps
    running and the server keeps monitoring it, applying auto-actions (low
    battery, geofence breach, link loss) without you. Poll progress with
    get_mission_status, and steer with control_managed_mission.

    The server uploads a takeoff item, your waypoints, and (optionally) an RTL
    item, then arms and starts the mission. The whole mission is validated
    against the server-side geofence BEFORE anything is uploaded.

    Args:
        waypoints (list): [{"latitude_deg": float, "longitude_deg": float,
            "altitude_m": float (above home), "hold_s": float (optional
            loiter time at the waypoint)}], in order.
        takeoff_altitude_m (float): altitude for the initial takeoff item.
        return_to_launch (bool): append an RTL item after the last waypoint
            (default True).

    Returns:
        dict: status, mission_id, and the initial phase.
    """
    log_tool_call(
        "start_managed_mission",
        waypoints=len(waypoints or []),
        takeoff_altitude_m=takeoff_altitude_m,
        return_to_launch=return_to_launch,
    )
    if not waypoints or not isinstance(waypoints, list):
        return _fail("waypoints must be a non-empty list")
    for i, wp in enumerate(waypoints):
        if not isinstance(wp, dict):
            return _fail(f"waypoints[{i}] must be an object with latitude_deg/longitude_deg/altitude_m")
        if wp.get("latitude_deg", wp.get("lat")) is None or wp.get("longitude_deg", wp.get("lon")) is None:
            return _fail(f"waypoints[{i}] is missing latitude_deg/longitude_deg")
    if RUNNER.record is not None and RUNNER.record.active:
        return _fail(
            f"mission {RUNNER.record.mission_id} is still {RUNNER.record.phase}",
            remedy="Wait for it to finish, or abort it with control_managed_mission(action='abort').",
        )

    drone = await get_drone(ctx)
    if drone is None:
        return dict(CONN_ERROR)

    record = RUNNER.start(drone, waypoints, float(takeoff_altitude_m), bool(return_to_launch))
    result = {
        "status": "success",
        "mission_id": record.mission_id,
        "phase": record.phase,
        "waypoint_count": record.waypoint_count,
        "message": "Mission accepted. The server is flying and monitoring it.",
        "next_step": (
            "Poll get_mission_status when you want an update. You may disconnect and "
            "reconnect at any time - the mission continues without you."
        ),
    }
    log_tool_output(result)
    return result


@mcp.tool()
async def get_mission_status(ctx: Context, include_events: bool = True, event_limit: int = 20) -> dict:
    """Get the current managed mission's full status and event history.

    Safe to call at any time, including after reconnecting to a mission that
    was submitted in an earlier session (or before a server restart).

    Args:
        include_events (bool): include the event history (default True).
        event_limit (int): how many of the most recent events to return
            (1-200, default 20).

    Returns:
        dict: phase, progress, position, battery, flight mode, elapsed time,
        auto-actions fired, configured auto-actions, and recent events.
    """
    log_tool_call("get_mission_status", include_events=include_events, event_limit=event_limit)
    result = _status_payload(bool(include_events), int(event_limit))
    log_tool_output({k: v for k, v in result.items() if k != "events"})
    return result


@mcp.tool()
async def control_managed_mission(ctx: Context, action: str) -> dict:
    """Steer the running managed mission.

    Args:
        action (str): "pause" (hold position, mission keeps its place),
            "resume" (continue a paused mission), "abort" (stop the mission and
            land where the drone is), or "clear" (forget a FINISHED mission's
            record so a new one can be submitted).

    Returns:
        dict: status and the resulting phase.
    """
    log_tool_call("control_managed_mission", action=action)
    action = str(action).lower()
    if action not in ("pause", "resume", "abort", "clear"):
        return _fail(f'action must be "pause", "resume", "abort" or "clear", got {action!r}')

    record = RUNNER.record
    if record is None:
        return _fail("no mission has been submitted")

    if action == "clear":
        if record.active:
            return _fail(
                f"mission {record.mission_id} is still {record.phase}",
                remedy="Abort it first with control_managed_mission(action='abort').",
            )
        try:
            RUNNER.store(get_mission_settings()).clear()
        except Exception as e:
            logger.warning(f"could not clear mission checkpoint: {e}")
        RUNNER.record = None
        return {"status": "success", "message": f"cleared finished mission {record.mission_id}"}

    if not record.active:
        return _fail(f"mission {record.mission_id} already finished ({record.phase})")

    if action == "pause":
        if record.phase_enum is not Phase.RUNNING:
            return _fail(f"can only pause a running mission (currently {record.phase})")
        RUNNER.request_pause()
    elif action == "resume":
        if record.phase_enum is not Phase.PAUSED:
            return _fail(f"can only resume a paused mission (currently {record.phase})")
        RUNNER.request_resume()
    else:
        RUNNER.request_abort()

    result = {
        "status": "success",
        "mission_id": record.mission_id,
        "requested": action,
        "phase": record.phase,
        "note": "The request is applied by the mission monitor within a poll interval; "
        "call get_mission_status to confirm.",
    }
    log_tool_output(result)
    return result
