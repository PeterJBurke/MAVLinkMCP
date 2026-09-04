# MAVLink MCP Testing Guide

> **This document is the *manual* test procedure** — prompts a human types into an
> MCP chat client to check the system by hand. It is not the automated suite.
>
> The automated suites are 937 unit tests (`uv run pytest`), 116 SITL integration
> tests (`uv run pytest -m "sitl and not longmission" tests/integration`), and the
> adversarial suite (`docs/adversarial_results.md`). See [README.md](README.md).

---

## Prerequisites

Before testing, ensure:
1. ✅ MAVLink MCP server is running (`./start_http_server.sh`)
2. ✅ Drone/SITL is connected and GPS lock acquired
3. ✅ Your MCP chat client is connected to the server over the tailnet (or loopback)
4. ✅ You're in an open, safe area for testing

---

## 📚 Test Suites

Choose the test that fits your needs:

### 🚀 Quick Test (5 minutes)
**Best for:** First-time setup verification or quick feature check
- 14 simple steps
- Tests basic flight + new v1.2.0 features
- Minimal complexity

### 🎯 Comprehensive Test (15-20 minutes)
**Best for:** Full system validation
- 7 phases, 33 operations
- Complete tower inspection scenario
- Tests all 35 tools in realistic workflow
- Includes detailed final report template

### 🔬 Granular Test (30-45 minutes)
**Best for:** Deep verification with ACK/NACK logic
- 44 individual tests with prerequisites
- Verifies drone *actually* performed each action
- Intelligent safety checks
- Production readiness assessment

### 🧪 Individual Feature Tests
**Best for:** Debugging specific features or learning individual tools
- Parameter Management (3 tests)
- Advanced Navigation (3 tests)
- Mission Enhancements (2 tests)
- Isolated, repeatable tests

### 🔧 Upload/Download Mission Diagnostic
**Best for:** Verifying mission upload/download functionality
- Quick 5-step test (no flight required)
- Verifies vehicle_action fix is deployed
- Troubleshooting guide for common issues

---

## 🚀 Quick Test (5 Minutes)

Fast validation of MAVLink MCP v1.2.3 features.

**Duration:** ~5 minutes  
**Tools Tested:** 15 out of 35  
**Complexity:** Low  
**Best For:** First-time setup verification or quick feature check

### ⚠️ CRITICAL SAFETY NOTE

**ALTITUDE REFERENCE:** All altitude commands in this test use **relative altitude** (height above home/ground), NOT absolute MSL altitude. The MCP server handles the conversion automatically.

If you modify this test, be aware:
- `takeoff`, `reposition`: Use relative altitude
- Home elevation may vary (e.g., 25m MSL in SITL)
- Never command altitudes below home elevation

### Copy This Prompt Into ChatGPT

```
Quick inspection test:

1. Show me all battery parameters
2. Get the RTL altitude parameter - if it's less than 20m, set it to 20m
3. Run a health check
4. Arm and takeoff to 10 meters
5. Face north (0 degrees) to orient the camera
6. Fly to lat 33.6459, lon -117.8427 staying at your current altitude
7. Face east (90 degrees) and hold position for 10 seconds
8. Reposition to lat 33.6460, lon -117.8428, climb to 20m relative altitude
9. Check battery level
10. IMPORTANT: Use upload_mission (NOT initiate_mission) to create a 3-waypoint mission going north, east, then back (don't start it)
11. Download the mission using download_mission to verify it was uploaded correctly
12. Check if any mission is currently running (should be false)
13. Return to launch and land
14. Disarm

Execute this step by step and report status after each action.

After completing all steps, create a summary report:
- Total steps completed: X/14
- Steps that succeeded: [list]
- Steps that failed: [list with reasons]
- Battery used: X%
- New features tested: [list which v1.2.0+ features were used]
- Overall success: YES/NO
- Key observations: [brief notes]
```

### What This Tests

**Parameter Management (v1.2.0)**
- ✅ `list_parameters` - List battery parameters
- ✅ `get_parameter` - Check RTL altitude
- ✅ `set_parameter` - Modify RTL altitude if needed

