# MAVLink MCP Testing Guide

Comprehensive test scenarios for v1.2.0 features using ChatGPT natural language control.

## Prerequisites

Before testing, ensure:
1. ✅ MAVLink MCP server is running (`./start_http_server.sh`)
2. ✅ Drone/SITL is connected and GPS lock acquired
3. ✅ ChatGPT is connected to your MCP server via ngrok HTTPS
4. ✅ You're in an open, safe area for testing

---

## 🎯 Comprehensive Test Scenario - Tower Inspection Mission

This test exercises **all 35 tools** including the 10 new v1.2.0 features.

### Copy this prompt into ChatGPT:

```
I need to conduct a detailed inspection of a cell tower. Here's what I need you to do:

PHASE 1 - PRE-FLIGHT CONFIGURATION:
1. First, show me all the RTL (Return to Launch) parameters currently configured
2. Check what the current RTL altitude is set to
3. If RTL altitude is less than 30 meters, set it to 30 meters for safety
4. Show me the battery capacity parameter and tell me what it's set to
5. Run a complete health check to make sure we're ready to fly

PHASE 2 - MISSION PREPARATION:
6. I want to create a 4-waypoint inspection mission around the tower. Upload (but don't start yet) a mission with these waypoints:
   - Waypoint 1: Approach point at lat 33.6459, lon -117.8427, altitude 20m
   - Waypoint 2: North side at lat 33.6460, lon -117.8427, altitude 30m  
   - Waypoint 3: East side at lat 33.6460, lon -117.8426, altitude 40m
   - Waypoint 4: Return to approach at lat 33.6459, lon -117.8427, altitude 20m

7. Download the mission back from the drone to verify it uploaded correctly

PHASE 3 - FLIGHT OPERATIONS:
8. Arm the drone and take off to 15 meters
9. Check our current position and battery level
10. Fly to the first waypoint position (lat 33.6459, lon -117.8427) and hold there at 20m
11. Rotate the drone to face due east (90 degrees) so the camera is pointing at the tower
12. Now orbit around the tower base at a 25 meter radius, moving at 3 m/s, centered on lat 33.6460, lon -117.8427 at 25m altitude, going clockwise
13. After 30 seconds of orbiting, tell me what our current speed is
14. Check the battery level again - if it's below 70%, I want you to warn me

PHASE 4 - DETAILED INSPECTION:
15. Stop the orbit and reposition to lat 33.6460, lon -117.8426 at 40m altitude to get a closer view of the upper tower section
16. Face north (0 degrees) to align with the tower
17. Get our current attitude (roll, pitch, yaw) to confirm we're level and facing the right direction

PHASE 5 - MISSION EXECUTION:
18. Now start the 4-waypoint mission we uploaded earlier
19. Monitor the mission and tell me when we reach waypoint 2
20. At waypoint 2, pause the mission temporarily
21. Check if the mission is finished (it shouldn't be since we paused it)
22. Resume the mission and let it continue
23. Keep checking until the mission is finished

PHASE 6 - RETURN AND LANDING:
24. Once mission is complete, check battery one more time
25. If battery is above 40%, orbit one more time around lat 33.6460, lon -117.8427 at 30m radius at 15m altitude, counter-clockwise at 2 m/s
26. Return to launch position
27. Land the drone
28. Disarm when safely on the ground

PHASE 7 - POST-FLIGHT:
29. Download the mission from the drone one more time to save it
30. Show me all parameters that changed during the flight (compare with initial values)

Please execute this entire inspection mission step by step, confirming each action before moving to the next. Warn me immediately if any step fails or if battery gets critically low.
```

### What This Tests

**Parameter Management (v1.2.0):**
- ✅ `list_parameters` - List RTL parameters
- ✅ `get_parameter` - Check RTL_ALT and battery capacity
- ✅ `set_parameter` - Modify RTL altitude if needed

**Advanced Navigation (v1.2.0):**
- ✅ `orbit_location` - Circle tower at 25m radius (clockwise then counter-clockwise)
- ✅ `set_yaw` - Face east (90°), then north (0°)
- ✅ `reposition` - Move to inspection position and hold

**Mission Enhancements (v1.2.0):**
- ✅ `upload_mission` - Upload 4-waypoint mission without starting
- ✅ `download_mission` - Verify upload and save at end
- ✅ `set_current_waypoint` - (implicitly tested with pause/resume)
- ✅ `is_mission_finished` - Check completion status

**Existing Features:**
- Flight control (arm, takeoff, land, disarm, hold)
- Safety (return_to_launch, battery monitoring)
- Navigation (go_to_location, get_position)
- Mission management (pause, resume, progress)
- Telemetry (health, speed, attitude, battery)

---

## 🔬 Intelligent Granular Test (Complete Coverage with Verification)

This test systematically validates **all 35 tools** with **intelligent prerequisites** and **ACK/NACK verification**. Each test confirms the drone actually performed the action, not just that the API call succeeded.

### Copy this prompt into ChatGPT:

```
I need you to test every single MCP tool with INTELLIGENT VERIFICATION. For each test:
1. Check PREREQUISITES before executing
2. Execute the command
3. Wait appropriate time
4. VERIFY the drone actually did what was requested
5. Report ACK (verified success) or NACK (failed to verify)

CRITICAL RULES:
- Never apply yaw/movement commands unless drone is in the air (altitude > 2m)
- Before disarming, MUST verify altitude < 0.5m AND drone is landed
- After each movement, verify position/altitude/attitude changed as expected
- If verification fails, report NACK with explanation

═══════════════════════════════════════════════════════════
CATEGORY 1: TELEMETRY & HEALTH (Test before flight)
═══════════════════════════════════════════════════════════

TEST 1: get_health
- PREREQUISITE: None (can run anytime)
- ACTION: Run get_health and show me the full report
- VERIFY: 
  * GPS status = operational
  * Accelerometer = operational  
  * Gyroscope = operational
  * Magnetometer = operational
- ACK CRITERIA: All critical systems show "true" or "operational"
- NACK CRITERIA: Any critical system shows failure
- Report: ACK/NACK with details

TEST 2: get_telemetry  
- PREREQUISITE: None
- ACTION: Run get_telemetry
- VERIFY: Response contains position, altitude, velocity, battery data
- ACK CRITERIA: All fields populated with valid numbers
- NACK CRITERIA: Missing fields or error response
- SAVE: Initial telemetry for comparison later
- Report: ACK/NACK

TEST 3: get_battery
- PREREQUISITE: None
- ACTION: Run get_battery
- VERIFY: Shows voltage_v and remaining_percent (or estimated_percent)
- ACK CRITERIA: Voltage > 10V, percentage shown (or estimated)
- NACK CRITERIA: Voltage < 10V or error
- SAVE: Initial battery level (call it BATTERY_START)
- Report: ACK/NACK (voltage: X.XV, percent: X%)

TEST 4: get_gps_info
- PREREQUISITE: None
- ACTION: Run get_gps_info
- VERIFY: Satellite count and fix type
- ACK CRITERIA: Fix type = "3D" AND satellites >= 6
- NACK CRITERIA: No fix or satellites < 6
- Report: ACK/NACK (sats: X, fix: X)

TEST 5: get_flight_mode
- PREREQUISITE: None
- ACTION: Run get_flight_mode
- VERIFY: Returns a valid flight mode string
- ACK CRITERIA: Mode is returned (e.g., HOLD, MANUAL, STABILIZE)
- NACK CRITERIA: Error or empty response
- SAVE: Initial flight mode
- Report: ACK/NACK (mode: X)

TEST 6: get_armed
- PREREQUISITE: None
- ACTION: Run get_armed (should be false)
- VERIFY: Armed status returned
- ACK CRITERIA: is_armed = false (expected before arming)
- NACK CRITERIA: is_armed = true (unexpected) or error
- Report: ACK/NACK

TEST 7: get_position
- PREREQUISITE: GPS lock (from TEST 4)
- ACTION: Run get_position
- VERIFY: Shows lat, lon, altitude
- ACK CRITERIA: Valid GPS coordinates (-90 to 90 lat, -180 to 180 lon), altitude near 0
- NACK CRITERIA: Invalid coordinates or error
- SAVE: HOME position (lat, lon) for later verification
- Report: ACK/NACK (lat: X, lon: X, alt: Xm)

═══════════════════════════════════════════════════════════
CATEGORY 2: PARAMETER MANAGEMENT (v1.2.0)
═══════════════════════════════════════════════════════════

TEST 8: list_parameters (with filter)
- PREREQUISITE: None
- ACTION: Run list_parameters with filter_prefix="RTL"
- VERIFY: Returns list of RTL-related parameters
- ACK CRITERIA: List contains at least 1 RTL parameter (RTL_ALT, RTL_RETURN_ALT, etc.)
- NACK CRITERIA: Empty list or error
- Report: ACK/NACK (found X parameters)

TEST 9: get_parameter (read)
- PREREQUISITE: Know parameter name from TEST 8
- ACTION: Run get_parameter for "RTL_ALT" (or "RTL_RETURN_ALT" for PX4)
- VERIFY: Returns numerical value
- ACK CRITERIA: Returns value (typically 1500-3000 for altitude in cm)
- NACK CRITERIA: Parameter not found or error
- SAVE: Original RTL_ALT value (call it RTL_ORIGINAL)
- Report: ACK/NACK (value: X)

TEST 10: set_parameter (write)
- PREREQUISITE: None
- ACTION: Run set_parameter to set RTL_ALT to 2500
- VERIFY: Response shows old value and new value
- ACK CRITERIA: old_value = RTL_ORIGINAL, new_value = 2500, status = success
- NACK CRITERIA: Error or values don't match expected
- Report: ACK/NACK (changed from X to 2500)

TEST 11: get_parameter (verify persistence)
- PREREQUISITE: TEST 10 succeeded
- ACTION: Run get_parameter for RTL_ALT again
- VERIFY: Value matches what we just set
- ACK CRITERIA: Value = 2500 (confirms write was persistent)
- NACK CRITERIA: Value ≠ 2500 (write didn't persist)
- Report: ACK/NACK (verified: X)

═══════════════════════════════════════════════════════════
CATEGORY 3: BASIC FLIGHT CONTROL
═══════════════════════════════════════════════════════════

TEST 12: arm_drone
- PREREQUISITE: Drone disarmed (is_armed = false), health check passed (TEST 1)
- ACTION: Run arm_drone
- VERIFY: Command returns success
- ACK CRITERIA: Success message returned (but not verified yet)
- NACK CRITERIA: Error message
- Report: ACK/NACK

TEST 13: get_armed (verify arming)
- PREREQUISITE: TEST 12 executed
- ACTION: Run get_armed
- VERIFY: Drone actually armed
- ACK CRITERIA: is_armed = true (VERIFIED drone is armed)
- NACK CRITERIA: is_armed = false (arm command didn't work)
- Report: ACK/NACK - If NACK, previous test also fails

TEST 14: takeoff_drone
- PREREQUISITE: Drone armed (is_armed = true)
- ACTION: Run takeoff_drone to 12 meters
- VERIFY: Command accepted
- ACK CRITERIA: Success message, no error
- NACK CRITERIA: Error or command rejected
- WAIT: 15 seconds for takeoff to complete
- Report: ACK/NACK

TEST 15: get_position (verify takeoff altitude)
- PREREQUISITE: TEST 14 executed
- ACTION: Run get_position
- VERIFY: Drone actually climbed to target altitude
- ACK CRITERIA: altitude_m between 10m and 14m (±2m tolerance)
- NACK CRITERIA: altitude < 5m (takeoff failed) or error
- SAVE: Current altitude as ALT_CURRENT
- Report: ACK/NACK (altitude: Xm, target: 12m, error: Xm)

TEST 16: get_speed (verify hovering)
- PREREQUISITE: Drone in air (altitude > 5m)
- ACTION: Run get_speed
- VERIFY: Drone is stationary (hovering)
- ACK CRITERIA: ground_speed < 1 m/s AND vertical_speed < 0.5 m/s
- NACK CRITERIA: Speed too high (drone drifting) or error
- Report: ACK/NACK (ground: X m/s, vertical: X m/s)

TEST 17: get_attitude (baseline)
- PREREQUISITE: Drone in air
- ACTION: Run get_attitude
- VERIFY: Returns roll, pitch, yaw
- ACK CRITERIA: Roll and pitch between -10° and +10°, yaw between 0° and 360°
- NACK CRITERIA: Invalid values or error
- SAVE: Initial yaw as YAW_INITIAL
- Report: ACK/NACK (roll: X°, pitch: X°, yaw: X°)

═══════════════════════════════════════════════════════════
CATEGORY 4: ADVANCED NAVIGATION (v1.2.0)
═══════════════════════════════════════════════════════════

TEST 18: set_yaw (face north)
- PREREQUISITE: Drone IN AIR (altitude > 5m from TEST 15)
- IF altitude < 2m: SKIP this test (yaw only works in air)
- ACTION: Run set_yaw to 0 degrees (north)
- VERIFY: Command accepted
- ACK CRITERIA: Success message
- NACK CRITERIA: Error or command rejected
- WAIT: 8 seconds for rotation to complete
- Report: ACK/NACK

TEST 19: get_attitude (VERIFY yaw changed)
- PREREQUISITE: TEST 18 executed successfully
- ACTION: Run get_attitude
- VERIFY: Drone actually rotated to target heading
- ACK CRITERIA: Yaw between 350° and 10° (accounting for 0°/360° wraparound) 
              OR within 15° of 0° (±15° tolerance)
- NACK CRITERIA: Yaw unchanged from YAW_INITIAL (rotation didn't happen)
- SAVE: New yaw as YAW_CURRENT
- Report: ACK/NACK (target: 0°, actual: X°, error: X°)

TEST 20: set_yaw (face east - verify rotation works)
- PREREQUISITE: Drone in air, TEST 19 verified
- ACTION: Run set_yaw to 90 degrees (east)
- VERIFY: Command accepted
- WAIT: 8 seconds for rotation
- ACTION: Run get_attitude immediately after
- VERIFY: Yaw actually changed from previous
- ACK CRITERIA: Yaw between 75° and 105° (90° ±15° tolerance)
- NACK CRITERIA: Yaw still at previous value (no rotation)
- Report: ACK/NACK (target: 90°, actual: X°, error: X°)

TEST 21: go_to_location (horizontal movement)
- PREREQUISITE: Drone in air (altitude > 5m), have HOME position from TEST 7
- ACTION: Get current position first via get_position
- SAVE: Position as POS_BEFORE (lat, lon, alt)
- CALCULATE TARGET: 
  * new_lat = current_lat + 0.00027 (≈30m north)
  * new_lon = current_lon + 0.00033 (≈30m east at 33° latitude)
  * altitude = 15m
- ACTION: Run go_to_location with calculated coordinates
- VERIFY: Command accepted
- WAIT: 20 seconds for movement
- Report: ACK/NACK

TEST 22: get_position (VERIFY movement happened)
- PREREQUISITE: TEST 21 executed
- ACTION: Run get_position
- CALCULATE: Distance moved from POS_BEFORE
- VERIFY: Drone actually moved to target location
- ACK CRITERIA: 
  * Latitude within 0.00005° of target (≈5m)
  * Longitude within 0.00006° of target (≈5m)
  * Altitude within ±3m of 15m target
  * Overall distance from POS_BEFORE > 20m (moved significantly)
- NACK CRITERIA: Position unchanged or <10m movement (didn't go to location)
- Report: ACK/NACK (target: X,X @ 15m | actual: X,X @ Xm | error: Xm)

TEST 23: reposition (with altitude change)
- PREREQUISITE: Drone in air
- ACTION: Get current position
- SAVE: Position as POS_BEFORE2
- CALCULATE TARGET:
  * new_lat = current_lat - 0.00018 (≈20m south)
  * new_lon = current_lon (same)
  * altitude = 20m
- ACTION: Run reposition with calculated coordinates and 20m altitude
- WAIT: 15 seconds for movement + altitude change
- ACTION: Run get_position
- VERIFY: Both position AND altitude changed
- ACK CRITERIA:
  * Latitude changed by ~0.00018° (moved south)
  * Altitude between 18m and 22m (±2m)
- NACK CRITERIA: Position or altitude didn't change
- Report: ACK/NACK (moved: Xm, altitude: Xm vs 20m target)

TEST 24: hold_position (verify hover stability)
- PREREQUISITE: Drone in air
- ACTION: Get current position - SAVE as POS_HOLD
- ACTION: Run hold_position
- WAIT: 8 seconds
- ACTION: Get current position again
- VERIFY: Drone stayed in place (minimal drift)
- ACK CRITERIA: Position changed < 3m in any direction
- NACK CRITERIA: Position changed > 5m (excessive drift)
- Report: ACK/NACK (drift: Xm)

TEST 25: orbit_location (circular movement)
- PREREQUISITE: Drone in air (altitude > 10m)
- ACTION: Get current GPS position
- SAVE: Position as ORBIT_START
- ACTION: Run orbit_location with:
  * radius: 20 meters
  * velocity: 2 m/s  
  * center: current position
  * altitude: 18m (absolute MSL - convert from relative if needed)
  * clockwise: true
- VERIFY: Command response
- IF SUCCESS:
  * WAIT: 15 seconds (should complete ~1/4 of circle at 2 m/s)
  * ACTION: Run get_position
  * ACTION: Run get_speed
  * VERIFY: Drone is moving (speed > 1 m/s) AND position changed
  * ACK CRITERIA: Speed between 1-4 m/s AND moved from ORBIT_START
- IF ERROR "not supported":
  * CHECK: Error message provides workaround or firmware info
  * ACK CRITERIA: Helpful error with firmware requirements + alternative
- NACK CRITERIA: Error without helpful message OR command succeeded but no movement
- Report: ACK/NACK (supported: yes/no, if yes: speed Xm/s, moved: Xm)

TEST 26: hold_position (stop orbit/verify command interruption)
- PREREQUISITE: Previous command executed (orbit or movement)
- ACTION: Run hold_position
- WAIT: 5 seconds
- ACTION: Run get_speed
- VERIFY: Drone actually stopped moving
- ACK CRITERIA: ground_speed < 1 m/s (drone stopped)
- NACK CRITERIA: Speed still > 2 m/s (didn't stop)
- Report: ACK/NACK (stopped: yes/no, speed: Xm/s)

═══════════════════════════════════════════════════════════
CATEGORY 5: MISSION MANAGEMENT
═══════════════════════════════════════════════════════════

TEST 27: upload_mission (mission pre-load)
- PREREQUISITE: Get current position for waypoint calculation
- ACTION: Create 3-waypoint triangle mission (EXACT format):
  [
    {"latitude_deg": [current_lat], "longitude_deg": [current_lon + 0.0001], "relative_altitude_m": 15},
    {"latitude_deg": [current_lat + 0.0001], "longitude_deg": [current_lon + 0.0001], "relative_altitude_m": 18},
    {"latitude_deg": [current_lat + 0.0001], "longitude_deg": [current_lon], "relative_altitude_m": 15}
  ]
- ACTION: Run upload_mission (do NOT start it)
- VERIFY: Upload accepted
- ACK CRITERIA: 
  * Success message
  * waypoint_count = 3
  * Response shows waypoint summary
- NACK CRITERIA: Error, format rejected, or waypoint_count ≠ 3
- Report: ACK/NACK (uploaded: 3 waypoints)

TEST 28: download_mission (verify upload persistence)
- PREREQUISITE: TEST 27 succeeded
- ACTION: Run download_mission
- VERIFY: Downloaded mission matches uploaded mission
- ACK CRITERIA:
  * Returns 3 waypoints
  * Waypoint coordinates approximately match uploaded values (±0.00001°)
  * OR firmware reports "not supported" with helpful message
- NACK CRITERIA: Returns wrong number of waypoints OR different coordinates
- NOTE: If unsupported, mark ACK if error is informative
- Report: ACK/NACK (matched: yes/no OR unsupported)

TEST 29: is_mission_finished (before start)
- PREREQUISITE: Mission uploaded but NOT started
- ACTION: Run is_mission_finished
- VERIFY: Correctly reports no mission is running
- ACK CRITERIA: Returns false OR "no mission active"
- NACK CRITERIA: Returns true (mission can't be finished if not started)
- Report: ACK/NACK

TEST 30: initiate_mission (start execution)
- PREREQUISITE: Mission uploaded, drone in air
- ACTION: Get current position - SAVE as POS_MISSION_START
- ACTION: Run initiate_mission
- VERIFY: Mission actually starts
- WAIT: 10 seconds
- ACTION: Run get_position
- VERIFY: Drone started moving toward waypoint 1
- ACK CRITERIA: 
  * Success message from initiate_mission
  * Position changed > 5m from POS_MISSION_START (drone moving)
- NACK CRITERIA: Position unchanged (mission didn't start)
- Report: ACK/NACK (started: yes/no, moved: Xm)

TEST 31: print_mission_progress (during execution)
- PREREQUISITE: Mission running
- ACTION: Run print_mission_progress
- VERIFY: Shows accurate progress
- ACK CRITERIA: Shows "current/total" like "1 of 3" or "2 of 3"
- NACK CRITERIA: Shows "0 of 3" or error (no progress tracking)
- SAVE: Current waypoint number
- Report: ACK/NACK (progress: X of 3)

TEST 32: is_mission_finished (during execution)
- PREREQUISITE: Mission running, not completed yet
- ACTION: Run is_mission_finished
- VERIFY: Correctly reports mission still in progress
- ACK CRITERIA: Returns false (mission ongoing)
- NACK CRITERIA: Returns true (incorrect - mission not done)
- Report: ACK/NACK

TEST 33: pause_mission (verify can interrupt)
- PREREQUISITE: Mission running
- ACTION: Run pause_mission
- WAIT: 5 seconds
- ACTION: Run get_position - SAVE as POS_PAUSED
- WAIT: 5 more seconds
- ACTION: Run get_position again
- VERIFY: Drone actually stopped (not just API call succeeded)
- ACK CRITERIA: Position changed < 3m between two checks (drone holding)
- NACK CRITERIA: Position changed > 5m (still moving - pause didn't work)
- Report: ACK/NACK (holding: yes/no, drift: Xm)

TEST 34: set_current_waypoint (mission skip/jump)
- PREREQUISITE: Mission paused
- ACTION: Run set_current_waypoint to jump to waypoint 2
- ACTION: Run print_mission_progress
- VERIFY: Waypoint index actually changed
- ACK CRITERIA: Progress now shows "2 of 3" (jumped to waypoint 2)
- NACK CRITERIA: Still shows waypoint 1 (jump didn't work)
- Report: ACK/NACK (now at waypoint: X)

TEST 35: resume_mission (verify restart)
- PREREQUISITE: Mission paused
- ACTION: Get current position - SAVE as POS_RESUME
- ACTION: Run resume_mission
- WAIT: 10 seconds
- ACTION: Get current position
- VERIFY: Drone actually resumed movement
- ACK CRITERIA: Position changed > 5m from POS_RESUME (drone moving again)
- NACK CRITERIA: Position unchanged (resume didn't work)
- Report: ACK/NACK (resumed: yes/no, moved: Xm)

WAIT: 25 seconds for mission to complete (waypoints 2 and 3)

TEST 36: is_mission_finished (after completion)
- PREREQUISITE: Waited for mission completion
- ACTION: Run is_mission_finished
- VERIFY: Mission actually completed
- ACK CRITERIA: Returns true (mission done)
- NACK CRITERIA: Returns false (mission should be done by now)
- Report: ACK/NACK

TEST 37: clear_mission (cleanup)
- PREREQUISITE: Mission finished
- ACTION: Run clear_mission
- ACTION: Run download_mission or print_mission_progress
- VERIFY: Mission actually cleared from drone
- ACK CRITERIA: 
  * Clear command succeeds
  * Follow-up command shows no mission or empty list
- NACK CRITERIA: Mission still showing after clear
- Report: ACK/NACK

═══════════════════════════════════════════════════════════
CATEGORY 6: SAFETY & EMERGENCY
═══════════════════════════════════════════════════════════

TEST 38: return_to_launch (verify RTL function)
- PREREQUISITE: Drone in air, have HOME position from TEST 7
- ACTION: Get current position - SAVE as POS_RTL_START
- ACTION: Run return_to_launch
- VERIFY: Command accepted
- WAIT: 20 seconds for RTL to complete
- Report: ACK/NACK

TEST 39: get_position (VERIFY RTL actually returned home)
- PREREQUISITE: TEST 38 executed
- ACTION: Run get_position
- CALCULATE: Distance from HOME position (saved in TEST 7)
- VERIFY: Drone actually returned to launch point
- ACK CRITERIA:
  * Latitude within 0.00005° of HOME (≈5m)
  * Longitude within 0.00006° of HOME (≈5m)
  * Overall distance from HOME < 8m
- NACK CRITERIA: Distance from HOME > 10m (didn't return)
- Report: ACK/NACK (distance from home: Xm)

TEST 40: land_drone (verify landing)
- PREREQUISITE: Drone in air, near home position
- ACTION: Get current altitude via get_position - SAVE as ALT_LAND_START
- ACTION: Run land_drone
- WAIT: 5 seconds
- ACTION: Get altitude - should be decreasing
- WAIT: another 10-20 seconds depending on altitude
- ACTION: Get altitude repeatedly until < 1m or stopped changing
- VERIFY: Drone actually descended to ground
- ACK CRITERIA: Final altitude < 0.5m (on ground)
- NACK CRITERIA: Altitude > 2m after 30 seconds (didn't land)
- SAVE: Time when altitude first reached < 0.5m
- Report: ACK/NACK (landed: yes/no, final alt: Xm)

═══════════════════════════════════════════════════════════
SAFETY CHECK BEFORE DISARM
═══════════════════════════════════════════════════════════

TEST 41: ⚠️ MANDATORY PRE-DISARM SAFETY CHECK ⚠️
- CRITICAL: This test prevents disarming in the air
- ACTION: Run get_position
- CHECK 1: altitude_m < 0.5
- ACTION: Run get_telemetry
- CHECK 2: vertical_speed_ms near 0 (not falling)
- ACTION: Run get_speed
- CHECK 3: ground_speed < 0.5 m/s (not moving)
- EVALUATE SAFETY:
  * IF altitude > 0.5m: **ABORT! DRONE IN AIR! DO NOT DISARM!**
  * IF altitude < 0.5m AND speed < 0.5 m/s: Safe to disarm
  * IF altitude < 0.5m BUT speed > 1 m/s: WAIT, drone still moving
- ACK CRITERIA: ALL safety checks pass
- NACK CRITERIA: ANY safety check fails
- Report: ACK (SAFE TO DISARM) or NACK (NOT SAFE - reason: X)
- IF NACK: STOP TEST HERE, DO NOT PROCEED TO DISARM

═══════════════════════════════════════════════════════════
CATEGORY 7: POST-FLIGHT
═══════════════════════════════════════════════════════════

TEST 42: disarm_drone (ONLY if TEST 41 = ACK)
- PREREQUISITE: TEST 41 returned ACK (safe to disarm)
- IF TEST 41 = NACK: SKIP THIS TEST (NOT SAFE)
- ACTION: Run disarm_drone
- VERIFY: Command accepted
- ACK CRITERIA: Success message returned
- NACK CRITERIA: Error message or command rejected
- Report: ACK/NACK or SKIPPED

TEST 43: get_armed (VERIFY disarm worked)
- PREREQUISITE: TEST 42 executed
- ACTION: Run get_armed
- VERIFY: Drone actually disarmed (not just API success)
- ACK CRITERIA: is_armed = false (VERIFIED disarmed)
- NACK CRITERIA: is_armed = true (disarm command failed)
- Report: ACK/NACK - If NACK, TEST 42 also fails

TEST 44: get_battery (verify battery drain)
- PREREQUISITE: Flight complete, drone disarmed
- ACTION: Run get_battery
- COMPARE: Current battery vs BATTERY_START (from TEST 3)
- VERIFY: Battery decreased (drone actually flew)
- ACK CRITERIA: 
  * Battery voltage OR percentage lower than start
  * Decrease is reasonable (at least 1-5%)
- NACK CRITERIA: Battery unchanged (suspicious - may indicate no flight)
- Report: ACK/NACK (started: X%, ended: X%, used: X%)

═══════════════════════════════════════════════════════════
FINAL REPORT
═══════════════════════════════════════════════════════════

Please provide comprehensive report:

1. **Summary Statistics:**
   - Total tests: 44
   - ACK (verified success): [count]
   - NACK (verified failure): [count]
   - SKIPPED (prerequisites not met): [count]
   - Success rate: [ACK / (ACK + NACK)] %

2. **Detailed Results Table:**
   Format: TEST# | Tool | Result | Verification | Notes
   Example:
   - TEST 15 | get_position | ACK | Altitude 11.8m (target 12m, error 0.2m) | ✓
   - TEST 19 | get_attitude | NACK | Yaw unchanged at 127° (target 0°) | Rotation failed
   - TEST 25 | orbit_location | ACK | Not supported, helpful workaround provided | ✓

3. **Failed Tests Analysis:**
   For each NACK:
   - Which test failed
   - What was expected
   - What actually happened  
   - Why verification failed
   - Impact on subsequent tests

4. **Prerequisite Failures:**
   List any tests skipped due to failed prerequisites

5. **Verification Insights:**
   - How many tools returned success but drone didn't act? (API worked but action failed)
   - How many tools had proper error handling? (detected unsupported features)

6. **Safety Assessment:**
   - Was pre-disarm safety check effective?
   - Were any unsafe conditions detected?
   - Did prerequisite checks prevent inappropriate commands?

7. **Production Readiness:**
   - Overall: YES/NO
   - Core flight control (arm, takeoff, move, land, disarm): X/10 ACK
   - Navigation (yaw, reposition, orbit): X/3 ACK
   - Missions (upload, execute, control): X/11 ACK
   - Telemetry & monitoring: X/7 ACK
   - Safety & prerequisites: Effective? YES/NO

8. **Recommendations:**
   Based on NACK tests, what needs fixing?
```

