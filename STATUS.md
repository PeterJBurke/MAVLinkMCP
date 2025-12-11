# MAVLink MCP - Project Status & Roadmap

## 🔴 CRITICAL SAFETY UPDATE (v1.2.3)

**⛔ `pause_mission()` HAS BEEN DEPRECATED DUE TO CRASH RISK ⛔**

During flight testing, `pause_mission()` caused a **drone crash** by descending from 25m to ground impact. The tool entered LOITER mode, which **does NOT hold current altitude** in ArduPilot.

**✅ SAFE ALTERNATIVE:** Use `hold_mission_position()` which stays in GUIDED mode.

**See:** [LOITER_MODE_CRASH_REPORT.md](LOITER_MODE_CRASH_REPORT.md) for full details.

---

## 🛡️ Active Flight Management System

**This MCP server is NOT just a MAVLink command pass-through!**

Traditional drone APIs simply forward commands to the flight controller. This MCP server actively manages the flight to prevent common AI mistakes:

### Why Active Management Matters

| Problem | Without Active Management | With MAVLink MCP |
|---------|--------------------------|------------------|
| LLM sends `go_to_location` then immediately `land` | Drone lands before reaching destination | **Landing Gate BLOCKS** - "Cannot land, 1.2km from destination!" |
| LLM sends `go_to_location` before takeoff completes | Drone flies horizontally at low altitude | **Takeoff waits** for altitude before returning |
| LLM forgets to check arrival status | User doesn't know flight progress | **monitor_flight** guides LLM with "CALL AGAIN" instructions |
| LLM stops monitoring mid-flight | Mission abandoned, drone left hovering | **action_required** field keeps LLM engaged until landed |

### Safety Features (v1.3.1)

#### Landing Gate
- `land()` checks if drone is at the registered destination
- If >20m away, landing is **BLOCKED** with helpful error
- Use `land(force=True)` only for emergencies

#### Takeoff Altitude Wait
- `takeoff()` waits until target altitude is reached before returning
- Prevents navigation commands while still climbing

#### Destination Tracking
- `go_to_location()` registers the target in `pending_destination`
- Other tools know where you're heading and can verify arrival

#### Complete Flight Lifecycle Management (v1.4.0)
- `monitor_flight()` provides 30-second progress updates (hardcoded, LLM cannot override)
- Each response includes `DISPLAY_TO_USER` string to show the user
- When drone arrives (within 20m): automatically initiates landing
- Waits for confirmed touchdown: ON_GROUND + not in_air + altitude < 2m
- Confirms stability for 3 seconds before declaring landed
- Returns `mission_complete: true` only when drone is physically on the ground
- **The LLM cannot return until the drone has landed**

### Recommended Prompt (v1.4.0)

```
Arm the drone, takeoff to 50 meters, fly to [DESTINATION], and land.

After each monitor_flight, you MUST print the DISPLAY_TO_USER value.
You MUST call monitor_flight at least 20 times or until mission_complete is true, whichever comes first.
```

**Why this prompt works:**
- "at least 20 times" prevents ChatGPT from stopping after a few calls
- "print the DISPLAY_TO_USER value" forces visible output to the user
- Landing is fully automatic - when drone arrives, `monitor_flight` lands it and waits for touchdown
- `mission_complete: true` only returns when drone is physically on the ground

**Update Interval:** 30 seconds (hardcoded, LLM cannot override)

Set `auto_land=False` if you want to hover at destination instead of landing.

### Example Flight Output (v1.4.0)

A typical 3-minute flight with 30-second updates:

```
#1: 🚁 FLYING | Dist: 1096m | Alt: 36.9m | Speed: 9.9m/s | ETA: 1m 51s | 27%
#2: 🚁 FLYING | Dist: 626m | Alt: 22.9m | Speed: 9.9m/s | ETA: 1m 3s | 59%
#3: 🚁 FLYING | Dist: 57m | Alt: 6.0m | Speed: 9.9m/s | ETA: 5s | 96%
#4: ✅ MISSION COMPLETE | Landed safely | Flight time: 198s
```

