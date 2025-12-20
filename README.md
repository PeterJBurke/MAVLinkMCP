# MAVLink MCP Server

A Python-based Model Context Protocol (MCP) server for AI-powered drone control. Connect LLMs to MAVLink-enabled drones (PX4, ArduPilot) for natural language flight control.

## 🔴 CRITICAL SAFETY NOTICE (v1.2.3)

**⛔ `pause_mission()` HAS BEEN DEPRECATED ⛔**

During flight testing, this tool caused a drone crash (25m → ground impact). Use `hold_mission_position()` instead.  
**See:** [LOITER_MODE_CRASH_REPORT.md](LOITER_MODE_CRASH_REPORT.md)

## Features

- 🤖 **AI-Powered Control**: Use natural language to command drones via GPT-4, Claude, or other LLMs
- 🚁 **MAVLink Compatible**: Works with PX4, ArduPilot, and other MAVLink drones
- 🔧 **MCP Protocol**: Standard Model Context Protocol for tool integration
- 📡 **Network/Serial Support**: Connect via UDP, TCP, or serial ports
- 💬 **ChatGPT Integration**: Direct control from ChatGPT web interface (see below)
- 📝 **Flight Logging**: Automatic logging of all tool calls and MAVLink commands (see [FLIGHT_LOGS.md](FLIGHT_LOGS.md))

---

## 🛡️ Active Flight Management System (v1.4.0)

**This MCP server doesn't just send MAVLink commands** — it actively manages the entire flight lifecycle to ensure safe, complete missions.

### The Problem with Simple Command Pass-Through

When an LLM controls a drone with simple command pass-through, common failures occur:

| AI Mistake | What Happens | Result |
|------------|--------------|--------|
| LLM sends `land()` before arrival | Drone lands kilometers from destination | ❌ Mission failed |
| LLM sends `go_to_location()` before takeoff completes | Drone flies horizontally at low altitude | ❌ Crash risk |
| LLM forgets to monitor flight | User has no idea what's happening | ❌ Poor UX |
| LLM stops monitoring mid-flight | Drone left hovering, battery drains | ❌ Drone lost |
| LLM checks arrival once, gives up | Mission abandoned at 50% | ❌ Incomplete |

### How MAVLink MCP Solves This

The server implements **Active Flight Management** with these safety systems:

#### 1. Takeoff Altitude Wait
```
LLM: takeoff(50)
MCP: [Waits until drone reaches 50m]
MCP: "Takeoff complete - drone at 50m AGL, safe to navigate"
```
The `takeoff()` function doesn't return until the target altitude is reached, preventing premature navigation commands.

#### 2. Landing Gate
```
LLM: land()
MCP: "BLOCKED - Drone is 1.2km from destination! Use monitor_flight() to track progress."
```
The `land()` function checks if the drone is at its registered destination. If not, landing is blocked to prevent accidental landings in wrong locations.

#### 3. Auto-Land with Confirmed Touchdown
```
LLM: monitor_flight()  [when drone is within 20m of destination]
MCP: [Automatically initiates landing]
MCP: [Waits for drone to physically touch ground - checks every 2 seconds]
MCP: [Confirms stable on ground for 3 seconds]
MCP: "✅ MISSION COMPLETE | Landed safely | Flight time: 198s"
     mission_complete: true
```
When the drone arrives at destination, `monitor_flight()` automatically:
1. Calls `land()`
2. Monitors descent (checking landed_state, in_air, and altitude)
3. Waits for confirmed touchdown (ON_GROUND + not in_air + altitude < 2m)
4. Confirms stability for 3 seconds
5. Returns `mission_complete: true`

**The LLM only stops when the drone is physically on the ground.**

#### 4. 30-Second Progress Updates
```
#1: 🚁 FLYING | Dist: 1096m | Alt: 36.9m | Speed: 9.9m/s | ETA: 1m 51s | 27%
#2: 🚁 FLYING | Dist: 626m | Alt: 22.9m | Speed: 9.9m/s | ETA: 1m 3s | 59%
#3: 🚁 FLYING | Dist: 57m | Alt: 6.0m | Speed: 9.9m/s | ETA: 5s | 96%
#4: ✅ MISSION COMPLETE | Landed safely | Flight time: 198s
```
Each `monitor_flight()` call waits 30 seconds (checking for arrival every second internally), then returns a progress update. This means:
- A 3-minute flight needs only ~6 tool calls
- ChatGPT doesn't hit its tool call limits
- User sees meaningful progress updates

---

## 🌐 Control Your Drone with ChatGPT

### Recommended Prompt (Copy This!)

```
Arm the drone, takeoff to 50 meters, fly to Aldrich park at 33.645834416678824, -117.84260096803916, and land.

After each monitor_flight, you MUST print the DISPLAY_TO_USER value.
You MUST call monitor_flight at least 20 times or until mission_complete is true, whichever comes first.
```

### What Happens

1. **Arm**: LLM calls `arm_drone()` → Motors armed
2. **Takeoff**: LLM calls `takeoff(50)` → MCP waits until 50m reached → Returns success
3. **Navigate**: LLM calls `go_to_location(...)` → Returns immediately, registers destination
4. **Monitor Loop**: LLM calls `monitor_flight()` repeatedly:
   - Each call waits 30 seconds, checking for arrival every second
   - Returns progress update with `DISPLAY_TO_USER`
   - When within 20m of destination: auto-lands and waits for touchdown
   - Returns `mission_complete: true` only when drone is on ground

