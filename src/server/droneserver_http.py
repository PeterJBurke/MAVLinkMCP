#!/usr/bin/env python3
"""
MAVLink MCP Server - HTTP/SSE Transport
For use with ChatGPT Developer Mode and other web-based MCP clients

This version runs on HTTP with Server-Sent Events (SSE) transport,
allowing web-based clients like ChatGPT to connect.
"""

import sys
import os
from pathlib import Path

# Add parent directory to path
sys.path.insert(0, str(Path(__file__).parent.parent.parent))

# Configuration - must set BEFORE importing droneserver module
PORT = int(os.environ.get("MCP_PORT", "8080"))
HOST = os.environ.get("MCP_HOST", "0.0.0.0")
MOUNT_PATH = os.environ.get("MCP_MOUNT_PATH", "/mcp")

# Load environment variables
from dotenv import load_dotenv
env_path = Path(__file__).parent.parent.parent / '.env'
load_dotenv(dotenv_path=env_path)

# Now import after env vars are set
from src.server.droneserver import logger

if __name__ == "__main__":
    logger.info("=" * 60)
    logger.info("MAVLink MCP Server - HTTP/SSE Mode")
    logger.info("=" * 60)
    logger.info(f"Starting SSE server on {HOST}:{PORT}")
    logger.info("")
    logger.info("Connect from ChatGPT Developer Mode using ngrok HTTPS:")
    logger.info(f"  https://YOUR-NGROK-ID.ngrok-free.app/sse")
    logger.info("")
    logger.info(f"Example: https://abc123xyz.ngrok-free.app/sse")
    logger.info("=" * 60)
    logger.info("")
    logger.info(f"⚠️  IMPORTANT: Use /sse (not /mcp/sse)")
    logger.info(f"   Server will start on port {PORT}")
    logger.info("   Make sure ngrok forwards to this port:")
    logger.info(f"   ngrok http {PORT}")
    logger.info("")
    logger.info("=" * 60)
    
    # Import the mcp instance with all tools registered
    from src.server.droneserver import mcp
    import threading
    import time
    import requests
    
    # Update settings on the existing mcp instance
    mcp.settings.host = HOST
    mcp.settings.port = PORT
    
    # Trigger connection initialization after server starts
    def trigger_initialization():
        """Make a request to the server to trigger lifespan and connection initialization"""
        logger.info("🔧 Background: Waiting for server to start...")
        time.sleep(3)  # Give uvicorn time to fully start
        
        try:
            logger.info("🔧 Triggering connection initialization via GET /sse...")
            # Make a simple GET request to trigger the lifespan
            response = requests.get(f"http://localhost:{PORT}/sse", timeout=5)
            logger.info(f"✓ Initialization request completed (status: {response.status_code})")
        except Exception as e:
            logger.warning(f"Initialization trigger request failed (this is normal): {e}")
            logger.info("Connection will initialize on first ChatGPT request instead.")
    
    # Start background thread to trigger initialization
    init_thread = threading.Thread(target=trigger_initialization, daemon=True)
    init_thread.start()
    
    # Suppress noisy HTTP/framework logs using a filter (most reliable method)
    import logging
    
    # Check for verbose mode first
    verbose_mode = os.getenv("MAVLINK_VERBOSE", "0") == "1"
    
    if not verbose_mode:
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
    mcp.run(transport='sse')

