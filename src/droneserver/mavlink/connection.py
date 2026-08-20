"""Persistent MavSDK connection to the drone, shared across all MCP requests."""

import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass, field

from mavsdk import System
from mcp.server.fastmcp import FastMCP

from droneserver.config import get_settings
from droneserver.geo import haversine_distance
from droneserver.logging_setup import logger
from droneserver.telemetry import ground_stream
from droneserver.telemetry.flight_log import LogColors
from droneserver.telemetry.ground import ground_evidence

#: Two launch fixes closer than this - horizontally and in elevation - are the
#: same place. A parked aircraft's GPS wanders by a metre or two, and a
#: re-anchor that logged every metre of that would bury the moves that matter.
ANCHOR_UNCHANGED_M = 2.0

#: Bound on each telemetry read taken while deciding where this session began.
#: Short: the link has just come up, this runs before the server answers
#: anything, and every branch has a fallback.
LAUNCH_READ_TIMEOUT_S = 5.0


def _same_place(launch: dict, latitude_deg: float, longitude_deg: float, absolute_altitude_m: float | None) -> bool:
    """Is this fix the same spot as the one already recorded?"""
    if haversine_distance(launch["latitude_deg"], launch["longitude_deg"], latitude_deg, longitude_deg) > (
        ANCHOR_UNCHANGED_M
    ):
        return False
    have, new = launch.get("absolute_altitude_m"), absolute_altitude_m
    if have is None or new is None:
        return have is None and new is None
    return abs(have - new) <= ANCHOR_UNCHANGED_M


