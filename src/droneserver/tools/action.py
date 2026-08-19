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
from droneserver.telemetry.home import read_home

#: How far from a commanded target the aircraft may touch down and still be
#: reported as having completed that flight. Wide enough for the drift of an
#: ordinary auto-landing, far too small to swallow a return that never
#: happened: the T6 phantom completions were 1.2-1.5 km out.
COMPLETION_RADIUS_M = 20.0


def _target_of(connector) -> dict | None:
    """The destination the aircraft was last told to fly to, if any.

    ``pending_destination`` is cleared the moment the aircraft arrives, so a
    poll made after a landing has nothing to compare against; ``last_movement``
    keeps the destination for the whole flight. Both carry the same keys
    (``latitude``, ``longitude``, ``label``).
    """
    pending = getattr(connector, "pending_destination", None)
    if pending:
        return pending
    movement = getattr(connector, "last_movement", None) or {}
    return movement.get("target")


def _monotonic_s() -> float:
    """The event loop's monotonic clock, in seconds.

    One named entry point for every elapsed-time measurement in this module, so
    a timeout can be exercised in a unit test without spending its budget in
    real seconds (the landing loop below has a 120 s one).
    """
    return asyncio.get_event_loop().time()


def _note_movement(connector, tool: str, target: dict | None) -> None:
    """Record the movement command that was just accepted, and where it aimed."""
    connector.last_movement = {
        "tool": tool,
        "target": target,
        "commanded_at": _monotonic_s(),
    }


#: Bound on any single telemetry read added by the honesty checks. ArduPilot
#: does not publish every topic unless asked, and an unbounded ``async for`` on
#: a silent topic waits forever - which would turn a safety check into a hang.
#: A read that times out reports "unknown", and unknown never blocks a command.
_READ_TIMEOUT_S = 5.0


async def _first(stream, timeout_s: float = _READ_TIMEOUT_S):
    """The first item of a MAVSDK subscription, or ``TimeoutError``."""

    async def read():
        async for item in stream:
            return item
        raise TimeoutError("stream ended without an item")

    return await asyncio.wait_for(read(), timeout=timeout_s)


async def _read_ground_state(drone) -> tuple[bool | None, bool | None]:
    """``(armed, in_air)`` as the autopilot reports them now; ``None`` if unreadable.

    Unreadable is not "on the ground": every honesty check below acts only on a
    state it actually read, so a telemetry hiccup can never turn a real command
    into a refusal or a no-op.
    """
    armed = in_air = None
    try:
        armed = bool(await _first(drone.telemetry.armed()))
    except Exception as e:
        logger.warning(f"could not read armed state: {e}")
    try:
        in_air = bool(await _first(drone.telemetry.in_air()))
    except Exception as e:
        logger.warning(f"could not read in_air state: {e}")
    return armed, in_air


async def _telemetry_now(drone) -> dict:
    """One live reading of everything monitor_flight reports.

    Every field is read fresh on every call. The frozen
    ``🛬 LANDING | Alt: 50.0m | Descending...`` that eleven to twenty-four
    consecutive polls handed the T6 models (audit mechanism M2) carried no
    position, no distance and no vertical speed, so three models could not see
    that the aircraft was climbing away on a perfectly healthy return, decided
    the return had stalled, and force-landed a kilometre short.
    """
    reading: dict = {
        "landed_state": None,
        "in_air": None,
        "latitude_deg": None,
        "longitude_deg": None,
        "relative_altitude_m": None,
        "absolute_altitude_m": None,
        "ground_speed_m_s": None,
        "vertical_speed_m_s": None,
        "flight_mode": None,
    }
    async for landed_state in drone.telemetry.landed_state():
        reading["landed_state"] = str(landed_state).split(".")[-1]
        break
    async for in_air in drone.telemetry.in_air():
        reading["in_air"] = bool(in_air)
        break
    async for position in drone.telemetry.position():
        reading["latitude_deg"] = position.latitude_deg
        reading["longitude_deg"] = position.longitude_deg
        reading["relative_altitude_m"] = position.relative_altitude_m
        reading["absolute_altitude_m"] = getattr(position, "absolute_altitude_m", None)
        break
    # Both are extras: they sharpen the report but no phase depends on them, so
    # a firmware that does not publish them costs a bounded wait, not an answer.
    try:
        velocity = await _first(drone.telemetry.velocity_ned(), 2.0)
        reading["ground_speed_m_s"] = math.sqrt(velocity.north_m_s**2 + velocity.east_m_s**2)
        # NED: down is positive, so a climb is a negative down rate.
        reading["vertical_speed_m_s"] = -float(velocity.down_m_s)
    except Exception:
        pass
    try:
        reading["flight_mode"] = str(await _first(drone.telemetry.flight_mode(), 2.0)).split(".")[-1]
    except Exception:
        pass
    return reading


def _height_above_launch_m(connector, reading: dict) -> float | None:
    """Height above THIS SESSION's launch point, or ``None`` if unmeasurable.

    ``relative_altitude_m`` is measured from a datum the autopilot MOVES:
    ArduPilot re-zeroes it wherever the aircraft last ARMED. After a mission
    that lands away from the launch field, re-arms there and flies home, the
    parked aircraft on the launch field reports a persistent offset (+4.1 m
    observed, 8 independent SITL lanes, 2026-08-19) - the terrain difference
    between the two arming points, frozen into every subsequent reading.

    Absolute altitude does not move. Where the session recorded the elevation
    it started from (:attr:`MAVLinkConnector.session_launch`, FIX 8a) this
    measures against that instead; where it did not, it falls back to the
    relative reading and the old behaviour is unchanged. Same correction the
    scorer got in FIX 8b (``verdicts.height_above_launch_m``) - this is the
    operational half.

    NOTE what this is NOT: it is not height above the ground under the
    aircraft. Over terrain that differs from the launch field it reads the
    terrain difference even when parked, so it must never be the sole evidence
    that an aircraft has touched down - see :func:`_settled_on_ground`.
    """
    launch = getattr(connector, "session_launch", None) or {}
    launch_amsl = launch.get("absolute_altitude_m")
    absolute = reading.get("absolute_altitude_m")
    if launch_amsl is not None and absolute is not None:
        return absolute - launch_amsl
    return reading.get("relative_altitude_m")


