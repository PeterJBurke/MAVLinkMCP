# MCP Tools vs MAVSDK Methods

Complete reference showing which MCP tools are direct equivalents to MAVSDK methods and which are custom implementations.

**Last Updated:** December 2024  
**MAVLink MCP Version:** 1.3.1  
**Total Tools:** 41

---

## Summary

| Category | Direct MAVSDK Equivalent | Custom Implementation |
|----------|:------------------------:|:---------------------:|
| **Flight Control** | 3 | 2 |
| **Navigation** | 2 | 6 |
| **Mission Management** | 5 | 5 |
| **Telemetry** | 14 | 0 |
| **Parameter Management** | 3 | 0 |
| **Safety** | 1 | 1 |
| **TOTAL** | **28** | **14** |

---

## Complete Tool Reference

| MCP Tool | MAVSDK Equivalent? | MAVSDK Method / Description |
|----------|:------------------:|----------------------------|
| **Basic Flight Control** |
| `arm_drone` | ✅ Yes | `drone.action.arm()` |
| `disarm_drone` | ✅ Yes | `drone.action.disarm()` |
| `takeoff` | ⚠️ Partial | `drone.action.set_takeoff_altitude()` + `drone.action.takeoff()` |
| `land` | ✅ Yes | `drone.action.land()` |
| `hold_position` | ❌ No | Uses `goto_location()` with current position (avoids LOITER mode altitude drift) |
| **Emergency & Safety** |
| `return_to_launch` | ✅ Yes | `drone.action.return_to_launch()` |
| `kill_motors` | ⚠️ Partial | `drone.action.disarm()` with force flag (emergency shutdown) |
| `get_battery` | ✅ Yes | `drone.telemetry.battery()` |
| **Navigation** |
| `get_position` | ✅ Yes | `drone.telemetry.position()` |
| `move_to_relative` | ❌ No | Calculates GPS coordinates from NED offsets, then calls `goto_location()` |
| `go_to_location` | ⚠️ Partial | `drone.action.goto_location()` with destination tracking for landing gate |
| `monitor_flight` | ❌ No | Custom chunked progress monitoring (30s updates, uses telemetry.position) |
| `get_home_position` | ✅ Yes | `drone.telemetry.home()` |
| `set_max_speed` | ✅ Yes | `drone.action.set_maximum_speed()` |
| `set_yaw` | ❌ No | Uses `goto_location()` with current position + yaw parameter (MAVSDK workaround) |
| `reposition` | ❌ No | Uses `goto_location()` with loiter behavior (move and hold at location) |
| **Mission Management** |
| `initiate_mission` | ❌ No | Combines: `mission_raw.upload_mission()` + `mission.set_return_to_launch_after_mission()` + `mission.start_mission()` |
| `print_mission_progress` | ✅ Yes | `drone.mission.mission_progress()` |
| `pause_mission` | ⚠️ Deprecated | `drone.mission.pause_mission()` (DEPRECATED - unsafe, causes crashes) |
| `hold_mission_position` | ❌ No | Custom GUIDED mode hold using `goto_location()` (safe alternative to pause_mission) |
| `resume_mission` | ⚠️ Partial | `drone.mission.start_mission()` with enhanced diagnostics and mode verification |
| `clear_mission` | ✅ Yes | `drone.mission.clear_mission()` |
| `upload_mission` | ✅ Yes | `drone.mission_raw.upload_mission()` |
| `download_mission` | ✅ Yes | `drone.mission_raw.download_mission()` |
| `set_current_waypoint` | ✅ Yes | `drone.mission.set_current_mission_item()` |
| `is_mission_finished` | ⚠️ Partial | `drone.mission.is_mission_finished()` with enhanced progress details |
| **Telemetry & Monitoring** |
| `get_flight_mode` | ✅ Yes | `drone.telemetry.flight_mode()` |
| `get_health` | ✅ Yes | `drone.telemetry.health()` |
| `get_speed` | ✅ Yes | `drone.telemetry.velocity_ned()` |
| `get_attitude` | ✅ Yes | `drone.telemetry.attitude_euler()` |
| `get_gps_info` | ✅ Yes | `drone.telemetry.gps_info()` |
| `get_in_air` | ✅ Yes | `drone.telemetry.in_air()` |
| `get_armed` | ✅ Yes | `drone.telemetry.armed()` |
| `print_status_text` | ✅ Yes | `drone.telemetry.status_text()` |
| `get_imu` | ✅ Yes | `drone.telemetry.imu()` |
| `get_health_all_ok` | ✅ Yes | `drone.telemetry.health_all_ok()` |
| `get_landed_state` | ✅ Yes | `drone.telemetry.landed_state()` |
| `get_rc_status` | ✅ Yes | `drone.telemetry.rc_status()` |
| `get_heading` | ✅ Yes | `drone.telemetry.heading()` |
| `get_odometry` | ✅ Yes | `drone.telemetry.odometry()` |
| **Parameter Management** |
| `get_parameter` | ⚠️ Partial | `drone.param.get_param_int()` or `get_param_float()` with auto-type detection |
| `set_parameter` | ⚠️ Partial | `drone.param.set_param_int()` or `set_param_float()` with auto-type detection |
| `list_parameters` | ✅ Yes | `drone.param.get_all_params()` |
| **Custom Tools** |
| `check_arrival` | ❌ No | Calculates distance using haversine formula, checks if within threshold |
| `set_flight_mode` | ❌ No | Maps mode names to actions (HOLD→hold(), RTL→return_to_launch(), etc.) |

