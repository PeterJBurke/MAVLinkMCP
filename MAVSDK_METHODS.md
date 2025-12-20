# MAVSDK Python Methods Reference

Complete reference of all MAVSDK Python methods and their implementation status in MAVLink MCP.

**Last Updated:** December 2024  
**MAVSDK Version:** 2.x  
**MAVLink MCP Version:** 1.2.4

---

## Implementation Summary

| Category | Total Methods | Implemented | Coverage |
|----------|:-------------:|:-----------:|:--------:|
| **Action** | 22 | 10 | 45% |
| **Telemetry** | 31 | 11 | 35% |
| **Mission** | 10 | 6 | 60% |
| **MissionRaw** | 7 | 2 | 29% |
| **Param** | 7 | 5 | 71% |
| **Camera** | 21 | 0 | 0% |
| **Gimbal** | 8 | 0 | 0% |
| **Offboard** | 10 | 0 | 0% |
| **FollowMe** | 7 | 0 | 0% |
| **Geofence** | 2 | 0 | 0% |
| **ManualControl** | 3 | 0 | 0% |
| **Info** | 5 | 0 | 0% |
| **Calibration** | 6 | 0 | 0% |
| **LogFiles** | 3 | 0 | 0% |
| **FTP** | 9 | 0 | 0% |
| **Tune** | 1 | 0 | 0% |
| **Shell** | 2 | 0 | 0% |
| **Transponder** | 1 | 0 | 0% |
| **TOTAL** | **~155** | **~35** | **~23%** |

---

## Detailed Method Reference

### ACTION Plugin - Basic Flight Commands

| Method | Implemented | MCP Tool | Description |
|--------|:-----------:|----------|-------------|
| `arm()` | ✅ | `arm_drone` | Arm motors for flight |
| `disarm()` | ✅ | `disarm_drone` | Disarm motors |
| `takeoff()` | ✅ | `takeoff` | Autonomous takeoff |
| `land()` | ✅ | `land` | Land at current position |
| `return_to_launch()` | ✅ | `return_to_launch` | Return to home/launch position |
| `goto_location()` | ✅ | `go_to_location` | Fly to GPS coordinates |
| `hold()` | ✅ | `hold_position` | Hold/hover at current position |
| `kill()` | ✅ | `kill_motors` | Emergency motor cutoff |
| `set_takeoff_altitude()` | ✅ | `takeoff` | Set takeoff target altitude |
| `set_maximum_speed()` | ✅ | `set_max_speed` | Set max flight speed |
| `get_takeoff_altitude()` | ❌ | - | Get current takeoff altitude setting |
| `get_maximum_speed()` | ❌ | - | Get current max speed setting |
| `set_return_to_launch_altitude()` | ❌ | - | Set RTL altitude |
| `get_return_to_launch_altitude()` | ❌ | - | Get RTL altitude setting |
| `transition_to_fixedwing()` | ❌ | - | VTOL: switch to fixed-wing mode |
| `transition_to_multicopter()` | ❌ | - | VTOL: switch to multicopter mode |
| `do_orbit()` | ❌ | - | Orbit around a point (unreliable with ArduPilot) |
| `reboot()` | ❌ | - | Reboot autopilot |
| `shutdown()` | ❌ | - | Shutdown autopilot |
| `terminate()` | ❌ | - | Flight termination |
| `set_actuator()` | ❌ | - | Direct actuator/servo control |
| `set_current_speed()` | ❌ | - | Set current target speed |

---

### TELEMETRY Plugin - Sensor Data & Status

