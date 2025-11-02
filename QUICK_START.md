# Quick Start Guide - MAVLink MCP Server

## 🚀 Get Flying in 5 Minutes

This guide uses **uv** exclusively for fast, reliable dependency management.

---

## Prerequisites

- Python 3.10+
- Your drone's IP address and port
- OpenAI or Anthropic API key (for AI agent)

---

## Installation

### 1. Install uv

```bash
curl -LsSf https://astral.sh/uv/install.sh | sh
```

### 2. Clone and Setup

```bash
git clone https://github.com/PeterJBurke/MAVLinkMCP.git
cd MAVLinkMCP
uv sync
```

That's it! All dependencies are now installed.

---

## Configuration

### 3. Configure Drone Connection

Your `.env` file is already set up with:
```bash
MAVLINK_ADDRESS=172.233.128.95
MAVLINK_PORT=5678
```

To change it:
```bash
nano .env
```

### 4. Add API Key

Create the secrets file:
```bash
nano examples/fastagent.secrets.yaml
```

Add your key:
```yaml
openai:
    api_key: sk-your-actual-key-here
```

---

## Usage

### Test Connection

```bash
./test_connection.sh
```

### Start Flying

```bash
./start_agent.sh
```

Or manually:
```bash
uv run examples/example_agent.py
```

---

## Basic Flight Commands

Once the agent is running, try these commands:

```
You: What is the current position?
You: Arm the drone
You: Take off to 5 meters
You: Move 2 meters forward
You: What is the current altitude?
You: Land the drone
You: STOP
```

---

## Available Commands

### Flight Control
- `arm_drone()` - Arm motors
- `takeoff(altitude)` - Take off to altitude
- `land()` - Land at current location
- `move_to_relative(lr, fb, alt, yaw)` - Move relative to position

### Telemetry
- `get_position()` - Get GPS coordinates
- `get_flight_mode()` - Get flight mode
- `get_imu(n)` - Get IMU sensor data
- `print_status_text()` - Get status messages

### Missions
- `initiate_mission(points, rtl)` - Start waypoint mission
- `print_mission_progress()` - Check mission progress

---

## Troubleshooting

**Connection failed?**
```bash
ping 172.233.128.95
```

**Dependencies issue?**
```bash
uv sync
```

**Can't find uv command?**
```bash
curl -LsSf https://astral.sh/uv/install.sh | sh
source ~/.bashrc  # or restart terminal
```

---

## Safety Checklist

Before every flight:

- [ ] GPS lock confirmed
- [ ] Battery charged
- [ ] Clear flight area
- [ ] RC override ready
- [ ] Weather is good
- [ ] Visual line of sight maintained

---

## More Info

- Full Guide: [DEPLOYMENT_GUIDE.md](DEPLOYMENT_GUIDE.md)
- Project: [README.md](README.md)
- Examples: [examples/README.md](examples/README.md)

---

**Ready to fly? Run `./start_agent.sh` now! 🚁**

