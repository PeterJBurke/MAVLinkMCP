# MAVLink Commands Reference

Complete reference of all MAVLink commands (MAV_CMD) and their implementation status in MAVLink MCP.

**Last Updated:** December 2024  
**MAVLink Protocol Version:** 2.x  
**MAVLink MCP Version:** 1.2.4

---

## Important Note

**MAVLink MCP uses MAVSDK**, which abstracts MAVLink commands into high-level methods. Most commands are sent **indirectly** through MAVSDK methods rather than directly as raw MAVLink commands.

**Usage Types:**
- **Direct:** MAVLink command explicitly used in code (e.g., `command=16` for MAV_CMD_NAV_WAYPOINT in mission upload)
- **Indirect:** MAVLink command sent via MAVSDK method (e.g., `drone.action.arm()` sends MAV_CMD_COMPONENT_ARM_DISARM)
- **Not Implemented:** No MCP tool exists for this command

---

## Implementation Summary

| Category | Total Commands | Implemented | Coverage |
|----------|:--------------:|:-----------:|:--------:|
| **Navigation (NAV_*)** | 20 | 5 | 25% |
| **Flight Control (DO_*)** | 50+ | 3 | ~6% |
| **Mission** | 5 | 2 | 40% |
| **Camera (IMAGE_*/VIDEO_*)** | 10 | 0 | 0% |
| **Gimbal (DO_GIMBAL_*)** | 5 | 0 | 0% |
| **System (COMPONENT_*)** | 3 | 1 | 33% |
| **Calibration** | 5 | 0 | 0% |
| **Other** | 20+ | 0 | 0% |
| **TOTAL** | **~120** | **~11** | **~9%** |

---

## Navigation Commands (MAV_CMD_NAV_*)

| Command ID | Command Name | Implemented | MCP Tool | Usage Type | Notes |
|-----------|--------------|:-----------:|----------|------------|-------|
| 16 | `MAV_CMD_NAV_WAYPOINT` | ✅ | `go_to_location`, `reposition`, `upload_mission`, `initiate_mission` | Direct & Indirect | Used directly in mission upload (command=16), indirectly via goto_location() |
| 17 | `MAV_CMD_NAV_LOITER_UNLIM` | ❌ | - | - | Loiter indefinitely (not implemented) |
| 18 | `MAV_CMD_NAV_LOITER_TURNS` | ❌ | - | - | Loiter for N turns (not implemented) |
| 19 | `MAV_CMD_NAV_LOITER_TIME` | ❌ | - | - | Loiter for time duration (not implemented) |
| 20 | `MAV_CMD_NAV_RETURN_TO_LAUNCH` | ✅ | `return_to_launch` | Indirect | Via `drone.action.return_to_launch()` |
| 21 | `MAV_CMD_NAV_LAND` | ✅ | `land` | Indirect | Via `drone.action.land()` |
| 22 | `MAV_CMD_NAV_TAKEOFF` | ✅ | `takeoff` | Indirect | Via `drone.action.takeoff()` |
| 23 | `MAV_CMD_NAV_LAND_LOCAL` | ❌ | - | - | Land at local position (not implemented) |
| 24 | `MAV_CMD_NAV_TAKEOFF_LOCAL` | ❌ | - | - | Takeoff from local position (not implemented) |
| 25 | `MAV_CMD_NAV_FOLLOW` | ❌ | - | - | Follow target (not implemented) |
| 30 | `MAV_CMD_NAV_CONTINUE_AND_CHANGE_ALT` | ❌ | - | - | Continue and change altitude (not implemented) |
| 31 | `MAV_CMD_NAV_LOITER_TO_ALT` | ❌ | - | - | Loiter until altitude reached (not implemented) |
| 80 | `MAV_CMD_NAV_ROI` | ❌ | - | - | Set region of interest (not implemented) |
| 81 | `MAV_CMD_NAV_PATHPLANNING` | ❌ | - | - | Autonomous path planning (not implemented) |
| 82 | `MAV_CMD_NAV_SPLINE_WAYPOINT` | ❌ | - | - | Spline waypoint navigation (not implemented) |
| 84 | `MAV_CMD_NAV_VTOL_TAKEOFF` | ❌ | - | - | VTOL takeoff (not implemented) |
| 85 | `MAV_CMD_NAV_VTOL_LAND` | ❌ | - | - | VTOL landing (not implemented) |
| 92 | `MAV_CMD_NAV_GUIDED_ENABLE` | ✅ | `go_to_location`, `reposition`, `hold_position` | Indirect | Entered via `goto_location()` in GUIDED mode |
| 93 | `MAV_CMD_NAV_DELAY` | ❌ | - | - | Delay mission (not implemented) |
| 94 | `MAV_CMD_NAV_RALLY_POINT` | ❌ | - | - | Rally point (not implemented) |