### What This Tests

**Intelligent Verification (Not Just API Calls):**
- ✅ **Prerequisites checked** before each command (e.g., must be in air for yaw)
- ✅ **ACK/NACK logic** - Verifies drone actually performed action, not just API success
- ✅ **Telemetry confirmation** after every movement (position, altitude, attitude, speed)
- ✅ **Value comparison** - Saves initial state and verifies changes
- ✅ **Tolerance-based verification** - Realistic ±2m altitude, ±15° heading tolerances
- ✅ **Multi-step verification** - Example: set_yaw → wait → get_attitude → verify heading changed

**Complete Tool Coverage:**
- ✅ **Telemetry (7 tools)**: health, battery, GPS, position, speed, attitude, flight mode
- ✅ **Parameters (3 tools)**: list, get, set + verification of persistence
- ✅ **Flight Control (10 tools)**: arm, takeoff, move, hold, land, disarm, RTL
- ✅ **Navigation (3 tools)**: set_yaw, reposition, orbit (with movement verification)
- ✅ **Missions (11 tests)**: upload, download, start, pause, resume, progress, jump, clear
- ✅ **Safety (4 checks)**: RTL, battery, pre-disarm altitude+speed, prerequisite gates

**Smart Safety Features:**
- ✅ **Prerequisite gates**: Yaw/movement commands ONLY if altitude > 2m
- ✅ **Triple safety check before disarm**: altitude < 0.5m + speed < 0.5 m/s + not falling
- ✅ **ABORT on unsafe conditions**: Will NOT disarm if any safety check fails
- ✅ **State tracking**: Saves HOME position, initial battery, initial yaw for verification
- ✅ **Distance calculations**: Verifies drone actually moved expected distance

