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

# Import the existing MCP server
from src.server.mavlinkmcp import mcp, logger

# Configuration
PORT = int(os.environ.get("MCP_PORT", "8080"))
MOUNT_PATH = os.environ.get("MCP_MOUNT_PATH", "/mcp")

if __name__ == "__main__":
    logger.info("=" * 60)
    logger.info("MAVLink MCP Server - HTTP/SSE Mode")
    logger.info("=" * 60)
    logger.info(f"Starting SSE server on port {PORT}")
    logger.info(f"Mount path: {MOUNT_PATH}")
    logger.info("")
    logger.info("Connect from ChatGPT Developer Mode using:")
    logger.info(f"  http://YOUR_SERVER_IP:{PORT}{MOUNT_PATH}/sse")
    logger.info("")
    logger.info("Example: http://203.0.113.10:{PORT}{MOUNT_PATH}/sse")
    logger.info("=" * 60)
    
    # Set port via environment variable for the underlying server
    os.environ["PORT"] = str(PORT)
    
    # Run server with SSE transport
    # Note: FastMCP's SSE mode creates its own HTTP server
    # The port is controlled via PORT environment variable
    mcp.run(transport='sse', mount_path=MOUNT_PATH)