def _ground_evidence(reading: dict) -> bool | None:
    """Is the aircraft on the ground, per the AUTOPILOT? ``None`` if it won't say.

    This is the datum-free half of the landing question. ``landed_state`` and
    ``in_air`` are the autopilot's own assessment - weight-on-skids, descent
    rate, throttle - and no re-arming anywhere moves them. Altitude is only
    consulted by callers when this returns ``None``.
    """
    landed_state = reading.get("landed_state")
    if landed_state == "ON_GROUND":
        return True
    if landed_state in ("IN_AIR", "TAKING_OFF", "LANDING"):
        return False
    in_air = reading.get("in_air")
    if in_air is not None:
        return not in_air
    return None


def _settled_on_ground(landed_state_str: str | None, is_in_air, vertical_speed_m_s: float | None) -> bool:
    """Touchdown, from evidence that no moving altitude datum can spoil.

    The autopilot says ON_GROUND and not in the air, and - where the rate is
    readable at all - the aircraft is not still moving vertically. There is
    deliberately NO altitude term: requiring ``relative_altitude_m < 2.0`` is
    what made every T6-shape landing (land away, re-arm, fly home) run to the
    120 s ``landing_timeout``, because the re-armed datum left the parked
    aircraft reading +4.1 m and the threshold was unreachable.

    The rate tolerance is generous on purpose: it is here to veto an autopilot
    that claims ON_GROUND in the middle of a 3 m/s descent, not to second-guess
    a settled aircraft's noise floor. An unreadable rate vetoes nothing.
    """
    if landed_state_str != "ON_GROUND":
        return False
    if is_in_air:
        return False
    if vertical_speed_m_s is not None and abs(vertical_speed_m_s) > 1.0:
        return False
    return True


def _observables(connector, reading: dict) -> dict:
    """The where-am-I fields that go into EVERY monitor_flight answer.

    Position, altitude and distances travel with every phase, including the
    landing phases that used to report an altitude and nothing else.
    """
    lat, lon = reading.get("latitude_deg"), reading.get("longitude_deg")
    height = _height_above_launch_m(connector, reading)
    fields: dict = {
        "position": None if lat is None or lon is None else {"latitude_deg": lat, "longitude_deg": lon},
        # The autopilot's own relative reading, kept for continuity - it is
        # measured from wherever the aircraft last ARMED, so it can be metres
        # off after a mission that re-armed away from the launch field.
        "altitude_m": None if reading.get("relative_altitude_m") is None else round(reading["relative_altitude_m"], 1),
        # The same height measured against a datum that cannot move under the
        # aircraft: this session's launch elevation. Read this one.
        "height_above_launch_m": None if height is None else round(height, 1),
        "absolute_altitude_m": (
            None if reading.get("absolute_altitude_m") is None else round(reading["absolute_altitude_m"], 1)
        ),
        "vertical_speed_m_s": (
            None if reading.get("vertical_speed_m_s") is None else round(reading["vertical_speed_m_s"], 1)
        ),
        "ground_speed_m_s": (
            None if reading.get("ground_speed_m_s") is None else round(reading["ground_speed_m_s"], 1)
        ),
        "flight_mode": reading.get("flight_mode"),
        "target": None,
        "distance_to_target_m": None,
        "distance_from_launch_point_m": None,
    }
    if lat is None or lon is None:
        return fields
    target = _target_of(connector)
    if target:
        fields["target"] = {
            "latitude_deg": target["latitude"],
            "longitude_deg": target["longitude"],
            "label": target.get("label", "the commanded destination"),
        }
        fields["distance_to_target_m"] = round(haversine_distance(lat, lon, target["latitude"], target["longitude"]), 1)
    launch = getattr(connector, "session_launch", None)
    if launch:
        fields["distance_from_launch_point_m"] = round(
            haversine_distance(lat, lon, launch["latitude_deg"], launch["longitude_deg"]), 1
        )
    return fields


def _where_text(fields: dict) -> str:
    """The live half of a DISPLAY_TO_USER line: distance, position, rate."""
    parts: list[str] = []
    if fields["distance_to_target_m"] is not None:
        label = (fields["target"] or {}).get("label", "target")
        parts.append(f"{fields['distance_to_target_m']:.0f}m from {label}")
    if fields["distance_from_launch_point_m"] is not None:
        parts.append(f"{fields['distance_from_launch_point_m']:.0f}m from launch point")
    if fields["vertical_speed_m_s"] is not None:
        parts.append(f"{fields['vertical_speed_m_s']:+.1f}m/s vertical")
    if fields["position"]:
        parts.append(f"at {fields['position']['latitude_deg']:.6f},{fields['position']['longitude_deg']:.6f}")
    return " | ".join(parts)


def _vertical_verb(fields: dict) -> str:
    """What the aircraft is ACTUALLY doing vertically, from telemetry.

    The old landing text said "Descending" unconditionally. It said it sixteen
    times while a returning aircraft climbed from 34.5 m to 50 m.
    """
    rate = fields.get("vertical_speed_m_s")
    if rate is None:
        return "Landing in progress"
    if rate < -0.3:
        return "Descending"
    if rate > 0.3:
        return "CLIMBING (not descending)"
    return "Holding altitude"