**Advanced Navigation (v1.2.0)**
- ✅ `set_yaw` - Face north (0°) and east (90°)
- ✅ `go_to_location` - Navigate to GPS coordinates
- ✅ `reposition` - Move to new position and hold

**Mission Enhancements (v1.2.0)**
- ✅ `upload_mission` - Upload without starting
- ✅ `download_mission` - Verify upload
- ✅ `is_mission_finished` - Check mission status

**Basic Flight Control**
- ✅ `get_health` - Pre-flight check
- ✅ `arm_drone` - Arm motors
- ✅ `takeoff` - Climb to altitude
- ✅ `get_battery` - Battery monitoring
- ✅ `return_to_launch` - RTL
- ✅ `land` - Landing
- ✅ `disarm_drone` - Disarm motors

### Success Criteria

- ✅ At least 10/14 steps complete successfully
- ✅ Parameter management works (steps 1-2)
- ✅ Basic flight works (steps 3-5, 12-13)
- ✅ At least 1 advanced navigation feature works (steps 6-7)
- ✅ Mission upload works (step 9)

---

## 🎯 Comprehensive Test - Tower Inspection Mission

Complete system validation through a realistic inspection scenario.

**Duration:** 15-20 minutes  
**Tools Tested:** 35 out of 35  
**Phases:** 7  
**Total Operations:** 33  
**Complexity:** Medium  
**Best For:** Full system validation in realistic workflow

### ⚠️ CRITICAL SAFETY NOTE

**ALTITUDE REFERENCE:** All altitude commands in this test use **relative altitude** (height above home/ground), NOT absolute MSL altitude. Commands like `reposition`, `go_to_location`, and `takeoff` automatically handle the conversion.

**Never command underground altitudes!** Always ensure relative altitudes are positive values.

### Copy This Prompt Into ChatGPT

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
12. Fly to lat 33.6459, lon -117.8425 (west side of tower) staying at your current altitude
13. Rotate to face the tower (face east at 90 degrees) and hold position for 10 seconds
14. Now fly to lat 33.6461, lon -117.8427 (east side of tower), staying at same altitude
15. Rotate to face west (270 degrees) toward the tower and hold for 10 seconds
16. Tell me what our current speed is
17. Check the battery level again - if it's below 70%, I want you to warn me

PHASE 4 - DETAILED INSPECTION:
19. Reposition to lat 33.6460, lon -117.8426, climb to 40m relative altitude to get a closer view of the upper tower section
20. Face north (0 degrees) to align with the tower
21. Get our current attitude (roll, pitch, yaw) to confirm we're level and facing the right direction