**Only 4 monitor_flight calls needed!** The auto-land + touchdown wait happens inside call #4.

---

## ✅ Current Status (v1.4.0 - Complete Flight Lifecycle Management)

### Production Ready with Full Autonomous Flight
The MAVLink MCP Server is **production-ready** with complete autonomous flight from arm to confirmed landing. **v1.4.0 ensures the drone physically lands before returning mission_complete.**

**Last Updated:** December 11, 2025  
**Version:** 1.4.0 (Complete Flight Lifecycle Management)  
**Total Tools:** 41 MCP tools (1 deprecated for safety)  
**Tested With:** ArduPilot SITL, ChatGPT Developer Mode

### What's New in v1.4.0

| Feature | Description |
|---------|-------------|
| **Confirmed Touchdown** | `monitor_flight` now waits for drone to physically land before returning `mission_complete: true` |
| **30-Second Updates** | Reduced update frequency to prevent ChatGPT from hitting tool call limits |
| **Robust Landing Detection** | Requires: ON_GROUND + not in_air + altitude < 2m + 3-second stability check |
| **Fixed wait_seconds Override** | Removed parameter so LLM can't override the 30-second interval |
| **Bug Fixes** | Fixed LogColors.CMD typo that caused crashes at arrival |

---

## 🎯 Available Tools (41 Total)

### Basic Flight Control (5 tools)
- ✅ `arm_drone` - Arm motors for flight
- ✅ `disarm_drone` - Disarm motors safely
- ✅ `takeoff` - Autonomous takeoff to specified altitude (waits for altitude)
- ✅ `land` - Land at current position (**LANDING GATE**: blocks if not at destination)
- ✅ `hold_position` - Hold position in GUIDED mode (prevents altitude drift)

### Emergency & Safety (3 tools)
- ✅ `return_to_launch` - Emergency return home (RTL)
- ✅ `kill_motors` - Emergency motor cutoff ⚠️
- ✅ `get_battery` - Battery voltage & percentage monitoring

### Navigation (8 tools)
- ✅ `get_position` - Current GPS coordinates & altitude
- ✅ `move_to_relative` - Relative NED movement
- ✅ `go_to_location` - Absolute GPS navigation (returns immediately, registers destination)
- ✅ `monitor_flight` - **NEW** Real-time flight updates (5s, returns DISPLAY_TO_USER for user)
- ✅ `get_home_position` - Home/RTL location
- ✅ `set_max_speed` - Speed limiting for safety
- ✅ `set_yaw` - Set heading without moving
- ✅ `reposition` - Move to location and loiter

### Mission Management (10 tools)
- ✅ `initiate_mission` - Upload and start waypoint missions
- ✅ `print_mission_progress` - Mission status monitoring
- ⛔ `pause_mission` - **DEPRECATED** - Unsafe, causes crashes (use hold_mission_position)
- ✅ `hold_mission_position` - **NEW** Hold position in GUIDED mode (SAFE alternative)
- ✅ `resume_mission` - Resume paused mission (with diagnostics)
- ✅ `clear_mission` - Remove all waypoints
- ✅ `upload_mission` - **NEW** Upload mission without starting
- ✅ `download_mission` - **NEW** Retrieve mission from drone
- ✅ `set_current_waypoint` - **NEW** Jump to specific waypoint
- ✅ `is_mission_finished` - **NEW** Check mission completion (with progress)

