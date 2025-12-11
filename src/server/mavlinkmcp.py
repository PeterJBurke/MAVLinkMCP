# Add lifespan support for startup/shutdown with strong typing
from contextlib import asynccontextmanager
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from mcp.server.fastmcp import Context, FastMCP
from typing import Tuple
from mavsdk import System
from mavsdk.mission_raw import MissionItem
import asyncio
import os
import logging
import math
from dotenv import load_dotenv
from pathlib import Path

# Load environment variables from .env file
env_path = Path(__file__).parent.parent.parent / '.env'
load_dotenv(dotenv_path=env_path)

# Configure logger with clean single-line format for systemd
logger = logging.getLogger("MAVLinkMCP")
logger.setLevel(logging.INFO)

# Remove any existing handlers to avoid duplicates
logger.handlers.clear()

# Single-line format for clean journalctl output
console_handler = logging.StreamHandler()
# Compact format: timestamp | level | message (no logger name, no multi-line)
console_formatter = logging.Formatter('%(asctime)s | %(levelname)-7s | %(message)s', datefmt='%H:%M:%S')
console_handler.setFormatter(console_formatter)
logger.addHandler(console_handler)

# Prevent propagation to avoid duplicate logs from parent loggers
logger.propagate = False

# Note: HTTP/framework log suppression is done in mavlinkmcp_http.py
# (must be set right before server start to prevent uvicorn from overriding)

# Ensure output is unbuffered for systemd journalctl
import sys
sys.stdout.reconfigure(line_buffering=True) if hasattr(sys.stdout, 'reconfigure') else None
sys.stderr.reconfigure(line_buffering=True) if hasattr(sys.stderr, 'reconfigure') else None

# ============================================================
# Flight Logging System
# ============================================================
from datetime import datetime

# ANSI color codes for terminal output
class LogColors:
    """ANSI color codes for colored terminal output (dark/normal variants)"""
    RESET = '\033[0m'
    BOLD = '\033[1m'
    
    # Dark colors (3x codes) - easier to read than bright (9x codes)
    RED = '\033[31m'      # Dark red
    GREEN = '\033[32m'    # Dark green
    YELLOW = '\033[33m'   # Dark yellow/orange
    BLUE = '\033[34m'     # Dark blue
    MAGENTA = '\033[35m'  # Dark magenta
    CYAN = '\033[36m'     # Dark cyan
    WHITE = '\033[37m'    # Light gray
    GRAY = '\033[90m'     # Dark gray for separators
    
    # Combined styles for specific log types
    MAVLINK = '\033[36m'  # Dark cyan for MAVLink commands
    TOOL = '\033[32m'     # Dark green for MCP tool calls
    ERROR = '\033[31m'    # Dark red for errors
    HTTP = '\033[35m'     # Dark magenta for HTTP requests (GET/POST)
    STATUS = '\033[94m'   # Bright blue for drone status/responses
    SUCCESS = '\033[92m'  # Bright green for success messages (✓)
    SEPARATOR = '\033[90m'  # Dark gray for visual separators

class FlightLogger:
    """Logs flight operations to a timestamped file"""
    def __init__(self):
        self.log_dir = Path(__file__).parent.parent.parent / "flight_logs"
        self.log_dir.mkdir(exist_ok=True)
        
        # Create log file with timestamp
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        self.log_file = self.log_dir / f"flight_{timestamp}.log"
        
        # Write header
        with open(self.log_file, 'w') as f:
            f.write("=" * 80 + "\n")
            f.write(f"MAVLink MCP Flight Log\n")
            f.write(f"Started: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n")
            f.write("=" * 80 + "\n\n")
        
        logger.info(f"✈️ Flight log created: {self.log_file}")
    
    def log_entry(self, entry_type: str, message: str):
        """Write a timestamped entry to the log file"""
        timestamp = datetime.now().strftime("%H:%M:%S.%f")[:-3]  # Include milliseconds
        try:
            with open(self.log_file, 'a') as f:
                f.write(f"[{timestamp}] {entry_type}: {message}\n")
        except Exception as e:
            logger.error(f"{LogColors.ERROR}Failed to write to flight log: {e}{LogColors.RESET}")

# Global flight logger instance
_flight_logger: FlightLogger | None = None

def get_flight_logger() -> FlightLogger:
    """Get or create the global flight logger"""
    global _flight_logger
    if _flight_logger is None:
        _flight_logger = FlightLogger()
    return _flight_logger

def log_tool_call(tool_name: str, **kwargs):
    """Log MCP tool call with parameters (GREEN) with visual separator"""
    # Add visual separator before each tool call
    logger.info(f"{LogColors.SEPARATOR}{'─' * 60}{LogColors.RESET}")
    
    if kwargs:
        params_str = ", ".join([f"{k}={v}" for k, v in kwargs.items() if v is not None])
        msg = f"{tool_name}({params_str})"
        logger.info(f"{LogColors.TOOL}🔧 MCP TOOL: {msg}{LogColors.RESET}")
        # Log input JSON
        import json
        logger.info(f"{LogColors.TOOL}📥 INPUT: {json.dumps(kwargs, default=str)}{LogColors.RESET}")
        get_flight_logger().log_entry("MCP_TOOL", msg)
    else:
        msg = f"{tool_name}()"
        logger.info(f"{LogColors.TOOL}🔧 MCP TOOL: {msg}{LogColors.RESET}")
        logger.info(f"{LogColors.TOOL}📥 INPUT: {{}}{LogColors.RESET}")
        get_flight_logger().log_entry("MCP_TOOL", msg)

def log_tool_output(output: dict):
    """Log MCP tool output as JSON (GREEN)"""
    import json
    logger.info(f"{LogColors.TOOL}📤 OUTPUT: {json.dumps(output, default=str, indent=2)}{LogColors.RESET}")

def log_mavlink_cmd(command: str, **kwargs):
    """Log MAVLink command being sent to drone (CYAN)"""
    if kwargs:
        params_str = ", ".join([f"{k}={v}" for k, v in kwargs.items() if v is not None])
        msg = f"{command}({params_str})"
        logger.info(f"{LogColors.MAVLINK}📡 MAVLink → {msg}{LogColors.RESET}")
        get_flight_logger().log_entry("MAVLink_CMD", msg)
    else:
        msg = f"{command}()"
        logger.info(f"{LogColors.MAVLINK}📡 MAVLink → {msg}{LogColors.RESET}")
        get_flight_logger().log_entry("MAVLink_CMD", msg)

def haversine_distance(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """
    Calculate the distance between two GPS coordinates using the Haversine formula.
    
    Args:
        lat1, lon1: First point (latitude, longitude in degrees)
        lat2, lon2: Second point (latitude, longitude in degrees)
        
    Returns:
        Distance in meters
    """
    R = 6371000  # Earth's radius in meters
    
    # Convert to radians
    phi1 = math.radians(lat1)
    phi2 = math.radians(lat2)
    delta_phi = math.radians(lat2 - lat1)
    delta_lambda = math.radians(lon2 - lon1)
    
    # Haversine formula
    a = math.sin(delta_phi / 2) ** 2 + \
        math.cos(phi1) * math.cos(phi2) * math.sin(delta_lambda / 2) ** 2
    c = 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))
    
    return R * c

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

async def connect_drone_background(drone: System, address: str, port: str, protocol: str, connection_ready: asyncio.Event):
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
        
        # Read connection settings from environment (.env file)
        address = os.environ.get("MAVLINK_ADDRESS", "")
        port = os.environ.get("MAVLINK_PORT", "14540")
        protocol = os.environ.get("MAVLINK_PROTOCOL", "udp").lower()
        
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

# Pass lifespan to server
mcp = FastMCP("MAVLink MCP", lifespan=app_lifespan)


# ============================================================
# Startup Hook for SSE Mode
# ============================================================
async def initialize_drone_connection():
    """
    Initialize the global drone connection.
    Call this from mavlinkmcp_http.py after the server starts.
    """
    logger.info("=" * 60)
    logger.info("🚀 STARTUP: Initializing drone connection...")
    logger.info("=" * 60)
    try:
        await get_or_create_global_connector()
        logger.info("✓ Drone connection initialization complete!")
    except Exception as e:
        logger.error("❌ Failed to initialize drone connection: %s", str(e), exc_info=True)


# ARM
@mcp.tool()
async def arm_drone(ctx: Context) -> dict:
    """Arm the drone. Waits for drone connection if not yet ready."""
    log_tool_call("arm_drone")
    connector = ctx.request_context.lifespan_context
    
    # Wait for connection
    if not await ensure_connection(connector):
        return {"status": "failed", "error": "Drone connection timeout. Please wait and try again."}
    
    drone = connector.drone
    log_mavlink_cmd("drone.action.arm")
    await drone.action.arm()
    return {"status": "success", "message": "Drone armed"}


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
            result = {"status": "success", "position": {
                "latitude_deg": position.latitude_deg,
                "longitude_deg": position.longitude_deg,
                "absolute_altitude_m": position.absolute_altitude_m,
                "relative_altitude_m": position.relative_altitude_m
            }}
            log_tool_output(result)
            return result
    except Exception as e:
        logger.error(f"{LogColors.ERROR}❌ TOOL ERROR - Failed to retrieve position: {e}{LogColors.RESET}")
        return {"status": "failed", "error": str(e)}

