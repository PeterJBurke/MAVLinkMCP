#!/bin/bash
# Quick Start Script for MAVLink MCP Agent

echo "🚁 MAVLink MCP Agent Launcher"
echo "=============================="
echo ""

# Load environment from .env file
if [ -f .env ]; then
    echo "✓ Loading configuration from .env..."
    export $(cat .env | grep -v '^#' | xargs)
else
    echo "❌ ERROR: .env file not found!"
    echo "Please create .env file with your drone connection details."
    echo "See .env.example for template."
    exit 1
fi

# Verify configuration
echo "✓ Drone Address: $MAVLINK_ADDRESS"
echo "✓ Drone Port: $MAVLINK_PORT"
echo ""

# Check if API keys are configured
if [ -f examples/fastagent.secrets.yaml ]; then
    echo "✓ API keys configured"
else
    echo "⚠️  WARNING: examples/fastagent.secrets.yaml not found"
    echo "   You'll need this file to run the AI agent."
    echo "   See examples/README.md for instructions."
    echo ""
    read -p "Continue anyway? (y/n) " -n 1 -r
    echo
    if [[ ! $REPLY =~ ^[Yy]$ ]]; then
        exit 1
    fi
fi

echo ""
echo "🚀 Starting MAVLink MCP Agent..."
echo "================================"
echo ""
echo "Once connected, you can use commands like:"
echo "  - 'Arm the drone'"
echo "  - 'Take off to 5 meters'"
echo "  - 'What is the current position?'"
echo "  - 'Land the drone'"
echo ""
echo "Type 'STOP' to exit the agent."
echo ""
echo "Press Ctrl+C to abort startup..."
sleep 2

# Run the agent with uv
uv run examples/example_agent.py

