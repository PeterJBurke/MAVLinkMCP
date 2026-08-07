"""Persistent MavSDK connection to the drone, shared across all MCP requests."""

import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass, field

from mavsdk import System
from mcp.server.fastmcp import FastMCP

from droneserver.config import get_settings
from droneserver.logging_setup import logger
from droneserver.telemetry.flight_log import LogColors


@dataclass
class MAVLinkConnector:
    drone: System
    connection_ready: asyncio.Event = field(default_factory=asyncio.Event)
    # Track pending navigation destination for landing gate safety
    pending_destination: dict | None = field(default=None)
    # Track if landing has been initiated (to properly monitor landing progress)
    landing_in_progress: bool = field(default=False)


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


async def connect_drone_background(
    drone: System, address: str, port: str, protocol: str, connection_ready: asyncio.Event
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

        drone = System()
        connection_ready = asyncio.Event()

        # Create the global connector
        _global_connector = MAVLinkConnector(drone=drone, connection_ready=connection_ready)

        # Start drone connection in background
        logger.info("Starting persistent drone connection in background...")
        logger.info("This connection will be shared across all requests")
        logger.info("-" * 60)

        _connection_task = asyncio.create_task(
            connect_drone_background(drone, address, port, protocol, connection_ready)
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