@mcp.tool()
async def move_to_relative(ctx: Context, north_m: float, east_m: float, down_m: float, yaw_deg: float = 0.0) -> dict:
    """
    Move the drone relative to the current position using ArduPilot's GUIDED mode.
    
    ArduPilot automatically enters GUIDED mode when receiving goto_location commands
    (as long as the drone is armed). No manual mode switching required.
    
    The drone must be armed and in the air. Waits for connection if not ready.

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
        # Earth radius in meters (approximate)
        EARTH_RADIUS = 6371000.0
        
        # Latitude: 1 degree = ~111,320 meters (constant)
        # north_m positive = increase latitude
        lat_offset_deg = north_m / 111320.0
        
        # Longitude: varies with latitude
        # east_m positive = increase longitude
        lon_offset_deg = east_m / (111320.0 * math.cos(math.radians(current_lat)))
        
        # Calculate target position
        target_lat = current_lat + lat_offset_deg
        target_lon = current_lon + lon_offset_deg
        
        logger.info(f"Moving in GUIDED mode:")
        logger.info(f"  Current: {current_lat:.6f}°, {current_lon:.6f}°")
        logger.info(f"  Altitude: {position.relative_altitude_m:.1f}m AGL (relative) / {current_alt:.1f}m MSL")
        logger.info(f"  Offset: north={north_m:.1f}m, east={east_m:.1f}m, down={down_m:.1f}m")
        target_rel_alt = position.relative_altitude_m - down_m
        logger.info(f"  Target: {target_lat:.6f}°, {target_lon:.6f}°, {target_rel_alt:.1f}m AGL (relative) / {target_alt:.1f}m MSL")
        
        # Use goto_location with calculated target coordinates
        log_mavlink_cmd("drone.action.goto_location", lat=f"{target_lat:.6f}", lon=f"{target_lon:.6f}", alt=f"{target_alt:.1f}", yaw=f"{yaw_deg:.1f}" if not math.isnan(yaw_deg) else "nan")
        await drone.action.goto_location(
            target_lat,
            target_lon,
            target_alt,
            yaw_deg
        )
        
        logger.info("✓ Movement command sent successfully")
        return {
            "status": "success", 
            "message": f"Moving: north={north_m}m, east={east_m}m, altitude_change={-down_m}m",
            "target_position": {
                "latitude_deg": target_lat,
                "longitude_deg": target_lon,
                "altitude_m": target_alt
            }
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
            "warning": "⚠️ Takeoff in progress - do NOT send navigation commands until altitude is reached!"
        }
    
    # Wait for drone to reach target altitude
    logger.info(f"Waiting for drone to reach {takeoff_altitude}m...")
    altitude_threshold = 0.5  # Consider arrived when within 0.5m of target
    max_wait_time = 60  # Maximum wait time in seconds
    check_interval = 1.0  # Check every second
    elapsed_time = 0
    
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
                        "safe_to_navigate": True
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
    except:
        current_alt = 0
    
    logger.warning(f"Takeoff timeout after {max_wait_time}s - current altitude: {current_alt:.1f}m")
    return {
        "status": "warning",
        "message": f"Takeoff timeout - drone at {current_alt:.1f}m (target was {takeoff_altitude}m)",
        "altitude_reached_m": round(current_alt, 1),
        "target_altitude_m": takeoff_altitude,
        "safe_to_navigate": current_alt >= (takeoff_altitude - altitude_threshold)
    }

@mcp.tool()
async def land(ctx: Context, force: bool = False) -> dict:
    """Command the drone to initiate landing at its current location.
    
    LANDING GATE SAFETY: If there's a pending navigation destination (from go_to_location),
    this will check if the drone has arrived before allowing landing. This prevents
    accidentally landing far from the intended destination.
    
    Use force=True to override the landing gate (emergency use only).

    Args:
        ctx (Context): The context of the request.
        force (bool): If True, bypass landing gate safety check (default: False).

    Returns:
        dict: Status message with success, blocked, or error.
    """
    log_tool_call("land", force=force)
    connector = ctx.request_context.lifespan_context
    
    # Wait for connection
    if not await ensure_connection(connector):
        return {"status": "failed", "error": "Drone connection timeout. Please wait and try again."}
    
    drone = connector.drone
    
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
                logger.warning(f"{LogColors.ERROR}🚫 LANDING BLOCKED - {distance:.0f}m from destination!{LogColors.RESET}")
                
                result = {
                    "status": "blocked",
                    "message": f"Cannot land - drone is {distance:.0f}m from destination!",
                    "distance_to_destination_m": round(distance, 1),
                    "current_position": {
                        "latitude": current_lat,
                        "longitude": current_lon
                    },
                    "destination": {
                        "latitude": dest_lat,
                        "longitude": dest_lon
                    },
                    "recommendation": "Call monitor_flight() to check progress, or use land(force=True) for emergency landing",
                    "safe_to_land": False
                }
                log_tool_output(result)
                return result
            else:
                # Close enough - clear destination and proceed with landing
                logger.info(f"Landing gate passed - {distance:.1f}m from destination (within {landing_gate_threshold}m threshold)")
                connector.pending_destination = None
                
        except Exception as e:
            logger.warning(f"Could not check position for landing gate: {e}")
            # Proceed with landing if we can't check position
    
    # Clear any pending destination since we're landing
    connector.pending_destination = None
    # Set landing flag so monitor_flight knows we're descending
    connector.landing_in_progress = True
    
    log_mavlink_cmd("drone.action.land")
    await drone.action.land()
    
    result = {
        "status": "success", 
        "message": "Landing initiated",
        "next_step": "Call monitor_flight() until mission_complete is true"
    }
    log_tool_output(result)
    return result

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
        imu_data.append({
            "timestamp_us": imu.timestamp_us,
            "acceleration": {
                "x": imu.acceleration_frd.forward_m_s2,
                "y": imu.acceleration_frd.right_m_s2,
                "z": imu.acceleration_frd.down_m_s2
            },
            "angular_velocity": {
                "x": imu.angular_velocity_frd.forward_rad_s,
                "y": imu.angular_velocity_frd.right_rad_s,
                "z": imu.angular_velocity_frd.down_rad_s
            },
            "magnetic_field": {
                "x": imu.magnetic_field_frd.forward_gauss,
                "y": imu.magnetic_field_frd.right_gauss,
                "z": imu.magnetic_field_frd.down_gauss
            },
            "temperature_degc": imu.temperature_degc
        })
        count += 1
        if count >= n:
            break

    return {"status": "success", "imu_data": imu_data, "count": len(imu_data)}

@mcp.tool()
async def print_mission_progress(ctx: Context) -> dict:
    """
    Print and return the current mission progress of the drone. Waits for connection if not ready.

    Args:
        ctx (Context): The context of the request.

    Returns:
        dict: A dictionary containing the current and total mission progress or error status.
    """
    connector = ctx.request_context.lifespan_context
    
    # Wait for connection
    if not await ensure_connection(connector):
        return {"status": "failed", "error": "Drone connection timeout. Please wait and try again."}
    
    drone = connector.drone
    async for mission_progress in drone.mission.mission_progress():
        logger.info(f"Mission progress: {mission_progress.current}/{mission_progress.total}")
        return {"status": "success", "current": mission_progress.current, "total": mission_progress.total}



@mcp.tool()
async def initiate_mission(ctx: Context, mission_points: list, return_to_launch: bool = True) -> dict:
    """
    Initiate a mission with a list of mission points. The drone must be armed. Waits for connection if not ready.

    Args:
        ctx (Context): The context of the request.
        mission_points (list): A list of dictionaries representing mission points. Each dictionary must include:
            - latitude_deg (float): Latitude in degrees (range: -90 to +90).
            - longitude_deg (float): Longitude in degrees (range: -180 to +180).
            - relative_altitude_m (float): Altitude relative to the takeoff altitude in meters.
            - speed_m_s (float): Speed in meters per second.
            - is_fly_through (bool): Whether to fly through the point or stop.
            - gimbal_pitch_deg (float): Gimbal pitch angle in degrees (optional).
            - gimbal_yaw_deg (float): Gimbal yaw angle in degrees (optional).
            - camera_action (MissionItem.CameraAction): Camera action at the point (optional).
            - loiter_time_s (float): Loiter time in seconds (optional).
            - camera_photo_interval_s (float): Camera photo interval in seconds (optional).
            - acceptance_radius_m (float): Acceptance radius in meters (optional).
            - yaw_deg (float): Yaw angle in degrees (optional).
            - camera_photo_distance_m (float): Camera photo distance in meters (optional).
            - vehicle_action (MissionItem.VehicleAction): Vehicle action at the point (optional).
        return_to_launch (bool): Whether to return to launch after completing the mission. Default is True.

    Returns:
        dict: Status message with success or error.
    """
    log_tool_call("initiate_mission", waypoint_count=len(mission_points), return_to_launch=return_to_launch)
    connector = ctx.request_context.lifespan_context
    
    # Wait for connection
    if not await ensure_connection(connector):
        return {"status": "failed", "error": "Drone connection timeout. Please wait and try again."}
    
    drone = connector.drone

    # Validate and construct mission items using mission_raw (ArduPilot-compatible)
    mission_items = []
    for i, point in enumerate(mission_points):
        try:
            # Validate latitude and longitude ranges
            if not (-90 <= point["latitude_deg"] <= 90):
                return {"status": "failed", "error": f"Invalid latitude_deg: {point['latitude_deg']}. Must be between -90 and 90."}
            if not (-180 <= point["longitude_deg"] <= 180):
                return {"status": "failed", "error": f"Invalid longitude_deg: {point['longitude_deg']}. Must be between -180 and 180."}

            # Use mission_raw format (raw MAVLink protocol)
            mission_items.append(MissionItem(
                seq=i,
                frame=3,  # MAV_FRAME_GLOBAL_RELATIVE_ALT
                command=16,  # MAV_CMD_NAV_WAYPOINT
                current=1 if i == 0 else 0,
                autocontinue=1,
                param1=point.get("loiter_time_s", 0),  # Hold time
                param2=point.get("acceptance_radius_m", 2.0),  # Acceptance radius
                param3=0,  # Pass radius
                param4=point.get("yaw_deg", float('nan')),  # Yaw angle
                x=int(point["latitude_deg"] * 1e7),  # Latitude * 1e7
                y=int(point["longitude_deg"] * 1e7),  # Longitude * 1e7
                z=float(point["relative_altitude_m"]),  # Altitude
                mission_type=0  # MAV_MISSION_TYPE_MISSION
            ))
        except KeyError as e:
            return {"status": "failed", "error": f"Missing required field in mission point: {e}"}

    # Set return-to-launch behavior
    log_mavlink_cmd("drone.mission.set_return_to_launch_after_mission", return_to_launch=return_to_launch)
    await drone.mission.set_return_to_launch_after_mission(return_to_launch)

    log_mavlink_cmd("drone.mission_raw.upload_mission", waypoint_count=len(mission_items))
    logger.info("Uploading mission using mission_raw (ArduPilot-compatible)")
    await drone.mission_raw.upload_mission(mission_items)

    log_mavlink_cmd("drone.mission.start_mission")
    logger.info("⚠️  Mission starting - drone will switch to AUTO flight mode")
    await drone.mission.start_mission()

    return {
        "status": "success", 
        "message": f"Mission started with {len(mission_items)} waypoints",
        "note": "Flight mode automatically changed to AUTO for mission execution"
    }

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
            "hint": "For AUTO mode, use initiate_mission or resume_mission instead."
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
            log_mavlink_cmd("drone.action.goto_location (GUIDED mode)", 
                          lat=f"{position.latitude_deg:.6f}", 
                          lon=f"{position.longitude_deg:.6f}",
                          alt=f"{position.absolute_altitude_m:.1f}")
            await drone.action.goto_location(
                position.latitude_deg,
                position.longitude_deg,
                position.absolute_altitude_m,
                float('nan')  # Maintain current yaw
            )
            result_mode = "GUIDED"
        
        # Verify mode changed
        await asyncio.sleep(0.5)
        try:
            new_mode = await drone.telemetry.flight_mode().__anext__()
            actual_mode = str(new_mode)
        except:
            actual_mode = "UNKNOWN"
        
        logger.info(f"{LogColors.SUCCESS}✅ Flight mode set to {result_mode} (actual: {actual_mode}){LogColors.RESET}")
        
        return {
            "status": "success",
            "message": f"Flight mode changed to {result_mode}",
            "requested_mode": mode_upper,
            "actual_mode": actual_mode
        }
        
    except Exception as e:
        logger.error(f"{LogColors.ERROR}❌ TOOL ERROR - Failed to set flight mode: {e}{LogColors.RESET}")
        return {"status": "failed", "error": f"Failed to set flight mode: {str(e)}"}

# ============================================================================
# PRIORITY 1: CRITICAL SAFETY TOOLS (v1.1.0)
# ============================================================================

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

    Args:
        ctx (Context): The context of the request.

    Returns:
        dict: Status message with success or error.
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
        return {"status": "success", "message": "Return to Launch initiated - drone returning home"}
    except Exception as e:
        logger.error(f"{LogColors.ERROR}❌ TOOL ERROR - RTL failed: {e}{LogColors.RESET}")
        return {"status": "failed", "error": f"Return to Launch failed: {str(e)}"}

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
            "warning": "This is an emergency action. Drone may be damaged."
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
        log_mavlink_cmd("drone.action.goto_location", lat=f"{current_lat:.6f}", lon=f"{current_lon:.6f}", alt=f"{current_alt:.1f}")
        await drone.action.goto_location(
            current_lat,
            current_lon,
            current_alt,
            float('nan')  # Maintain current heading
        )
        
        logger.info(f"{LogColors.SUCCESS}✓ Holding position at ({current_lat:.6f}, {current_lon:.6f}) @ {position.relative_altitude_m:.1f}m AGL (relative) / {current_alt:.1f}m MSL{LogColors.RESET}")
        
        return {
            "status": "success",
            "message": "Drone holding position in GUIDED mode",
            "position": {
                "latitude_deg": current_lat,
                "longitude_deg": current_lon,
                "altitude_m": position.relative_altitude_m,
                "altitude_rel": position.relative_altitude_m
            },
            "note": "Using GUIDED mode instead of LOITER to prevent altitude drift"
        }
    except Exception as e:
        logger.error(f"{LogColors.ERROR}❌ TOOL ERROR - Hold position failed: {e}{LogColors.RESET}")
        return {"status": "failed", "error": f"Hold position failed: {str(e)}"}

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
            
            logger.info(f"{LogColors.STATUS}Battery: {battery_data['voltage_v']}V, {battery_data['remaining_percent']}% "
                       f"{'(estimated: ' + str(battery_data.get('estimated_percent', '')) + '%)' if 'estimated_percent' in battery_data else ''}{LogColors.RESET}")
            return {"status": "success", "battery": battery_data}
    except Exception as e:
        logger.error(f"{LogColors.ERROR}❌ TOOL ERROR - Failed to get battery status: {e}{LogColors.RESET}")
        return {"status": "failed", "error": f"Battery read failed: {str(e)}"}

# ============================================================================
# PRIORITY 2: FLIGHT MODE MANAGEMENT & SYSTEM HEALTH (v1.1.0)
# ============================================================================

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
async def pause_mission(ctx: Context) -> dict:
    """
    ⛔ DEPRECATED - DO NOT USE ⛔
    
    This tool has been deprecated due to CRITICAL SAFETY ISSUES:
    - Entering LOITER mode causes ALTITUDE DESCENT
    - LOITER does NOT hold current altitude
    - This has caused CRASHES in testing
    
    ✅ USE hold_mission_position() INSTEAD ✅
    
    The hold_mission_position() tool:
    - Stays in GUIDED mode (safe)
    - Maintains current altitude (no descent)
    - Holds position reliably
    
    This tool will be removed in a future version.
    
    Args:
        ctx (Context): The context of the request.

    Returns:
        dict: Error message directing to safe alternative.
    """
    logger.error(f"{LogColors.ERROR}⛔ pause_mission() called - THIS TOOL IS DEPRECATED AND UNSAFE!{LogColors.RESET}")
    logger.error(f"{LogColors.ERROR}⚠️  CRITICAL: pause_mission enters LOITER mode which requires RC throttle input{LogColors.RESET}")
    logger.error(f"{LogColors.ERROR}⚠️  Without RC throttle at 50%, altitude is unpredictable - this has caused crashes!{LogColors.RESET}")
    logger.error(f"{LogColors.ERROR}⚠️  Use hold_mission_position() instead - it stays in GUIDED mode{LogColors.RESET}")
    
    return {
        "status": "failed",
        "error": "⛔ pause_mission() is DEPRECATED due to safety issues",
        "reason": "LOITER mode requires RC throttle input (50% to hold altitude) - not available via MAVLink",
        "technical_details": "Per ArduPilot docs: 'Altitude can be controlled with the Throttle control stick' - we don't have throttle control via MAVSDK",
        "crash_report": "Flight testing: unknown throttle position → altitude descent from 25m → GROUND IMPACT",
        "safe_alternative": "Use hold_mission_position() instead",
        "why_safe": "hold_mission_position() uses GUIDED mode which doesn't require RC input and maintains altitude autonomously",
        "how_to_use": "Call hold_mission_position() to pause, then set_current_waypoint() + resume_mission() to continue",
        "migration_guide": "See LOITER_MODE_CRASH_REPORT.md for full details"
    }

@mcp.tool()
async def hold_mission_position(ctx: Context) -> dict:
    """
    Alternative to pause_mission that holds position in GUIDED mode instead of LOITER.
    This interrupts the current mission and switches to GUIDED mode to hold the current position.
    Unlike pause_mission, this does NOT enter LOITER mode.
    
    NOTE: This stops the mission entirely. To continue the mission after using this,
    you must use set_current_waypoint() to jump back to a waypoint and resume_mission(),
    or upload/initiate a new mission.
    
    Use this when you want to:
    - Pause flight without entering LOITER mode
    - Maintain altitude stability (GUIDED mode)
    - Temporarily interrupt a mission for manual control
    
    Waits for connection if not ready.

    Args:
        ctx (Context): The context of the request.

    Returns:
        dict: Status message with current position and waypoint info.
    """
    connector = ctx.request_context.lifespan_context
    
    # Wait for connection
    if not await ensure_connection(connector):
        return {"status": "failed", "error": "Drone connection timeout. Please wait and try again."}
    
    drone = connector.drone
    log_tool_call("hold_mission_position")
    
    try:
        # Get current mission progress before holding
        current_wp = 0
        total_wp = 0
        async for mission_progress in drone.mission.mission_progress():
            current_wp = mission_progress.current
            total_wp = mission_progress.total
            break
        
        # Get current position
        async for position in drone.telemetry.position():
            current_lat = position.latitude_deg
            current_lon = position.longitude_deg
            current_alt = position.absolute_altitude_m
            break
        
        # Use hold_position to stay in GUIDED mode
        # This will call goto_location with current position
        log_mavlink_cmd(f"drone.action.goto_location(lat={current_lat}, lon={current_lon}, alt={current_alt})")
        logger.info(f"⚠️  Holding mission position in GUIDED mode (not LOITER) - was at waypoint {current_wp}/{total_wp}")
        await drone.action.goto_location(current_lat, current_lon, current_alt, float('nan'))
        
        return {
            "status": "success",
            "message": f"Holding position in GUIDED mode - mission interrupted at waypoint {current_wp}/{total_wp}",
            "was_at_waypoint": current_wp,
            "total_waypoints": total_wp,
            "position": {
                "latitude": current_lat,
                "longitude": current_lon,
                "altitude": current_alt
            },
            "flight_mode": "GUIDED",
            "note": "Mission stopped. To continue: use set_current_waypoint() then resume_mission(), or start a new mission."
        }
    except Exception as e:
        logger.error(f"{LogColors.ERROR}❌ TOOL ERROR - Failed to hold mission position: {e}{LogColors.RESET}")
        return {"status": "failed", "error": f"Hold mission position failed: {str(e)}"}

@mcp.tool()
async def resume_mission(ctx: Context) -> dict:
    """
    Resume a previously paused mission.
    The drone will continue from where it was paused.
    Waits for connection if not ready.

    Args:
        ctx (Context): The context of the request.

    Returns:
        dict: Status message with success or error including current waypoint.
    """
    connector = ctx.request_context.lifespan_context
    
    # Wait for connection
    if not await ensure_connection(connector):
        return {"status": "failed", "error": "Drone connection timeout. Please wait and try again."}
    
    drone = connector.drone
    log_tool_call("resume_mission")
    
    try:
        # Get current mission progress before resuming
        current_wp = 0
        total_wp = 0
        async for mission_progress in drone.mission.mission_progress():
            current_wp = mission_progress.current
            total_wp = mission_progress.total
            break
        
        log_mavlink_cmd("drone.mission.start_mission")
        logger.info(f"⚠️  Resuming mission from waypoint {current_wp}/{total_wp} - drone will switch to AUTO flight mode")
        await drone.mission.start_mission()
        
        # Give the autopilot a moment to process the command
        await asyncio.sleep(0.5)
        
        # Verify flight mode changed to AUTO
        try:
            flight_mode = await drone.telemetry.flight_mode().__anext__()
            logger.info(f"Flight mode after resume: {flight_mode}")
            mode_ok = "AUTO" in str(flight_mode) or "MISSION" in str(flight_mode)
        except:
            mode_ok = False
            flight_mode = "UNKNOWN"
        
        return {
            "status": "success", 
            "message": f"Mission resumed from waypoint {current_wp}/{total_wp}",
            "current_waypoint": current_wp,
            "total_waypoints": total_wp,
            "flight_mode": str(flight_mode),
            "mode_transition_ok": mode_ok,
            "note": "Flight mode should have changed to AUTO/MISSION for mission execution"
        }
    except Exception as e:
        logger.error(f"{LogColors.ERROR}❌ TOOL ERROR - Failed to resume mission: {e}{LogColors.RESET}")
        return {"status": "failed", "error": f"Mission resume failed: {str(e)}"}

@mcp.tool()
async def clear_mission(ctx: Context) -> dict:
    """
    Clear the current mission from the drone.
    Removes all uploaded waypoints.
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
    logger.info("Clearing mission")
    
    try:
        log_mavlink_cmd("drone.mission.clear_mission")
        await drone.mission.clear_mission()
        return {"status": "success", "message": "Mission cleared - all waypoints removed"}
    except Exception as e:
        logger.error(f"{LogColors.ERROR}❌ TOOL ERROR - Failed to clear mission: {e}{LogColors.RESET}")
        return {"status": "failed", "error": f"Mission clear failed: {str(e)}"}