### Telemetry & Monitoring (14 tools)
- ✅ `get_flight_mode` - Current flight mode
- ✅ `get_health` - Pre-flight system checks (detailed)
- ✅ `get_health_all_ok` - **NEW** Quick go/no-go health check
- ✅ `get_speed` - Ground speed & velocity
- ✅ `get_attitude` - Roll, pitch, yaw
- ✅ `get_heading` - **NEW** Compass heading in degrees
- ✅ `get_gps_info` - Satellite count & GPS quality
- ✅ `get_in_air` - Airborne status detection
- ✅ `get_landed_state` - **NEW** Detailed state (on ground/taking off/in air/landing)
- ✅ `get_armed` - Motor armed status
- ✅ `get_rc_status` - **NEW** RC controller connection & signal strength
- ✅ `get_odometry` - **NEW** Combined position, velocity, orientation
- ✅ `print_status_text` - Status message streaming
- ✅ `get_imu` - IMU sensor data (accel, gyro, mag)

### Parameter Management (3 tools) 🆕
- ✅ `get_parameter` - Read drone parameters (auto-detect type)
- ✅ `set_parameter` - Write drone parameters (with safety warnings)
- ✅ `list_parameters` - List all parameters (with filtering)

---

## 🔌 Connectivity & Deployment

### Supported Connections
- ✅ **TCP/UDP/Serial** - Configurable via `.env` file
- ✅ **Remote Network** - Connects to drones over internet
- ✅ **GPS Lock Detection** - Automatic GPS wait
- ✅ **Background Connection** - Async, non-blocking

### Integration Options
- ✅ **ChatGPT Web Interface** - HTTP/SSE transport
- ✅ **ngrok HTTPS Support** - Secure web tunneling
- ✅ **systemd Services** - Production deployment with auto-restart
- ✅ **Interactive CLI** - Direct command-line control
- ✅ **MCP Protocol** - Standard AI agent integration

---

## 🧪 Verified Test Results

### v1.1.0 Test (November 12, 2025)
**Platform:** ArduPilot SITL Copter  
**Interface:** ChatGPT Developer Mode via ngrok HTTPS

**Results:**
- ✅ All 25 tools accessible in ChatGPT
- ✅ Natural language commands working
- ✅ Battery monitoring functional
- ✅ Return to launch tested
- ✅ Emergency procedures verified
- ✅ Simultaneous QGroundControl + ChatGPT control

### v1.0.0 Test (November 2, 2025)
**Platform:** Virtual drone at TCP network address  
**Results:**
- ✅ Connection success
- ✅ Arming success
- ✅ Takeoff to 10m success
- ✅ Position tracking success
- ✅ Landing success

---

## 🐛 Known Limitations

1. **Battery Telemetry** - May show 0% on some simulated drones (works on real hardware)
2. **Windows Support** - Primarily tested on Ubuntu 24.04
3. **Single Drone** - One drone per server instance currently

---

## 🚀 Development Roadmap

### v1.2.0 - Advanced Features (In Progress) ✅

**Target:** 1-2 months  
**Focus:** Advanced control and mission planning

#### Parameter Management ✅ COMPLETE
- ✅ `get_parameter` - Read drone parameters (implemented Nov 16, 2025)
- ✅ `set_parameter` - Write drone parameters (implemented Nov 16, 2025)
- ✅ `list_parameters` - List all available parameters (implemented Nov 16, 2025)

#### Advanced Navigation ✅ COMPLETE
- ✅ `set_yaw` - Set heading without moving (implemented Nov 16, 2025)
- ✅ `reposition` - Move to location and loiter (implemented Nov 16, 2025)

#### Mission Enhancements ✅ COMPLETE
- ✅ `upload_mission` - Upload mission without starting (implemented Nov 16, 2025)
- ✅ `download_mission` - Get current mission from drone (implemented Nov 16, 2025)
- ✅ `set_current_waypoint` - Jump to specific waypoint (implemented Nov 16, 2025)
- ✅ `is_mission_finished` - Check mission completion status (implemented Nov 16, 2025)

**Estimated Time:** 3-4 weeks

---

### v1.3.0 - Telemetry & Monitoring Enhancements

**Target:** 1-2 months  
**Focus:** Complete telemetry coverage and monitoring tools

