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

# Configuration - must set BEFORE importing mavlinkmcp module
PORT = int(os.environ.get("MCP_PORT", "8080"))
HOST = os.environ.get("MCP_HOST", "0.0.0.0")
MOUNT_PATH = os.environ.get("MCP_MOUNT_PATH", "/mcp")

# Load environment variables
from dotenv import load_dotenv
env_path = Path(__file__).parent.parent.parent / '.env'
load_dotenv(dotenv_path=env_path)

# Now import after env vars are set
from src.server.mavlinkmcp import logger

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
    from src.server.mavlinkmcp import mcp
    
    # Update settings on the existing mcp instance
    mcp.settings.host = HOST
    mcp.settings.port = PORT
    
    # Run server with SSE transport using default mount path
    mcp.run(transport='sse')

