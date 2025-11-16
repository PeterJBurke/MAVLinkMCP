#!/bin/bash
# Start MAVLink MCP Server in HTTP/SSE mode for ChatGPT and web clients

echo "==========================================================="
echo "MAVLink MCP Server - HTTP/SSE Mode"
echo "==========================================================="
echo ""
echo "Starting server for ChatGPT Developer Mode..."
echo ""

# Check if .env exists
if [ ! -f .env ]; then
    echo "ERROR: .env file not found!"
    echo "Please copy .env.example to .env and configure your drone connection."
    exit 1
fi

# Check if uv is installed
if ! command -v uv &> /dev/null; then
    echo "ERROR: uv is not installed!"
    echo "Install it with: curl -LsSf https://astral.sh/uv/install.sh | sh"
    exit 1
fi

# Get configuration
HOST=${MCP_HOST:-0.0.0.0}
PORT=${MCP_PORT:-8080}

# Get drone connection details from .env
MAVLINK_ADDRESS=$(grep -E "^MAVLINK_ADDRESS=" .env | cut -d '=' -f2)
MAVLINK_PORT=$(grep -E "^MAVLINK_PORT=" .env | cut -d '=' -f2)
MAVLINK_PROTOCOL=$(grep -E "^MAVLINK_PROTOCOL=" .env | cut -d '=' -f2)

echo "Configuration:"
echo "  MCP Server Host: $HOST"
echo "  MCP Server Port: $PORT"
echo ""
echo "Drone Connection:"
echo "  Address: $MAVLINK_ADDRESS"
echo "  Port: $MAVLINK_PORT"
echo "  Protocol: $MAVLINK_PROTOCOL"
echo ""
echo "Your MCP server will be accessible at:"
echo "  http://localhost:$PORT/mcp/sse"
echo ""
echo "For remote access (from ChatGPT):"
echo "  http://YOUR_SERVER_IP:$PORT/mcp/sse"
echo ""
echo "==========================================================="
echo ""

# Run the HTTP server
uv run python src/server/mavlinkmcp_http.py