#### Enhanced Telemetry Tools
- ✅ `get_health_all_ok` - Quick health check (all systems OK?)
- ✅ `get_landed_state` - Detailed landed state (on ground / taking off / in air / landing)
- ✅ `get_rc_status` - RC controller connection status and signal strength
- ✅ `get_heading` - Compass heading in degrees
- ✅ `get_odometry` - Combined position, velocity, and orientation data

#### Mission Tools (Already Implemented)
- ✅ `start_mission` - Available via `initiate_mission` and `resume_mission`
- ⛔ `pause_mission` - DEPRECATED (use `hold_mission_position` instead)
- ✅ `clear_mission` - Already implemented

**Estimated Time:** 2-3 weeks

---

### v2.0.0 - Intelligent Automation

**Target:** 3-6 months  
**Focus:** AI-friendly automation and complex operations

#### Intelligent Flight Patterns
- [ ] `survey_area` - Automated area survey (lawn mower pattern)
- [ ] `perimeter_inspection` - Fly around building perimeter
- [ ] `spiral_climb` - Spiral up from position (360° panorama)
- [ ] `return_via_path` - Return using outbound path

#### Geofencing & Safety
- [ ] `set_geofence` - Define flight boundaries
- [ ] `check_geofence_violation` - Check if position violates fence
- [ ] `set_safety_radius` - Emergency RTL trigger distance
- [ ] `set_min_altitude` - Minimum safe altitude
- [ ] `set_max_altitude` - Maximum allowed altitude

#### Telemetry Logging & Analysis
- [ ] `start_telemetry_log` - Begin recording telemetry
- [ ] `stop_telemetry_log` - Stop recording
- [ ] `get_flight_statistics` - Flight time, distance, max altitude
- [ ] `export_flight_path` - Export GPS track for visualization

#### Multi-Drone Support
- [ ] Multiple drone connections
- [ ] Collision avoidance coordination
- [ ] Formation flying capabilities

**Estimated Time:** 2-3 months

---

### v3.0.0 - Enterprise & Community

**Target:** 6-12 months  
**Focus:** Production deployment and ecosystem

#### Web Interface
- [ ] Real-time flight monitoring dashboard
- [ ] Map visualization with drone position
- [ ] Flight history and replay
- [ ] Browser-based remote control
- [ ] Multi-user access with roles

#### Developer Experience
- [ ] Comprehensive unit tests
- [ ] Integration tests with SITL
- [ ] CI/CD pipeline
- [ ] Docker containerization
- [ ] REST API endpoints
- [ ] Plugin system for extensions

#### Enterprise Features
- [ ] User authentication
- [ ] Audit logging
- [ ] Compliance reports
- [ ] High availability setup
- [ ] Monitoring & alerting

#### Community
- [ ] Video tutorials
- [ ] Example mission templates
- [ ] Community forum
- [ ] Plugin marketplace

**Estimated Time:** 4-6 months

---

## 📊 Version Comparison

| Feature | v1.0.0 | v1.1.0 | v1.2.0 | v1.3.0 (Planned) |
|---------|--------|--------|--------|------------------|
| **Total Tools** | 10 | 25 | 35 | 40 (planned) |
| **Safety Tools** | 1 | 5 | 5 | 5 |
| **Complete Flight Cycle** | ❌ | ✅ | ✅ | ✅ |
| **Emergency Procedures** | ❌ | ✅ | ✅ | ✅ |
| **Battery Monitoring** | ❌ | ✅ | ✅ | ✅ |
| **Parameter Access** | ❌ | ❌ | ✅ | ✅ |
| **Advanced Navigation** | ❌ | ❌ | ✅ | ✅ |
| **Mission Enhancements** | Basic | Basic | Advanced | Advanced |
| **Enhanced Telemetry** | ❌ | ❌ | ❌ | ✅ **NEW** |
| **Production Ready** | ❌ | ✅ | ✅ | ✅ |