PHASE 5 - MISSION EXECUTION:
22. Now start the 4-waypoint mission we uploaded earlier
23. Monitor the mission and tell me when we reach waypoint 2
24. At waypoint 2, use hold_mission_position to pause safely (pause_mission with its default mode="guided_hold" does the same thing safely; only pause_mission(mode="native_hold") enters LOITER)
25. Check if the mission is finished (it shouldn't be since we paused it)
26. Resume the mission and let it continue
27. Keep checking until the mission is finished

PHASE 6 - RETURN AND LANDING:
28. Once mission is complete, check battery one more time
29. Return to launch position
30. Land the drone
31. Disarm when safely on the ground

PHASE 7 - POST-FLIGHT:
32. Download the mission from the drone one more time to save it
33. Show me all parameters that changed during the flight (compare with initial values)

Please execute this entire inspection mission step by step, confirming each action before moving to the next. Warn me immediately if any step fails or if battery gets critically low.

═══════════════════════════════════════════════════════════
FINAL COMPREHENSIVE REPORT
═══════════════════════════════════════════════════════════

After completing all phases, please create a detailed report with:

**1. Mission Summary:**
   - Total phases completed: X/7
   - Total operations performed: X/33
   - Flight time: X minutes
   - Battery consumed: X%
   - Final location: [coordinates]

**2. Phase-by-Phase Results:**
   - Phase 1 (Pre-flight): ✅/❌ - [brief summary]
   - Phase 2 (Mission Prep): ✅/❌ - [brief summary]
   - Phase 3 (Flight Ops): ✅/❌ - [brief summary]
   - Phase 4 (Inspection): ✅/❌ - [brief summary]
   - Phase 5 (Mission Exec): ✅/❌ - [brief summary]
   - Phase 6 (Return): ✅/❌ - [brief summary]
   - Phase 7 (Post-flight): ✅/❌ - [brief summary]

**3. Tools Used:**
   - List all MCP tools called during the mission
   - Note which tools worked perfectly vs had issues

**4. Issues Encountered:**
   - List any errors, warnings, or unexpected behavior
   - For each issue: what happened, what was expected, impact

**5. New Features Performance (v1.2.0+):**
   - Parameter management: ✅/❌ [notes]
   - Advanced navigation (yaw, reposition): ✅/❌ [notes]
   - Mission enhancements: ✅/❌ [notes]
   - hold_mission_position (v1.2.2): ✅/❌ [notes]

**6. Safety & Monitoring:**
   - Battery warnings: [any triggered?]
   - Pre-disarm safety checks: [passed?]
   - Flight mode changes: [list when modes changed]

**7. Overall Assessment:**
   - Mission success: YES/NO
   - System stability: Excellent/Good/Fair/Poor
   - Production ready: YES/NO
   - Key observations: [your analysis]

**8. Recommendations:**
   - What worked well
   - What needs improvement
   - Suggested next tests

Format this report clearly with sections and bullet points for easy reading.
```

### Success Criteria

- ✅ At least 6/7 phases complete successfully
- ✅ At least 25/33 operations complete successfully
- ✅ All v1.2.0 features tested (parameters, navigation, missions)
- ✅ All v1.2.2/v1.2.3 features tested (hold_mission_position, enhanced resume)
- ✅ No safety violations
- ✅ Drone returns home safely

---

## 🔬 Granular Test - Complete Coverage with Verification

Deep verification with ACK/NACK logic for production readiness assessment.

**Duration:** 30-45 minutes  
**Tests:** 44 individual tests  
**Complexity:** High  
**Best For:** Production readiness validation with detailed verification

This test systematically validates **all 35 tools** with **intelligent prerequisites** and **ACK/NACK verification**. Each test confirms the drone actually performed the action, not just that the API call succeeded.

### Copy This Prompt Into ChatGPT

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

TEST 2: get_battery
- PREREQUISITE: None
- ACTION: Run get_battery
- VERIFY: Shows voltage_v and remaining_percent (or estimated_percent)
- ACK CRITERIA: Voltage > 10V, percentage shown (or estimated)
- NACK CRITERIA: Voltage < 10V or error
- SAVE: Initial battery level (call it BATTERY_START)
- Report: ACK/NACK (voltage: X.XV, percent: X%)

TEST 3: get_gps_info
- PREREQUISITE: None
- ACTION: Run get_gps_info
- VERIFY: Satellite count and fix type
- ACK CRITERIA: Fix type = "3D" AND satellites >= 6
- NACK CRITERIA: No fix or satellites < 6
- Report: ACK/NACK (sats: X, fix: X)

TEST 4: get_flight_mode
- PREREQUISITE: None
- ACTION: Run get_flight_mode
- VERIFY: Returns a valid flight mode string
- ACK CRITERIA: Mode is returned (e.g., HOLD, MANUAL, STABILIZE)
- NACK CRITERIA: Error or empty response
- SAVE: Initial flight mode
- Report: ACK/NACK (mode: X)

TEST 5: get_armed
- PREREQUISITE: None
- ACTION: Run get_armed (should be false)
- VERIFY: Armed status returned
- ACK CRITERIA: is_armed = false (expected before arming)
- NACK CRITERIA: is_armed = true (unexpected) or error
- Report: ACK/NACK

TEST 6: get_position
- PREREQUISITE: GPS lock (from TEST 3)
- ACTION: Run get_position
- VERIFY: Shows lat, lon, altitude
- ACK CRITERIA: Valid GPS coordinates (-90 to 90 lat, -180 to 180 lon), altitude near 0
- NACK CRITERIA: Invalid coordinates or error
- SAVE: HOME position (lat, lon) for later verification
- Report: ACK/NACK (lat: X, lon: X, alt: Xm)

═══════════════════════════════════════════════════════════
CATEGORY 2: PARAMETER MANAGEMENT (v1.2.0)
═══════════════════════════════════════════════════════════

TEST 7: list_parameters (with filter)
- PREREQUISITE: None
- ACTION: Run list_parameters with filter_prefix="RTL"
- VERIFY: Returns list of RTL-related parameters
- ACK CRITERIA: List contains at least 1 RTL parameter (RTL_ALT, RTL_RETURN_ALT, etc.)
- NACK CRITERIA: Empty list or error
- Report: ACK/NACK (found X parameters)

TEST 8: get_parameter (read)
- PREREQUISITE: Know parameter name from TEST 7
- ACTION: Run get_parameter for "RTL_ALT" (or "RTL_RETURN_ALT" for PX4)
- VERIFY: Returns numerical value
- ACK CRITERIA: Returns value (typically 1500-3000 for altitude in cm)
- NACK CRITERIA: Parameter not found or error
- SAVE: Original RTL_ALT value (call it RTL_ORIGINAL)
- Report: ACK/NACK (value: X)

TEST 9: set_parameter (write)
- PREREQUISITE: None
- ACTION: Run set_parameter to set RTL_ALT to 2500
- VERIFY: Response shows old value and new value
- ACK CRITERIA: old_value = RTL_ORIGINAL, new_value = 2500, status = success
- NACK CRITERIA: Error or values don't match expected
- Report: ACK/NACK (changed from X to 2500)

TEST 10: get_parameter (verify persistence)
- PREREQUISITE: TEST 9 succeeded
- ACTION: Run get_parameter for RTL_ALT again
- VERIFY: Value matches what we just set
- ACK CRITERIA: Value = 2500 (confirms write was persistent)
- NACK CRITERIA: Value ≠ 2500 (write didn't persist)
- Report: ACK/NACK (verified: X)

═══════════════════════════════════════════════════════════
CATEGORY 3: BASIC FLIGHT CONTROL
═══════════════════════════════════════════════════════════

TEST 11: arm_drone
- PREREQUISITE: Drone disarmed (is_armed = false), health check passed (TEST 1)
- ACTION: Run arm_drone
- VERIFY: Command returns success
- ACK CRITERIA: Success message returned (but not verified yet)
- NACK CRITERIA: Error message
- Report: ACK/NACK

TEST 12: get_armed (verify arming)
- PREREQUISITE: TEST 11 executed
- ACTION: Run get_armed
- VERIFY: Drone actually armed
- ACK CRITERIA: is_armed = true (VERIFIED drone is armed)
- NACK CRITERIA: is_armed = false (arm command didn't work)
- Report: ACK/NACK - If NACK, previous test also fails

TEST 13: takeoff
- PREREQUISITE: Drone armed (is_armed = true)
- ACTION: Run takeoff to 12 meters
- VERIFY: Command accepted
- ACK CRITERIA: Success message, no error
- NACK CRITERIA: Error or command rejected
- WAIT: 15 seconds for takeoff to complete
- Report: ACK/NACK

TEST 14: get_position (verify takeoff altitude)
- PREREQUISITE: TEST 13 executed
- ACTION: Run get_position
- VERIFY: Drone actually climbed to target altitude
- ACK CRITERIA: altitude_m between 10m and 14m (±2m tolerance)
- NACK CRITERIA: altitude < 5m (takeoff failed) or error
- SAVE: Current altitude as ALT_CURRENT
- Report: ACK/NACK (altitude: Xm, target: 12m, error: Xm)

TEST 15: get_speed (verify hovering)
- PREREQUISITE: Drone in air (altitude > 5m)
- ACTION: Run get_speed
- VERIFY: Drone is stationary (hovering)
- ACK CRITERIA: ground_speed < 1 m/s AND vertical_speed < 0.5 m/s
- NACK CRITERIA: Speed too high (drone drifting) or error
- Report: ACK/NACK (ground: X m/s, vertical: X m/s)

TEST 16: get_attitude (baseline)
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

TEST 17: set_yaw (face north)
- PREREQUISITE: Drone IN AIR (altitude > 5m from TEST 14)
- IF altitude < 2m: SKIP this test (yaw only works in air)
- ACTION: Run set_yaw to 0 degrees (north)
- VERIFY: Command accepted
- ACK CRITERIA: Success message
- NACK CRITERIA: Error or command rejected
- WAIT: 8 seconds for rotation to complete
- Report: ACK/NACK

TEST 18: get_attitude (VERIFY yaw changed)
- PREREQUISITE: TEST 17 executed successfully
- ACTION: Run get_attitude
- VERIFY: Drone actually rotated to target heading
- ACK CRITERIA: Yaw between 350° and 10° (accounting for 0°/360° wraparound) 
              OR within 15° of 0° (±15° tolerance)
- NACK CRITERIA: Yaw unchanged from YAW_INITIAL (rotation didn't happen)
- SAVE: New yaw as YAW_CURRENT
- Report: ACK/NACK (target: 0°, actual: X°, error: X°)

TEST 19: set_yaw (face east - verify rotation works)
- PREREQUISITE: Drone in air, TEST 18 verified
- ACTION: Run set_yaw to 90 degrees (east)
- VERIFY: Command accepted
- WAIT: 8 seconds for rotation
- ACTION: Run get_attitude immediately after
- VERIFY: Yaw actually changed from previous
- ACK CRITERIA: Yaw between 75° and 105° (90° ±15° tolerance)
- NACK CRITERIA: Yaw still at previous value (no rotation)
- Report: ACK/NACK (target: 90°, actual: X°, error: X°)

TEST 20: go_to_location (horizontal movement)
- PREREQUISITE: Drone in air (altitude > 5m), have HOME position from TEST 6
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

TEST 21: get_position (VERIFY movement happened)
- PREREQUISITE: TEST 20 executed
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

TEST 22: reposition (with altitude change)
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

TEST 23: hold_position (verify hover stability)
- PREREQUISITE: Drone in air
- ACTION: Get current position - SAVE as POS_HOLD
- ACTION: Run hold_position
- WAIT: 8 seconds
- ACTION: Get current position again
- VERIFY: Drone stayed in place (minimal drift)
- ACK CRITERIA: Position changed < 3m in any direction
- NACK CRITERIA: Position changed > 5m (excessive drift)
- Report: ACK/NACK (drift: Xm)

═══════════════════════════════════════════════════════════
CATEGORY 5: MISSION MANAGEMENT
═══════════════════════════════════════════════════════════

TEST 24: upload_mission (mission pre-load)
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

TEST 25: download_mission (verify upload persistence)
- PREREQUISITE: TEST 24 succeeded
- ACTION: Run download_mission
- VERIFY: Downloaded mission matches uploaded mission
- ACK CRITERIA:
  * Returns 3 waypoints
  * Waypoint coordinates approximately match uploaded values (±0.00001°)
  * OR firmware reports "not supported" with helpful message
- NACK CRITERIA: Returns wrong number of waypoints OR different coordinates
- NOTE: If unsupported, mark ACK if error is informative
- Report: ACK/NACK (matched: yes/no OR unsupported)

TEST 26: is_mission_finished (before start)
- PREREQUISITE: Mission uploaded but NOT started
- ACTION: Run is_mission_finished
- VERIFY: Correctly reports no mission is running
- ACK CRITERIA: Returns false OR "no mission active"
- NACK CRITERIA: Returns true (mission can't be finished if not started)
- Report: ACK/NACK

TEST 27: initiate_mission (start execution)
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

TEST 28: print_mission_progress (during execution)
- PREREQUISITE: Mission running
- ACTION: Run print_mission_progress
- VERIFY: Shows accurate progress
- ACK CRITERIA: Shows "current/total" like "1 of 3" or "2 of 3"
- NACK CRITERIA: Shows "0 of 3" or error (no progress tracking)
- SAVE: Current waypoint number
- Report: ACK/NACK (progress: X of 3)

TEST 29: is_mission_finished (during execution)
- PREREQUISITE: Mission running, not completed yet
- ACTION: Run is_mission_finished
- VERIFY: Correctly reports mission still in progress
- ACK CRITERIA: Returns false (mission ongoing)
- NACK CRITERIA: Returns true (incorrect - mission not done)
- Report: ACK/NACK

TEST 30: hold_mission_position (verify safe pause - v1.2.2)
- PREREQUISITE: Mission running
- ACTION: Run hold_mission_position (or pause_mission with its default mode="guided_hold" - both hold in GUIDED; do NOT pass mode="native_hold" without an RC at mid-throttle)
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

TEST 31: set_current_waypoint (mission skip/jump)
- PREREQUISITE: Mission paused/held
- ACTION: Run set_current_waypoint to jump to waypoint 2
- ACTION: Run print_mission_progress
- VERIFY: Waypoint index actually changed
- ACK CRITERIA: Progress now shows "2 of 3" (jumped to waypoint 2)
- NACK CRITERIA: Still shows waypoint 1 (jump didn't work)
- Report: ACK/NACK (now at waypoint: X)

TEST 32: resume_mission (verify restart - enhanced v1.2.2)
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

TEST 33: is_mission_finished (after completion - enhanced v1.2.2)
- PREREQUISITE: Waited for mission completion
- ACTION: Run is_mission_finished
- VERIFY: Mission actually completed and response includes details
- ACK CRITERIA: 
  * Returns true (mission done)
  * Provides current_waypoint, total_waypoints, progress_percentage
  * Shows flight_mode
- NACK CRITERIA: Returns false (mission should be done by now)
- Report: ACK/NACK (finished: yes/no, progress: X%)

TEST 34: clear_mission (cleanup)
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

TEST 35: return_to_launch (verify RTL function)
- PREREQUISITE: Drone in air, have HOME position from TEST 6
- ACTION: Get current position - SAVE as POS_RTL_START
- ACTION: Run return_to_launch
- VERIFY: Command accepted
- WAIT: 20 seconds for RTL to complete
- Report: ACK/NACK

TEST 36: get_position (VERIFY RTL actually returned home)
- PREREQUISITE: TEST 35 executed
- ACTION: Run get_position
- CALCULATE: Distance from HOME position (saved in TEST 6)
- VERIFY: Drone actually returned to launch point
- ACK CRITERIA:
  * Latitude within 0.00005° of HOME (≈5m)
  * Longitude within 0.00006° of HOME (≈5m)
  * Overall distance from HOME < 8m
- NACK CRITERIA: Distance from HOME > 10m (didn't return)
- Report: ACK/NACK (distance from home: Xm)

TEST 37: land
- PREREQUISITE: Drone in air, near home position
- ACTION: Get current altitude via get_position - SAVE as ALT_LAND_START
- ACTION: Run land
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

TEST 38: ⚠️ MANDATORY PRE-DISARM SAFETY CHECK ⚠️
- CRITICAL: This test prevents disarming in the air
- ACTION: Run get_position
- CHECK 1: altitude_m < 0.5
- ACTION: Run get_speed
- CHECK 2: ground_speed < 0.5 m/s (not moving)
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

TEST 39: disarm_drone (ONLY if TEST 38 = ACK)
- PREREQUISITE: TEST 38 returned ACK (safe to disarm)
- IF TEST 38 = NACK: SKIP THIS TEST (NOT SAFE)
- ACTION: Run disarm_drone
- VERIFY: Command accepted
- ACK CRITERIA: Success message returned
- NACK CRITERIA: Error message or command rejected
- Report: ACK/NACK or SKIPPED

TEST 40: get_armed (VERIFY disarm worked)
- PREREQUISITE: TEST 39 executed
- ACTION: Run get_armed
- VERIFY: Drone actually disarmed (not just API success)
- ACK CRITERIA: is_armed = false (VERIFIED disarmed)
- NACK CRITERIA: is_armed = true (disarm command failed)
- Report: ACK/NACK - If NACK, TEST 39 also fails

TEST 41: get_battery (verify battery drain)
- PREREQUISITE: Flight complete, drone disarmed
- ACTION: Run get_battery
- COMPARE: Current battery vs BATTERY_START (from TEST 2)
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
   - Total tests: 41
   - ACK (verified success): [count]
   - NACK (verified failure): [count]
   - SKIPPED (prerequisites not met): [count]
   - Success rate: [ACK / (ACK + NACK)] %

2. **Detailed Results Table:**
   Format: TEST# | Tool | Result | Verification | Notes

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
   - Navigation (yaw, reposition, go_to_location): X/3 ACK
   - Missions (upload, execute, control): X/11 ACK
   - Telemetry & monitoring: X/7 ACK
   - Safety & prerequisites: Effective? YES/NO

8. **Recommendations:**
   Based on NACK tests, what needs fixing?
```

### Success Criteria

- ✅ At least 35/41 tests ACK (80% success)
- ✅ All safety tests pass (TEST 38: pre-disarm check)
- ✅ Core flight tests pass (arm, takeoff, move, land, disarm)
- ✅ Mission management works (upload, execute, monitor)
- ✅ hold_mission_position works in GUIDED mode (TEST 30)

---

## 🧪 Individual Feature Tests

Isolated tests for specific features - perfect for debugging or learning.

**Duration:** 2-3 minutes per test  
**Complexity:** Low-Medium  
**Best For:** Testing specific features, debugging issues, or learning individual tools

Each test is self-contained and can be run independently.

### Parameter Management Tests

#### Test 1: Read Parameters

```
Show me all parameters that start with "RTL"
Then show me all parameters that start with "BATT"
What is the current RTL_ALT value?
```

**What it tests:**
- ✅ `list_parameters` with filter
- ✅ `get_parameter` for specific values

**Expected result:** List of parameters displayed, RTL_ALT value shown (typically 1500-3000 cm)

#### Test 2: Modify Parameters

```
Get the current RTL altitude
Set it to 2500 (25 meters)
Read it back to confirm the change
```

**What it tests:**
- ✅ `get_parameter` - Read current value
- ✅ `set_parameter` - Write new value
- ✅ `get_parameter` - Verify persistence

**Expected result:** RTL_ALT changes from old value to 2500, confirmed on readback

### Advanced Navigation Tests

#### Test 3: Multi-Point Navigation

```
Arm the drone and takeoff to 20 meters
Check our current GPS position - remember this as the starting point
Fly to a position 30 meters north of current location
Face east (90 degrees) and hold for 5 seconds
Now fly to a position 30 meters east of starting point
Face south (180 degrees) and hold for 5 seconds
Return to starting GPS position
Check our speed to confirm we're holding position
Land and disarm
```

**What it tests:**
- ✅ `arm_drone` + `takeoff`
- ✅ `get_position`
- ✅ `go_to_location` - Navigate to GPS coordinates
- ✅ `set_yaw` - Face different directions (east, south)
- ✅ `hold_position` - Hold at waypoints
- ✅ `get_speed` - Verify stopped
- ✅ `land` + `disarm_drone`

**Expected result:** 
- Drone navigates to multiple GPS waypoints accurately
- Yaw control rotates drone to face different directions
- Speed at waypoints: < 1 m/s
- Returns to starting position within 2m

#### Test 4: Heading Control

```
Arm and takeoff to 15 meters
Face north (0 degrees)
Wait 5 seconds, then face east (90 degrees)
Wait 5 seconds, then face south (180 degrees)
Wait 5 seconds, then face west (270 degrees)
Get our current attitude to confirm heading
Land and disarm
```

**What it tests:**
- ✅ `set_yaw` - Rotate to specific headings
- ✅ `get_attitude` - Verify heading changes
- ✅ Cardinal direction rotation

**Expected result:**
- Drone rotates to each heading (±15° tolerance)
- get_attitude confirms each rotation
- Total test time: ~2 minutes

**Note:** Yaw changes only work when drone is in the air (altitude > 2m)

### Mission Enhancement Tests

#### Test 5: Mission Upload/Download

```
I want to upload a 3-waypoint mission. Here are the waypoints in the correct format:

Waypoint 1: {"latitude_deg": 33.6459, "longitude_deg": -117.8427, "relative_altitude_m": 15}
Waypoint 2: {"latitude_deg": 33.6460, "longitude_deg": -117.8427, "relative_altitude_m": 15}
Waypoint 3: {"latitude_deg": 33.6460, "longitude_deg": -117.8428, "relative_altitude_m": 15}

Upload this mission using the upload_mission tool (don't start it yet)
Then download the mission back using download_mission
Show me the downloaded waypoints to verify they match
```

**What it tests:**
- ✅ `upload_mission` - Upload waypoints without starting
- ✅ `download_mission` - Retrieve mission from drone
- ✅ Waypoint format validation
- ✅ Mission persistence

**Expected result:**
- Upload succeeds with 3 waypoints
- Download returns matching coordinates (±0.00001°)
- OR download reports "not supported" (some autopilots)

**Critical format requirements:**
- Must use exact field names: `latitude_deg`, `longitude_deg`, `relative_altitude_m`
- Each waypoint must be a dictionary
- Coordinates must be numbers (not strings)

---

## 🔧 Upload/Download Mission Diagnostic Test

Quick test to verify the mission upload/download fix is working.

**Prerequisites:**
1. Server must be updated to latest code (with vehicle_action fix)
2. Drone must be connected and GPS locked
3. Drone should be on the ground (don't need to fly for this test)

### Test Prompt for ChatGPT

```
I need to test mission upload and download. Please do exactly these steps:

STEP 1: Check current mission status
- Run is_mission_finished to see current state

STEP 2: Clear any existing mission
- Run clear_mission to start fresh

STEP 3: Upload a simple 2-waypoint mission (DO NOT START IT)
Use upload_mission with these exact waypoints:
[
  {"latitude_deg": 33.6459, "longitude_deg": -117.8427, "relative_altitude_m": 10},
  {"latitude_deg": 33.6460, "longitude_deg": -117.8428, "relative_altitude_m": 15}
]

STEP 4: Download the mission immediately after upload
- Run download_mission
- Verify the waypoints match what was uploaded

STEP 5: Report results
Tell me:
- Did upload_mission succeed? (status, any errors)
- Did download_mission succeed? (status, any errors)
- Do the downloaded waypoints match the uploaded waypoints?
- If there were errors, what were the EXACT error messages?

CRITICAL: 
- Use upload_mission (NOT initiate_mission) for step 3
- Download BEFORE starting any mission
- Report EXACT error messages if anything fails
```

### Expected Results

**✅ If Server is Updated (vehicle_action fix applied):**
- upload_mission: Success with 2 waypoints
- download_mission: Returns matching waypoints

**❌ If Server NOT Updated (still has vehicle_action bug):**
- upload_mission: Error about missing 'vehicle_action' argument
- download_mission: Fails or reports no mission

---

## 🆕 What's New in v1.2.3

### 🔴 CRITICAL SAFETY UPDATE
- **`pause_mission()` DEPRECATED** - Enters LOITER mode which does NOT hold altitude
- **Use `hold_mission_position()` instead** - Safe GUIDED mode hold
- See [LOITER_MODE_CRASH_REPORT.md](LOITER_MODE_CRASH_REPORT.md) for details

### ✅ Enhanced Mission Control (v1.2.2)
- `resume_mission()` - Now verifies mode transition and reports waypoint progress
- `is_mission_finished()` - Returns detailed status including waypoint progress
- `hold_mission_position()` - NEW safe alternative to pause_mission

### ✅ Flight Logging
- Automatic logging of all tool calls and MAVLink commands
- Timestamped log files in `flight_logs/`
- See [FLIGHT_LOGS.md](FLIGHT_LOGS.md)

---

## 📖 Reference & Troubleshooting

For detailed troubleshooting, firmware compatibility, and common issues, see:

**[TESTING_REFERENCE.md](TESTING_REFERENCE.md)** - Complete troubleshooting guide

Includes:
- Common issues & solutions
- Firmware compatibility matrix
- GPS coordinate calculations
- Safety notes
- Test results templates

---

## 🚨 Safety Notes

⚠️ **Before running any tests:**
- Ensure GPS lock (at least 6 satellites)
- Check battery is > 70%
- Clear area of obstacles
- Have RC transmitter ready for manual override

⚠️ **During tests:**
- Monitor altitude during navigation
- Watch battery level
- Stay within visual line of sight
- Be ready to take manual control

---

## Next Steps

**Test passed?** → System ready for production!

**Test failed?** → Review [TESTING_REFERENCE.md](TESTING_REFERENCE.md) for troubleshooting

**Want automated tests?** → Automated test suite is planned for future development
