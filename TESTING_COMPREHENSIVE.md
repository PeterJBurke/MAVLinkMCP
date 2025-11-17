# Comprehensive Test - Tower Inspection Mission

Complete system validation through a realistic inspection scenario.

## Overview

- **Duration:** 15-20 minutes
- **Tools Tested:** 35 out of 36
- **Phases:** 7
- **Total Operations:** 30
- **Complexity:** Medium
- **Best For:** Full system validation in realistic workflow

---

## Test Scenario

This test simulates a detailed cell tower inspection mission, exercising **all 36 tools** including the 10 new v1.2.0 features and the enhanced v1.2.2/v1.2.3 mission control improvements.

---

## Copy This Prompt Into ChatGPT

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
20. At waypoint 2, use hold_mission_position to pause safely (do NOT use pause_mission - it's deprecated)
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

═══════════════════════════════════════════════════════════
FINAL COMPREHENSIVE REPORT
═══════════════════════════════════════════════════════════

After completing all phases, please create a detailed report with:

**1. Mission Summary:**
   - Total phases completed: X/7
   - Total operations performed: X/30
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
   - Advanced navigation (orbit, yaw, reposition): ✅/❌ [notes]
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

---

## What This Tests

### Parameter Management (v1.2.0)
- ✅ `list_parameters` - List RTL parameters
- ✅ `get_parameter` - Check RTL_ALT and battery capacity
- ✅ `set_parameter` - Modify RTL altitude if needed

### Advanced Navigation (v1.2.0)
- ✅ `orbit_location` - Circle tower at 25m radius (clockwise then counter-clockwise)
- ✅ `set_yaw` - Face east (90°), then north (0°)
- ✅ `reposition` - Move to inspection position and hold

### Mission Enhancements (v1.2.0)
- ✅ `upload_mission` - Upload 4-waypoint mission without starting
- ✅ `download_mission` - Verify upload and save at end
- ✅ `set_current_waypoint` - (implicitly tested with pause/resume)
- ✅ `is_mission_finished` - Check completion status with detailed info (v1.2.2)

### Mission Control Improvements (v1.2.2/v1.2.3)
- ✅ `hold_mission_position` - NEW safe alternative to pause_mission (GUIDED mode)
- ✅ `resume_mission` - Enhanced with waypoint tracking and mode verification
- ⛔ `pause_mission` - DEPRECATED (unsafe LOITER mode - do not use)

### Existing Features
- Flight control (arm, takeoff, land, disarm, hold)
- Safety (return_to_launch, battery monitoring)
- Navigation (go_to_location, get_position)
- Mission management (initiate, progress monitoring)
- Telemetry (health, speed, attitude, battery)

---

## Expected Results

### Success Indicators

✅ ChatGPT sequences actions logically  
✅ All 36 tools (except deprecated pause_mission) are called appropriately  
✅ Parameters read/write correctly  
✅ Orbit executes smoothly (or provides firmware workaround)  
✅ Yaw control shows cardinal directions  
✅ Mission upload/download cycle completes  
✅ **hold_mission_position works in GUIDED mode (no altitude drift)**  
✅ resume_mission provides detailed waypoint status  
✅ Battery monitoring triggers warnings  
✅ Mission status checks work correctly  

### Success Criteria

- ✅ At least 6/7 phases complete successfully
- ✅ At least 25/30 operations complete successfully
- ✅ All v1.2.0 features tested (parameters, navigation, missions)
- ✅ All v1.2.2/v1.2.3 features tested (hold_mission_position, enhanced resume)
- ✅ No safety violations
- ✅ Drone returns home safely

---

## Common Issues & Solutions

**GPS coordinates drift:**
- Adjust lat/lon for your test location
- Use relative coordinates from current position

**Orbit not working:**
- Normal for older firmware (ArduPilot < 4.0, PX4 < 1.13)
- Error message provides waypoint-based workaround

**Mission upload format errors:**
- Ensure exact field names: `latitude_deg`, `longitude_deg`, `relative_altitude_m`
- Each waypoint must be a dictionary with all three fields

**Mission download fails:**
- Some autopilots don't support download
- Keep local copy of uploaded missions

**Battery showing 0%:**
- Likely uncalibrated sensor or simulator limitation
- Check voltage instead (should be > 10V)
- Set `BATT_CAPACITY` parameter

For more solutions, see [TESTING_REFERENCE.md](TESTING_REFERENCE.md)

---

## Performance Benchmarks

Expected execution times:
- Phase 1 (Pre-flight): ~2 minutes
- Phase 2 (Mission Prep): ~2 minutes
- Phase 3 (Flight Ops): ~5 minutes
- Phase 4 (Inspection): ~2 minutes
- Phase 5 (Mission Exec): ~4 minutes
- Phase 6 (Return): ~2 minutes
- Phase 7 (Post-flight): ~1 minute

**Total:** 15-20 minutes

---

## Next Steps

**Test passed?** → System ready for production!

**Test failed?** → Review [TESTING_REFERENCE.md](TESTING_REFERENCE.md) for troubleshooting

**Want deeper verification?** → Run [TESTING_GRANULAR.md](TESTING_GRANULAR.md) for ACK/NACK validation

**Need to test specific features?** → See [TESTING_INDIVIDUAL.md](TESTING_INDIVIDUAL.md)

---

[← Back to Testing Guide](TESTING_GUIDE.md)