---

## Flight Control Commands (MAV_CMD_DO_*)

| Command ID | Command Name | Implemented | MCP Tool | Usage Type | Notes |
|-----------|--------------|:-----------:|----------|------------|-------|
| 176 | `MAV_CMD_DO_SET_MODE` | ✅ | `set_flight_mode` | Indirect | Via flight mode changes (HOLD, RTL, LAND, GUIDED) |
| 178 | `MAV_CMD_DO_CHANGE_SPEED` | ✅ | `set_max_speed` | Indirect | Via `drone.action.set_maximum_speed()` |
| 180 | `MAV_CMD_DO_SET_PARAMETER` | ✅ | `set_parameter` | Indirect | Via `drone.param.set_param_int/float()` |
| 181 | `MAV_CMD_DO_JUMP` | ❌ | - | - | Jump to mission item (not implemented) |
| 182 | `MAV_CMD_DO_CHANGE_ALTITUDE` | ❌ | - | - | Change altitude (not implemented) |
| 183 | `MAV_CMD_DO_SET_HOME` | ❌ | - | - | Set home position (not implemented) |
| 189 | `MAV_CMD_DO_LAND_START` | ❌ | - | - | Start landing sequence (not implemented) |
| 190 | `MAV_CMD_DO_RALLY_LAND` | ❌ | - | - | Land at rally point (not implemented) |
| 191 | `MAV_CMD_DO_GO_AROUND` | ❌ | - | - | Go around (not implemented) |
| 192 | `MAV_CMD_DO_REPOSITION` | ✅ | `reposition` | Indirect | Via `goto_location()` with loiter |
| 193 | `MAV_CMD_DO_PAUSE_CONTINUE` | ❌ | - | - | Pause/continue mission (deprecated - use hold_mission_position) |
| 194 | `MAV_CMD_DO_SET_REVERSE` | ❌ | - | - | Set reverse mode (not implemented) |
| 195 | `MAV_CMD_DO_SET_ROI_LOCATION` | ❌ | - | - | Set ROI location (not implemented) |
| 196 | `MAV_CMD_DO_SET_ROI_WPNEXT_OFFSET` | ❌ | - | - | Set ROI to next waypoint (not implemented) |
| 197 | `MAV_CMD_DO_SET_ROI_NONE` | ❌ | - | - | Clear ROI (not implemented) |
| 200 | `MAV_CMD_DO_CONTROL_VIDEO` | ❌ | - | - | Control video (not implemented) |
| 201 | `MAV_CMD_DO_SET_ROI` | ❌ | - | - | Set ROI (not implemented) |
| 202 | `MAV_CMD_DO_DIGICAM_CONFIGURE` | ❌ | - | - | Configure camera (not implemented) |
| 203 | `MAV_CMD_DO_DIGICAM_CONTROL` | ❌ | - | - | Control camera (not implemented) |
| 204 | `MAV_CMD_DO_MOUNT_CONFIGURE` | ❌ | - | - | Configure gimbal mount (not implemented) |
| 205 | `MAV_CMD_DO_MOUNT_CONTROL` | ❌ | - | - | Control gimbal mount (not implemented) |
| 206 | `MAV_CMD_DO_SET_CAM_TRIGG_DIST` | ❌ | - | - | Set camera trigger distance (not implemented) |
| 214 | `MAV_CMD_DO_FENCE_ENABLE` | ❌ | - | - | Enable geofence (not implemented) |
| 215 | `MAV_CMD_DO_PARACHUTE` | ❌ | - | - | Parachute control (not implemented) |
| 220 | `MAV_CMD_DO_MOTOR_TEST` | ❌ | - | - | Motor test (not implemented) |
| 221 | `MAV_CMD_DO_INVERTED_FLIGHT` | ❌ | - | - | Inverted flight (not implemented) |
| 222 | `MAV_CMD_DO_GRIPPER` | ❌ | - | - | Gripper control (not implemented) |
| 223 | `MAV_CMD_DO_AUTOTUNE_ENABLE` | ❌ | - | - | Enable autotune (not implemented) |
| 224 | `MAV_CMD_NAV_SET_YAW_SPEED` | ✅ | `set_yaw` | Indirect | Via `goto_location()` with yaw parameter |
| 240 | `MAV_CMD_DO_SET_CAM_TRIGG_INTERVAL` | ❌ | - | - | Set camera trigger interval (not implemented) |
| 241 | `MAV_CMD_DO_MOUNT_CONTROL_QUAT` | ❌ | - | - | Control gimbal with quaternion (not implemented) |
| 242 | `MAV_CMD_DO_GUIDED_MASTER` | ❌ | - | - | Set GUIDED master (not implemented) |
| 243 | `MAV_CMD_DO_GUIDED_LIMITS` | ❌ | - | - | Set GUIDED limits (not implemented) |
| 245 | `MAV_CMD_DO_ENGINE_CONTROL` | ❌ | - | - | Engine control (not implemented) |
| 246 | `MAV_CMD_DO_SET_MISSION_CURRENT` | ✅ | `set_current_waypoint` | Indirect | Via `drone.mission.set_current_mission_item()` |
| 280 | `MAV_CMD_DO_SET_ACTUATOR` | ❌ | - | - | Set actuator (not implemented) |
| 300 | `MAV_CMD_DO_ORBIT` | ❌ | - | - | Orbit around point (not implemented - unreliable) |
| 400 | `MAV_CMD_DO_TRIGGER_CONTROL` | ❌ | - | - | Trigger control (not implemented) |

