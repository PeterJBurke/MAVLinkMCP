# Individual Feature Tests

Isolated tests for specific features - perfect for debugging or learning.

## Overview

- **Duration:** 2-3 minutes per test
- **Complexity:** Low-Medium
- **Best For:** Testing specific features, debugging issues, or learning individual tools

Each test is self-contained and can be run independently.

---

## 🧪 Parameter Management Tests

### Test 1: Read Parameters

```
Show me all parameters that start with "RTL"
Then show me all parameters that start with "BATT"
What is the current RTL_ALT value?
```

**What it tests:**
- ✅ `list_parameters` with filter
- ✅ `get_parameter` for specific values

**Expected result:** List of parameters displayed, RTL_ALT value shown (typically 1500-3000 cm)

---

### Test 2: Modify Parameters

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

---

### Test 3: Parameter Discovery

```
List all parameters (show me the count first)
Then show me just the GPS-related parameters
Show me the first 10 parameters alphabetically
```

**What it tests:**
- ✅ `list_parameters` without filter (full list)
- ✅ `list_parameters` with "GPS" filter
- ✅ Parameter listing and filtering

**Expected result:** 
- Total parameters: 300-700 (depending on autopilot)
- GPS parameters: 5-15 found
- First 10 shown alphabetically

**Note:** Full parameter list can be very long (5-10 seconds to retrieve)

---

## 🚁 Advanced Navigation Tests

### Test 4: Orbit Mode

```
Arm the drone and takeoff to 20 meters
Check our GPS position
Orbit around our current location at 30 meter radius, 3 m/s, clockwise
After 30 seconds, stop and hold position
Check our speed to confirm we stopped
Land and disarm
```

**What it tests:**
- ✅ `arm_drone` + `takeoff_drone`
- ✅ `get_position`
- ✅ `orbit_location` - Circular flight pattern
- ✅ `hold_position` - Stop orbit
- ✅ `get_speed` - Verify stopped
- ✅ `land_drone` + `disarm_drone`

**Expected result:** 
- Drone orbits at 30m radius (or firmware reports not supported with workaround)
- Speed during orbit: ~3 m/s
- Speed after hold: < 1 m/s

**Troubleshooting:**
- "Command not supported" → Normal for ArduPilot < 4.0 or PX4 < 1.13
- Error message should provide waypoint-based alternative

---

### Test 5: Heading Control

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

---

### Test 6: Reposition

```
Arm and takeoff to 10 meters
Check current position
Reposition to 50 meters north of current position at 20m altitude
Check new position to confirm
Hold there for 30 seconds
Return to launch and land
Disarm
```

**What it tests:**
- ✅ `reposition` - Move to new GPS position and altitude
- ✅ `get_position` - Verify movement
- ✅ `hold_position` - Maintain new position
- ✅ `return_to_launch` - RTL
- ✅ Position and altitude changes

**Expected result:**
- Drone moves ~50m north
- Altitude changes to 20m
- Position held for 30 seconds
- Returns home successfully

**Calculation helper:**
- 0.0001° latitude ≈ 11 meters
- 0.00045° latitude ≈ 50 meters north
- Use: `new_lat = current_lat + 0.00045`

---

## 📋 Mission Enhancement Tests

### Test 7: Mission Upload/Download

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

**Troubleshooting:**
- "Missing required fields" → Check field names exactly match
- "Invalid argument" → Check coordinates are valid ranges
- Download fails → Some autopilots don't support download, keep local copy

---

### Test 8: Mission Control

```
Assuming a mission is uploaded:
1. Check if the mission is finished (should be false - not started)
2. Arm, takeoff to 10m, and start the mission
3. Wait until waypoint 1 is reached
4. Use hold_mission_position to pause safely (do NOT use pause_mission - deprecated!)
5. Check if mission is finished (should be false - paused)
6. Jump to waypoint 3 using set_current_waypoint
7. Resume the mission
8. Monitor until mission is finished
9. Return to launch and disarm
```

**What it tests:**
- ✅ `is_mission_finished` - Check mission status (enhanced v1.2.2)
- ✅ `initiate_mission` - Start mission
- ✅ `print_mission_progress` - Monitor waypoint progress
- ✅ `hold_mission_position` - Safe pause in GUIDED mode (v1.2.2)
- ✅ `set_current_waypoint` - Jump to specific waypoint
- ✅ `resume_mission` - Continue mission (enhanced v1.2.2)
- ✅ Complete mission flow