@dataclass
class MAVLinkConnector:
    drone: System
    connection_ready: asyncio.Event = field(default_factory=asyncio.Event)
    # Track pending navigation destination for landing gate safety
    pending_destination: dict | None = field(default=None)
    # Track if landing has been initiated (to properly monitor landing progress)
    landing_in_progress: bool = field(default=False)
    # Latched the first time the drone is SEEN airborne. monitor_flight reports
    # "MISSION COMPLETE - landed" from being on the ground, which is also true of
    # a drone that never took off: on 2026-08-16 gemma-4-e4b's T4 got
    # mission_complete=true on its first monitor_flight call with ever_armed
    # false and max_altitude 0.0 m, and reported the flight as flawless.
    # Without evidence of a flight there is nothing to report complete.
    was_airborne: bool = field(default=False)
    # The last movement command this session issued, and where it pointed.
    # ``{"tool": str, "target": {"latitude_deg", "longitude_deg", "label"} | None,
    #   "commanded_at": float}``. monitor_flight needs it because
    # ``pending_destination`` is cleared the moment the aircraft arrives, so by
    # the time a later poll asks "did the commanded flight actually happen?"
    # there is nothing left to compare the aircraft's position against. That is
    # half of the T6 phantom-return defect (audit 2026-08-19, mechanism M1):
    # eight trials commanded RTL at a hospital 1.2-1.5 km from the launch point
    # and were told "MISSION COMPLETE - Drone has landed safely!".
    last_movement: dict | None = field(default=None)
    # Where this SESSION started, recorded once when the link came up. The
    # autopilot's own home moves to wherever the aircraft last armed
    # (ArduPilot re-zeroes it on every arm), so after a trial that armed at a
    # destination the autopilot's "home" is that destination. This field does
    # not move, so the two can be compared and the difference reported instead
    # of silently handed to a model as "home". Deliberately NOT cleared by
    # reset_flight_latches: it is a property of the session, not of a flight.
    session_launch: dict | None = field(default=None)

    def record_session_launch(
        self, latitude_deg: float, longitude_deg: float, absolute_altitude_m: float | None, source: str
    ) -> None:
        """Record where this session started, once. First writer wins.

        Called when the link comes up. Later callers cannot overwrite it,
        because the whole point of the field is that it does not follow the
        aircraft around. The one exception is deliberate and explicit - see
        :meth:`reanchor_session_launch`.
        """
        if self.session_launch is not None:
            return
        self.session_launch = {
            "latitude_deg": latitude_deg,
            "longitude_deg": longitude_deg,
            "absolute_altitude_m": absolute_altitude_m,
            "source": source,
        }

    def reanchor_session_launch(
        self, latitude_deg: float, longitude_deg: float, absolute_altitude_m: float | None, source: str
    ) -> bool:
        """Move the session launch point deliberately. ``True`` if it moved.

        The ONLY way this field is ever overwritten, and it exists for one
        caller: the trial layer, which parks the aircraft on the run's launch
        point before each trial and therefore knows something the connector
        cannot - that a new flight starts HERE. A link that came up while the
        aircraft was standing 143 m (or, once, 2.0 km) from where the next
        trial would fly from otherwise carries that stale point all session.

        This is emphatically NOT for the aircraft's own arming. ArduPilot
        re-homes on every arm, and a launch point that followed those arms
        would be the moving datum FIX 8a/10/11/12 exist to escape: a T6-shape
        mission that lands at a hospital and re-arms there would report itself
        0 m from its launch point while standing 1.4 km away. Nothing on the
        vehicle side may call this.

        Returns ``False`` when the new fix is the same place (within
        :data:`ANCHOR_UNCHANGED_M` horizontally and vertically), so a caller
        that re-anchors on every poll logs a move only when there is one.
        """
        current = self.session_launch
        if current is not None and _same_place(current, latitude_deg, longitude_deg, absolute_altitude_m):
            return False
        self.session_launch = {
            "latitude_deg": latitude_deg,
            "longitude_deg": longitude_deg,
            "absolute_altitude_m": absolute_altitude_m,
            "source": source,
        }
        if current is not None:
            logger.info(
                "Session launch point re-anchored to %.7f, %.7f (%s); it was %.7f, %.7f (%s)",
                latitude_deg,
                longitude_deg,
                source,
                current["latitude_deg"],
                current["longitude_deg"],
                current["source"],
            )
        return True

    def reset_flight_latches(self) -> None:
        """Clear per-flight tracking state so no trial inherits the last one's.

        These three fields are latches on the process-wide connector. They are
        cleared on landing, but a trial that ends mid-landing (or that never
        completes the landing handshake) leaves ``landing_in_progress`` set, and
        the NEXT trial then inherits it: monitor_flight keeps answering
        "LANDING... call monitor_flight again" on a motionless aircraft and the
        model loops (an 88-call loop was seen this way). ``was_airborne`` and
        ``pending_destination`` leak the same way, so all three are reset at the
        start of a fresh flight (a fresh connection, and every arm_drone, which
        the between-trial ferry runs once per trial). ``last_movement`` is a
        per-flight fact for the same reason: the previous trial's destination
        must not decide this trial's completion.

        ``session_launch`` is NOT reset here. It is where the session began,
        and a new flight does not move it.
        """
        self.landing_in_progress = False
        self.was_airborne = False
        self.pending_destination = None
        self.last_movement = None


# Global connector instance - persists across all HTTP requests
_global_connector: MAVLinkConnector | None = None
_connection_task: asyncio.Task | None = None
_connection_lock = asyncio.Lock()
_lifespan_initialized = False  # Track if lifespan has run (to reduce log noise)


async def ensure_connection(connector: MAVLinkConnector, timeout: float = 30.0) -> bool:
    """
    Wait for the drone connection to be ready.

    Args:
        connector: The MAVLinkConnector instance
        timeout: Maximum time to wait in seconds

    Returns:
        bool: True if connected, False if timeout
    """
    try:
        await asyncio.wait_for(connector.connection_ready.wait(), timeout=timeout)
        return True
    except asyncio.TimeoutError:
        logger.error(f"{LogColors.ERROR}❌ Drone connection timeout after {timeout}s{LogColors.RESET}")
        return False


async def _read_topic(drone, topic: str, timeout_s: float = LAUNCH_READ_TIMEOUT_S):
    """One item from a MavSDK subscription, or ``None``. Never raises.

    A topic the firmware does not publish, a stream that never emits and a
    plugin that does not exist at all are the same answer here - "no reading" -
    because every caller of this module has a fallback for exactly that.
    """

    async def first():
        async for item in getattr(drone.telemetry, topic)():
            return item
        raise TimeoutError("stream ended without an item")

    try:
        return await asyncio.wait_for(first(), timeout=timeout_s)
    except Exception:
        return None