# ============================================================================
# PRIORITY 3: NAVIGATION ENHANCEMENTS (v1.1.0)
# ============================================================================

@mcp.tool()
async def go_to_location(ctx: Context, latitude_deg: float, longitude_deg: float, 
                        absolute_altitude_m: float, yaw_deg: float = float('nan')) -> dict:
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
    log_tool_call("go_to_location", latitude_deg=latitude_deg, longitude_deg=longitude_deg, 
                  absolute_altitude_m=absolute_altitude_m)
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
        # Get current position to calculate relative altitude and initial distance
        position = await drone.telemetry.position().__anext__()
        home_alt = position.absolute_altitude_m - position.relative_altitude_m
        relative_alt = absolute_altitude_m - home_alt
        initial_distance = haversine_distance(position.latitude_deg, position.longitude_deg, 
                                               latitude_deg, longitude_deg)
        
        # Get current speed to estimate flight time
        try:
            async for velocity in drone.telemetry.velocity_ned():
                ground_speed = math.sqrt(velocity.north_m_s**2 + velocity.east_m_s**2)
                break
        except:
            ground_speed = 10.0  # Default assumption
        
        # Estimate flight time (assuming ~10-15 m/s cruise speed for copter)
        estimated_speed = max(ground_speed, 10.0)  # At least 10 m/s for ETA
        eta_seconds = initial_distance / estimated_speed
        
        logger.info(f"Flying to GPS location: {latitude_deg}, {longitude_deg} at {relative_alt:.1f}m AGL / {absolute_altitude_m:.1f}m MSL")
        logger.info(f"Distance to target: {initial_distance:.1f}m, ETA: {eta_seconds:.0f}s")
        
        log_mavlink_cmd("drone.action.goto_location", lat=f"{latitude_deg:.6f}", lon=f"{longitude_deg:.6f}", 
                       alt=f"{absolute_altitude_m:.1f}", yaw=f"{yaw_deg:.1f}" if not math.isnan(yaw_deg) else "nan")
        await drone.action.goto_location(latitude_deg, longitude_deg, absolute_altitude_m, yaw_deg)
        
        # Register pending destination for landing gate safety
        connector.pending_destination = {
            "latitude": latitude_deg,
            "longitude": longitude_deg,
            "altitude_msl": absolute_altitude_m,
            "initial_distance": initial_distance,
            "start_time": asyncio.get_event_loop().time()
        }
        
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
                "yaw": yaw_deg if not math.isnan(yaw_deg) else "maintain current"
            },
            "next_step": "Call monitor_flight() repeatedly until mission_complete is true"
        }
        log_tool_output(result)
        return result
        
    except Exception as e:
        logger.error(f"Go to location failed: {e}{LogColors.RESET}")
        return {"status": "failed", "error": f"Navigation failed: {str(e)}"}