---

## Mission Commands

| Command ID | Command Name | Implemented | MCP Tool | Usage Type | Notes |
|-----------|--------------|:-----------:|----------|------------|-------|
| 16 | `MAV_CMD_NAV_WAYPOINT` | ✅ | `upload_mission`, `initiate_mission` | Direct | Used directly in mission upload (command=16) |
| Mission Start | Mission Start | ✅ | `initiate_mission`, `resume_mission` | Indirect | Via `drone.mission.start_mission()` |
| Mission Pause | Mission Pause | ✅ | `hold_mission_position` | Indirect | Via GUIDED mode hold (not deprecated pause_mission) |
| Mission Clear | Mission Clear | ✅ | `clear_mission` | Indirect | Via `drone.mission.clear_mission()` |
| Mission Download | Mission Download | ✅ | `download_mission` | Indirect | Via `drone.mission_raw.download_mission()` |

---

## Camera Commands (MAV_CMD_IMAGE_* / MAV_CMD_VIDEO_*)

| Command ID | Command Name | Implemented | MCP Tool | Usage Type | Notes |
|-----------|--------------|:-----------:|----------|------------|-------|
| 2000 | `MAV_CMD_IMAGE_START_CAPTURE` | ❌ | - | - | Start image capture (not implemented) |
| 2001 | `MAV_CMD_IMAGE_STOP_CAPTURE` | ❌ | - | - | Stop image capture (not implemented) |
| 2002 | `MAV_CMD_IMAGE_START_CAPTURE_SEQ` | ❌ | - | - | Start capture sequence (not implemented) |
| 2003 | `MAV_CMD_IMAGE_STOP_CAPTURE_SEQ` | ❌ | - | - | Stop capture sequence (not implemented) |
| 2500 | `MAV_CMD_VIDEO_START_CAPTURE` | ❌ | - | - | Start video capture (not implemented) |
| 2501 | `MAV_CMD_VIDEO_STOP_CAPTURE` | ❌ | - | - | Stop video capture (not implemented) |
| 2502 | `MAV_CMD_VIDEO_START_STREAMING` | ❌ | - | - | Start video streaming (not implemented) |
| 2503 | `MAV_CMD_VIDEO_STOP_STREAMING` | ❌ | - | - | Stop video streaming (not implemented) |
| 531 | `MAV_CMD_SET_CAMERA_ZOOM` | ❌ | - | - | Set camera zoom (not implemented) |
| 532 | `MAV_CMD_SET_CAMERA_FOCUS` | ❌ | - | - | Set camera focus (not implemented) |

