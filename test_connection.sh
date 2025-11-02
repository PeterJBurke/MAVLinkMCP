#!/bin/bash
# Test MAVLink Drone Connection

echo "🔍 MAVLink Connection Tester"
echo "============================="
echo ""

# Load environment from .env file
if [ -f .env ]; then
    echo "✓ Loading configuration from .env..."
    export $(cat .env | grep -v '^#' | xargs)
else
    echo "❌ ERROR: .env file not found!"
    exit 1
fi

echo "Testing connection to: $MAVLINK_ADDRESS:$MAVLINK_PORT"
echo ""

# Test 1: Ping the host
echo "Test 1: Network Connectivity"
echo "-----------------------------"
if ping -c 3 -W 2 $MAVLINK_ADDRESS > /dev/null 2>&1; then
    echo "✅ PASS: Host $MAVLINK_ADDRESS is reachable"
else
    echo "❌ FAIL: Cannot reach host $MAVLINK_ADDRESS"
    echo "   Check that:"
    echo "   - Drone is powered on"
    echo "   - Network connection is active"
    echo "   - IP address is correct in .env"
    exit 1
fi
echo ""

# Test 2: Check if port is accessible
echo "Test 2: Port Accessibility"
echo "--------------------------"
if command -v nc > /dev/null 2>&1; then
    if timeout 3 nc -zv $MAVLINK_ADDRESS $MAVLINK_PORT 2>&1 | grep -q succeeded; then
        echo "✅ PASS: Port $MAVLINK_PORT is accessible"
    else
        echo "⚠️  WARNING: Cannot verify port $MAVLINK_PORT"
        echo "   This may be normal for UDP ports"
    fi
else
    echo "⚠️  SKIP: netcat (nc) not installed, cannot test port"
fi
echo ""

# Test 3: uv installation
echo "Test 3: uv Package Manager"
echo "---------------------------"
if command -v uv > /dev/null 2>&1; then
    UV_VERSION=$(uv --version 2>&1)
    echo "✅ PASS: uv installed ($UV_VERSION)"
else
    echo "❌ FAIL: uv not installed"
    echo ""
    echo "Install uv with:"
    echo "  curl -LsSf https://astral.sh/uv/install.sh | sh"
    exit 1
fi
echo ""

# Test 4: Python environment
echo "Test 4: Python Environment"
echo "--------------------------"
PYTHON_VERSION=$(python --version 2>&1)
echo "Python version: $PYTHON_VERSION"

if python -c "import sys; exit(0 if sys.version_info >= (3, 10) else 1)"; then
    echo "✅ PASS: Python version is 3.10 or higher"
else
    echo "❌ FAIL: Python 3.10+ required"
    exit 1
fi
echo ""

# Test 5: Check if dependencies are synced
echo "Test 5: Project Dependencies"
echo "----------------------------"
if [ -f "uv.lock" ]; then
    echo "✅ uv.lock found"
    
    # Try to import dependencies using uv run
    if uv run python -c "import mavsdk; import mcp" 2>/dev/null; then
        echo "✅ All dependencies installed"
    else
        echo "⚠️  Dependencies may need syncing"
        echo ""
        echo "Run: uv sync"
        exit 1
    fi
else
    echo "⚠️  uv.lock not found"
    echo ""
    echo "Run: uv sync"
    exit 1
fi
echo ""

# Summary
echo "================================"
echo "✅ All basic tests passed!"
echo "================================"
echo ""
echo "Your system is ready to connect to the drone."
echo ""
echo "Next steps:"
echo "  1. Start the agent:     ./start_agent.sh"
echo "  2. Or run manually:     uv run examples/example_agent.py"
echo ""