| Method | Implemented | MCP Tool | Description |
|--------|:-----------:|----------|-------------|
| `position()` | ✅ | `get_position` | GPS position (lat, lon, alt) |
| `home()` | ✅ | `get_home_position` | Home/launch position |
| `attitude_euler()` | ✅ | `get_attitude` | Roll, pitch, yaw angles |
| `velocity_ned()` | ✅ | `get_speed` | Velocity (North, East, Down) |
| `battery()` | ✅ | `get_battery` | Battery voltage & percentage |
| `gps_info()` | ✅ | `get_gps_info` | Satellite count, fix type |
| `flight_mode()` | ✅ | `get_flight_mode` | Current flight mode |
| `health()` | ✅ | `get_health` | System health checks |
| `in_air()` | ✅ | `get_in_air` | Is drone flying? |
| `armed()` | ✅ | `get_armed` | Are motors armed? |
| `status_text()` | ✅ | `print_status_text` | Status messages stream |
| `health_all_ok()` | ✅ | `get_health_all_ok` | All health checks passed? |
| `landed_state()` | ✅ | `get_landed_state` | On ground / taking off / in air / landing |
| `rc_status()` | ✅ | `get_rc_status` | RC controller status & signal |
| `heading()` | ✅ | `get_heading` | Compass heading (degrees) |
| `attitude_quaternion()` | ❌ | - | Attitude as quaternion |
| `attitude_angular_velocity_body()` | ❌ | - | Angular velocity (body frame) |
| `ground_speed_ned()` | ❌ | - | Ground speed (NED frame) |
| `fixedwing_metrics()` | ❌ | - | Airspeed, climb rate (fixed-wing) |
| `imu()` | ❌ | - | Raw IMU data (accel, gyro) |
| `scaled_imu()` | ❌ | - | Scaled IMU readings |
| `raw_imu()` | ❌ | - | Unprocessed IMU data |
| `odometry()` | ✅ | `get_odometry` | Position + velocity + orientation |
| `distance_sensor()` | ❌ | - | Rangefinder/lidar distance |
| `scaled_pressure()` | ❌ | - | Barometer pressure |
| `actuator_control_target()` | ❌ | - | Commanded actuator values |
| `actuator_output_status()` | ❌ | - | Actual actuator outputs |
| `vtol_state()` | ❌ | - | VTOL mode (MC/FW/transition) |
| `unix_epoch_time()` | ❌ | - | System time |
| `position_velocity_ned()` | ❌ | - | Combined position & velocity |
| `ground_truth()` | ❌ | - | Simulation ground truth |

---

### MISSION Plugin - Waypoint Missions (High-Level)

| Method | Implemented | MCP Tool | Description |
|--------|:-----------:|----------|-------------|
| `start_mission()` | ✅ | `initiate_mission` | Start uploaded mission |
| `mission_progress()` | ✅ | `print_mission_progress` | Current waypoint progress |
| `clear_mission()` | ✅ | `clear_mission` | Clear all waypoints |
| `set_current_mission_item()` | ✅ | `set_current_waypoint` | Jump to specific waypoint |
| `is_mission_finished()` | ✅ | `is_mission_finished` | Check if mission complete |
| `set_return_to_launch_after_mission()` | ✅ | `initiate_mission` | RTL after mission ends |
| `upload_mission()` | ❌ | - | Upload mission plan (high-level API) |
| `download_mission()` | ❌ | - | Download mission from drone (high-level) |
| `pause_mission()` | ❌ | - | Pause current mission |
| `get_return_to_launch_after_mission()` | ❌ | - | Get RTL-after-mission setting |

---

### MISSION_RAW Plugin - Waypoint Missions (Low-Level MAVLink)

| Method | Implemented | MCP Tool | Description |
|--------|:-----------:|----------|-------------|
| `upload_mission()` | ✅ | `upload_mission` | Upload raw mission items |
| `download_mission()` | ✅ | `download_mission` | Download raw mission items |
| `start_mission()` | ❌ | - | Start mission (raw API) |
| `pause_mission()` | ❌ | - | Pause mission (raw API) |
| `clear_mission()` | ❌ | - | Clear mission (raw API) |
| `set_current_mission_item()` | ❌ | - | Set current waypoint (raw) |
| `import_qgroundcontrol_mission()` | ❌ | - | Import QGC mission file |

---

### PARAM Plugin - Parameter Management

| Method | Implemented | MCP Tool | Description |
|--------|:-----------:|----------|-------------|
| `get_param_int()` | ✅ | `get_parameter` | Get integer parameter |
| `get_param_float()` | ✅ | `get_parameter` | Get float parameter |
| `set_param_int()` | ✅ | `set_parameter` | Set integer parameter |
| `set_param_float()` | ✅ | `set_parameter` | Set float parameter |
| `get_all_params()` | ✅ | `list_parameters` | List all parameters |
| `get_param_custom()` | ❌ | - | Get custom parameter type |
| `set_param_custom()` | ❌ | - | Set custom parameter type |

---

### CAMERA Plugin - Photo & Video Control

