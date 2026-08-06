"""Flight logging system: colored console log helpers + timestamped flight log files."""

import json
from datetime import datetime

from droneserver.config import get_settings
from droneserver.logging_setup import logger


class LogColors:
    """ANSI color codes for colored terminal output (dark/normal variants)"""

    RESET = "\033[0m"
    BOLD = "\033[1m"

    # Dark colors (3x codes) - easier to read than bright (9x codes)
    RED = "\033[31m"  # Dark red
    GREEN = "\033[32m"  # Dark green
    YELLOW = "\033[33m"  # Dark yellow/orange
    BLUE = "\033[34m"  # Dark blue
    MAGENTA = "\033[35m"  # Dark magenta
    CYAN = "\033[36m"  # Dark cyan
    WHITE = "\033[37m"  # Light gray
    GRAY = "\033[90m"  # Dark gray for separators

    # Combined styles for specific log types
    MAVLINK = "\033[36m"  # Dark cyan for MAVLink commands
    TOOL = "\033[32m"  # Dark green for MCP tool calls
    ERROR = "\033[31m"  # Dark red for errors
    HTTP = "\033[35m"  # Dark magenta for HTTP requests (GET/POST)
    STATUS = "\033[94m"  # Bright blue for drone status/responses
    SUCCESS = "\033[92m"  # Bright green for success messages (✓)
    SEPARATOR = "\033[90m"  # Dark gray for visual separators


class FlightLogger:
    """Logs flight operations to a timestamped file"""

    def __init__(self):
        self.log_dir = get_settings().flight_log_dir
        self.log_dir.mkdir(exist_ok=True)

        # Create log file with timestamp
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        self.log_file = self.log_dir / f"flight_{timestamp}.log"

        # Write header
        with open(self.log_file, "w") as f:
            f.write("=" * 80 + "\n")
            f.write("MAVLink MCP Flight Log\n")
            f.write(f"Started: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n")
            f.write("=" * 80 + "\n\n")

        logger.info(f"✈️ Flight log created: {self.log_file}")

    def log_entry(self, entry_type: str, message: str):
        """Write a timestamped entry to the log file"""
        timestamp = datetime.now().strftime("%H:%M:%S.%f")[:-3]  # Include milliseconds
        try:
            with open(self.log_file, "a") as f:
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
        logger.info(f"{LogColors.TOOL}📥 INPUT: {json.dumps(kwargs, default=str)}{LogColors.RESET}")
        get_flight_logger().log_entry("MCP_TOOL", msg)
    else:
        msg = f"{tool_name}()"
        logger.info(f"{LogColors.TOOL}🔧 MCP TOOL: {msg}{LogColors.RESET}")
        logger.info(f"{LogColors.TOOL}📥 INPUT: {{}}{LogColors.RESET}")
        get_flight_logger().log_entry("MCP_TOOL", msg)


def log_tool_output(output: dict):
    """Log MCP tool output as JSON (GREEN)"""
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