**Enhanced Reporting:**
- ✅ **ACK** = Tool worked AND drone did what was requested (verified)
- ✅ **NACK** = Tool call succeeded but drone didn't act as expected (failed verification)
- ✅ **SKIPPED** = Prerequisite not met (e.g., can't test yaw if drone not in air)
- ✅ Detailed failure analysis showing expected vs actual values
- ✅ Identifies API success vs action failure (critical distinction)

---

## 🚀 Quick Test (5 Minutes)

For a faster feature test:

```
Quick inspection test:

1. Show me all battery parameters
2. Get the RTL altitude parameter - if it's less than 20m, set it to 20m
3. Run a health check
4. Arm and takeoff to 10 meters
5. Face north (0 degrees) to orient the camera
6. Orbit around lat 33.6459, lon -117.8427 at 15 meter radius, 2 m/s, clockwise, at 15m altitude
7. After 20 seconds, stop and reposition to lat 33.6460, lon -117.8428 at 20m
8. Check battery level
9. Create and upload (don't start) a 3-waypoint mission going north, east, then back
10. Download the mission to verify
11. Check if any mission is currently running (should be false)
12. Return to launch and land
13. Disarm

Execute this step by step and report status after each action.
```

---

## 🧪 Individual Feature Tests

### Parameter Management Tests

**Test 1: Read Parameters**
```
Show me all parameters that start with "RTL"
Then show me all parameters that start with "BATT"
What is the current RTL_ALT value?
```

**Test 2: Modify Parameters**
```
Get the current RTL altitude
Set it to 2500 (25 meters)
Read it back to confirm the change
```

**Test 3: Parameter Discovery**
```
List all parameters (show me the count first)
Then show me just the GPS-related parameters
Show me the first 10 parameters alphabetically
```

---

### Advanced Navigation Tests

**Test 4: Orbit Mode**
```
Arm the drone and takeoff to 20 meters
Check our GPS position
Orbit around our current location at 30 meter radius, 3 m/s, clockwise
After 30 seconds, stop and hold position
Check our speed to confirm we stopped
Land and disarm
```

**Test 5: Heading Control**
```
Arm and takeoff to 15 meters
Face north (0 degrees)
Wait 5 seconds, then face east (90 degrees)
Wait 5 seconds, then face south (180 degrees)
Wait 5 seconds, then face west (270 degrees)
Get our current attitude to confirm heading
Land and disarm
```

**Test 6: Reposition**
```
Arm and takeoff to 10 meters
Check current position
Reposition to 50 meters north of current position at 20m altitude
Check new position to confirm
Hold there for 30 seconds
Return to launch and land
Disarm
```

---

### Mission Enhancement Tests

**Test 7: Mission Upload/Download**
```
I want to upload a 3-waypoint mission. Here are the waypoints in the correct format:

Waypoint 1: {"latitude_deg": 33.6459, "longitude_deg": -117.8427, "relative_altitude_m": 15}
Waypoint 2: {"latitude_deg": 33.6460, "longitude_deg": -117.8427, "relative_altitude_m": 15}
Waypoint 3: {"latitude_deg": 33.6460, "longitude_deg": -117.8428, "relative_altitude_m": 15}

Upload this mission using the upload_mission tool (don't start it yet)
Then download the mission back using download_mission
Show me the downloaded waypoints to verify they match
```

**Note:** Make sure to specify the exact field names (`latitude_deg`, `longitude_deg`, `relative_altitude_m`) as shown above. The v1.2.1 update provides better error messages if the format is wrong.

**Test 8: Mission Control**
```
Assuming a mission is uploaded:
1. Check if the mission is finished (should be false - not started)
2. Arm, takeoff to 10m, and start the mission
3. Wait until waypoint 1 is reached
4. Pause the mission
5. Check if mission is finished (should be false - paused)
6. Jump to waypoint 3 using set_current_waypoint
7. Resume the mission
8. Monitor until mission is finished
9. Return to launch and disarm
```

---

## 🔍 Validation Checklist

After running the granular test, verify:

### Telemetry & Health (7 tools)
- [ ] `get_health` shows all system status
- [ ] `get_telemetry` returns comprehensive data
- [ ] `get_battery` shows voltage and percentage
- [ ] `get_gps_info` shows satellite count and fix
- [ ] `get_flight_mode` returns current mode
- [ ] `get_armed` correctly reports armed state
- [ ] `get_position` shows accurate GPS coordinates

### Parameter Management (3 tools)
- [ ] `list_parameters` can filter by prefix
- [ ] `get_parameter` reads individual parameters
- [ ] `set_parameter` writes and confirms changes
- [ ] Invalid parameters return helpful error messages

### Flight Control (10 tools)
- [ ] `arm_drone` successfully arms
- [ ] `takeoff_drone` reaches target altitude
- [ ] `go_to_location` moves to GPS coordinates
- [ ] `hold_position` maintains current position
- [ ] `land_drone` descends safely
- [ ] `disarm_drone` only works when landed (safety check)
- [ ] `return_to_launch` returns to takeoff point

### Advanced Navigation (3 tools)
- [ ] `set_yaw` rotates to specified heading
- [ ] `reposition` moves and holds new GPS location
- [ ] `orbit_location` works OR provides firmware workaround
- [ ] `get_attitude` confirms heading changes
- [ ] `get_speed` tracks movement

### Mission Management (8 tools)
- [ ] `upload_mission` accepts correct format
- [ ] `download_mission` retrieves waypoints (or reports unsupported)
- [ ] `initiate_mission` starts mission execution
- [ ] `print_mission_progress` shows current waypoint
- [ ] `pause_mission` stops at current waypoint
- [ ] `resume_mission` continues from pause
- [ ] `set_current_waypoint` jumps to specific waypoint
- [ ] `is_mission_finished` correctly reports completion
- [ ] `clear_mission` removes mission from drone

### Safety Features
- [ ] Pre-disarm altitude check prevents mid-air disarm
- [ ] Battery warnings appear when low
- [ ] GPS lock verified before flight
- [ ] Health check shows system readiness

---

## 🎯 Expected Results

### Success Indicators
✅ ChatGPT sequences actions logically  
✅ All 35 tools are called appropriately  
✅ Parameters read/write correctly  
✅ Orbit executes smoothly  
✅ Yaw control shows cardinal directions  
✅ Mission upload/download cycle completes  
✅ Battery monitoring triggers warnings  
✅ Mission status checks work correctly  

### Common Issues & Solutions

**Issue: GPS coordinates drift**
- Solution: Use relative coordinates from current position
- Or: Adjust lat/lon for your test location

**Issue: Orbit not working - "Command not supported by autopilot"**
- **Root Cause:** Orbit command requires ArduPilot 4.0+ or PX4 1.13+
- **Workaround:** Server provides waypoint-based circle pattern instructions
- **Alternative:** Use repeated `go_to_location` + `set_yaw` for manual orbit
- **Note:** The error message will suggest how many waypoints to use for the requested radius

**Issue: Parameter names not found**
- Solution: Parameter names vary (ArduPilot vs PX4)
- Try: List all parameters first to find correct names
- **Examples:**
  - ArduPilot: `RTL_ALT`, `BATT_CAPACITY`, `WP_SPEED`
  - PX4: `RTL_RETURN_ALT`, `BAT_CAPACITY`, `MIS_SPEED`

**Issue: Mission upload format errors - "Missing required fields"**
- **Root Cause:** Each waypoint must be a dictionary with exact field names
- **Correct Format:**
```python
waypoints = [
  {"latitude_deg": 33.6459, "longitude_deg": -117.8427, "relative_altitude_m": 15},
  {"latitude_deg": 33.6460, "longitude_deg": -117.8427, "relative_altitude_m": 20}
]
```
- **Common Mistakes:**
  - Using `lat`/`lon` instead of `latitude_deg`/`longitude_deg`
  - Using `altitude` instead of `relative_altitude_m`
  - Passing string coordinates instead of numbers
- **v1.2.1 Improvement:** Error messages now show exactly what was received vs expected

**Issue: Mission download returns empty or fails**
- **Root Cause:** Some autopilots don't support mission download or require specific firmware
- **Workaround:** Keep a local copy of uploaded missions
- **Check:** ArduPilot: works on 4.0+; PX4: works on 1.12+

**Issue: Battery showing 0% throughout flight**
- **Root Cause:** Battery monitoring not calibrated or not supported by simulator
- **v1.2.1 Fix:** Server now detects this and provides voltage-based estimates
- **Look For:** `estimated_percent` field in battery response
- **Solution:** Set `BATT_CAPACITY` parameter to your battery's mAh rating
- **Simulator Note:** SITL often doesn't simulate battery drain accurately

---

## 📊 Performance Benchmarks

Expected execution times:
- **Quick Test:** ~5 minutes
- **Full Tower Inspection:** ~15-20 minutes
- **Individual Feature Tests:** ~2-3 minutes each

---

## 🔧 Customizing Tests

### Adjust GPS Coordinates
Replace these coordinates with your location:
```python
# Example coordinates (Irvine, CA)
BASE_LAT = 33.6459
BASE_LON = -117.8427

# Calculate offsets for waypoints
# ~0.0001° latitude ≈ 11 meters
# ~0.0001° longitude ≈ 9 meters (at 33° latitude)
```

### Modify Mission Patterns
Common patterns:
- **Square:** 4 waypoints in square formation
- **Circle:** Use orbit instead of waypoints
- **Grid:** Multiple parallel lines for surveys
- **Vertical:** Same lat/lon, different altitudes

---

## 🚨 Safety Notes

⚠️ **ALWAYS:**
- Test in open area away from people/buildings
- Maintain visual line of sight
- Have RC transmitter ready for manual override
- Monitor battery levels closely
- Start with low altitudes (5-10m)

⚠️ **NEVER:**
- Test near airports or restricted airspace
- Fly in bad weather (wind, rain, fog)
- Test with low battery
- Leave drone unattended
- Ignore safety warnings

---

## 🔧 Firmware Compatibility Matrix

Different autopilots support different features. Here's what's required:

| Feature | ArduPilot | PX4 | Notes |
|---------|-----------|-----|-------|
| **Basic Flight** | ✅ All versions | ✅ All versions | Core features work everywhere |
| **Parameter Management** | ✅ All versions | ✅ All versions | Universal support |
| **Set Yaw** | ✅ All versions | ✅ All versions | Universal support |
| **Reposition** | ✅ All versions | ✅ All versions | Universal support |
| **Orbit Location** | ✅ 4.0+ | ✅ 1.13+ | Older versions: use waypoint workaround |
| **Upload Mission** | ✅ All versions | ✅ All versions | Format may vary slightly |
| **Download Mission** | ✅ 4.0+ | ✅ 1.12+ | Older versions may not support |
| **Set Current Waypoint** | ✅ All versions | ✅ All versions | Universal support |
| **Battery Monitoring** | ⚠️ Needs calibration | ⚠️ Needs calibration | Set `BATT_CAPACITY` parameter |

**Recommended Minimum Versions:**
- **ArduPilot Copter:** 4.0.0 or newer (for full v1.2.0 features)
- **PX4:** 1.13.0 or newer (for full v1.2.0 features)
- **SITL:** Latest stable (some features may not work in simulation)

---

## 📈 Test Results Template

Use this template to record your test results:

```markdown
## Test Session - [Date]

**Environment:**
- Drone: [Model/SITL]
- Autopilot: [ArduPilot/PX4 version]
- MCP Version: v1.2.0
- Location: [Indoor/Outdoor/SITL]

**Tests Completed:**
- [ ] Parameter Management (all 3 tools)
- [ ] Advanced Navigation (all 3 tools)
- [ ] Mission Enhancements (all 4 tools)
- [ ] Full Tower Inspection Scenario

**Results:**
- Parameters tested: ✅ Pass / ❌ Fail
- Orbit location: ✅ Pass / ❌ Fail
- Set yaw: ✅ Pass / ❌ Fail
- Reposition: ✅ Pass / ❌ Fail
- Upload mission: ✅ Pass / ❌ Fail
- Download mission: ✅ Pass / ❌ Fail
- Set waypoint: ✅ Pass / ❌ Fail
- Mission finished: ✅ Pass / ❌ Fail

**Issues Encountered:**
[Describe any problems]

**Notes:**
[Additional observations]
```

---

## 🤝 Contributing Test Results

Found an issue or have suggestions? Please:
1. Open an issue on GitHub
2. Include your test results
3. Provide logs from the MCP server
4. Describe your setup (drone, autopilot, location)

---

## 📚 Additional Resources

- [README.md](README.md) - Main documentation
- [STATUS.md](STATUS.md) - Complete feature list
- [CHATGPT_SETUP.md](CHATGPT_SETUP.md) - Setup guide
- [TESTING_FIXES.md](TESTING_FIXES.md) - Detailed fixes from comprehensive testing
- [examples/](examples/) - Example scripts

---

## 📋 v1.2.1 Testing Improvements

Based on comprehensive real-world testing, v1.2.1 includes:

1. **✅ Better Mission Upload Validation**
   - Clear error messages showing exactly what's wrong with waypoint format
   - Type checking for each waypoint
   - Coordinate validation (lat/lon ranges, altitude >= 0)
   - Helpful examples in error responses

2. **✅ Orbit Capability Detection**
   - Automatically detects unsupported orbit commands
   - Provides waypoint-based circle alternative with calculations
   - Shows firmware requirements in error message

3. **✅ Battery Monitoring Fallback**
   - Detects when percentage is uncalibrated (0% with good voltage)
   - Provides voltage-based estimates using LiPo curves
   - Suggests setting `BATT_CAPACITY` parameter

4. **✅ Firmware Compatibility Matrix**
   - Clear documentation of which features need which firmware versions
   - Workarounds provided for unsupported features

See [TESTING_FIXES.md](TESTING_FIXES.md) for detailed analysis and workarounds.

---

**Happy Testing! 🚁✨**

Report any issues at: https://github.com/PeterJBurke/MAVLinkMCP/issues