**Expected result:**
- is_mission_finished returns false before start
- Mission starts and drone flies to waypoint 1
- hold_mission_position enters GUIDED mode (NOT LOITER)
- Altitude maintained during pause (±1m)
- Jump to waypoint 3 succeeds
- resume_mission reports waypoint info and mode transition
- is_mission_finished returns true with progress details
- Mission completes successfully

**v1.2.2/v1.2.3 Features:**
- ✅ `hold_mission_position` maintains altitude in GUIDED mode
- ✅ `resume_mission` returns waypoint tracking and mode verification
- ✅ `is_mission_finished` provides detailed status (waypoints, progress %, flight mode)
- ⛔ `pause_mission` DEPRECATED (unsafe LOITER mode)

**Troubleshooting:**
- Altitude drops during pause → Make sure you used `hold_mission_position`, NOT `pause_mission`
- Mission won't resume → Check flight mode is AUTO/MISSION after resume
- See [MISSION_PAUSE_FIX.md](MISSION_PAUSE_FIX.md) for detailed migration guide
- See [LOITER_MODE_CRASH_REPORT.md](LOITER_MODE_CRASH_REPORT.md) for why pause_mission is unsafe

---

## 📊 Validation Checklist

After running individual tests, verify:

### Parameter Management
- [ ] `list_parameters` can filter by prefix
- [ ] `get_parameter` reads individual parameters
- [ ] `set_parameter` writes and confirms changes
- [ ] Invalid parameters return helpful error messages

### Advanced Navigation
- [ ] `set_yaw` rotates to specified heading
- [ ] `reposition` moves and holds new GPS location
- [ ] `orbit_location` works OR provides firmware workaround
- [ ] `get_attitude` confirms heading changes
- [ ] `get_speed` tracks movement

### Mission Management
- [ ] `upload_mission` accepts correct format
- [ ] `download_mission` retrieves waypoints (or reports unsupported)
- [ ] `initiate_mission` starts mission execution
- [ ] `print_mission_progress` shows current waypoint
- [ ] `hold_mission_position` holds in GUIDED mode (v1.2.2)
- [ ] `resume_mission` continues from pause with verification (v1.2.2)
- [ ] `set_current_waypoint` jumps to specific waypoint
- [ ] `is_mission_finished` correctly reports completion with details (v1.2.2)
- [ ] `clear_mission` removes mission from drone

---

## 🎯 Success Criteria

### Test 1-3 (Parameters)
- ✅ At least 2/3 tests pass
- ✅ Can read and list parameters
- ✅ Can write at least one parameter

### Test 4-6 (Navigation)
- ✅ At least 2/3 tests pass
- ✅ Yaw control works
- ✅ Reposition works
- ⚠️ Orbit may fail (firmware limitation) - informative error is OK

### Test 7-8 (Missions)
- ✅ Both tests pass
- ✅ Mission upload/download cycle works
- ✅ Mission control flow works
- ✅ hold_mission_position works in GUIDED mode

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

### Modify Test Parameters
Common adjustments:
- **Altitude:** Lower for indoor/small areas (5-10m), higher for outdoor (15-30m)
- **Orbit radius:** Smaller for confined spaces (10-15m), larger for open areas (30-50m)
- **Speed:** Slower for testing (1-2 m/s), normal for operations (3-5 m/s)

---

## 🚨 Safety Notes

⚠️ **Before running individual tests:**
- Ensure GPS lock (at least 6 satellites)
- Check battery is > 70%
- Clear area of obstacles
- Have RC transmitter ready for manual override

⚠️ **During tests:**
- Monitor altitude (especially during orbit)
- Watch battery level
- Stay within visual line of sight
- Be ready to take manual control

---

## Next Steps

**Tests passed?** → Try [TESTING_COMPREHENSIVE.md](TESTING_COMPREHENSIVE.md) for full workflow

**Tests failed?** → See [TESTING_REFERENCE.md](TESTING_REFERENCE.md) for troubleshooting

**Need detailed verification?** → Run [TESTING_GRANULAR.md](TESTING_GRANULAR.md)

**Need quick check?** → Try [TESTING_QUICK.md](TESTING_QUICK.md)

---

[← Back to Testing Guide](TESTING_GUIDE.md)

