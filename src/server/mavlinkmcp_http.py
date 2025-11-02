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

# Load environment variables
from dotenv import load_dotenv
env_path = Path(__file__).parent.parent.parent / '.env'
load_dotenv(dotenv_path=env_path)

# Import the necessary components
from mcp.server.fastmcp import FastMCP
from src.server.mavlinkmcp import app_lifespan, logger
import src.server.mavlinkmcp  # Import all the tools

# Configuration
PORT = int(os.environ.get("MCP_PORT", "8080"))
HOST = os.environ.get("MCP_HOST", "0.0.0.0")
MOUNT_PATH = os.environ.get("MCP_MOUNT_PATH", "/mcp")

if __name__ == "__main__":
    logger.info("=" * 60)
    logger.info("MAVLink MCP Server - HTTP/SSE Mode")
    logger.info("=" * 60)
    logger.info(f"Starting SSE server on {HOST}:{PORT}")
    logger.info(f"Mount path: {MOUNT_PATH}")
    logger.info("")
    logger.info("Connect from ChatGPT Developer Mode using ngrok HTTPS:")
    logger.info(f"  https://YOUR-NGROK-ID.ngrok-free.app{MOUNT_PATH}/sse")
    logger.info("")
    logger.info("Example: https://abc123xyz.ngrok-free.app{MOUNT_PATH}/sse")
    logger.info("=" * 60)
    logger.info("")
    logger.info("⚠️  Note: This server is now on port " + str(PORT))
    logger.info("   Make sure ngrok forwards to this port:")
    logger.info(f"   ngrok http {PORT}")
    logger.info("")
    logger.info("=" * 60)
    
    # Create FastMCP instance with correct port
    # We need to import the tools from the main module
    from src.server import mavlinkmcp as mav_module
    
    # Get the mcp instance with all the tools already registered
    mcp = mav_module.mcp
    
    # Update the server's port and host settings
    mcp._server._host = HOST
    mcp._server._port = PORT
    mcp._server._mount_path = MOUNT_PATH
    
    # Run server with SSE transport
    mcp.run(transport='sse', mount_path=MOUNT_PATH)