---

## Gimbal Commands (MAV_CMD_DO_GIMBAL_*)

| Command ID | Command Name | Implemented | MCP Tool | Usage Type | Notes |
|-----------|--------------|:-----------:|----------|------------|-------|
| 1000 | `MAV_CMD_DO_GIMBAL_MANAGER_PITCHYAW` | ❌ | - | - | Set gimbal pitch/yaw (not implemented) |
| 1001 | `MAV_CMD_DO_GIMBAL_MANAGER_CONFIGURE` | ❌ | - | - | Configure gimbal manager (not implemented) |
| 204 | `MAV_CMD_DO_MOUNT_CONFIGURE` | ❌ | - | - | Configure mount (not implemented) |
| 205 | `MAV_CMD_DO_MOUNT_CONTROL` | ❌ | - | - | Control mount (not implemented) |
| 201 | `MAV_CMD_DO_SET_ROI_LOCATION` | ❌ | - | - | Set ROI location for gimbal (not implemented) |

---

## System Commands (MAV_CMD_COMPONENT_*)

| Command ID | Command Name | Implemented | MCP Tool | Usage Type | Notes |
|-----------|--------------|:-----------:|----------|------------|-------|
| 400 | `MAV_CMD_COMPONENT_ARM_DISARM` | ✅ | `arm_drone`, `disarm_drone` | Indirect | Via `drone.action.arm()` and `disarm()` |
| 401 | `MAV_CMD_COMPONENT_ARM_DISARM_SHUTDOWN` | ❌ | - | - | Arm/disarm with shutdown (not implemented) |
| 420 | `MAV_CMD_RUN_PREARM_CHECKS` | ❌ | - | - | Run prearm checks (not implemented) |

---

## Calibration Commands

| Command ID | Command Name | Implemented | MCP Tool | Usage Type | Notes |
|-----------|--------------|:-----------:|----------|------------|-------|
| 241 | `MAV_CMD_PREFLIGHT_CALIBRATION` | ❌ | - | - | Preflight calibration (not implemented) |
| 242 | `MAV_CMD_PREFLIGHT_SET_SENSOR_OFFSETS` | ❌ | - | - | Set sensor offsets (not implemented) |
| 243 | `MAV_CMD_PREFLIGHT_UAVCAN` | ❌ | - | - | UAVCAN preflight (not implemented) |
| 245 | `MAV_CMD_PREFLIGHT_STORAGE` | ❌ | - | - | Preflight storage (not implemented) |
| 246 | `MAV_CMD_PREFLIGHT_REBOOT_SHUTDOWN` | ❌ | - | - | Reboot/shutdown (not implemented) |

---

## Parameter Commands

| Command ID | Command Name | Implemented | MCP Tool | Usage Type | Notes |
|-----------|--------------|:-----------:|----------|------------|-------|
| 180 | `MAV_CMD_DO_SET_PARAMETER` | ✅ | `set_parameter` | Indirect | Via `drone.param.set_param_int/float()` |
| Parameter Read | Parameter Read | ✅ | `get_parameter`, `list_parameters` | Indirect | Via `drone.param.get_param_int/float()` and `get_all_params()` |

---

## Other Commands