@mcp.tool()
async def check_arrival(
    ctx: Context,
    latitude_deg: float,
    longitude_deg: float,
    threshold_m: float = 10.0
) -> dict:
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
    log_tool_call("check_arrival", latitude_deg=latitude_deg, longitude_deg=longitude_deg, 
                  threshold_m=threshold_m)
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
        # Get current position (instant - no waiting)
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
                "current_position": {
                    "latitude": current_lat,
                    "longitude": current_lon,
                    "altitude_m": current_alt
                },
                "target": {"latitude": latitude_deg, "longitude": longitude_deg}
            }
            log_tool_output(result)
            return result
        else:
            result = {
                "status": "in_progress",
                "message": f"Still {distance:.1f}m from target. Call check_arrival again in a few seconds.",
                "distance_m": round(distance, 1),
                "current_position": {
                    "latitude": current_lat,
                    "longitude": current_lon,
                    "altitude_m": current_alt
                },
                "target": {"latitude": latitude_deg, "longitude": longitude_deg}
            }
            log_tool_output(result)
            return result
            
    except Exception as e:
        logger.error(f"{LogColors.ERROR}❌ Check arrival failed: {e}{LogColors.RESET}")
        return {"status": "failed", "error": f"Check failed: {str(e)}"}


@mcp.tool()
async def monitor_flight(ctx: Context, wait_seconds: float = 5.0, arrival_threshold_m: float = 20.0, auto_land: bool = True) -> dict:
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

    Args:
        ctx (Context): The context of the request.
        wait_seconds (float): Seconds to wait before returning (default: 5).
        arrival_threshold_m (float): Distance to consider "arrived" (default: 10m).
        auto_land (bool): Automatically land when arrived (default: True).

    Returns:
        dict: DISPLAY_TO_USER (print this!), status, mission_complete (ONLY stop when true).
    """
    log_tool_call("monitor_flight", wait_seconds=wait_seconds, arrival_threshold_m=arrival_threshold_m, auto_land=auto_land)
    connector = ctx.request_context.lifespan_context
    
    # Wait for connection
    if not await ensure_connection(connector):
        return {"status": "failed", "error": "Drone connection timeout. Please wait and try again."}
    
    drone = connector.drone
    
    try:
        # First, check if drone is on the ground (mission complete)
        async for landed_state in drone.telemetry.landed_state():
            landed_state_str = str(landed_state).split(".")[-1]
            break
        
        async for in_air in drone.telemetry.in_air():
            is_in_air = in_air
            break
        
        # Get current position
        async for position in drone.telemetry.position():
            current_lat = position.latitude_deg
            current_lon = position.longitude_deg
            current_alt = position.relative_altitude_m
            break
        
        # Check if landed (mission complete!)
        if landed_state_str == "ON_GROUND" or (not is_in_air and current_alt < 1.0):
            logger.info(f"{LogColors.SUCCESS}✅ MISSION COMPLETE - Drone has landed!{LogColors.RESET}")
            get_flight_logger().log_entry("LANDED", "Mission complete")
            
            # Clear all tracking state
            connector.pending_destination = None
            connector.landing_in_progress = False
            
            result = {
                "DISPLAY_TO_USER": "✅ MISSION COMPLETE - Drone has landed safely!",
                "status": "landed",
                "altitude_m": round(current_alt, 1),
                "action_required": None,
                "mission_complete": True
            }
            log_tool_output(result)
            return result
        
        # Check if landing in progress
        if landed_state_str == "LANDING":
            logger.info(f"🛬 Landing in progress... altitude: {current_alt:.1f}m")
            
            result = {
                "DISPLAY_TO_USER": f"🛬 LANDING | Alt: {current_alt:.1f}m | Descending...",
                "status": "landing",
                "altitude_m": round(current_alt, 1),
                "action_required": "SHOW the DISPLAY_TO_USER to user, then CALL monitor_flight() AGAIN",
                "mission_complete": False
            }
            log_tool_output(result)
            return result
        
        # Check if there's a pending destination (still navigating)
        if not connector.pending_destination:
            # Check if we initiated landing (auto_land or manual land call)
            if connector.landing_in_progress:
                logger.info(f"🛬 Landing in progress (flag set)... altitude: {current_alt:.1f}m")
                result = {
                    "DISPLAY_TO_USER": f"🛬 LANDING | Alt: {current_alt:.1f}m | Descending...",
                    "status": "landing",
                    "altitude_m": round(current_alt, 1),
                    "action_required": "call monitor_flight again",
                    "mission_complete": False
                }
                log_tool_output(result)
                return result
            
            # No destination and not landing - drone is just hovering
            result = {
                "DISPLAY_TO_USER": f"🚁 HOVERING | Alt: {current_alt:.1f}m | No destination set",
                "status": "hovering",
                "altitude_m": round(current_alt, 1),
                "action_required": "Call go_to_location() to set destination, or land() to land here",
                "mission_complete": False
            }
            log_tool_output(result)
            return result
        
        # Get destination from pending navigation
        dest = connector.pending_destination
        dest_lat = dest["latitude"]
        dest_lon = dest["longitude"]
        initial_distance = dest["initial_distance"]
        start_time = dest.get("start_time", asyncio.get_event_loop().time())
        
        logger.info(f"Monitoring flight for {wait_seconds}s...")
        
        check_interval = 1.0  # Check every second for arrival detection
        elapsed_in_monitor = 0
        
        while elapsed_in_monitor < wait_seconds:
            # Get current position
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
            except:
                ground_speed = 0
            
            # Calculate ETA
            if ground_speed > 0.5:
                eta_seconds = distance / ground_speed
            else:
                eta_seconds = None
            
            logger.info(f"  📍 Distance: {distance:.1f}m ({progress:.0f}%), Speed: {ground_speed:.1f}m/s, Alt: {current_alt:.1f}m")
            
            # Check if arrived at destination
            if distance <= arrival_threshold_m:
                logger.info(f"{LogColors.SUCCESS}✅ ARRIVED at destination! Distance: {distance:.1f}m{LogColors.RESET}")
                get_flight_logger().log_entry("ARRIVED", f"Distance: {distance:.1f}m")
                
                # Clear pending destination
                connector.pending_destination = None
                
                total_flight_time = asyncio.get_event_loop().time() - start_time
                
                if auto_land:
                    # Automatically initiate landing and WAIT for it to complete
                    logger.info(f"{LogColors.MAVLINK}🛬 Auto-landing initiated - waiting for touchdown{LogColors.RESET}")
                    get_flight_logger().log_entry("AUTO_LAND", "Landing initiated automatically")
                    connector.landing_in_progress = True
                    await drone.action.land()
                    
                    # Wait for landing to complete (up to 120 seconds)
                    landing_timeout = 120
                    landing_start = asyncio.get_event_loop().time()
                    
                    while (asyncio.get_event_loop().time() - landing_start) < landing_timeout:
                        # Check landed state
                        async for state in drone.telemetry.landed_state():
                            landed_state = state
                            break
                        
                        async for position in drone.telemetry.position():
                            current_alt = position.relative_altitude_m
                            break
                        
                        async for in_air in drone.telemetry.in_air():
                            is_in_air = in_air
                            break
                        
                        landed_state_str = str(landed_state).split(".")[-1]
                        
                        # Only consider landed when PX4 reports ON_GROUND AND not in air AND altitude < 2m
                        if landed_state_str == "ON_GROUND" and not is_in_air and current_alt < 2.0:
                            # Wait 3 more seconds to confirm stable on ground
                            logger.info(f"🛬 Touchdown detected, confirming stable...")
                            await asyncio.sleep(3)
                            
                            # Re-check to confirm
                            async for state in drone.telemetry.landed_state():
                                landed_state = state
                                break
                            async for in_air in drone.telemetry.in_air():
                                is_in_air = in_air
                                break
                            
                            landed_state_str = str(landed_state).split(".")[-1]
                            if landed_state_str == "ON_GROUND" and not is_in_air:
                                # Confirmed landed!
                                connector.landing_in_progress = False
                                total_flight_time = asyncio.get_event_loop().time() - start_time
                                
                                logger.info(f"{LogColors.SUCCESS}✅ LANDED! Flight complete.{LogColors.RESET}")
                                get_flight_logger().log_entry("LANDED", "Mission complete")
                                
                                result = {
                                    "DISPLAY_TO_USER": f"✅ MISSION COMPLETE | Landed safely | Flight time: {total_flight_time:.0f}s",
                                    "status": "landed",
                                    "flight_time_seconds": round(total_flight_time, 0),
                                    "mission_complete": True
                                }
                                log_tool_output(result)
                                return result
                        
                        logger.info(f"🛬 Landing... altitude: {current_alt:.1f}m, state: {landed_state_str}, in_air: {is_in_air}")
                        await asyncio.sleep(2)  # Check every 2 seconds
                    
                    # Timeout - return landing status
                    result = {
                        "DISPLAY_TO_USER": f"⚠️ LANDING TIMEOUT | Alt: {current_alt:.1f}m | Check drone status",
                        "status": "landing_timeout",
                        "altitude_m": round(current_alt, 1),
                        "mission_complete": False
                    }
                    log_tool_output(result)
                    return result
                else:
                    # Manual landing required (auto_land=False)
                    result = {
                        "DISPLAY_TO_USER": f"✅ ARRIVED | Distance: {distance:.1f}m | Alt: {current_alt:.1f}m | Call land() to land",
                        "status": "arrived",
                        "distance_m": round(distance, 1),
                        "altitude_m": round(current_alt, 1),
                        "mission_complete": False
                    }
                    log_tool_output(result)
                    return result
            
            # Wait before next check
            await asyncio.sleep(check_interval)
            elapsed_in_monitor += check_interval
        
        # Monitoring period ended, still in progress
        total_elapsed = asyncio.get_event_loop().time() - start_time
        
        # Format ETA nicely
        if eta_seconds:
            if eta_seconds > 60:
                eta_str = f"{int(eta_seconds // 60)}m {int(eta_seconds % 60)}s"
            else:
                eta_str = f"{int(eta_seconds)}s"
        else:
            eta_str = "calculating..."
        
        result = {
            "DISPLAY_TO_USER": f"🚁 FLYING | Dist: {distance:.0f}m | Alt: {current_alt:.1f}m | Speed: {ground_speed:.1f}m/s | ETA: {eta_str} | {progress:.0f}%",
            "status": "in_progress",
            "distance_m": round(distance, 1),
            "progress_percent": round(progress, 0),
            "action_required": "call monitor_flight again",
            "mission_complete": False
        }
        log_tool_output(result)
        return result
        
    except Exception as e:
        logger.error(f"{LogColors.ERROR}❌ Monitor flight failed: {e}{LogColors.RESET}")
        return {"status": "failed", "error": f"Monitoring failed: {str(e)}"}


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
            logger.info(f"Home position: {home_data['latitude_deg']}, {home_data['longitude_deg']} at {home_data['absolute_altitude_m']}m")
            return {"status": "success", "home": home_data}
    except Exception as e:
        logger.error(f"{LogColors.ERROR}❌ TOOL ERROR - Failed to get home position: {e}{LogColors.RESET}")
        return {"status": "failed", "error": f"Home position read failed: {str(e)}"}

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
    
    try:
        log_mavlink_cmd("drone.action.set_maximum_speed", speed_m_s=speed_m_s)
        await drone.action.set_maximum_speed(speed_m_s)
        return {
            "status": "success", 
            "message": f"Maximum speed set to {speed_m_s} m/s",
            "speed_kmh": round(speed_m_s * 3.6, 1)  # Also provide in km/h
        }
    except Exception as e:
        logger.error(f"{LogColors.ERROR}❌ TOOL ERROR - Failed to set max speed: {e}{LogColors.RESET}")
        return {"status": "failed", "error": f"Set max speed failed: {str(e)}"}

# ============================================================================
# PRIORITY 4: TELEMETRY & MONITORING (v1.1.0)
# ============================================================================

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
            
            logger.info(f"Attitude: roll={attitude_data['roll_deg']}°, pitch={attitude_data['pitch_deg']}°, yaw={attitude_data['yaw_deg']}°")
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
            return {
                "status": "success", 
                "in_air": in_air,
                "status_text": status_text
            }
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
            return {
                "status": "success", 
                "armed": armed,
                "status_text": status_text
            }
    except Exception as e:
        logger.error(f"{LogColors.ERROR}❌ TOOL ERROR - Failed to check armed status: {e}{LogColors.RESET}")
        return {"status": "failed", "error": f"Armed check failed: {str(e)}"}

# ============================================================================
# v1.2.0: PARAMETER MANAGEMENT
# ============================================================================

@mcp.tool()
async def get_parameter(ctx: Context, name: str, param_type: str = "auto") -> dict:
    """
    Get the value of a drone parameter by name.
    Parameters control drone behavior (e.g., flight speeds, sensor settings).
    Waits for connection if not ready.

    Args:
        ctx (Context): The context of the request.
        name (str): Parameter name (e.g., "RTL_ALT", "WPNAV_SPEED", "BATT_CAPACITY").
        param_type (str): Type of parameter - "int", "float", or "auto" (default: auto).
                          If "auto", will try float first, then int.

    Returns:
        dict: Parameter value and type, or error if parameter not found.
    
    Examples:
        - get_parameter("RTL_ALT", "float") - Get return-to-launch altitude
        - get_parameter("BATT_CAPACITY", "int") - Get battery capacity in mAh
        - get_parameter("WPNAV_SPEED") - Auto-detect parameter type
    """
    connector = ctx.request_context.lifespan_context
    
    # Wait for connection
    if not await ensure_connection(connector):
        return {"status": "failed", "error": "Drone connection timeout. Please wait and try again."}
    
    drone = connector.drone
    logger.info(f"Getting parameter: {name} (type: {param_type})")
    
    try:
        if param_type == "int":
            value = await drone.param.get_param_int(name)
            return {
                "status": "success",
                "name": name,
                "value": value,
                "type": "int"
            }
        elif param_type == "float":
            value = await drone.param.get_param_float(name)
            return {
                "status": "success",
                "name": name,
                "value": value,
                "type": "float"
            }
        else:  # auto-detect
            # Try float first (most common)
            try:
                value = await drone.param.get_param_float(name)
                return {
                    "status": "success",
                    "name": name,
                    "value": value,
                    "type": "float"
                }
            except:
                # If float fails, try int
                value = await drone.param.get_param_int(name)
                return {
                    "status": "success",
                    "name": name,
                    "value": value,
                    "type": "int"
                }
    except Exception as e:
        logger.error(f"{LogColors.ERROR}❌ TOOL ERROR - Failed to get parameter {name}: {e}{LogColors.RESET}")
        return {
            "status": "failed", 
            "error": f"Parameter '{name}' not found or inaccessible: {str(e)}",
            "suggestion": "Check parameter name spelling. Use list_parameters to see available parameters."
        }

@mcp.tool()
async def set_parameter(ctx: Context, name: str, value: float, param_type: str = "auto") -> dict:
    """
    Set the value of a drone parameter by name.
    ⚠️ WARNING: Changing parameters can affect flight behavior. Only modify if you know what you're doing!
    Waits for connection if not ready.

    Args:
        ctx (Context): The context of the request.
        name (str): Parameter name (e.g., "RTL_ALT", "WPNAV_SPEED").
        value (float): New parameter value.
        param_type (str): Type of parameter - "int", "float", or "auto" (default: auto).
                          If "auto", will detect based on value (int if no decimal).

    Returns:
        dict: Confirmation of parameter change with old and new values.
    
    Examples:
        - set_parameter("RTL_ALT", 1500.0, "float") - Set RTL altitude to 15m
        - set_parameter("BATT_CAPACITY", 5200, "int") - Set battery capacity to 5200 mAh
    
    ⚠️ CAUTION: 
        - Invalid parameters can make the drone unflyable
        - Always verify values are within safe ranges
        - Consider backing up parameters before changes
        - Some parameters require reboot to take effect
    """
    connector = ctx.request_context.lifespan_context
    
    # Wait for connection
    if not await ensure_connection(connector):
        return {"status": "failed", "error": "Drone connection timeout. Please wait and try again."}
    
    drone = connector.drone
    logger.warning(f"⚠️ Setting parameter: {name} = {value} (type: {param_type})")
    
    try:
        # Get old value first
        try:
            if param_type == "int" or (param_type == "auto" and value == int(value)):
                old_value = await drone.param.get_param_int(name)
                param_type_final = "int"
            else:
                old_value = await drone.param.get_param_float(name)
                param_type_final = "float"
        except:
            old_value = None
            # Assume float if we can't get old value
            param_type_final = "float" if param_type == "auto" else param_type
        
        # Set new value
        if param_type_final == "int":
            log_mavlink_cmd("drone.param.set_param_int", name=name, value=int(value))
            await drone.param.set_param_int(name, int(value))
        else:
            log_mavlink_cmd("drone.param.set_param_float", name=name, value=float(value))
            await drone.param.set_param_float(name, float(value))
        
        logger.info(f"{LogColors.SUCCESS}✓ Parameter {name} changed from {old_value} to {value}{LogColors.RESET}")
        
        return {
            "status": "success",
            "name": name,
            "old_value": old_value,
            "new_value": int(value) if param_type_final == "int" else float(value),
            "type": param_type_final,
            "message": f"Parameter '{name}' set to {value}",
            "warning": "Some parameters may require a reboot to take effect."
        }
    except Exception as e:
        logger.error(f"{LogColors.ERROR}❌ TOOL ERROR - Failed to set parameter {name}: {e}{LogColors.RESET}")
        return {
            "status": "failed", 
            "error": f"Failed to set parameter '{name}': {str(e)}",
            "suggestion": "Verify parameter name and value are valid for this drone."
        }

@mcp.tool()
async def list_parameters(ctx: Context, filter_prefix: str = "") -> dict:
    """
    List all available drone parameters.
    This can return a large number of parameters (100-1000+).
    Optionally filter by prefix to narrow results.
    Waits for connection if not ready.

    Args:
        ctx (Context): The context of the request.
        filter_prefix (str): Optional prefix to filter parameters (e.g., "BATT" for battery params).
                            Leave empty to get all parameters.

    Returns:
        dict: List of all parameters with their names, values, and types.
    
    Examples:
        - list_parameters() - Get ALL parameters (may be very long!)
        - list_parameters("RTL") - Get all Return-to-Launch parameters
        - list_parameters("BATT") - Get all battery-related parameters
        - list_parameters("WPNAV") - Get all waypoint navigation parameters
    
    Common Parameter Prefixes:
        - RTL_ : Return to Launch settings
        - BATT_ : Battery settings
        - WPNAV_ : Waypoint navigation
        - EK2_ / EK3_ : EKF (Extended Kalman Filter) settings
        - COMPASS_ : Compass/magnetometer settings
        - GPS_ : GPS settings
    """
    connector = ctx.request_context.lifespan_context
    
    # Wait for connection
    if not await ensure_connection(connector):
        return {"status": "failed", "error": "Drone connection timeout. Please wait and try again."}
    
    drone = connector.drone
    logger.info(f"Listing parameters{f' (filter: {filter_prefix}*)' if filter_prefix else ''}")
    
    try:
        log_mavlink_cmd("drone.param.get_all_params", filter_prefix=filter_prefix if filter_prefix else "none")
        all_params = await drone.param.get_all_params()
        
        # Filter if prefix provided
        if filter_prefix:
            filter_upper = filter_prefix.upper()
            filtered = []
            for param in all_params.int_params:
                if param.name.upper().startswith(filter_upper):
                    filtered.append({"name": param.name, "value": param.value, "type": "int"})
            for param in all_params.float_params:
                if param.name.upper().startswith(filter_upper):
                    filtered.append({"name": param.name, "value": param.value, "type": "float"})
            
            filtered.sort(key=lambda x: x["name"])
            logger.info(f"Found {len(filtered)} parameters matching '{filter_prefix}*'")
            
            return {
                "status": "success",
                "filter": filter_prefix,
                "count": len(filtered),
                "parameters": filtered
            }
        else:
            # Return all parameters
            params_list = []
            for param in all_params.int_params:
                params_list.append({"name": param.name, "value": param.value, "type": "int"})
            for param in all_params.float_params:
                params_list.append({"name": param.name, "value": param.value, "type": "float"})
            
            params_list.sort(key=lambda x: x["name"])
            logger.info(f"Found {len(params_list)} total parameters")
            
            return {
                "status": "success",
                "count": len(params_list),
                "parameters": params_list,
                "warning": f"This is a large list ({len(params_list)} parameters). Consider using filter_prefix to narrow results."
            }
    except Exception as e:
        logger.error(f"{LogColors.ERROR}❌ TOOL ERROR - Failed to list parameters: {e}{LogColors.RESET}")
        return {"status": "failed", "error": f"Failed to retrieve parameters: {str(e)}"}

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
            current_rel_alt = position.relative_altitude_m
            
            logger.info(f"Reading current position: ({current_lat:.6f}, {current_lon:.6f}) @ {current_rel_alt:.1f}m AGL")
            logger.info(f"Commanding: same position, new yaw = {yaw_normalized}°")
            
            # Use goto_location with current position but new yaw
            # This is the standard MAVSDK workaround for yaw-only control
            log_mavlink_cmd("drone.action.goto_location", lat=f"{current_lat:.6f}", 
                           lon=f"{current_lon:.6f}", alt=f"{current_alt:.1f}", 
                           yaw=f"{yaw_normalized:.1f}")
            await drone.action.goto_location(
                current_lat,
                current_lon,
                current_alt,
                yaw_normalized
            )
            
            # Convert heading to cardinal direction
            directions = ['N', 'NE', 'E', 'SE', 'S', 'SW', 'W', 'NW']
            direction_index = int((yaw_normalized + 22.5) / 45) % 8
            cardinal = directions[direction_index]
            
            logger.info(f"{LogColors.SUCCESS}✓ Yaw set to {yaw_normalized}° ({cardinal}){LogColors.RESET}")
            
            return {
                "status": "success",
                "message": f"Rotating to heading {yaw_normalized}°",
                "yaw_degrees": yaw_normalized,
                "cardinal_direction": cardinal,
                "yaw_rate_deg_s": yaw_rate_deg_s
            }
    except Exception as e:
        logger.error(f"Set yaw failed: {e}{LogColors.RESET}")
        return {"status": "failed", "error": f"Yaw control failed: {str(e)}"}

@mcp.tool()
async def reposition(
    ctx: Context,
    latitude_deg: float,
    longitude_deg: float,
    altitude_m: float
) -> dict:
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
        # Get current position to calculate relative altitude for display
        position = await drone.telemetry.position().__anext__()
        home_alt = position.absolute_altitude_m - position.relative_altitude_m
        relative_alt = altitude_m - home_alt
        
        logger.info(f"Repositioning to ({latitude_deg}, {longitude_deg}) at {relative_alt:.1f}m AGL (relative) / {altitude_m:.1f}m MSL")
        
        # Move to new location (will loiter automatically in GUIDED mode)
        log_mavlink_cmd("drone.action.goto_location", lat=f"{latitude_deg:.6f}", 
                       lon=f"{longitude_deg:.6f}", alt=f"{altitude_m:.1f}", yaw="nan")
        await drone.action.goto_location(
            latitude_deg,
            longitude_deg,
            altitude_m,
            float('nan')  # Maintain current heading
        )
        
        return {
            "status": "success",
            "message": "Repositioning to new location",
            "target": {
                "latitude": latitude_deg,
                "longitude": longitude_deg,
                "altitude_msl": altitude_m
            },
            "note": "Drone will fly to location and loiter (hover) there"
        }
    except Exception as e:
        logger.error(f"Reposition failed: {e}{LogColors.RESET}")
        return {"status": "failed", "error": f"Reposition failed: {str(e)}"}

# ============================================================================
# v1.2.0: MISSION ENHANCEMENTS
# ============================================================================

@mcp.tool()
async def upload_mission(ctx: Context, waypoints: list) -> dict:
    """
    Upload a mission to the drone WITHOUT starting it.
    Allows preparing missions in advance.
    Waits for connection if not ready.

    Args:
        ctx (Context): The context of the request.
        waypoints (list): List of waypoint dictionaries with keys:
                         - latitude_deg (float): Waypoint latitude
                         - longitude_deg (float): Waypoint longitude
                         - relative_altitude_m (float): Altitude above home
                         - speed_m_s (float, optional): Speed to waypoint

    Returns:
        dict: Status message with mission summary.
    
    Examples:
        waypoints = [
            {"latitude_deg": 33.645, "longitude_deg": -117.842, "relative_altitude_m": 10},
            {"latitude_deg": 33.646, "longitude_deg": -117.843, "relative_altitude_m": 15},
            {"latitude_deg": 33.647, "longitude_deg": -117.844, "relative_altitude_m": 20}
        ]
        upload_mission(waypoints)
    
    Note:
        - Mission is uploaded but NOT started automatically
        - Use initiate_mission or start_mission to begin execution
        - Clears any existing mission first
        
    Important:
        Pass waypoints as a properly formatted list of dictionaries.
        Each waypoint MUST have: latitude_deg, longitude_deg, relative_altitude_m
    """
    connector = ctx.request_context.lifespan_context
    
    # Wait for connection
    if not await ensure_connection(connector):
        return {"status": "failed", "error": "Drone connection timeout. Please wait and try again."}
    
    # Validate waypoints input
    if not waypoints:
        return {
            "status": "failed", 
            "error": "No waypoints provided",
            "example": [
                {"latitude_deg": 33.645, "longitude_deg": -117.842, "relative_altitude_m": 10},
                {"latitude_deg": 33.646, "longitude_deg": -117.843, "relative_altitude_m": 15}
            ]
        }
    
    if not isinstance(waypoints, list):
        return {
            "status": "failed",
            "error": f"Waypoints must be a list, got {type(waypoints).__name__}",
            "hint": "Pass waypoints as: [{'latitude_deg': 33.645, 'longitude_deg': -117.842, 'relative_altitude_m': 10}]"
        }
    
    drone = connector.drone
    logger.info(f"Uploading mission with {len(waypoints)} waypoints")
    
    try:
        # Validate and create mission items
        mission_items = []
        for i, wp in enumerate(waypoints):
            # Type check
            if not isinstance(wp, dict):
                return {
                    "status": "failed",
                    "error": f"Waypoint {i} must be a dictionary, got {type(wp).__name__}",
                    "hint": "Each waypoint needs: latitude_deg, longitude_deg, relative_altitude_m"
                }
            
            # Required field check
            required_fields = ["latitude_deg", "longitude_deg", "relative_altitude_m"]
            missing = [f for f in required_fields if f not in wp]
            if missing:
                return {
                    "status": "failed",
                    "error": f"Waypoint {i} missing required fields: {', '.join(missing)}",
                    "received": list(wp.keys()),
                    "required": required_fields
                }
            
            # Validate coordinates
            if not (-90 <= wp["latitude_deg"] <= 90):
                return {"status": "failed", "error": f"Waypoint {i}: invalid latitude {wp['latitude_deg']} (must be -90 to 90)"}
            if not (-180 <= wp["longitude_deg"] <= 180):
                return {"status": "failed", "error": f"Waypoint {i}: invalid longitude {wp['longitude_deg']} (must be -180 to 180)"}
            if wp["relative_altitude_m"] < 0:
                return {"status": "failed", "error": f"Waypoint {i}: altitude cannot be negative"}
            
            # Use mission_raw format (ArduPilot-compatible)
            # MAVLink uses lat/lon * 1e7 as integers
            mission_item = MissionItem(
                seq=i,  # Sequence number
                frame=3,  # MAV_FRAME_GLOBAL_RELATIVE_ALT
                command=16,  # MAV_CMD_NAV_WAYPOINT
                current=1 if i == 0 else 0,  # First waypoint is current
                autocontinue=1,  # Auto-continue to next waypoint
                param1=0,  # Hold time (seconds)
                param2=2.0,  # Acceptance radius (meters)
                param3=0,  # Pass radius (meters)
                param4=float('nan'),  # Yaw angle (NaN = don't change)
                x=int(wp["latitude_deg"] * 1e7),  # Latitude * 1e7
                y=int(wp["longitude_deg"] * 1e7),  # Longitude * 1e7
                z=float(wp["relative_altitude_m"]),  # Altitude (meters)
                mission_type=0  # MAV_MISSION_TYPE_MISSION
            )
            mission_items.append(mission_item)
        
        # Upload mission using mission_raw (ArduPilot-compatible)
        log_mavlink_cmd("drone.mission_raw.upload_mission", waypoint_count=len(waypoints))
        await drone.mission_raw.upload_mission(mission_items)
        
        logger.info(f"{LogColors.SUCCESS}✓ Mission uploaded successfully: {len(waypoints)} waypoints{LogColors.RESET}")
        
        return {
            "status": "success",
            "message": f"Mission uploaded with {len(waypoints)} waypoints",
            "waypoint_count": len(waypoints),
            "waypoints_summary": [
                f"WP{i}: ({wp['latitude_deg']:.5f}, {wp['longitude_deg']:.5f}) @ {wp['relative_altitude_m']}m"
                for i, wp in enumerate(waypoints)
            ],
            "note": "Mission uploaded but NOT started. Use initiate_mission to start."
        }
    except Exception as e:
        logger.error(f"Mission upload failed: {e}{LogColors.RESET}")
        return {
            "status": "failed", 
            "error": f"Mission upload failed: {str(e)}",
            "troubleshooting": "Ensure waypoints are formatted correctly as list of dictionaries"
        }

@mcp.tool()
async def download_mission(ctx: Context) -> dict:
    """
    Download the current mission from the drone.
    Retrieves all waypoints stored on the drone.
    Waits for connection if not ready.

    Args:
        ctx (Context): The context of the request.

    Returns:
        dict: Mission data with all waypoints.
    
    Use Cases:
        - Backup current mission
        - Verify uploaded mission
        - Check drone's planned route
        - Mission debugging
    
    Note:
        Mission download may occasionally fail with "UNSUPPORTED" immediately after upload
        due to ArduPilot mission state synchronization. If this happens, wait a moment and
        try again, or use mission_progress() to verify the mission exists.
    """
    connector = ctx.request_context.lifespan_context
    
    # Wait for connection
    if not await ensure_connection(connector):
        return {"status": "failed", "error": "Drone connection timeout. Please wait and try again."}
    
    drone = connector.drone
    logger.info("Downloading mission from drone")
    
    # Check mission progress first to verify mission exists
    try:
        log_mavlink_cmd("drone.mission.mission_progress")
        async for progress in drone.mission.mission_progress():
            logger.info(f"{LogColors.STATUS}Mission has {progress.total} waypoints, currently at {progress.current}{LogColors.RESET}")
            if progress.total == 0:
                return {
                    "status": "failed",
                    "error": "No mission on drone (mission count is 0)",
                    "hint": "Upload a mission first using upload_mission"
                }
            break
    except Exception as e:
        logger.warning(f"Could not check mission progress: {e}")
    
    # Try to download mission with proper retry logic
    max_retries = 5  # Increased retries
    retry_delay = 0.3  # Shorter, more frequent retries
    
    for attempt in range(max_retries):
        try:
            if attempt > 0:
                logger.info(f"Retry attempt {attempt + 1}/{max_retries} after {retry_delay}s delay...")
                await asyncio.sleep(retry_delay)
            
            log_mavlink_cmd("drone.mission_raw.download_mission")
            mission_items = await drone.mission_raw.download_mission()
            
            # Convert raw mission items to dict format
            # Filter for waypoint commands only (command 16 = MAV_CMD_NAV_WAYPOINT)
            waypoints = []
            for item in mission_items:
                if item.command == 16:  # MAV_CMD_NAV_WAYPOINT
                    waypoints.append({
                        "seq": item.seq,
                        "latitude_deg": item.x / 1e7,  # Convert from int * 1e7 to float
                        "longitude_deg": item.y / 1e7,  # Convert from int * 1e7 to float
                        "relative_altitude_m": item.z,
                        "frame": item.frame,
                        "command": item.command
                    })
            
            logger.info(f"{LogColors.SUCCESS}✓ Downloaded mission with {len(waypoints)} waypoints (from {len(mission_items)} total items){LogColors.RESET}")
            
            return {
                "status": "success",
                "waypoint_count": len(waypoints),
                "waypoints": waypoints,
                "note": f"Downloaded on attempt {attempt + 1}" if attempt > 0 else None
            }
            
        except Exception as e:
            error_str = str(e)
            
            # If UNSUPPORTED and not last attempt, retry
            if "UNSUPPORTED" in error_str.upper() and attempt < max_retries - 1:
                logger.warning(f"Mission download attempt {attempt + 1} failed (UNSUPPORTED), retrying...")
                continue
            
            # Last attempt or different error - report it
            logger.error(f"Mission download failed after {attempt + 1} attempts: {e}{LogColors.RESET}")
            
            # Provide helpful error message
            if "UNSUPPORTED" in error_str.upper():
                logger.error(f"{LogColors.ERROR}Mission download failed - ArduPilot may need mission state refresh{LogColors.RESET}")
                return {
                    "status": "failed", 
                    "error": "Mission download UNSUPPORTED by current autopilot state",
                    "hint": "Try waiting a moment after upload, or use mission_progress() to verify mission exists",
                    "mission_exists": "Mission was successfully uploaded and verified via mission_progress",
                    "attempts": attempt + 1,
                    "technical_error": error_str,
                    "workaround": "Use is_mission_finished() to monitor mission execution even without download"
                }
            else:
                return {
                    "status": "failed", 
                    "error": f"Mission download failed: {error_str}",
                    "hint": "Ensure a mission has been uploaded to the drone",
                    "attempts": attempt + 1
                }

@mcp.tool()
async def set_current_waypoint(ctx: Context, waypoint_index: int) -> dict:
    """
    Jump to a specific waypoint in the current mission.
    Allows skipping ahead or going back in a mission.
    Waits for connection if not ready.

    Args:
        ctx (Context): The context of the request.
        waypoint_index (int): Waypoint number to jump to (0-based index).

    Returns:
        dict: Status message with new current waypoint.
    
    Examples:
        - set_current_waypoint(0) - Jump to first waypoint (restart mission)
        - set_current_waypoint(5) - Skip to waypoint 5
        - set_current_waypoint(3) - Go back to waypoint 3
    
    Use Cases:
        - Skip completed waypoints
        - Restart mission from beginning
        - Re-survey specific area
        - Mission recovery after interruption
    """
    connector = ctx.request_context.lifespan_context
    
    # Wait for connection
    if not await ensure_connection(connector):
        return {"status": "failed", "error": "Drone connection timeout. Please wait and try again."}
    
    if waypoint_index < 0:
        return {"status": "failed", "error": f"Invalid waypoint index: {waypoint_index}. Must be 0 or greater."}
    
    drone = connector.drone
    logger.info(f"Setting current mission waypoint to index {waypoint_index}")
    
    try:
        log_mavlink_cmd("drone.mission.set_current_mission_item", waypoint_index=waypoint_index)
        await drone.mission.set_current_mission_item(waypoint_index)
        
        logger.info(f"{LogColors.SUCCESS}✓ Current waypoint set to index {waypoint_index}{LogColors.RESET}")
        
        return {
            "status": "success",
            "message": f"Current waypoint set to index {waypoint_index}",
            "waypoint_index": waypoint_index,
            "note": "Mission will continue from this waypoint"
        }
    except Exception as e:
        logger.error(f"Set current waypoint failed: {e}{LogColors.RESET}")
        return {"status": "failed", "error": f"Set waypoint failed: {str(e)}"}

@mcp.tool()
async def is_mission_finished(ctx: Context) -> dict:
    """
    Check if the current mission has completed.
    Returns true if all waypoints have been reached.
    Waits for connection if not ready.

    Args:
        ctx (Context): The context of the request.

    Returns:
        dict: Boolean indicating if mission is finished.
    
    Use Cases:
        - Monitor mission completion
        - Trigger post-mission actions
        - Mission automation
        - Status monitoring
    """
    connector = ctx.request_context.lifespan_context
    
    # Wait for connection
    if not await ensure_connection(connector):
        return {"status": "failed", "error": "Drone connection timeout. Please wait and try again."}
    
    drone = connector.drone
    logger.info("Checking if mission is finished")
    
    try:
        # Check mission finished status
        log_mavlink_cmd("drone.mission.is_mission_finished")
        finished = await drone.mission.is_mission_finished()
        
        # Get current waypoint progress
        current_wp = 0
        total_wp = 0
        async for mission_progress in drone.mission.mission_progress():
            current_wp = mission_progress.current
            total_wp = mission_progress.total
            break
        
        # Get current flight mode
        try:
            flight_mode = await drone.telemetry.flight_mode().__anext__()
        except:
            flight_mode = "UNKNOWN"
        
        status_text = "FINISHED" if finished else "IN PROGRESS"
        logger.info(f"Mission status: {status_text} - Waypoint {current_wp}/{total_wp} - Mode: {flight_mode}")
        
        return {
            "status": "success",
            "mission_finished": finished,
            "status_text": status_text,
            "current_waypoint": current_wp,
            "total_waypoints": total_wp,
            "flight_mode": str(flight_mode),
            "progress_percentage": round((current_wp / total_wp * 100) if total_wp > 0 else 0, 1)
        }
    except Exception as e:
        logger.error(f"Check mission finished failed: {e}{LogColors.RESET}")
        return {"status": "failed", "error": f"Mission status check failed: {str(e)}"}


# ============================================================================
# ENHANCED TELEMETRY TOOLS
# ============================================================================

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
                "recommendation": "Ready for flight" if health_all_ok else "Run get_health() for details on what's not ready"
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
                "LANDING": "Drone is in the process of landing"
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
                "is_transitioning": state_name in ["TAKING_OFF", "LANDING"]
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
            
            status_text = f"RC {'Available' if is_available else 'Not Available'} - Signal: {signal_strength:.0f}% ({quality})"
            logger.info(f"{LogColors.STATUS}RC Status: {status_text}{LogColors.RESET}")
            
            result = {
                "status": "success",
                "rc_available": is_available,
                "signal_strength_percent": round(signal_strength, 1) if is_available else 0,
                "signal_quality": quality,
                "status_text": status_text
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
                "direction_name": direction
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
            ground_speed = math.sqrt(velocity["forward_m_s"]**2 + velocity["right_m_s"]**2)
            total_speed = math.sqrt(ground_speed**2 + velocity["down_m_s"]**2)
            
            logger.info(f"{LogColors.STATUS}Odometry: Pos({position['north_m']:.1f}N, {position['east_m']:.1f}E, {-position['down_m']:.1f}Up) "
                       f"Vel({ground_speed:.1f}m/s ground) Yaw({euler_angles['yaw_deg']:.0f}°){LogColors.RESET}")
            
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
                "altitude_m": round(-position["down_m"], 2)  # Convert down to up (altitude)
            }
            log_tool_output(result)
            return result
    except Exception as e:
        logger.error(f"{LogColors.ERROR}❌ TOOL ERROR - Failed to get odometry: {e}{LogColors.RESET}")
        return {"status": "failed", "error": f"Odometry read failed: {str(e)}"}


if __name__ == "__main__":
    # Run the server
    mcp.run(transport='stdio')