async def _parked_here(drone) -> tuple[object | None, bool | None, bool | None]:
    """``(position, armed, on_ground)`` - each ``None`` where nothing answered.

    ``on_ground`` is the AUTOPILOT's own assessment (``landed_state`` then
    ``in_air``), which no arming anywhere can move; see
    :mod:`droneserver.telemetry.ground`.
    """
    position = await _read_topic(drone, "position")
    armed = await _read_topic(drone, "armed")
    # The two ground topics go through the re-requesting reader (FIX 15): at
    # link-up nothing has asked ArduPilot for EXTENDED_SYS_STATE yet, so a
    # plain read of landed_state here would nearly always come back empty and
    # this decision would fall to "could not be confirmed" on every connection.
    try:
        landed_state, in_air = await ground_stream.read_ground_topics(
            drone, LAUNCH_READ_TIMEOUT_S, link_live=position is not None
        )
    except Exception:
        landed_state, in_air = None, None
    return position, (None if armed is None else bool(armed)), ground_evidence(landed_state, in_air)


async def record_launch_point(drone, connector: "MAVLinkConnector") -> None:
    """Stamp the session's launch point on ``connector``. Best effort, once.

    **Where the aircraft is standing, not where it last armed.** This used to
    read the autopilot's HOME first, on the reasoning that nothing had flown
    yet on this link. That reasoning is wrong the moment the link is not the
    aircraft's first: ArduPilot keeps home wherever the vehicle last ARMED,
    across reboots of *this* server, so a session that came up after a trial
    that armed somewhere else inherits that somewhere else as its launch
    point - measured at 143 m on one lane and 2.0 km on another on 2026-08-19,
    and then handed to models as ``session_launch_point``, the very field
    FIX 8a added so they would have a coordinate that does NOT move.

    So the precedence is now the aircraft's own live position, taken when the
    autopilot says it is disarmed and on the ground - which at link-up is the
    ordinary case, and which is where any flight on this link will start. Home
    remains the fallback for the two cases where the position is not the
    launch point or cannot be had at all:

    * the position could not be read, or
    * the vehicle is already armed or airborne, in which case home is its
      takeoff point and the live position is somewhere along its flight.

    Every branch records WHICH of these happened in ``source``, so
    ``get_home_position`` can show a reader what the coordinate actually is
    instead of implying a certainty the link-up did not have.

    Never raises: a session that could not record its launch point reports that
    honestly in :func:`droneserver.tools.telemetry.get_home_position` rather
    than blocking the connection.
    """
    from droneserver.telemetry.home import read_home

    position, armed, on_ground = await _parked_here(drone)
    airborne = armed is True or on_ground is False
    if position is not None and not airborne:
        settled = armed is False and on_ground is not False
        source = (
            "parked position when the link came up"
            if settled
            else "position when the link came up (the aircraft's armed/ground state could not be confirmed)"
        )
        connector.record_session_launch(
            position.latitude_deg,
            position.longitude_deg,
            getattr(position, "absolute_altitude_m", None),
            source,
        )
        logger.info(
            "Session launch point recorded from the aircraft's own position: %.7f, %.7f (%s)",
            position.latitude_deg,
            position.longitude_deg,
            source,
        )
        return

    why = (
        "the aircraft was already armed or airborne"
        if airborne
        else "the aircraft's position could not be read at link-up"
    )
    logger.warning("using the autopilot's home as the session launch point: %s", why)
    try:
        home = await read_home(drone, 10.0)
    except Exception as e:
        logger.warning("could not record a session launch point at all: %s", e)
        return
    connector.record_session_launch(
        home.latitude_deg,
        home.longitude_deg,
        home.absolute_altitude_m,
        f"autopilot home when the link came up ({why}); the autopilot moves home to wherever it last armed",
    )
    logger.info(
        "Session launch point recorded from the autopilot's home: %.7f, %.7f at %.1f m",
        home.latitude_deg,
        home.longitude_deg,
        home.absolute_altitude_m,
    )