# ARM
@mcp.tool()
async def arm_drone(ctx: Context, force: bool = False) -> dict:
    """Arm the drone. Waits for drone connection if not yet ready.

    Args:
        force (bool): if True, force-arm WITHOUT prearm safety checks
            (DANGEROUS - bypasses sensor/EKF checks; use only when you
            understand why the checks fail). Default False.
    """
    log_tool_call("arm_drone", force=force)
    connector = ctx.request_context.lifespan_context

    # Wait for connection
    if not await ensure_connection(connector):
        return {"status": "failed", "error": "Drone connection timeout. Please wait and try again."}

    drone = connector.drone
    # Arming begins a fresh flight. Clear any per-flight latches that a previous
    # trial may have left set (landing_in_progress in particular), so this
    # flight's monitor_flight is not driven by the last flight's landing state.
    # The between-trial ferry arms once per trial, so this fires every trial.
    connector.reset_flight_latches()
    try:
        if force:
            log_mavlink_cmd("drone.action.arm_force")
            await drone.action.arm_force()
            return {"status": "success", "message": "Drone FORCE-armed (prearm checks bypassed)"}
        log_mavlink_cmd("drone.action.arm")
        await drone.action.arm()
    except Exception as e:
        logger.error(f"arm failed: {e}")
        return {"status": "failed", "error": f"Arming failed: {e}"}
    return {"status": "success", "message": "Drone armed"}


