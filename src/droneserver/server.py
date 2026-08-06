#!/usr/bin/env python3
"""
MAVLink MCP Server - HTTP/SSE transport entry point.

Absorbs the former ``src/server/droneserver_http.py``. Runs the FastMCP server
over HTTP with Server-Sent Events (SSE) so web-based MCP clients can connect.

Run as ``droneserver`` (console script) or ``python -m droneserver.server``.
Pass ``--transport stdio`` for the stdio mode formerly provided by running
``src/server/droneserver.py`` directly (used by ``start_agent.sh``).
"""

import argparse
import logging
import threading
import time

import requests

from droneserver.config import get_settings
from droneserver.logging_setup import logger


def main() -> None:
    """Parse CLI args and start the MCP server (blocks until shutdown)."""
    parser = argparse.ArgumentParser(prog="droneserver", description="MAVLink MCP server")
    parser.add_argument(
        "--transport",
        choices=["sse", "stdio"],
        default="sse",
        help="MCP transport: HTTP/SSE server (default) or stdio for local clients",
    )
    args = parser.parse_args()
    if args.transport == "stdio":
        run_stdio()
    else:
        run_sse()


def run_stdio() -> None:
    """Run over stdio (v1: ``python src/server/droneserver.py``). Logs go to stderr."""
    import droneserver.tools  # noqa: F401  - registration side effect
    from droneserver.app import mcp

    mcp.run(transport="stdio")


def run_sse() -> None:
    """Start the HTTP/SSE MCP server (v1: ``src/server/droneserver_http.py``)."""
    settings = get_settings()
    host = settings.mcp_host
    port = settings.mcp_port

    logger.info("=" * 60)
    logger.info("MAVLink MCP Server - HTTP/SSE Mode")
    logger.info("=" * 60)
    logger.info(f"Starting SSE server on {host}:{port}")
    logger.info("")
    logger.info("⚠️  IMPORTANT: Use /sse (not /mcp/sse)")
    logger.info(f"   Server will start on port {port}")
    logger.info("")
    logger.info("=" * 60)

    # Import registers all tools on the shared mcp instance
    import droneserver.tools  # noqa: F401  - registration side effect
    from droneserver.app import mcp

    # Update settings on the existing mcp instance
    mcp.settings.host = host
    mcp.settings.port = port

    # Trigger connection initialization after server starts
    def trigger_initialization():
        """Make a request to the server to trigger lifespan and connection initialization"""
        logger.info("🔧 Background: Waiting for server to start...")
        time.sleep(3)  # Give uvicorn time to fully start

        try:
            logger.info("🔧 Triggering connection initialization via GET /sse...")
            # Make a simple GET request to trigger the lifespan
            response = requests.get(f"http://localhost:{port}/sse", timeout=5)
            logger.info(f"✓ Initialization request completed (status: {response.status_code})")
        except Exception as e:
            logger.warning(f"Initialization trigger request failed (this is normal): {e}")
            logger.info("Connection will initialize on the first client request instead.")

    # Start background thread to trigger initialization
    init_thread = threading.Thread(target=trigger_initialization, daemon=True)
    init_thread.start()

    # Suppress noisy HTTP/framework logs using a filter (most reliable method)
    if not settings.mavlink_verbose:
        # Create a filter that drops all uvicorn access logs
        class SuppressUvicornFilter(logging.Filter):
            def filter(self, record):
                return False  # Drop all records

        # Add filter to uvicorn.access logger (this survives uvicorn reconfiguration)
        uvicorn_access = logging.getLogger("uvicorn.access")
        uvicorn_access.addFilter(SuppressUvicornFilter())

        # Also suppress FastMCP's "Processing request" logs
        mcp_server = logging.getLogger("mcp.server")
        mcp_server.setLevel(logging.WARNING)

        logger.info("🔇 HTTP access logs suppressed (set MAVLINK_VERBOSE=1 to re-enable)")
    else:
        logger.info("🔍 VERBOSE MODE: Showing all HTTP and framework logs")

    # Run server with SSE transport using default mount path
    mcp.run(transport="sse")


if __name__ == "__main__":
    main()