---

## 🎯 Success Metrics

### v1.1.0 Goals: ✅ ACHIEVED
- ✅ All Priority 1 safety tools working in SITL
- ✅ Battery monitoring functional
- ✅ Safe disarm capability
- ✅ Emergency RTL tested
- ✅ ChatGPT can safely fly complete missions

### v1.2.0 Goals
- ✅ Advanced mission planning
- ✅ Parameter configuration via ChatGPT
- ✅ Professional pilot feature set
- ✅ Enhanced navigation capabilities

### v1.3.0 Goals
- [ ] Complete telemetry coverage (health_all_ok, landed_state, rc_status, heading, odometry)
- [ ] Enhanced monitoring capabilities for AI agents
- [ ] Better safety checks with RC status monitoring
- [ ] Improved flight state awareness

### v2.0.0 Goals
- ✅ Autonomous survey missions
- ✅ Geofencing enforcement
- ✅ Multi-drone coordination
- ✅ Telemetry logging and analysis

---

## 🔧 Recent Changes

### November 17, 2025 - v1.2.4: 🎨 Logging & Code Cleanup ✅
**Improved:** JSON logging, transparency, and code quality
- **JSON I/O Logging:** All tool calls now log input/output as formatted JSON for easy debugging
- **Transparency:** `set_yaw` now clearly explains why it uses lat/lon coordinates (MAVSDK workaround)
- **Better Colors:** Darker, more readable colors in terminal logs
- **Code Cleanup:** Removed orbit functionality (unreliable with ArduPilot)
- **Mission Prep:** Switched to `mission_raw` API for future ArduPilot mission support
- **Tool Count:** 36 → 35 (orbit removed, missions moved to future development)

**Benefits:**
- Easier debugging with JSON input/output visibility
- Clearer logs with better color contrast and AGL/MSL altitude labels
- More focused feature set (removed confusing orbit feature)
- Foundation for reliable mission support in future release

---

### November 17, 2025 - v1.2.3: 🔴 CRITICAL SAFETY FIX 🔴
**DEPRECATED `pause_mission()` - Causes drone crashes!**
- **Issue:** Flight testing revealed `pause_mission()` causes altitude descent → ground impact
- **Root Cause:** LOITER mode does NOT hold current altitude in ArduPilot
- **Crash Details:** Descended from 25m → 5m → ground impact in 8 seconds
- **Fix:** `pause_mission()` now returns error and refuses to execute
- **Safe Alternative:** Use `hold_mission_position()` which stays in GUIDED mode
- **Documentation:** Added LOITER_MODE_CRASH_REPORT.md with full analysis
- **Impact:** Prevents future crashes - update immediately if using `pause_mission()`

### November 17, 2025 - v1.2.2: Mission Control Improvements ✅
**Added:** `hold_mission_position` tool and enhanced mission diagnostics
- **New Tool:** `hold_mission_position` - Alternative to `pause_mission` that stays in GUIDED mode (avoids LOITER)
- **Improved:** `resume_mission` now shows current waypoint, flight mode, and mode transition status
- **Improved:** `is_mission_finished` now includes waypoint progress, flight mode, and completion percentage
- **Benefit:** Better mission pause/resume control without unwanted flight mode changes
- **Use Case:** Pause mission for inspection without altitude drift from LOITER mode

### November 17, 2025 - Fixed `hold_position` Altitude Descent ✅
**Fixed:** `hold_position` now stays in GUIDED mode instead of switching to LOITER
- **Problem:** Drone was descending when `hold_position` was called due to mode switch
- **Root Cause:** `drone.action.hold()` switched from GUIDED → LOITER, causing altitude reference mismatch
- **Solution:** Now uses `goto_location(current_position)` to hold without mode change
- **Result:** Stable altitude maintenance, no unexpected descents during complex flight sequences