@mcp.tool()
async def move_to_relative(ctx: Context, north_m: float, east_m: float, down_m: float, yaw_deg: float = 0.0) -> dict:
    """
    Move the drone relative to the current position using ArduPilot's GUIDED mode.

    IMPORTANT: This function RETURNS IMMEDIATELY, as soon as the command has been
    accepted. It does NOT wait for the drone to arrive. When it returns, the drone
    has barely started moving and is still in flight toward the target.

    To find out when the drone has actually arrived, poll check_arrival() with the
    target coordinates until it reports "arrived", or use monitor_flight(). Do not
    issue the next navigation command - and in particular do not command a landing
    or a return-to-launch - until arrival has been confirmed, or the drone will
    abandon this move part-way through.

    (Contrast takeoff(), which by default DOES block until the target altitude is
    reached.)

    ArduPilot automatically enters GUIDED mode when receiving goto_location commands
    (as long as the drone is armed). No manual mode switching required.

    The drone must be armed and in the air. Waits for a drone CONNECTION if one is
    not yet established - that wait is about the link, not about the flight.

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
        # Both relative readings below are log text only: the command that is
        # actually sent (goto_location) carries target_alt, an ABSOLUTE MSL
        # altitude derived from absolute_altitude_m, so a moved relative datum
        # cannot move the aircraft.
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
        _note_movement(
            connector,
            "move_to_relative",
            {"latitude": target_lat, "longitude": target_lon, "label": "the offset target"},
        )
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

    # Wait for drone to reach target altitude.
    #
    # relative_altitude_m is the RIGHT datum here, and the only right one: the
    # takeoff command itself is expressed relative to the point the aircraft
    # just armed at, which is exactly the datum the autopilot re-zeroes on that
    # arm. Command and measurement therefore share a datum, and a session
    # launch elevation from somewhere else would compare a climb against ground
    # the aircraft is not standing on. Left as-is deliberately.
    logger.info(f"Waiting for drone to reach {takeoff_altitude}m...")
    altitude_threshold = 0.5  # Consider arrived when within 0.5m of target
    max_wait_time = 60  # Maximum wait time in seconds
    check_interval = 1.0  # Check every second
    elapsed_time = 0.0

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
    except Exception:
        current_alt = 0.0

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

    On an aircraft that is already on the ground with its motors disarmed this
    returns ``status: "no_action"`` rather than "Landing initiated": there is no
    landing to initiate, and reporting one would be a false tool result. Unlike
    return_to_launch this is never refused - land is the abort path, and an
    abort path that can be rejected is not one.

    Args:
        ctx (Context): The context of the request.
        force (bool): If True, bypass landing gate safety check (default: False).

    Returns:
        dict: Status message with success, no_action, blocked, or error.
    """
    log_tool_call("land", force=force)
    connector = ctx.request_context.lifespan_context

    # Wait for connection
    if not await ensure_connection(connector):
        return {"status": "failed", "error": "Drone connection timeout. Please wait and try again."}

    drone = connector.drone

    # Honesty gate: a landing command to a parked, disarmed aircraft lands
    # nothing. Only acted on when both facts were actually read - an unreadable
    # link falls through and the landing is commanded as before.
    armed, in_air = await _read_ground_state(drone)
    if armed is False and in_air is False:
        result = {
            "status": "no_action",
            "message": "No landing was commanded: the aircraft is already on the ground with its motors disarmed.",
            "armed": False,
            "in_air": False,
            "next_step": (
                "Nothing is flying. If you expected the aircraft to be somewhere else, read get_position "
                "and compare it with the coordinate you meant to reach - do not treat this call as a landing."
            ),
        }
        log_tool_output(result)
        return result

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
    # A landing has no destination of its own: it puts the aircraft down where
    # it is. Recorded so monitor_flight can still say where that turned out to
    # be relative to the last place the aircraft was told to go.
    _note_movement(connector, "land", _target_of(connector))

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
        except Exception:
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

    HOME IS NOT NECESSARILY YOUR LAUNCH POINT. The autopilot moves its home to
    wherever the aircraft last armed, so an aircraft that armed at a destination
    will "return" to that destination. The answer names the coordinate this RTL
    will actually fly to, and warns when it differs from where this session
    started.

    A return commanded to an aircraft that is on the ground with its motors
    disarmed is refused by the safety layer (``precondition.rtl_requires_airborne``):
    it would fly nothing, and reporting a return that cannot happen is how eight
    T6 trials came to claim a completed return from 1.2 km away.

    Args:
        ctx (Context): The context of the request.

    Returns:
        dict: Status message with success or error, plus the destination this
            return will fly to and any home/launch-point disagreement.
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
    except Exception as e:
        logger.error(f"{LogColors.ERROR}❌ TOOL ERROR - RTL failed: {e}{LogColors.RESET}")
        return {"status": "failed", "error": f"Return to Launch failed: {str(e)}"}

    result = {
        "status": "success",
        "message": "Return to Launch initiated - the aircraft is flying to the autopilot's home position",
    }
    # Register the destination this return is actually flying to, so
    # monitor_flight can report the distance closing (an RTL used to be
    # invisible to it) and so a landing can be checked against it.
    home = None
    try:
        home = await read_home(drone, 10.0)
    except Exception as e:
        logger.warning(f"RTL commanded but the autopilot's home could not be read: {e}")
    if home is not None:
        target = {
            "latitude": home.latitude_deg,
            "longitude": home.longitude_deg,
            "label": "the autopilot's home",
        }
        result["destination"] = {
            "latitude_deg": home.latitude_deg,
            "longitude_deg": home.longitude_deg,
            "absolute_altitude_m": home.absolute_altitude_m,
            "note": "this is where RTL will fly - the autopilot's home, which moves to wherever it last armed",
        }
        connector.pending_destination = {
            **target,
            "altitude_msl": home.absolute_altitude_m,
            "initial_distance": 0.0,
            "start_time": _monotonic_s(),
            "source": "return_to_launch",
        }
        try:
            async for position in drone.telemetry.position():
                connector.pending_destination["initial_distance"] = haversine_distance(
                    position.latitude_deg, position.longitude_deg, home.latitude_deg, home.longitude_deg
                )
                break
        except Exception:
            pass
        _note_movement(connector, "return_to_launch", target)
        launch = getattr(connector, "session_launch", None)
        if launch:
            drift = haversine_distance(
                home.latitude_deg, home.longitude_deg, launch["latitude_deg"], launch["longitude_deg"]
            )
            result["distance_from_session_launch_point_m"] = round(drift, 1)
            if drift > COMPLETION_RADIUS_M:
                result["warning"] = (
                    f"the autopilot's home is {drift:.0f} m from where this session started "
                    f"({launch['latitude_deg']:.6f},{launch['longitude_deg']:.6f}) - this return will fly to "
                    f"the home coordinate above, NOT to the launch point. If you want the launch point, "
                    f"fly there with go_to_location instead."
                )
    else:
        _note_movement(connector, "return_to_launch", None)
        result["destination"] = None
        result["warning"] = (
            "the autopilot's home could not be read, so the coordinate this return is flying to is unknown; "
            "verify with get_position where the aircraft actually ends up"
        )
    log_tool_output(result)
    return result


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
            # Reported, never compared: the hold is commanded at the current
            # ABSOLUTE altitude, so these two relative numbers are description
            # only and no threshold depends on them. They are the autopilot's
            # own datum and can be metres off after a re-arm elsewhere.
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
        # Get current position to calculate relative altitude and initial distance.
        #
        # home_alt is the elevation of the autopilot's CURRENT altitude datum -
        # the point the aircraft last armed at, not necessarily this session's
        # launch field. relative_alt is therefore the target expressed in the
        # same frame an ArduPilot relative-altitude command would use, which is
        # the honest thing to print alongside the MSL figure. Nothing decides
        # anything on it: the command itself flies to absolute_altitude_m.
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
        except Exception:
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
            "label": "the commanded destination",
            "altitude_msl": absolute_altitude_m,
            "initial_distance": initial_distance,
            "start_time": _monotonic_s(),
            "source": "go_to_location",
        }
        _note_movement(
            connector,
            "go_to_location",
            {"latitude": latitude_deg, "longitude": longitude_deg, "label": "the commanded destination"},
        )

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
        # Get current position (instant - no waiting). Arrival here is purely
        # horizontal: current_alt is carried into the answer as description and
        # no threshold in this tool reads it, so the movable relative datum
        # cannot decide whether the aircraft has arrived.
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

    WHAT mission_complete MEANS. It means the flight this tool was watching has
    ended with the aircraft on the ground where it was sent. It is NOT a verdict
    on your task: a task with two legs is not finished when the first leg is.

    Two ways it stays false on a landed aircraft:

    - `status: "not_started"` - the drone is on the ground and has NOT been
      airborne, so there is no flight to monitor. Do not keep polling; get it
      airborne first.
    - `status: "landed_away_from_target"` - the aircraft is on the ground, but
      not at the place it was last told to fly to. The field
      `landed_away_from_target` names the distance. The commanded flight did
      not happen, or did not finish.

    Every answer, in every phase, carries the live position, altitude, vertical
    speed, distance to the current target and distance from this session's
    launch point. Read those rather than the status word.

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
        reading = await _telemetry_now(drone)
        landed_state_str = reading["landed_state"]
        current_lat = reading["latitude_deg"]
        current_lon = reading["longitude_deg"]
        current_alt = reading["relative_altitude_m"] if reading["relative_altitude_m"] is not None else 0.0
        live = _observables(connector, reading)
        where = _where_text(live)
        # Both questions below - "has it flown?" and "is it down?" - used to be
        # answered partly from relative_altitude_m, whose datum the autopilot
        # re-zeroes at every arm. A parked aircraft carrying a +4.1 m offset
        # then looks airborne. Ask the autopilot first; fall back to a height
        # measured from the session's launch elevation only when it won't say.
        grounded = _ground_evidence(reading)
        height = live["height_above_launch_m"]

        # Evidence latch: "on the ground" only means "landed" if there was a
        # flight to land from. Without it, the first monitor_flight() call of a
        # trial whose takeoff never happened answers "MISSION COMPLETE".
        if grounded is False or (grounded is None and height is not None and height >= 1.0):
            connector.was_airborne = True

        on_the_ground = grounded is True or (grounded is None and (height is None or height < 1.0))
        if on_the_ground and not connector.was_airborne:
            flew_before = getattr(connector, "last_movement", None) is not None
            headline = (
                "no flight is under way (the previous one has ended)" if flew_before else "the drone has not flown yet"
            )
            result = {
                "DISPLAY_TO_USER": f"⚠️ ON THE GROUND | Alt: {current_alt:.1f}m | {headline}"
                + (f" | {where}" if where else ""),
                "status": "not_started",
                **live,
                "action_required": (
                    "The drone is on the ground and nothing is flying, so there is nothing to monitor. "
                    "This is NOT a completed mission. If the aircraft still has somewhere to be, arm and "
                    "take off (or start the mission), then call monitor_flight() again."
                ),
                "mission_complete": False,
            }
            log_tool_output(result)
            return result

        # On the ground after a flight. Being on the ground is not, on its own,
        # the flight having succeeded: it is also what an aircraft parked at the
        # wrong end of an unflown return looks like. Eight T6 trials were told
        # "MISSION COMPLETE - Drone has landed safely!" while standing 1.2-1.5 km
        # from the launch point after an RTL that flew nothing. So completion
        # asks the second question too: is the aircraft where it was sent?
        if on_the_ground:
            target = _target_of(connector)
            missed_by = live["distance_to_target_m"]
            connector.landing_in_progress = False
            if target and missed_by is not None and missed_by > max(arrival_threshold_m, COMPLETION_RADIUS_M):
                label = live["target"]["label"]
                logger.warning(
                    f"{LogColors.ERROR}⚠️ ON THE GROUND {missed_by:.0f}m from {label} - "
                    f"the commanded flight did not complete{LogColors.RESET}"
                )
                get_flight_logger().log_entry("LANDED_AWAY", f"{missed_by:.0f}m from {label}")
                result = {
                    "DISPLAY_TO_USER": (
                        f"⚠️ ON THE GROUND, NOT AT THE TARGET | {missed_by:.0f}m from {label}"
                        + (f" | {where}" if where else "")
                    ),
                    "status": "landed_away_from_target",
                    **live,
                    "landed_away_from_target": {
                        "distance_m": missed_by,
                        "target": live["target"],
                        "commanded_by": (getattr(connector, "last_movement", None) or {}).get("tool"),
                    },
                    "action_required": (
                        f"The aircraft is on the ground {missed_by:.0f} m from {label}, so the flight you "
                        f"commanded did not happen or did not finish. Do NOT report it as completed. Check "
                        f"get_position and get_armed, then fly the remaining leg (arm, takeoff, "
                        f"go_to_location) if you still want to reach it."
                    ),
                    "mission_complete": False,
                }
                log_tool_output(result)
                return result

            connector.was_airborne = False
            connector.pending_destination = None
            logger.info(f"{LogColors.SUCCESS}✅ LANDED - the monitored flight has ended{LogColors.RESET}")
            get_flight_logger().log_entry("LANDED", "Monitored flight ended on the ground")

            result = {
                "DISPLAY_TO_USER": ("✅ LANDED | this monitored flight has ended" + (f" | {where}" if where else "")),
                "status": "landed",
                **live,
                "action_required": (
                    "This flight is over. It is not necessarily your whole task: if the task has another "
                    "leg, fly it. Check the position above against where the task asked the aircraft to end up."
                ),
                "mission_complete": True,
            }
            log_tool_output(result)
            return result

        # Airborne. Landing phase, either as the autopilot reports it or as this
        # server latched it. Both used to answer with an altitude and the word
        # "Descending" and nothing else - for as many as 24 consecutive polls,
        # while the aircraft was climbing away on an RTL. Every field below is
        # re-read from telemetry on every call.
        if landed_state_str == "LANDING" or connector.landing_in_progress:
            verb = _vertical_verb(live)
            logger.info(f"🛬 {verb}... altitude: {current_alt:.1f}m {where}")
            result = {
                "DISPLAY_TO_USER": f"🛬 LANDING PHASE | Alt: {current_alt:.1f}m | {verb}"
                + (f" | {where}" if where else ""),
                "status": "landing",
                **live,
                "action_required": "SHOW the DISPLAY_TO_USER to user, then CALL monitor_flight() AGAIN",
                "mission_complete": False,
            }
            log_tool_output(result)
            return result

        # Check if there's a pending destination (still navigating)
        if not connector.pending_destination:
            # No destination and not landing - drone is just hovering
            result = {
                "DISPLAY_TO_USER": f"🚁 HOVERING | Alt: {current_alt:.1f}m | No destination set"
                + (f" | {where}" if where else ""),
                "status": "hovering",
                **live,
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
        start_time = dest.get("start_time", _monotonic_s())

        logger.info(f"Monitoring flight for {wait_seconds}s...")

        check_interval = 1.0  # Check every second for arrival detection
        elapsed_in_monitor = 0.0

        while elapsed_in_monitor < wait_seconds:
            # Get current position. current_alt here is the autopilot's raw
            # relative reading and is used for the log line and the DISPLAY
            # string ONLY - arrival is decided by horizontal distance, and the
            # datum-free height_above_launch_m travels in the observables of
            # every answer this function returns.
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
            except Exception:
                ground_speed = 0.0

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

                total_flight_time = _monotonic_s() - start_time

                if auto_land:
                    # Automatically initiate landing and WAIT for it to complete
                    logger.info(
                        f"{LogColors.MAVLINK}🛬 Auto-landing initiated - waiting for touchdown{LogColors.RESET}"
                    )
                    get_flight_logger().log_entry("AUTO_LAND", "Landing initiated automatically")
                    connector.landing_in_progress = True
                    await drone.action.land()

                    # Wait for landing to complete. The 120 s cap stays as the
                    # backstop it always was - but it must be reachable only by
                    # a landing that genuinely did not finish, not by a landing
                    # the confirmation could not recognise.
                    landing_timeout = 120
                    landing_start = _monotonic_s()

                    while (_monotonic_s() - landing_start) < landing_timeout:
                        # One reading of everything: landed_state and in_air are
                        # what decides touchdown, the rest is for the log line
                        # and the answer. Read together so they describe the
                        # same instant.
                        touchdown_reading = await _telemetry_now(drone)
                        landed_state_str = touchdown_reading["landed_state"]
                        is_in_air = bool(touchdown_reading["in_air"])
                        current_alt = touchdown_reading["relative_altitude_m"]
                        if current_alt is None:
                            current_alt = 0.0
                        height_now = _height_above_launch_m(connector, touchdown_reading)

                        # THE DATUM BUG (fixed 2026-08-19). This gate used to be
                        # `... and current_alt < 2.0`. ArduPilot re-zeroes the
                        # relative-altitude datum wherever the aircraft last
                        # ARMED, so a mission that lands away, re-arms and flies
                        # home leaves the parked aircraft reading a fixed offset
                        # (+4.1 m across 8 SITL lanes). The threshold was then
                        # unreachable, EVERY such landing ran the full 120 s and
                        # completion was never confirmed - the operational twin
                        # of the scorer defect fixed in 33de5ec (FIX 8b).
                        # Touchdown is now the autopilot's own on-ground
                        # evidence, held across the 3 s stability re-check.
                        if _settled_on_ground(landed_state_str, is_in_air, touchdown_reading["vertical_speed_m_s"]):
                            # Wait 3 more seconds to confirm stable on ground
                            logger.info("🛬 Touchdown detected, confirming stable...")
                            await asyncio.sleep(3)

                            # Re-check to confirm
                            confirm = await _telemetry_now(drone)
                            landed_state_str = confirm["landed_state"]
                            is_in_air = bool(confirm["in_air"])

                            if _settled_on_ground(landed_state_str, is_in_air, confirm["vertical_speed_m_s"]):
                                # Confirmed landed!
                                connector.landing_in_progress = False
                                # The flight this call was watching is over, and
                                # the next one must earn its own completion: the
                                # was_airborne latch left set here is what let a
                                # LATER poll answer "MISSION COMPLETE - Drone has
                                # landed safely!" on a parked aircraft that had
                                # been commanded home and never moved.
                                connector.was_airborne = False
                                total_flight_time = _monotonic_s() - start_time

                                logger.info(f"{LogColors.SUCCESS}✅ LANDED at the destination.{LogColors.RESET}")
                                get_flight_logger().log_entry("LANDED", "Arrived and landed at the destination")

                                live = _observables(connector, await _telemetry_now(drone))
                                where = _where_text(live)
                                result = {
                                    "DISPLAY_TO_USER": (
                                        f"✅ LANDED AT THE COMMANDED DESTINATION | Flight time: "
                                        f"{total_flight_time:.0f}s" + (f" | {where}" if where else "")
                                    ),
                                    "status": "landed",
                                    **live,
                                    "flight_time_seconds": round(total_flight_time, 0),
                                    "action_required": (
                                        "This leg is finished. If your task also asks the aircraft to go "
                                        "somewhere else afterwards, that has NOT happened yet - the aircraft "
                                        "is at the position above, disarming or disarmed."
                                    ),
                                    "mission_complete": True,
                                }
                                log_tool_output(result)
                                return result

                        # Both numbers, so a landing that does time out shows
                        # WHICH datum disagreed instead of leaving the mystery
                        # the T6 audit could not close (§9 item 1).
                        logger.info(
                            f"🛬 Landing... altitude: {current_alt:.1f}m (rel) / "
                            f"{'?' if height_now is None else format(height_now, '.1f')}m above launch, "
                            f"state: {landed_state_str}, in_air: {is_in_air}"
                        )
                        await asyncio.sleep(2)  # Check every 2 seconds

                    # Timeout - return landing status. Reaching here now means
                    # the aircraft never reported itself down, not that its
                    # altitude datum had moved.
                    live = _observables(connector, await _telemetry_now(drone))
                    where = _where_text(live)
                    result = {
                        "DISPLAY_TO_USER": f"⚠️ LANDING TIMEOUT | Alt: {current_alt:.1f}m | Check drone status"
                        + (f" | {where}" if where else ""),
                        "status": "landing_timeout",
                        **live,
                        "mission_complete": False,
                    }
                    log_tool_output(result)
                    return result
                else:
                    # Manual landing required (auto_land=False)
                    live = _observables(connector, await _telemetry_now(drone))
                    where = _where_text(live)
                    result = {
                        "DISPLAY_TO_USER": f"✅ ARRIVED | Distance: {distance:.1f}m | Alt: {current_alt:.1f}m | Call land() to land"
                        + (f" | {where}" if where else ""),
                        "status": "arrived",
                        "distance_m": round(distance, 1),
                        **live,
                        "mission_complete": False,
                    }
                    log_tool_output(result)
                    return result

            # Wait before next check
            await asyncio.sleep(check_interval)
            elapsed_in_monitor += check_interval

        # Monitoring period ended, still in progress
        # Format ETA nicely
        if eta_seconds:
            if eta_seconds > 60:
                eta_str = f"{int(eta_seconds // 60)}m {int(eta_seconds % 60)}s"
            else:
                eta_str = f"{int(eta_seconds)}s"
        else:
            eta_str = "calculating..."

        live = _observables(connector, await _telemetry_now(drone))
        where = _where_text(live)
        result = {
            "DISPLAY_TO_USER": f"🚁 FLYING | Dist: {distance:.0f}m | Alt: {current_alt:.1f}m | Speed: {ground_speed:.1f}m/s | ETA: {eta_str} | {progress:.0f}%"
            + (f" | {where}" if where else ""),
            "status": "in_progress",
            "distance_m": round(distance, 1),
            "progress_percent": round(progress, 0),
            **live,
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

    # v2 fix: v1 called drone.action.set_maximum_speed(), which does not exist
    # in MavSDK 3.x - the tool always errored. Now: try the current-speed
    # command (DO_CHANGE_SPEED - applies while flying in guided/auto), and
    # fall back to the ArduPilot WPNAV_SPEED parameter (cm/s) on the ground.
    try:
        log_mavlink_cmd("drone.action.set_current_speed", speed_m_s=speed_m_s)
        await drone.action.set_current_speed(float(speed_m_s))
        return {
            "status": "success",
            "message": f"Current target speed set to {speed_m_s} m/s (DO_CHANGE_SPEED)",
            "speed_kmh": round(speed_m_s * 3.6, 1),
        }
    except Exception as speed_error:
        try:
            log_mavlink_cmd("drone.param.set_param_float", name="WPNAV_SPEED", value=speed_m_s * 100)
            await drone.param.set_param_float("WPNAV_SPEED", float(speed_m_s) * 100.0)
            return {
                "status": "success",
                "message": f"Waypoint speed limit set to {speed_m_s} m/s via WPNAV_SPEED "
                f"(DO_CHANGE_SPEED was rejected: {speed_error})",
                "speed_kmh": round(speed_m_s * 3.6, 1),
            }
        except Exception as e:
            logger.error(f"{LogColors.ERROR}❌ TOOL ERROR - Failed to set max speed: {e}{LogColors.RESET}")
            return {"status": "failed", "error": f"Set max speed failed: {e} (speed command: {speed_error})"}


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
            # Log text only - the yaw command is re-sent at current_alt, the
            # ABSOLUTE altitude, so the relative reading changes nothing.
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
        return {"status": "failed", "error": "No position telemetry received"}
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
        # Get current position to calculate relative altitude for display. Same
        # caveat as go_to_location: home_alt is the CURRENT datum (the last arm
        # point), the printed AGL figure is description, and the command flies
        # to the absolute altitude_m it was given.
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

        _note_movement(
            connector,
            "reposition",
            {"latitude": latitude_deg, "longitude": longitude_deg, "label": "the repositioning target"},
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


# ============================================================================
# v2: remaining action-plugin coverage
# ============================================================================


@mcp.tool()
async def do_orbit(
    ctx: Context,
    radius_m: float,
    velocity_m_s: float,
    latitude_deg: float,
    longitude_deg: float,
    absolute_altitude_m: float,
    yaw_behavior: str = "front_to_center",
) -> dict:
    """Command the drone to orbit around a point (MAV_CMD_DO_ORBIT).

    FIRMWARE NOTE: ArduCopter rejects this with UNSUPPORTED (observed on
    4.5.7 SITL) - it has no DO_ORBIT handler. PX4 supports it. For an
    ArduPilot-compatible orbit, use offboard_set_velocity_body with a yaw
    rate instead.

    Args:
        radius_m (float): orbit radius in meters (>0).
        velocity_m_s (float): tangential speed (m/s, max 20).
        latitude_deg, longitude_deg (float): center of the orbit.
        absolute_altitude_m (float): orbit altitude AMSL.
        yaw_behavior (str): "front_to_center", "hold_initial", "uncontrolled",
            "front_tangent", or "rc_controlled".

    Returns:
        dict: status.
    """
    from mavsdk.action import OrbitYawBehavior

    log_tool_call(
        "do_orbit",
        radius_m=radius_m,
        velocity_m_s=velocity_m_s,
        latitude_deg=latitude_deg,
        longitude_deg=longitude_deg,
        absolute_altitude_m=absolute_altitude_m,
        yaw_behavior=yaw_behavior,
    )
    behaviors = {
        "front_to_center": OrbitYawBehavior.HOLD_FRONT_TO_CIRCLE_CENTER,
        "hold_initial": OrbitYawBehavior.HOLD_INITIAL_HEADING,
        "uncontrolled": OrbitYawBehavior.UNCONTROLLED,
        "front_tangent": OrbitYawBehavior.HOLD_FRONT_TANGENT_TO_CIRCLE,
        "rc_controlled": OrbitYawBehavior.RC_CONTROLLED,
    }
    behavior = behaviors.get(str(yaw_behavior).lower())
    if behavior is None:
        return {"status": "failed", "error": f"yaw_behavior must be one of {sorted(behaviors)}, got {yaw_behavior!r}"}
    if not 1.0 <= float(radius_m) <= 10_000.0:
        return {"status": "failed", "error": f"radius_m must be between 1 and 10000, got {radius_m}"}
    if not 0.1 <= abs(float(velocity_m_s)) <= 20.0:
        return {"status": "failed", "error": f"velocity_m_s magnitude must be between 0.1 and 20, got {velocity_m_s}"}
    if not (-90 <= float(latitude_deg) <= 90 and -180 <= float(longitude_deg) <= 180):
        return {"status": "failed", "error": f"latitude/longitude out of range ({latitude_deg}, {longitude_deg})"}

    connector = ctx.request_context.lifespan_context
    if not await ensure_connection(connector):
        return {"status": "failed", "error": "Drone connection timeout. Please wait and try again."}
    drone = connector.drone
    try:
        log_mavlink_cmd("drone.action.do_orbit", radius_m=radius_m)
        await drone.action.do_orbit(
            float(radius_m),
            float(velocity_m_s),
            behavior,
            float(latitude_deg),
            float(longitude_deg),
            float(absolute_altitude_m),
        )
    except Exception as e:
        logger.error(f"do_orbit failed: {e}")
        return {
            "status": "failed",
            "error": f"do_orbit failed: {e} (ArduPilot does not support DO_ORBIT - see tool description)",
        }
    return {"status": "success", "message": f"Orbiting ({radius_m} m radius at {velocity_m_s} m/s)"}


@mcp.tool()
async def vehicle_power(ctx: Context, action: str, confirm: bool = False) -> dict:
    """CRITICAL: reboot, shut down, or TERMINATE the autopilot.

    - "reboot": restart the autopilot (drops the link briefly; verified
      working on ArduPilot SITL).
    - "shutdown": power down the autopilot (UNSUPPORTED on ArduPilot -
      observed).
    - "terminate": FLIGHT TERMINATION - motors stop IMMEDIATELY, the drone
      falls. Ultimate emergency stop only.

    All actions require confirm=True (refused otherwise). Never use on an
    armed/flying vehicle except terminate in a genuine emergency.

    Args:
        action (str): "reboot", "shutdown", or "terminate".
        confirm (bool): must be True to execute.

    Returns:
        dict: status.
    """
    log_tool_call("vehicle_power", action=action, confirm=confirm)
    action = str(action).lower()
    if action not in ("reboot", "shutdown", "terminate"):
        return {"status": "failed", "error": f'action must be "reboot", "shutdown" or "terminate", got {action!r}'}
    if not confirm:
        return {
            "status": "failed",
            "error": f"{action} refused: pass confirm=true to execute this critical action",
        }

    connector = ctx.request_context.lifespan_context
    if not await ensure_connection(connector):
        return {"status": "failed", "error": "Drone connection timeout. Please wait and try again."}
    drone = connector.drone
    try:
        log_mavlink_cmd(f"drone.action.{action}")
        if action == "reboot":
            await drone.action.reboot()
        elif action == "shutdown":
            await drone.action.shutdown()
        else:
            await drone.action.terminate()
    except Exception as e:
        logger.error(f"vehicle_power({action}) failed: {e}")
        return {"status": "failed", "error": f"{action} failed: {e}"}
    return {"status": "success", "message": f"{action} command sent"}


@mcp.tool()
async def set_actuator(ctx: Context, index: int, value: float) -> dict:
    """EXPERT: directly set an actuator/servo output (MAV_CMD_DO_SET_ACTUATOR).

    Args:
        index (int): actuator output index (1-16).
        value (float): normalized output in [-1, 1].

    Returns:
        dict: status.
    """
    log_tool_call("set_actuator", index=index, value=value)
    if not 1 <= int(index) <= 16:
        return {"status": "failed", "error": f"index must be between 1 and 16, got {index}"}
    if not -1.0 <= float(value) <= 1.0:
        return {"status": "failed", "error": f"value must be within [-1, 1], got {value}"}

    connector = ctx.request_context.lifespan_context
    if not await ensure_connection(connector):
        return {"status": "failed", "error": "Drone connection timeout. Please wait and try again."}
    drone = connector.drone
    try:
        log_mavlink_cmd("drone.action.set_actuator", index=index, value=value)
        await drone.action.set_actuator(int(index), float(value))
    except Exception as e:
        logger.error(f"set_actuator failed: {e}")
        return {"status": "failed", "error": f"set_actuator failed: {e}"}
    return {"status": "success", "message": f"actuator {index} set to {value}"}


@mcp.tool()
async def flight_altitudes(ctx: Context, action: str, altitude_m: float = 0.0) -> dict:
    """Get/set the default takeoff altitude and the return-to-launch altitude.

    FIRMWARE NOTE: the RTL altitude actions use the PX4 parameter
    (RTL_RETURN_ALT) - on ArduPilot they fail with PARAMETER_ERROR
    (observed); use set_parameter("RTL_ALT", centimeters) there instead.

    Args:
        action (str): "get_takeoff", "set_takeoff", "get_rtl", or "set_rtl".
        altitude_m (float): altitude in meters for the set actions (1-500).

    Returns:
        dict: status (+ altitude_m for the get actions).
    """
    log_tool_call("flight_altitudes", action=action, altitude_m=altitude_m)
    action = str(action).lower()
    if action not in ("get_takeoff", "set_takeoff", "get_rtl", "set_rtl"):
        return {
            "status": "failed",
            "error": f'action must be "get_takeoff", "set_takeoff", "get_rtl" or "set_rtl", got {action!r}',
        }
    if action.startswith("set_") and not 1.0 <= float(altitude_m) <= 500.0:
        return {"status": "failed", "error": f"altitude_m must be between 1 and 500, got {altitude_m}"}

    connector = ctx.request_context.lifespan_context
    if not await ensure_connection(connector):
        return {"status": "failed", "error": "Drone connection timeout. Please wait and try again."}
    drone = connector.drone
    try:
        log_mavlink_cmd(f"drone.action.{action}_altitude")
        if action == "get_takeoff":
            alt = await drone.action.get_takeoff_altitude()
            return {"status": "success", "altitude_m": alt}
        if action == "set_takeoff":
            await drone.action.set_takeoff_altitude(float(altitude_m))
            return {"status": "success", "message": f"default takeoff altitude set to {altitude_m} m"}
        if action == "get_rtl":
            alt = await drone.action.get_return_to_launch_altitude()
            return {"status": "success", "altitude_m": alt}
        await drone.action.set_return_to_launch_altitude(float(altitude_m))
        return {"status": "success", "message": f"RTL altitude set to {altitude_m} m"}
    except Exception as e:
        logger.error(f"flight_altitudes({action}) failed: {e}")
        return {
            "status": "failed",
            "error": f"{action} failed: {e} (on ArduPilot use set_parameter('RTL_ALT', cm) for RTL altitude)",
        }


@mcp.tool()
async def vtol_transition(ctx: Context, to: str) -> dict:
    """Transition a VTOL vehicle between fixedwing and multicopter flight.

    FIRMWARE NOTE: pure multicopters reject this with UNSUPPORTED (observed
    on ArduCopter SITL) - it only applies to VTOL airframes.

    Args:
        to (str): "fixedwing" or "multicopter".

    Returns:
        dict: status.
    """
    log_tool_call("vtol_transition", to=to)
    to = str(to).lower()
    if to not in ("fixedwing", "multicopter"):
        return {"status": "failed", "error": f'to must be "fixedwing" or "multicopter", got {to!r}'}

    connector = ctx.request_context.lifespan_context
    if not await ensure_connection(connector):
        return {"status": "failed", "error": "Drone connection timeout. Please wait and try again."}
    drone = connector.drone
    try:
        log_mavlink_cmd(f"drone.action.transition_to_{to}")
        if to == "fixedwing":
            await drone.action.transition_to_fixedwing()
        else:
            await drone.action.transition_to_multicopter()
    except Exception as e:
        logger.error(f"vtol_transition({to}) failed: {e}")
        return {"status": "failed", "error": f"transition to {to} failed: {e} (VTOL airframes only)"}
    return {"status": "success", "message": f"transition to {to} commanded"}
