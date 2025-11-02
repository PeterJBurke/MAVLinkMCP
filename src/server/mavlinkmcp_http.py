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
    logger.info(f"Example: https://abc123xyz.ngrok-free.app{MOUNT_PATH}/sse")
    logger.info("=" * 60)
    logger.info("")
    logger.info(f"⚠️  Note: Server will start on port {PORT}")
    logger.info("   Make sure ngrok forwards to this port:")
    logger.info(f"   ngrok http {PORT}")
    logger.info("")
    logger.info("=" * 60)
    
    # Create new FastMCP instance with correct host and port
    mcp = FastMCP(
        "MAVLink MCP",
        lifespan=app_lifespan,
        host=HOST,
        port=PORT,
        mount_path=MOUNT_PATH
    )
    
    # Import all the tool registrations from the main module
    # This will register all the @mcp.tool() decorated functions
    import src.server.mavlinkmcp as mav_module
    
    # Copy all registered tools from the original mcp instance
    for tool_name, tool_func in mav_module.mcp._tools.items():
        mcp._tools[tool_name] = tool_func
    
    # Run server with SSE transport
    mcp.run(transport='sse', mount_path=MOUNT_PATH)