### November 16, 2025 - v1.2.1 Patch: Error Handling & Compatibility ✅
**Improved:** Based on comprehensive testing (67% success rate → better error messages)
- Enhanced mission upload validation with clear format error messages
- Battery monitoring fallback for uncalibrated systems (voltage-based estimates)
- Firmware compatibility matrix added to documentation

### November 16, 2025 - v1.2.0 Development: Advanced Navigation & Mission Enhancements ✅
**Added:** 6 new tools for advanced flight control

**Advanced Navigation (2 tools):**
- `set_yaw` - Rotate drone to face specific direction (with cardinal directions)
- `reposition` - Move to location and loiter

**Mission Enhancements (4 tools):**
- `upload_mission` - Upload mission without auto-starting
- `download_mission` - Retrieve mission from drone
- `set_current_waypoint` - Jump to specific waypoint index
- `is_mission_finished` - Check if mission completed

**Status:** v1.2.0 nearly complete! 35 tools total (+10 from v1.1.0)

### November 16, 2025 - v1.2.0 Development: Parameter Management ✅
**Added:** 3 new parameter management tools
- `get_parameter` - Read any drone parameter with auto-type detection
- `set_parameter` - Write parameters with safety warnings
- `list_parameters` - List all parameters with optional filtering

### November 16, 2025 - Documentation Cleanup
- Removed historical development notes
- Consolidated roadmap into STATUS.md
- Streamlined documentation structure
- **Files removed:** 4 redundant MD files

### November 12, 2025 - v1.1.0 Major Update
**Added:** 15 new MCP tools
- Critical safety tools (disarm, RTL, battery, hold, kill)
- System health checks
- GPS quality monitoring
- Speed and attitude telemetry
- Mission pause/resume/clear
- Absolute GPS navigation
- Speed limiting

**Impact:** Complete, safe drone operations from arm to disarm!

### November 12, 2025 - ArduPilot GUIDED Mode Fix
**Issue:** Previous implementation tried to use PX4 OFFBOARD mode on ArduPilot  
**Fix:** Updated to use ArduPilot-native GUIDED mode via `goto_location()` API

### November 2, 2025 - Relative Movement Bug Fix
**Issue:** `move_to_relative` not moving drone horizontally  
**Fix:** Added proper GPS coordinate calculation with latitude compensation

---

## 🤝 Contributing

We welcome contributions! Priority areas:

1. **Telemetry & Monitoring Tools** - Implement v1.3.0 features (health_all_ok, landed_state, rc_status, heading, odometry)
2. **Testing** - Test on different autopilots (PX4, ArduPlane)
3. **Documentation** - Improve setup guides and examples
4. **Bug Reports** - Report issues on GitHub
5. **Feature Requests** - Suggest new capabilities

---

## 📞 Support & Resources

- **Repository:** https://github.com/PeterJBurke/MAVLinkMCP
- **Issues:** https://github.com/PeterJBurke/MAVLinkMCP/issues
- **Discussions:** https://github.com/PeterJBurke/MAVLinkMCP/discussions
- **Documentation:** See README.md and other guides
- **Original Project:** https://github.com/ion-g-ion/MAVLinkMCP

### Documentation
- [README.md](README.md) - Main documentation
- [CHATGPT_SETUP.md](CHATGPT_SETUP.md) - ChatGPT integration guide
- [SERVICE_SETUP.md](SERVICE_SETUP.md) - systemd service deployment
- [LIVE_SERVER_UPDATE.md](LIVE_SERVER_UPDATE.md) - Update procedures
- [examples/README.md](examples/README.md) - Example usage

---

**Current Version:** v1.4.0 (41 tools - Complete Flight Lifecycle Management)  
**Status:** ✅ Production Ready - Full Autonomous Flight from Arm to Confirmed Landing  
**Next Release:** v2.0.0 (Intelligent Automation)  
**Maintainer:** Peter J Burke  
**Original Author:** Ion Gabriel