---

## Detailed Explanations

### Direct MAVSDK Equivalents (23 tools)

These tools are simple wrappers around MAVSDK methods with minimal additional logic:

- **Flight Control:** `arm_drone`, `disarm_drone`, `land`
- **Navigation:** `get_position`, `get_home_position`, `set_max_speed`
- **Mission:** `print_mission_progress`, `clear_mission`, `upload_mission`, `download_mission`, `set_current_waypoint`
- **Telemetry:** All 9 telemetry tools are direct equivalents
- **Parameters:** `list_parameters` is direct equivalent

### Partial Equivalents (6 tools)

These tools use MAVSDK methods but add functionality:

1. **`takeoff`** - Calls `set_takeoff_altitude()` then `takeoff()` (2 methods)
2. **`kill_motors`** - Calls `disarm()` with force flag for emergency shutdown
3. **`go_to_location`** - Wraps `goto_location()` with coordinate validation and altitude conversion
4. **`resume_mission`** - Calls `start_mission()` but adds mode transition verification and waypoint tracking
5. **`is_mission_finished`** - Calls `is_mission_finished()` but adds detailed progress information
6. **`get_parameter` / `set_parameter`** - Auto-detects parameter type (int/float) and calls appropriate method

### Custom Implementations (13 tools)

These tools provide functionality not directly available in MAVSDK or combine multiple operations:

1. **`hold_position`** - Uses `goto_location()` with current position to avoid LOITER mode altitude drift
2. **`move_to_relative`** - Converts NED offsets to GPS coordinates, then navigates
3. **`set_yaw`** - Uses `goto_location()` workaround (MAVSDK doesn't have direct yaw-only command)
4. **`reposition`** - Combines navigation with loiter behavior
5. **`initiate_mission`** - Combines upload, RTL setting, and mission start in one operation
6. **`hold_mission_position`** - Custom GUIDED mode hold (safe alternative to deprecated pause_mission)
7. **`check_arrival`** - Calculates distance using haversine formula and checks arrival status
8. **`set_flight_mode`** - Maps mode names to appropriate action methods (not direct mode setting)
9. **`pause_mission`** - Direct equivalent but DEPRECATED due to safety issues

---

## Why Custom Implementations?

### Safety & Reliability
- **`hold_position`** - Avoids LOITER mode which causes altitude drift in ArduPilot
- **`hold_mission_position`** - Safe alternative to pause_mission (prevents crashes)
- **`kill_motors`** - Emergency shutdown with force flag

### MAVSDK Limitations
- **`set_yaw`** - MAVSDK doesn't have yaw-only command, uses goto_location workaround
- **`check_arrival`** - MAVSDK doesn't provide arrival checking, calculates distance manually

### Convenience & Usability
- **`move_to_relative`** - Easier than calculating GPS coordinates manually
- **`initiate_mission`** - Single command instead of upload + configure + start
- **`go_to_location`** - Handles altitude conversion (relative vs absolute) automatically
- **`set_flight_mode`** - User-friendly mode names instead of raw mode IDs

### Enhanced Functionality
- **`get_parameter` / `set_parameter`** - Auto-detects parameter type
- **`resume_mission`** - Adds diagnostics and mode verification
- **`is_mission_finished`** - Adds detailed progress information

---

## MAVSDK Methods Not Exposed as Tools

The following MAVSDK methods are available but not implemented as MCP tools:

- `action.get_takeoff_altitude()` - Read takeoff altitude
- `action.get_maximum_speed()` - Read max speed
- `action.get_return_to_launch_altitude()` - Read RTL altitude
- `action.set_return_to_launch_altitude()` - Set RTL altitude
- `telemetry.landed_state()` - Get landed state
- `telemetry.rc_status()` - Get RC controller status
- `telemetry.heading()` - Get compass heading
- `info.get_version()` - Get firmware version
- `info.get_flight_information()` - Get flight time/distance
- All camera, gimbal, offboard, follow_me, geofence methods

See [MAVSDK_METHODS.md](MAVSDK_METHODS.md) for complete list.

---

## Usage Recommendations

### For Simple Operations
Use direct equivalent tools - they're straightforward wrappers:
- `arm_drone`, `disarm_drone`, `land`, `return_to_launch`
- All telemetry tools (`get_position`, `get_battery`, etc.)

### For Complex Operations
Use custom tools - they handle complexity for you:
- `initiate_mission` - Instead of upload + start separately
- `move_to_relative` - Instead of calculating GPS manually
- `check_arrival` - Instead of calculating distance yourself

### For Safety-Critical Operations
Use custom safe alternatives:
- `hold_mission_position` - Instead of deprecated `pause_mission`
- `hold_position` - Instead of `hold()` (avoids LOITER mode)

---

## Resources

- [MAVSDK Methods Reference](MAVSDK_METHODS.md) - Complete MAVSDK API reference
- [MAVLink Commands Reference](MAVLINK_COMMANDS.md) - MAVLink command mappings
- [Project Status](STATUS.md) - Current features and roadmap