| Command ID | Command Name | Implemented | MCP Tool | Usage Type | Notes |
|-----------|--------------|:-----------:|----------|------------|-------|
| 410 | `MAV_CMD_FIXED_MAG_CAL_YAW` | ❌ | - | - | Fixed mag cal yaw (not implemented) |
| 420 | `MAV_CMD_DO_START_MAG_CAL` | ❌ | - | - | Start magnetometer calibration (not implemented) |
| 421 | `MAV_CMD_DO_ACCEPT_MAG_CAL` | ❌ | - | - | Accept mag calibration (not implemented) |
| 422 | `MAV_CMD_DO_CANCEL_MAG_CAL` | ❌ | - | - | Cancel mag calibration (not implemented) |
| 424 | `MAV_CMD_SET_MESSAGE_INTERVAL` | ❌ | - | - | Set message interval (not implemented) |
| 500 | `MAV_CMD_REQUEST_AUTOPILOT_CAPABILITIES` | ❌ | - | - | Request autopilot capabilities (not implemented) |
| 520 | `MAV_CMD_REQUEST_PROTOCOL_VERSION` | ❌ | - | - | Request protocol version (not implemented) |
| 530 | `MAV_CMD_REQUEST_CAMERA_INFORMATION` | ❌ | - | - | Request camera info (not implemented) |
| 540 | `MAV_CMD_REQUEST_CAMERA_SETTINGS` | ❌ | - | - | Request camera settings (not implemented) |
| 550 | `MAV_CMD_REQUEST_STORAGE_INFORMATION` | ❌ | - | - | Request storage info (not implemented) |

---

## MAVLink Frame Types Used

| Frame ID | Frame Name | Used In | Notes |
|----------|------------|---------|-------|
| 3 | `MAV_FRAME_GLOBAL_RELATIVE_ALT` | `upload_mission`, `initiate_mission` | Global coordinates with relative altitude |

---

## Detailed Mappings

### Direct MAVLink Command Usage

**MAV_CMD_NAV_WAYPOINT (16)**
- **Location:** `src/server/mavlinkmcp.py` lines 678, 2272
- **Usage:** Directly specified in `MissionItem` creation for mission upload
- **Tools:** `upload_mission`, `initiate_mission`
- **Code:**
  ```python
  MissionItem(
      command=16,  # MAV_CMD_NAV_WAYPOINT
      frame=3,     # MAV_FRAME_GLOBAL_RELATIVE_ALT
      ...
  )
  ```

### Indirect MAVLink Commands (via MAVSDK)

**MAV_CMD_COMPONENT_ARM_DISARM (400)**
- **MAVSDK Method:** `drone.action.arm()`, `drone.action.disarm()`
- **MCP Tools:** `arm_drone`, `disarm_drone`

**MAV_CMD_NAV_TAKEOFF (22)**
- **MAVSDK Method:** `drone.action.takeoff()`
- **MCP Tool:** `takeoff`
- **Also uses:** `drone.action.set_takeoff_altitude()` (sets parameter)

**MAV_CMD_NAV_LAND (21)**
- **MAVSDK Method:** `drone.action.land()`
- **MCP Tool:** `land`

**MAV_CMD_NAV_RETURN_TO_LAUNCH (20)**
- **MAVSDK Method:** `drone.action.return_to_launch()`
- **MCP Tool:** `return_to_launch`

**MAV_CMD_NAV_WAYPOINT (16)**
- **MAVSDK Method:** `drone.action.goto_location()`
- **MCP Tools:** `go_to_location`, `reposition`, `move_to_relative`

**MAV_CMD_DO_SET_MODE (176)**
- **MAVSDK Method:** Flight mode changes via various actions
- **MCP Tool:** `set_flight_mode`
- **Modes:** HOLD, RTL, LAND, GUIDED

**MAV_CMD_DO_CHANGE_SPEED (178)**
- **MAVSDK Method:** `drone.action.set_maximum_speed()`
- **MCP Tool:** `set_max_speed`

**MAV_CMD_DO_SET_PARAMETER (180)**
- **MAVSDK Method:** `drone.param.set_param_int()`, `drone.param.set_param_float()`
- **MCP Tool:** `set_parameter`

**MAV_CMD_DO_SET_MISSION_CURRENT (246)**
- **MAVSDK Method:** `drone.mission.set_current_mission_item()`
- **MCP Tool:** `set_current_waypoint`

---

## Telemetry (Not Commands, but Related)

The following are MAVLink messages (not commands) used for telemetry:

| Message Type | MCP Tool | Usage |
|--------------|----------|-------|
| `HEARTBEAT` | (internal) | Connection status |
| `SYS_STATUS` | `get_health` | System health |
| `GPS_RAW_INT` | `get_gps_info` | GPS information |
| `GLOBAL_POSITION_INT` | `get_position` | Position data |
| `ATTITUDE` | `get_attitude` | Attitude data |
| `VFR_HUD` | `get_speed` | Speed/heading data |
| `BATTERY_STATUS` | `get_battery` | Battery data |
| `MISSION_CURRENT` | `print_mission_progress` | Mission progress |
| `PARAM_VALUE` | `get_parameter`, `list_parameters` | Parameter values |
| `STATUSTEXT` | `print_status_text` | Status messages |

---

## Summary by MCP Tool

| MCP Tool | MAVLink Command(s) | Usage Type |
|----------|-------------------|------------|
| `arm_drone` | MAV_CMD_COMPONENT_ARM_DISARM (400) | Indirect |
| `disarm_drone` | MAV_CMD_COMPONENT_ARM_DISARM (400) | Indirect |
| `takeoff` | MAV_CMD_NAV_TAKEOFF (22) | Indirect |
| `land` | MAV_CMD_NAV_LAND (21) | Indirect |
| `return_to_launch` | MAV_CMD_NAV_RETURN_TO_LAUNCH (20) | Indirect |
| `go_to_location` | MAV_CMD_NAV_WAYPOINT (16) | Indirect |
| `reposition` | MAV_CMD_NAV_WAYPOINT (16) | Indirect |
| `move_to_relative` | MAV_CMD_NAV_WAYPOINT (16) | Indirect |
| `hold_position` | MAV_CMD_NAV_WAYPOINT (16) via GUIDED | Indirect |
| `set_yaw` | MAV_CMD_NAV_WAYPOINT (16) with yaw param | Indirect |
| `set_max_speed` | MAV_CMD_DO_CHANGE_SPEED (178) | Indirect |
| `set_flight_mode` | MAV_CMD_DO_SET_MODE (176) | Indirect |
| `upload_mission` | MAV_CMD_NAV_WAYPOINT (16) | **Direct** |
| `initiate_mission` | MAV_CMD_NAV_WAYPOINT (16) + Mission Start | Direct + Indirect |
| `hold_mission_position` | MAV_CMD_NAV_WAYPOINT (16) via GUIDED | Indirect |
| `resume_mission` | Mission Start | Indirect |
| `clear_mission` | Mission Clear | Indirect |
| `download_mission` | Mission Download | Indirect |
| `set_current_waypoint` | MAV_CMD_DO_SET_MISSION_CURRENT (246) | Indirect |
| `set_parameter` | MAV_CMD_DO_SET_PARAMETER (180) | Indirect |
| `get_parameter` | PARAM_VALUE message (read) | Telemetry |
| `list_parameters` | PARAM_VALUE messages (read) | Telemetry |
| `kill_motors` | Emergency disarm (400 with force) | Indirect |

---

## Priority Recommendations

### High Priority (Commonly Needed)

1. **Camera Commands** - MAV_CMD_IMAGE_START_CAPTURE, MAV_CMD_VIDEO_START_CAPTURE
2. **Gimbal Commands** - MAV_CMD_DO_GIMBAL_MANAGER_PITCHYAW, MAV_CMD_DO_SET_ROI_LOCATION
3. **Geofence** - MAV_CMD_DO_FENCE_ENABLE
4. **Calibration** - MAV_CMD_PREFLIGHT_CALIBRATION

### Medium Priority

5. **Mission Commands** - MAV_CMD_NAV_LOITER_TIME, MAV_CMD_NAV_DELAY
6. **VTOL** - MAV_CMD_NAV_VTOL_TAKEOFF, MAV_CMD_NAV_VTOL_LAND
7. **Follow** - MAV_CMD_NAV_FOLLOW

---

## Resources

- [MAVLink Common Message Set](https://mavlink.io/en/messages/common.html)
- [MAVLink Command Enumeration](https://mavlink.io/en/messages/common.html#MAV_CMD)
- [MAVSDK Documentation](https://mavsdk.mavlink.io/)
- [MAVLink MCP GitHub](https://github.com/PeterJBurke/MAVLinkMCP)

---

**Note:** This reference focuses on commands that are commonly used in drone operations. There are additional specialized commands for specific vehicle types (fixed-wing, VTOL, rover, etc.) that are not listed here. For a complete list, refer to the official MAVLink documentation.