### Example Flight Output

```
User: Arm the drone, takeoff to 50 meters, fly to Aldrich park, and land.
      After each monitor_flight, you MUST print the DISPLAY_TO_USER value.
      You MUST call monitor_flight at least 20 times or until mission_complete is true.

ChatGPT: [Calls arm_drone, takeoff, go_to_location, then monitor_flight in a loop]

#1: 🚁 FLYING | Dist: 1096m | Alt: 36.9m | Speed: 9.9m/s | ETA: 1m 51s | 27%
Continuing to monitor flight...

#2: 🚁 FLYING | Dist: 626m | Alt: 22.9m | Speed: 9.9m/s | ETA: 1m 3s | 59%
Continuing to monitor flight...

#3: 🚁 FLYING | Dist: 57m | Alt: 6.0m | Speed: 9.9m/s | ETA: 5s | 96%
Continuing to monitor flight...

#4: ✅ MISSION COMPLETE | Landed safely | Flight time: 198s

The mission has completed in 4 monitor_flight calls. The drone has landed safely.
```

### Setup Steps

1. Enable **Developer Mode** in ChatGPT settings (ChatGPT Plus/Pro required)
2. Start the HTTP MCP server: `./start_http_server.sh`
3. Add the MCP connector in ChatGPT with your server URL
4. Start flying with natural language!

📖 **[Complete ChatGPT Setup Guide →](CHATGPT_SETUP.md)**

---

## 🚀 Quick Start

### Prerequisites

- **Python 3.12+** (comes with Ubuntu 24.04)
- **uv** package manager ([install here](https://github.com/astral-sh/uv))
- **MAVLink-compatible drone or simulator**

### Installation

```bash
git clone https://github.com/PeterJBurke/droneserver.git
cd droneserver
uv sync
cp .env.example .env
# Edit .env with your drone's IP/port
```

### Run the Server

```bash
uv run src/server/droneserver.py
```

---

## 📋 Available Tools (41 Total)

| Category | Count | Key Tools |
|----------|-------|-----------|
| **Flight Control** | 5 | `arm_drone`, `disarm_drone`, `takeoff`, `land`, `hold_position` |
| **Emergency & Safety** | 3 | `return_to_launch`, `kill_motors`, `get_battery` |
| **Navigation** | 8 | `get_position`, `go_to_location`, `monitor_flight`, `set_yaw`, `reposition` |
| **Mission Management** | 10 | `initiate_mission`, `upload_mission`, `pause_mission`, `hold_mission_position`, `resume_mission` |
| **Telemetry** | 12 | `get_health`, `get_health_all_ok`, `get_landed_state`, `get_heading`, `get_rc_status`, `get_odometry` |
| **Parameter Management** | 3 | `get_parameter`, `set_parameter`, `list_parameters` |

**See [STATUS.md](STATUS.md) for complete tool list and descriptions.**

---

## 📊 Recent Updates

- ✅ **Dec 11, 2025**: v1.4.0 - **Complete Flight Lifecycle Management**
  - Auto-land waits for confirmed touchdown before returning `mission_complete`
  - 30-second progress updates (reduced from 5s to minimize tool calls)
  - Robust landing detection (ON_GROUND + not in_air + altitude < 2m + 3s stability check)
  - Fixed LogColors.CMD bug
- ✅ **Dec 10, 2025**: v1.3.1 - Added `monitor_flight` + Landing Gate safety
- ✅ **Dec 10, 2025**: v1.3.0 - Added 5 enhanced telemetry tools
- 🔴 **Nov 17, 2025**: v1.2.3 - CRITICAL: Deprecated `pause_mission()` due to crash risk

---

## 🔧 Configuration

Create a `.env` file:

```bash
MAVLINK_ADDRESS=<your-drone-ip>
MAVLINK_PORT=14540
MAVLINK_PROTOCOL=tcp  # tcp, udp, or serial
```

---

## 📖 Documentation

- **[ChatGPT Setup Guide](CHATGPT_SETUP.md)** - Control drone with ChatGPT
- **[Service Setup Guide](SERVICE_SETUP.md)** - Production deployment with systemd
- **[Project Status & Roadmap](STATUS.md)** - Current features and future plans
- **[Testing Guide](TESTING.md)** - Manual testing procedures

---

## ⚠️ Safety Guidelines

1. **Always maintain visual line of sight** with your drone
2. **Check battery level** before flight
3. **Verify GPS lock** before arming
4. **Have manual RC override ready** at all times
5. **Test in open area** away from people and obstacles

---

## 📞 Support

- 🐛 [Report Issues](https://github.com/PeterJBurke/droneserver/issues)
- 💬 [Discussions](https://github.com/PeterJBurke/droneserver/discussions)
- 📊 [Status & Roadmap](STATUS.md)

---

## License

MIT License - see [LICENSE](LICENSE) file for details.

## Acknowledgments

- Original project by [Ion Gabriel](https://github.com/ion-g-ion/MAVLinkMCP)
- Built with [MAVSDK](https://mavsdk.mavlink.io/)
- Uses [Model Context Protocol](https://modelcontextprotocol.io/)