async def anchor_launch_point_here(drone, connector: "MAVLinkConnector", source: str) -> dict:
    """Re-anchor the session launch point to where the aircraft is parked NOW.

    The re-anchor hook FIX 13 owes the trial layer. Between trials the harness
    ferries the aircraft back to the run's launch point and only then does it
    know that the next flight starts there; this is how it says so. It is
    deliberately not reachable from the tool surface the model sees (see
    :func:`droneserver.safety.middleware.maybe_anchor_launch_point`): the
    aircraft's own arming must never move this datum.

    Refuses unless the autopilot itself says the vehicle is disarmed and on the
    ground, because a fix taken in flight is not a launch point. Returns a
    small record of what happened - ``{"anchored": bool, "reason": str, ...}`` -
    and never raises.
    """
    try:
        position, armed, on_ground = await _parked_here(drone)
    except Exception as e:  # noqa: BLE001 - a telemetry fault must not fail the call it rode in on
        return {"anchored": False, "reason": f"could not read the aircraft's state ({type(e).__name__}: {e})"}
    if position is None:
        return {"anchored": False, "reason": "the aircraft's position could not be read"}
    if armed is not False:
        return {"anchored": False, "reason": "the aircraft is armed, or its armed state could not be read"}
    if on_ground is not True:
        return {"anchored": False, "reason": "the autopilot does not report the aircraft on the ground"}
    moved = connector.reanchor_session_launch(
        position.latitude_deg,
        position.longitude_deg,
        getattr(position, "absolute_altitude_m", None),
        source,
    )
    return {
        "anchored": True,
        "moved": moved,
        "reason": "re-anchored to the parked position" if moved else "already anchored on this position",
        "latitude_deg": position.latitude_deg,
        "longitude_deg": position.longitude_deg,
        "absolute_altitude_m": getattr(position, "absolute_altitude_m", None),
    }


async def connect_drone_background(
    drone: System,
    address: str,
    port: str,
    protocol: str,
    connection_ready: asyncio.Event,
    connector: "MAVLinkConnector | None" = None,
):
    """Connect to drone in the background without blocking server startup"""
    connection_string = f"{protocol}://{address}:{port}"
    logger.info("Background: Connecting to drone...")
    logger.info("  Protocol: %s", protocol.upper())
    logger.info("  Target: %s:%s", address, port)

    await drone.connect(system_address=connection_string)

    logger.info("Background: Waiting for drone to respond...")
    async for state in drone.core.connection_state():
        if state.is_connected:
            logger.info("=" * 60)
            logger.info("✓ SUCCESS: Connected to drone at %s:%s!", address, port)
            logger.info("=" * 60)
            break

    logger.info("Background: Waiting for GPS lock...")
    async for health in drone.telemetry.health():
        if health.is_global_position_ok or health.is_home_position_ok:
            logger.info("=" * 60)
            logger.info("✓ GPS LOCK ACQUIRED")
            logger.info("  Global position: %s", "OK" if health.is_global_position_ok else "Not ready")
            logger.info("  Home position: %s", "OK" if health.is_home_position_ok else "Not ready")
            logger.info("=" * 60)
            logger.info("Drone is READY for commands")
            logger.info("=" * 60)
            # A fresh link is a fresh start: drop any per-flight latches that a
            # previous session may have left set, so the first trial on this
            # connection is not judged against stale landing/airborne state.
            if connector is not None:
                connector.reset_flight_latches()
                # A fresh link has a fresh set of stream requests coming, so
                # the previous link's re-request history says nothing about
                # this one (FIX 15).
                ground_stream.reset_rerequests()
                await record_launch_point(drone, connector)
            # Signal that connection is ready!
            connection_ready.set()
            # Phase 4: if a managed mission was in flight when this server was
            # last stopped, reattach the monitor to it.
            try:
                from droneserver.missions.runner import RUNNER

                resumed = RUNNER.resume_if_active(drone)
                if resumed is not None and resumed.resumed_after_restart:
                    logger.info(
                        "Resumed monitoring managed mission %s (phase %s)",
                        resumed.mission_id,
                        resumed.phase,
                    )
            except Exception:
                logger.exception("failed to resume managed mission monitoring")
            break