| Method | Implemented | MCP Tool | Description |
|--------|:-----------:|----------|-------------|
| `take_photo()` | ❌ | - | Capture single photo |
| `start_photo_interval()` | ❌ | - | Start time-lapse capture |
| `stop_photo_interval()` | ❌ | - | Stop time-lapse |
| `start_video()` | ❌ | - | Start video recording |
| `stop_video()` | ❌ | - | Stop video recording |
| `start_video_streaming()` | ❌ | - | Start video stream |
| `stop_video_streaming()` | ❌ | - | Stop video stream |
| `set_mode()` | ❌ | - | Set photo/video mode |
| `set_setting()` | ❌ | - | Adjust camera setting |
| `get_setting()` | ❌ | - | Get camera setting |
| `set_zoom_level()` | ❌ | - | Set zoom level |
| `zoom_in()` | ❌ | - | Increase zoom |
| `zoom_out()` | ❌ | - | Decrease zoom |
| `format_storage()` | ❌ | - | Format SD card |
| `select_camera()` | ❌ | - | Select camera by index |
| `information()` | ❌ | - | Get camera info |
| `status()` | ❌ | - | Get camera status |
| `capture_info()` | ❌ | - | Last capture info |
| `current_settings()` | ❌ | - | Current camera settings |
| `possible_setting_options()` | ❌ | - | Available setting options |
| `list_photos()` | ❌ | - | List captured photos |

---

### GIMBAL Plugin - Camera Gimbal Control

| Method | Implemented | MCP Tool | Description |
|--------|:-----------:|----------|-------------|
| `set_pitch_and_yaw()` | ❌ | - | Set gimbal angles |
| `set_pitch_rate_and_yaw_rate()` | ❌ | - | Set gimbal angular rates |
| `set_mode()` | ❌ | - | Set yaw follow/lock mode |
| `set_roi_location()` | ❌ | - | Point at GPS location |
| `take_control()` | ❌ | - | Take gimbal control |
| `release_control()` | ❌ | - | Release gimbal |
| `control()` | ❌ | - | Gimbal control stream |
| `attitude()` | ❌ | - | Get gimbal attitude |

---

### OFFBOARD Plugin - Direct Control Mode

| Method | Implemented | MCP Tool | Description |
|--------|:-----------:|----------|-------------|
| `start()` | ❌ | - | Enter offboard mode |
| `stop()` | ❌ | - | Exit offboard mode |
| `is_active()` | ❌ | - | Check if offboard active |
| `set_position_ned()` | ❌ | - | Set position (NED frame) |
| `set_position_global()` | ❌ | - | Set position (GPS) |
| `set_velocity_ned()` | ❌ | - | Set velocity (NED frame) |
| `set_velocity_body()` | ❌ | - | Set velocity (body frame) |
| `set_attitude()` | ❌ | - | Set attitude angles |
| `set_attitude_rate()` | ❌ | - | Set attitude rates |
| `set_actuator_control()` | ❌ | - | Direct actuator control |

---

### FOLLOW_ME Plugin - Target Following

| Method | Implemented | MCP Tool | Description |
|--------|:-----------:|----------|-------------|
| `start()` | ❌ | - | Start follow mode |
| `stop()` | ❌ | - | Stop follow mode |
| `is_active()` | ❌ | - | Check if following |
| `set_config()` | ❌ | - | Set follow behavior |
| `get_config()` | ❌ | - | Get follow config |
| `set_target_location()` | ❌ | - | Update target GPS |
| `get_last_location()` | ❌ | - | Get last target location |

---

### GEOFENCE Plugin - Flight Boundaries

| Method | Implemented | MCP Tool | Description |
|--------|:-----------:|----------|-------------|
| `upload_geofence()` | ❌ | - | Upload geofence polygons |
| `clear_geofence()` | ❌ | - | Clear all geofences |

---

### MANUAL_CONTROL Plugin - Joystick Control

| Method | Implemented | MCP Tool | Description |
|--------|:-----------:|----------|-------------|
| `start_position_control()` | ❌ | - | Start position control |
| `start_altitude_control()` | ❌ | - | Start altitude control |
| `set_manual_control_input()` | ❌ | - | Send joystick inputs |

---

### INFO Plugin - System Information

| Method | Implemented | MCP Tool | Description |
|--------|:-----------:|----------|-------------|
| `get_version()` | ❌ | - | Get firmware version |
| `get_product()` | ❌ | - | Get product/vendor info |
| `get_flight_information()` | ❌ | - | Flight time, distance |
| `get_identification()` | ❌ | - | System identification |
| `get_speed_factor()` | ❌ | - | Simulation speed factor |

---

### CALIBRATION Plugin - Sensor Calibration

