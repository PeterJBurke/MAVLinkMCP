# MAVLink MCP Server - Deployment and Usage Guide

## 🚁 Complete Guide to Flying Your Drone with AI

This guide will walk you through setting up and using the MAVLink MCP server to control your MAVLink-enabled drone.

---

## 📋 Table of Contents

1. [Prerequisites](#prerequisites)
2. [Installation](#installation)
3. [Configuration](#configuration)
4. [Testing the Connection](#testing-the-connection)
5. [Usage Methods](#usage-methods)
6. [Available Commands](#available-commands)
7. [Safety Guidelines](#safety-guidelines)
8. [Troubleshooting](#troubleshooting)

---

## Prerequisites

### Required Software
- **Python 3.12+** (comes with Ubuntu 24.04)
- **uv** package manager (recommended)
- **Ubuntu 24.04** cloud instance or server

### Required Hardware
- **MAVLink-compatible drone or simulator** with network access
  - Must have an accessible **IP address and port** (e.g., via UDP, TCP, or serial)
  - Connection details will be configured in `.env` file

### Required Accounts (for AI agent)
- **OpenAI API key** (for GPT-4o-mini) or **Anthropic API key** (for Claude)

### Verify Python Installation
```bash
python3 --version  # Ubuntu 24.04 comes with Python 3.12
```

---

## System Preparation (Ubuntu 24.04)

**If you're starting from a fresh Ubuntu 24.04 cloud instance** (AWS, Digital Ocean, Linode, etc.):

### Prepare the System

```bash
# Update package list
sudo apt update

# Upgrade packages (non-interactive, no prompts)
sudo DEBIAN_FRONTEND=noninteractive apt upgrade -y -o Dpkg::Options::="--force-confdef" -o Dpkg::Options::="--force-confold"

# Install essential dependencies
sudo apt install -y python3 python3-pip python3-venv git curl build-essential

# Verify Python version (Ubuntu 24.04 comes with Python 3.12)
python3 --version
```

**Expected output:** `Python 3.12.x`

---

## Installation

### Step 1: Clone the Repository

Clone the repository to your system:

```bash
git clone https://github.com/PeterJBurke/MAVLinkMCP.git
cd MAVLinkMCP
```

### Step 2: Install uv Package Manager

Install `uv` if you don't have it already:

```bash
curl -LsSf https://astral.sh/uv/install.sh | sh
```

**Restart your shell or load the environment:**

```bash
source $HOME/.bashrc
# Or if using zsh: source $HOME/.zshrc
```

Verify installation:
```bash
uv --version
```

### Step 3: Install Project Dependencies

Install all dependencies with uv:

```bash
uv sync
```

This will:
- Create a virtual environment automatically
- Install all required packages (mavsdk, mcp-agent, mcp-server)
- Lock dependencies in `uv.lock` for reproducibility

---

## Configuration

### Step 4: Verify Drone Connection Settings

Your drone connection is already configured in `.env`:
```bash
cat .env
```

Should show something like:
```
MAVLINK_ADDRESS=<your-drone-ip>
MAVLINK_PORT=14540
```

**Note:** Replace with your actual drone's IP address and port.

✅ **This file is gitignored and won't be committed to GitHub.**

### Step 5: Set Up API Keys (For AI Agent Usage)

If you want to use the AI agent to control the drone with natural language, create the secrets file:

```bash
cd examples/
nano fastagent.secrets.yaml
```

Add your API key:
```yaml
openai:
    api_key: sk-your-actual-api-key-here
```

Or for Anthropic Claude:
```yaml
anthropic:
    api_key: sk-ant-your-actual-api-key-here
```

**Save and exit** (Ctrl+X, then Y, then Enter)

---

## Testing the Connection

### Step 6: Test Basic MCP Server Connection

Before flying, verify the server can connect to your drone:

```bash
# Make sure you're in the project root
cd MAVLinkMCP

# Run the MCP server (environment is loaded from .env automatically)
uv run src/server/mavlinkmcp.py
```

**Expected Output:**
```
INFO - Connecting to drone at <your-drone-ip>:<port>
INFO - Waiting for drone to connect at <your-drone-ip>:<port>
INFO - Connected to drone at <your-drone-ip>:<port>!
INFO - Waiting for drone to have a global position estimate...
```

If you see these messages, **your connection is working!** Press `Ctrl+C` to stop.

⚠️ **If connection fails**, see [Troubleshooting](#troubleshooting) section below.

---

## Usage Methods

You have **two ways** to control your drone:

### Method 1: AI Agent with Natural Language (Recommended for Beginners)
### Method 2: Direct MCP Tool Calls (For Advanced Users/Integration)

---

## Method 1: Using the AI Agent 🤖

The AI agent lets you control the drone using **natural language commands**.

### Step 7: Run the Example Agent

```bash
cd MAVLinkMCP

# Run the agent (environment is loaded from .env automatically)
uv run examples/example_agent.py
```

**Important:** The FastAgent automatically starts the MCP server as a subprocess. You do **not** need to run the server separately in another terminal!

### Step 8: Start Flying! 🚁

The agent will start and you can type commands. Here are examples:

#### Basic Flight Commands

**Pre-flight:**
```
You: Arm the drone
Agent: [Arms the drone]

You: What is the current position of the drone?
Agent: [Returns GPS coordinates and altitude]
```

**Takeoff:**
```
You: Take off to 5 meters altitude
Agent: [Commands takeoff to 5m]
```

**Movement:**
```
You: Move 2 meters forward
Agent: [Moves drone forward]

You: Move 3 meters to the right and 1 meter up
Agent: [Executes the movement]
```

**Landing:**
```
You: Land the drone
Agent: [Initiates landing]
```

**Telemetry:**
```
You: Show me the IMU data
Agent: [Returns sensor data]

You: What is the flight mode?
Agent: [Returns current mode]
```

**Mission Planning:**
```
You: Start a mission to fly to latitude 47.397742, longitude 8.545594 at 10 meters altitude
Agent: [Creates and starts mission]
```

### Stop the Agent
Type `STOP` or press `Ctrl+C`

---

## Method 2: Direct MCP Integration (Advanced)

For developers integrating with other MCP clients or custom applications, you can run the MCP server standalone. **Only use this method if you're NOT using the AI agent** (Method 1 above - which starts the server automatically).

The server runs on `stdio` transport:

```bash
uv run src/server/mavlinkmcp.py
```

---

## Available Commands

### Flight Control Tools

| Tool | Description | Parameters |
|------|-------------|------------|
| `arm_drone()` | Arm the drone motors | None |
| `takeoff(altitude)` | Take off to specified altitude | `takeoff_altitude` (float, default: 3.0m) |
| `land()` | Land at current location | None |
| `move_to_relative(lr, fb, alt, yaw)` | Move relative to current position | `lr` (left/right), `fb` (forward/back), `altitude`, `yaw` |

### Telemetry Tools

| Tool | Description | Returns |
|------|-------------|---------|
| `get_position()` | Get GPS position and altitude | lat, lon, absolute_alt, relative_alt |
| `get_flight_mode()` | Get current flight mode | Flight mode string |
| `print_status_text()` | Get status messages | Status type and text |
| `get_imu(n)` | Get IMU sensor data | Acceleration, gyro, magnetometer (n samples) |

### Mission Tools

| Tool | Description | Parameters |
|------|-------------|------------|
| `initiate_mission(points, rtl)` | Start waypoint mission | `mission_points` (list), `return_to_launch` (bool) |
| `print_mission_progress()` | Get mission progress | None |

---

## Safety Guidelines ⚠️

### CRITICAL SAFETY RULES:

1. **Always maintain visual line of sight** with your drone
2. **Check battery level** before flight
3. **Verify GPS lock** before arming (wait for "global position ok")
4. **Test in open area** away from people, buildings, and obstacles
5. **Have manual RC override ready** at all times
6. **Start with low altitudes** (3-5 meters) for testing
7. **Check local regulations** and flight restrictions
8. **Never fly in bad weather** (wind, rain, low visibility)

### Pre-Flight Checklist:
- [ ] Drone battery charged
- [ ] Propellers secure
- [ ] Clear flight area
- [ ] GPS lock confirmed
- [ ] RC transmitter on and ready
- [ ] Network connection to drone stable
- [ ] Emergency landing plan prepared

### Emergency Procedures:
- **Loss of Connection**: Drone should RTL (Return to Launch) automatically
- **Unstable Flight**: Command immediate landing or use RC override
- **Network Issues**: Use RC transmitter for manual control

---

## Troubleshooting

### Connection Issues

**Problem: "Failed to connect to drone"**

Solutions:
1. Verify drone is powered on and network accessible:
   ```bash
   ping <your-drone-ip>
   ```

2. Check if port is open:
   ```bash
   nc -zv <your-drone-ip> <your-drone-port>
   ```

3. Verify environment variables are set:
   ```bash
   echo $MAVLINK_ADDRESS
   echo $MAVLINK_PORT
   ```

4. Check firewall settings (may need to allow UDP traffic)

**Problem: "Waiting for global position estimate" (hangs)**

Solutions:
1. Ensure drone has GPS fix (needs clear sky view)
2. Wait 1-2 minutes for GPS satellites to lock
3. Check drone's GPS module is functioning

**Problem: "OpenAI API key not found"**

Solutions:
1. Verify `fastagent.secrets.yaml` exists in `examples/` directory
2. Check YAML formatting is correct (no tabs, proper indentation)
3. Verify API key is valid

### Flight Issues

**Problem: "Arming failed"**

Solutions:
1. Check all pre-arm checks pass
2. Verify GPS lock is obtained
3. Ensure drone is in a safe state
4. Check battery voltage is sufficient

**Problem: "Offboard mode failed to start"**

Solutions:
1. Ensure drone is armed first
2. Check drone firmware supports offboard mode
3. Verify RC transmitter is on (some drones require this)

### Getting Logs

Enable detailed logging:
```bash
export MAVSDK_LOG_LEVEL=DEBUG
uv run src/server/mavlinkmcp.py
```

---

## Testing Without a Real Drone

For development/testing, you can use a **simulated drone** with PX4 SITL (Software In The Loop):

```bash
# Install PX4 SITL simulator (separate terminal)
# Then change .env to:
MAVLINK_ADDRESS=127.0.0.1
MAVLINK_PORT=14540
```

---

## Quick Reference Commands

### Start MCP Server Only:
```bash
cd MAVLinkMCP
uv run src/server/mavlinkmcp.py
```

### Start AI Agent:
```bash
cd MAVLinkMCP
uv run examples/example_agent.py
```

### Quick Launch with Script:
```bash
./start_agent.sh
```

### Check Connection:
```bash
ping <your-drone-ip>
nc -zv <your-drone-ip> <your-drone-port>
```

---

## Support and Documentation

- **MCP Protocol**: https://modelcontextprotocol.io/introduction
- **MAVSDK Documentation**: https://mavsdk.mavlink.io/main/en/
- **PX4 User Guide**: https://docs.px4.io/main/en/

---

## Example Flight Session

Here's a complete example session:

```bash
# Terminal 1: Start the agent
cd MAVLinkMCP
uv run examples/example_agent.py

# You see: Connected to drone at <your-drone-ip>:<port>!

You: What is the current position?
Agent: The drone is at latitude 47.397742, longitude 8.545594, altitude 0.0m

You: Arm the drone
Agent: Drone armed successfully

You: Take off to 3 meters
Agent: Takeoff initiated to 3.0 meters

# Wait 5-10 seconds for takeoff to complete

You: What is the current altitude?
Agent: Current relative altitude is 2.9 meters

You: Move 2 meters forward
Agent: Moving drone forward 2 meters

# Wait for movement to complete

You: Land the drone
Agent: Landing initiated

You: STOP
# Session ends
```

---

**Happy Flying! 🚁✨**

Remember: Safety first, always maintain visual contact with your drone, and have fun!