async def get_or_create_global_connector() -> MAVLinkConnector:
    """Get or create the global drone connector (thread-safe)"""
    global _global_connector, _connection_task

    async with _connection_lock:
        if _global_connector is not None:
            return _global_connector

        # Initialize for the first time
        logger.info("=" * 60)
        logger.info("MAVLink MCP Server - Initializing Global Drone Connection")
        logger.info("=" * 60)

        # Read connection settings from environment / .env file
        settings = get_settings()
        address = settings.mavlink_address
        port = settings.mavlink_port
        protocol = settings.mavlink_protocol.lower()

        # Display connection configuration
        logger.info("Configuration loaded from .env file:")
        logger.info("  MAVLINK_ADDRESS: %s", address if address else "(not set)")
        logger.info("  MAVLINK_PORT: %s", port)
        logger.info("  MAVLINK_PROTOCOL: %s", protocol)
        logger.info("=" * 60)

        if not address:
            logger.warning("WARNING: MAVLINK_ADDRESS not set in .env file!")
            raise ValueError("MAVLINK_ADDRESS not configured in .env file")

        # Validate protocol
        if protocol not in ["tcp", "udp", "serial"]:
            logger.warning("Invalid protocol '%s', defaulting to udp", protocol)
            protocol = "udp"

        # One mavsdk_server per droneserver instance. Sharing the default port
        # makes a second instance attach to the first instance's aircraft.
        logger.info("  MAVSDK_SERVER_PORT: %s", settings.mavsdk_server_port)
        drone = System(port=settings.mavsdk_server_port)
        connection_ready = asyncio.Event()

        # Create the global connector
        _global_connector = MAVLinkConnector(drone=drone, connection_ready=connection_ready)

        # Start drone connection in background
        logger.info("Starting persistent drone connection in background...")
        logger.info("This connection will be shared across all requests")
        logger.info("-" * 60)

        _connection_task = asyncio.create_task(
            connect_drone_background(drone, address, port, protocol, connection_ready, _global_connector)
        )

        return _global_connector


@asynccontextmanager
async def app_lifespan(server: FastMCP) -> AsyncIterator[MAVLinkConnector]:
    """Manage application lifecycle - returns global persistent connector

    Note: In HTTP/SSE mode, FastMCP calls this for EVERY request, not just once.
    We use _lifespan_initialized flag to suppress noisy logs after first call.
    """
    global _lifespan_initialized

    # Only log on first initialization to avoid spam
    if not _lifespan_initialized:
        logger.info("=" * 60)
        logger.info("🚀 LIFESPAN: Starting application lifespan...")
        logger.info("=" * 60)

    try:
        # Get or create the global connector (only happens once)
        if not _lifespan_initialized:
            logger.info("LIFESPAN: Calling get_or_create_global_connector()...")

        connector = await get_or_create_global_connector()

        if not _lifespan_initialized:
            logger.info("LIFESPAN: Connector created successfully!")
            _lifespan_initialized = True

        # Just yield the global connector - no teardown per request!
        yield connector
    except Exception as e:
        logger.error(f"{LogColors.ERROR}❌ LIFESPAN ERROR: {e}{LogColors.RESET}", exc_info=True)
        raise

    # Note: cleanup only happens on server shutdown (not per request)
    # In HTTP mode, this might not be called at all until process termination
    # Only log if this is actually a shutdown (not just end of request)


async def initialize_drone_connection():
    """
    Initialize the global drone connection.
    Call this from the HTTP entry point (droneserver.server) after startup.
    """
    logger.info("=" * 60)
    logger.info("🚀 STARTUP: Initializing drone connection...")
    logger.info("=" * 60)
    try:
        await get_or_create_global_connector()
        logger.info("✓ Drone connection initialization complete!")
    except Exception as e:
        logger.error("❌ Failed to initialize drone connection: %s", str(e), exc_info=True)
