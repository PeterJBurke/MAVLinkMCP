# Granular Test - Complete Coverage with Verification

Deep verification with ACK/NACK logic for production readiness assessment.

## Overview

- **Duration:** 30-45 minutes
- **Tests:** 44 individual tests
- **Complexity:** High
- **Best For:** Production readiness validation with detailed verification

This test systematically validates **all 36 tools** with **intelligent prerequisites** and **ACK/NACK verification**. Each test confirms the drone actually performed the action, not just that the API call succeeded.

---

## Copy This Prompt Into ChatGPT

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

TEST 33: hold_mission_position (verify safe pause - v1.2.2)
- PREREQUISITE: Mission running
- ACTION: Run hold_mission_position (NOT pause_mission - deprecated!)
- WAIT: 5 seconds
- ACTION: Run get_position - SAVE as POS_PAUSED
- ACTION: Run get_flight_mode
- VERIFY: Flight mode is GUIDED (not LOITER)
- WAIT: 5 more seconds
- ACTION: Run get_position again
- VERIFY: Drone actually stopped AND altitude maintained
- ACK CRITERIA: 
  * Flight mode = GUIDED
  * Position changed < 3m horizontally (drone holding)
  * Altitude maintained (±1m from POS_PAUSED altitude)
- NACK CRITERIA: Position changed > 5m OR altitude dropped > 2m OR mode = LOITER
- Report: ACK/NACK (mode: X, holding: yes/no, drift: Xm, altitude stable: yes/no)

TEST 34: set_current_waypoint (mission skip/jump)
- PREREQUISITE: Mission paused/held
- ACTION: Run set_current_waypoint to jump to waypoint 2
- ACTION: Run print_mission_progress
- VERIFY: Waypoint index actually changed
- ACK CRITERIA: Progress now shows "2 of 3" (jumped to waypoint 2)
- NACK CRITERIA: Still shows waypoint 1 (jump didn't work)
- Report: ACK/NACK (now at waypoint: X)

TEST 35: resume_mission (verify restart - enhanced v1.2.2)
- PREREQUISITE: Mission paused/held
- ACTION: Get current position - SAVE as POS_RESUME
- ACTION: Run resume_mission
- VERIFY: Response includes waypoint info and mode transition
- ACK CRITERIA:
  * Success message
  * Returns current_waypoint and total_waypoints
  * mode_transition_ok = true (changed to AUTO/MISSION)
- WAIT: 10 seconds
- ACTION: Get current position
- VERIFY: Drone actually resumed movement
- ACK CRITERIA: Position changed > 5m from POS_RESUME (drone moving again)
- NACK CRITERIA: Position unchanged (resume didn't work)
- Report: ACK/NACK (mode transition: yes/no, resumed: yes/no, moved: Xm)

WAIT: 25 seconds for mission to complete (waypoints 2 and 3)

TEST 36: is_mission_finished (after completion - enhanced v1.2.2)
- PREREQUISITE: Waited for mission completion
- ACTION: Run is_mission_finished
- VERIFY: Mission actually completed and response includes details
- ACK CRITERIA: 
  * Returns true (mission done)
  * Provides current_waypoint, total_waypoints, progress_percentage
  * Shows flight_mode
- NACK CRITERIA: Returns false (mission should be done by now)
- Report: ACK/NACK (finished: yes/no, progress: X%)

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

---

## What This Tests

### Intelligent Verification (Not Just API Calls)
- ✅ **Prerequisites checked** before each command (e.g., must be in air for yaw)
- ✅ **ACK/NACK logic** - Verifies drone actually performed action, not just API success
- ✅ **Telemetry confirmation** after every movement (position, altitude, attitude, speed)
- ✅ **Value comparison** - Saves initial state and verifies changes
- ✅ **Tolerance-based verification** - Realistic ±2m altitude, ±15° heading tolerances
- ✅ **Multi-step verification** - Example: set_yaw → wait → get_attitude → verify heading changed

### Complete Tool Coverage
- ✅ **Telemetry (7 tools)**: health, battery, GPS, position, speed, attitude, flight mode
- ✅ **Parameters (3 tools)**: list, get, set + verification of persistence
- ✅ **Flight Control (10 tools)**: arm, takeoff, move, hold, land, disarm, RTL
- ✅ **Navigation (3 tools)**: set_yaw, reposition, orbit (with movement verification)
- ✅ **Missions (11 tests)**: upload, download, start, pause, resume, progress, jump, clear
- ✅ **Safety (4 checks)**: RTL, battery, pre-disarm altitude+speed, prerequisite gates

### Smart Safety Features
- ✅ **Prerequisite gates**: Yaw/movement commands ONLY if altitude > 2m
- ✅ **Triple safety check before disarm**: altitude < 0.5m + speed < 0.5 m/s + not falling
- ✅ **ABORT on unsafe conditions**: Will NOT disarm if any safety check fails
- ✅ **State tracking**: Saves HOME position, initial battery, initial yaw for verification
- ✅ **Distance calculations**: Verifies drone actually moved expected distance

### Enhanced Reporting
- ✅ **ACK** = Tool worked AND drone did what was requested (verified)
- ✅ **NACK** = Tool call succeeded but drone didn't act as expected (failed verification)
- ✅ **SKIPPED** = Prerequisite not met (e.g., can't test yaw if drone not in air)
- ✅ Detailed failure analysis showing expected vs actual values
- ✅ Identifies API success vs action failure (critical distinction)

---

## Expected Results

### High Success Rate
- ✅ 38-42 out of 44 tests should ACK
- ✅ Common NACKs: orbit (firmware limitation), download_mission (autopilot limitation)
- ✅ All safety checks should work perfectly

### Success Criteria for Production Readiness
- ✅ At least 35/44 tests ACK (80% success)
- ✅ All safety tests pass (TEST 41: pre-disarm check)
- ✅ Core flight tests pass (arm, takeoff, move, land, disarm)
- ✅ Mission management works (upload, execute, monitor)
- ✅ hold_mission_position works in GUIDED mode (TEST 33)

---

## Time Estimate

- Category 1-2 (Telemetry + Parameters): ~5 minutes
- Category 3-4 (Flight + Navigation): ~10 minutes
- Category 5 (Missions): ~15 minutes
- Category 6-7 (Safety + Post-flight): ~5 minutes
- **Total:** 30-45 minutes

---

## Next Steps

**All tests passed?** → System is production-ready!

**Some NACKs?** → Review failed tests in [TESTING_REFERENCE.md](TESTING_REFERENCE.md)

**Want quicker test?** → Try [TESTING_COMPREHENSIVE.md](TESTING_COMPREHENSIVE.md)

**Need to debug specific feature?** → See [TESTING_INDIVIDUAL.md](TESTING_INDIVIDUAL.md)

---

[← Back to Testing Guide](TESTING_GUIDE.md)