| Method | Implemented | MCP Tool | Description |
|--------|:-----------:|----------|-------------|
| `calibrate_gyro()` | ❌ | - | Calibrate gyroscope |
| `calibrate_accelerometer()` | ❌ | - | Calibrate accelerometer |
| `calibrate_magnetometer()` | ❌ | - | Calibrate compass |
| `calibrate_level_horizon()` | ❌ | - | Level horizon calibration |
| `calibrate_gimbal_accelerometer()` | ❌ | - | Calibrate gimbal accel |
| `cancel()` | ❌ | - | Cancel calibration |

---

### LOG_FILES Plugin - Flight Logs

| Method | Implemented | MCP Tool | Description |
|--------|:-----------:|----------|-------------|
| `get_entries()` | ❌ | - | List available logs |
| `download_log_file()` | ❌ | - | Download specific log |
| `erase_all_log_files()` | ❌ | - | Delete all logs |

---

### FTP Plugin - File Transfer

| Method | Implemented | MCP Tool | Description |
|--------|:-----------:|----------|-------------|
| `reset()` | ❌ | - | Reset FTP server |
| `download()` | ❌ | - | Download file from drone |
| `upload()` | ❌ | - | Upload file to drone |
| `list_directory()` | ❌ | - | List directory contents |
| `create_directory()` | ❌ | - | Create directory |
| `remove_directory()` | ❌ | - | Remove directory |
| `remove_file()` | ❌ | - | Delete file |
| `rename()` | ❌ | - | Rename file/directory |
| `are_files_identical()` | ❌ | - | Compare files (CRC) |

---

### TUNE Plugin - Audio Feedback

| Method | Implemented | MCP Tool | Description |
|--------|:-----------:|----------|-------------|
| `play_tune()` | ❌ | - | Play buzzer tune |

---

### SHELL Plugin - MAVLink Shell

| Method | Implemented | MCP Tool | Description |
|--------|:-----------:|----------|-------------|
| `send()` | ❌ | - | Send shell command |
| `subscribe_receive()` | ❌ | - | Receive shell output |

---

### TRANSPONDER Plugin - ADS-B

| Method | Implemented | MCP Tool | Description |
|--------|:-----------:|----------|-------------|
| `transponder()` | ❌ | - | Get nearby aircraft data |

---

### CORE Plugin - Connection Management

| Method | Implemented | MCP Tool | Description |
|--------|:-----------:|----------|-------------|
| `connection_state()` | ✅ | (internal) | Connection status |
| `set_mavlink_timeout()` | ❌ | - | Set connection timeout |

---

## Priority Recommendations for Implementation

### High Priority (Commonly Needed)

1. ✅ `get_landed_state()` - Know if drone is on ground, taking off, flying, or landing
2. ✅ `get_rc_status()` - Important safety check (is RC connected?)
3. ✅ `get_heading()` - Simple compass heading
4. ✅ `get_health_all_ok()` - Quick go/no-go health check
5. ✅ `get_odometry()` - Combined position, velocity, and orientation
6. `get_flight_information()` - Flight time since arm
7. `get_version()` - Know what firmware you're talking to
8. `get_takeoff_altitude()` / `get_maximum_speed()` - Read settings you can already set
9. `set_return_to_launch_altitude()` / `get_return_to_launch_altitude()` - RTL safety

### Medium Priority (Camera/Gimbal - Requires Hardware)

8. `take_photo()` - Most requested camera feature
9. `start_video()` / `stop_video()` - Video recording
10. `set_pitch_and_yaw()` - Point the gimbal
11. `set_roi_location()` - Point camera at GPS location

### Medium Priority (Advanced Control)

12. `upload_geofence()` / `clear_geofence()` - Safety boundaries
13. `start()` / `stop()` (FollowMe) - Target following
14. `set_velocity_ned()` (Offboard) - Direct velocity control

### Lower Priority (Specialized)

15. VTOL transitions - Only for VTOL aircraft
16. Calibration tools - Usually done via GCS
17. File transfer (FTP) - Specialized use case
18. Shell commands - Advanced debugging

---

## Contributing

Want to help implement more MAVSDK methods? Contributions welcome!

1. Pick a method from the "not implemented" list
2. Check the [MAVSDK Python docs](https://mavsdk.mavlink.io/)
3. Add the tool to `src/server/droneserver.py`
4. Test with SITL
5. Submit a PR

See [STATUS.md](STATUS.md) for the development roadmap.

---

## Resources

- [MAVSDK Python GitHub](https://github.com/mavlink/MAVSDK-Python)
- [MAVSDK Documentation](https://mavsdk.mavlink.io/)
- [MAVLink Protocol](https://mavlink.io/en/)
- [MAVLink MCP GitHub](https://github.com/PeterJBurke/droneserver)
