# Testing Reference Guide

Troubleshooting, firmware compatibility, safety notes, and templates.

---

## 🔧 Common Issues & Solutions

### GPS Coordinates Drift

**Problem:** Waypoints end up in wrong locations

**Solutions:**
- Use relative coordinates from current position
- Adjust lat/lon for your test location
- Remember: ~0.0001° latitude ≈ 11 meters, longitude varies by latitude

**Example:**
```python
# Get current position first
current_lat = 33.6459
current_lon = -117.8427

# Calculate 30m north:
new_lat = current_lat + 0.00027

# Calculate 30m east (at 33° latitude):
new_lon = current_lon + 0.00033
```

---

### Navigation Accuracy Issues

**Problem:** Drone doesn't reach exact GPS coordinates (off by 2-5 meters)

**Root Cause:** GPS accuracy limitations and satellite geometry

**Solutions:**
1. **Check GPS lock:** Ensure 6+ satellites with 3D fix
2. **Check HDOP:** Horizontal dilution of precision < 2.0 is good
3. **Use relative navigation:** For precise movements < 50m, use `move_to_relative` with NED coordinates

**Best Practice:**
For inspection tasks requiring < 1m accuracy:
- Get current position as reference
- Use `move_to_relative(north_m, east_m, down_m)` for precise offsets
- This bypasses GPS error accumulation

**Note:** GPS accuracy is normal behavior, not a bug. Use relative movements for precision tasks.

---

### Parameter Names Not Found

**Problem:** `get_parameter RTL_ALT` returns "Parameter not found"

**Root Cause:** Parameter names vary between ArduPilot and PX4

**Solutions:**
1. List all parameters first: `list_parameters` (no filter)
2. Use filter to find similar: `list_parameters filter_prefix="RTL"`
3. Check autopilot-specific names below

**Common Parameter Names:**

| Feature | ArduPilot | PX4 |
|---------|-----------|-----|
| RTL Altitude | `RTL_ALT` | `RTL_RETURN_ALT` |
| Battery Capacity | `BATT_CAPACITY` | `BAT_CAPACITY` |
| Waypoint Speed | `WP_SPEED` | `MIS_SPEED` |
| Takeoff Altitude | `PILOT_TKOFF_ALT` | `MIS_TAKEOFF_ALT` |
| Loiter Radius | `WP_LOITER_RAD` | `NAV_LOITER_RAD` |

**v1.2.0 Improvement:** `get_parameter` now suggests similar parameter names if not found

---

### Mission Upload Format Errors - "Missing required fields"

**Problem:** `upload_mission` returns format validation error

**Root Cause:** Each waypoint must be a dictionary with exact field names

**Correct Format:**
```python
waypoints = [
  {"latitude_deg": 33.6459, "longitude_deg": -117.8427, "relative_altitude_m": 15},
  {"latitude_deg": 33.6460, "longitude_deg": -117.8427, "relative_altitude_m": 20}
]
```

**Common Mistakes:**
- ❌ Using `lat`/`lon` instead of `latitude_deg`/`longitude_deg`
- ❌ Using `altitude` or `alt` instead of `relative_altitude_m`
- ❌ Passing string coordinates: `"33.6459"` instead of `33.6459`
- ❌ Missing any of the three required fields
- ❌ Using absolute altitude instead of relative

**v1.2.1 Improvement:** Error messages now show exactly what was received vs expected

**Validation Rules:**
- Latitude: -90 to 90 degrees
- Longitude: -180 to 180 degrees
- Altitude: >= 0 meters (relative to home)

---

### Mission Download Returns Empty or Fails

**Problem:** `download_mission` returns error or empty list

**Root Cause:** Some autopilots don't support mission download or require specific firmware

**Solutions:**
1. **Keep local copy:** Save waypoints when uploading
2. **Check firmware version:** ArduPilot 4.0+, PX4 1.12+
3. **Alternative:** Use `print_mission_progress` to see current waypoint

**Workaround:**
```
# When uploading, save a copy:
uploaded_mission = [waypoint1, waypoint2, waypoint3]

# Later, reference your saved copy instead of downloading
```

**Note:** If `download_mission` reports "not supported" with helpful message, this is expected - mark as successful in tests

---

### Battery Showing 0% Throughout Flight

**Problem:** `get_battery` reports 0% remaining with ~12V voltage

**Root Cause:** Battery monitoring not calibrated or not supported by simulator

**Solutions:**
1. **Set capacity:** `set_parameter BATT_CAPACITY 3300` (your battery's mAh)
2. **Use voltage:** Check `voltage_v` field (12.0-12.6V = healthy for 3S LiPo)
3. **Check estimated:** v1.2.1 provides `estimated_percent` based on voltage curve

**v1.2.1 Improvement:** 
- Server detects uncalibrated battery (0% with good voltage)
- Provides voltage-based estimates using LiPo discharge curves
- Suggests setting `BATT_CAPACITY` parameter

**Voltage Reference (3S LiPo):**
- 12.6V = 100% (fully charged)
- 11.7V = 50% (mid-flight)
- 10.8V = 20% (return home)
- 10.2V = 0% (land immediately)

**Simulator Note:** SITL often doesn't simulate battery drain accurately - expect static readings

---

### Mission Pauses and Altitude Drops - CRITICAL SAFETY ISSUE

**Problem:** Using `pause_mission()` causes drone to descend and crash

**Root Cause:** `pause_mission()` enters LOITER mode, which requires RC throttle input (50% for altitude hold). Without RC input, altitude is NOT maintained and drone descends.

**🔴 CRITICAL: `pause_mission()` IS DEPRECATED AS OF v1.2.3**

**Solutions:**
1. **✅ USE `hold_mission_position()` INSTEAD** - Safe alternative that uses GUIDED mode
2. **Never use `pause_mission()`** - It will return an error directing you to the safe alternative
3. **See crash report:** [LOITER_MODE_CRASH_REPORT.md](LOITER_MODE_CRASH_REPORT.md)
4. **See migration guide:** [MISSION_PAUSE_FIX.md](MISSION_PAUSE_FIX.md)

**Why `hold_mission_position()` is Safe:**
- Uses GUIDED mode (NOT LOITER)
- Holds current position without RC input required
- Maintains altitude autonomously
- Easy resume with `resume_mission()`

**Enhanced Mission Control (v1.2.2):**
- `resume_mission()` now verifies mode transition and reports waypoint progress
- `is_mission_finished()` provides detailed status (waypoints, progress %, flight mode)
- `hold_mission_position()` logs position and waypoint before holding

---

### Mission Never Completes After Pause/Resume

**Problem:** `is_mission_finished()` never returns true after resuming mission

**Root Cause (pre-v1.2.2):** Insufficient diagnostic information to debug

**Solutions:**
1. **Upgrade to v1.2.2+** - Enhanced mission status reporting
2. **Check flight mode** - Should be AUTO/MISSION after resume
3. **Verify waypoint progress** - `is_mission_finished()` now shows current/total waypoints
4. **Use `resume_mission()` enhancements** - Confirms mode transition

**v1.2.2 Improvements:**
```
resume_mission() now returns:
- current_waypoint: X
- total_waypoints: Y
- flight_mode: "AUTO" or "MISSION"
- mode_transition_ok: true/false

is_mission_finished() now returns:
- finished: true/false
- current_waypoint: X
- total_waypoints: Y
- progress_percentage: X%
- flight_mode: current mode
```

**Debugging Steps:**
1. After `resume_mission()`, check `mode_transition_ok = true`
2. Use `is_mission_finished()` to see `current_waypoint` vs `total_waypoints`
3. If stuck at last waypoint, check if drone reached the position
4. Use `print_mission_progress` for additional diagnostics

---

## 🔥 Firmware Compatibility Matrix

Different autopilots support different features. Here's what's required:

| Feature | ArduPilot | PX4 | Notes |
|---------|-----------|-----|-------|
| **Basic Flight** | ✅ All versions | ✅ All versions | Core features work everywhere |
| **Parameter Management** | ✅ All versions | ✅ All versions | Universal support |
| **Set Yaw** | ✅ All versions | ✅ All versions | Universal support |
| **Reposition** | ✅ All versions | ✅ All versions | Universal support |
| **Upload Mission** | ✅ All versions | ✅ All versions | Format may vary slightly |
| **Download Mission** | ✅ 4.0+ | ✅ 1.12+ | Older versions may not support |
| **Set Current Waypoint** | ✅ All versions | ✅ All versions | Universal support |
| **Battery Monitoring** | ⚠️ Needs calibration | ⚠️ Needs calibration | Set `BATT_CAPACITY` parameter |
| **hold_mission_position** | ✅ All versions | ✅ All versions | v1.2.2+ feature, uses GUIDED mode |

**Recommended Minimum Versions for Full v1.2.3 Features:**
- **ArduPilot Copter:** 4.0.0 or newer
- **PX4:** 1.13.0 or newer
- **SITL:** Latest stable (some features may not work in simulation)

**Version Check:**
```
# Check your autopilot version:
1. Connect to drone
2. Run get_health or check boot messages
3. Look for version string like "ArduCopter V4.7.0" or "PX4 v1.13.2"
```

---

## 🚨 Safety Notes

### ⚠️ ALWAYS:

**Pre-Flight:**
- Test in open area away from people/buildings
- Check GPS lock (at least 6 satellites, 3D fix)
- Verify battery > 70% before starting
- Run `get_health` to check all systems
- Have RC transmitter powered on and ready

**During Flight:**
- Maintain visual line of sight
- Have RC transmitter ready for manual override
- Monitor battery levels closely
- Monitor altitude (especially during missions)
- Start with low altitudes (5-10m) for initial tests

**Post-Flight:**
- Wait for drone to fully land (altitude < 0.5m)
- Verify speed = 0 before disarming
- Check flight logs for errors

### ⚠️ NEVER:

**Don't:**
- Test near airports or restricted airspace
- Fly in bad weather (wind > 15 mph, rain, fog)
- Test with low battery (< 30%)
- Leave drone unattended during autonomous flight
- Ignore safety warnings from the system
- Disarm while in the air (altitude > 0.5m)
- **Use `pause_mission()` - IT'S DEPRECATED AND UNSAFE**
- Fly without RC transmitter ready for manual override

### 🔴 CRITICAL SAFETY - Mission Pausing

**DO:**
- ✅ Use `hold_mission_position()` - Safe GUIDED mode hold
- ✅ Verify altitude maintained during pause
- ✅ Check flight mode after pause (should be GUIDED)
- ✅ Use `resume_mission()` with enhanced verification

**DON'T:**
- ⛔ Use `pause_mission()` - Enters unsafe LOITER mode
- ⛔ Assume LOITER holds altitude without RC input
- ⛔ Ignore altitude drift during mission pauses

**Why:** LOITER mode in ArduPilot requires active RC throttle input (50% = altitude hold). Without RC input, the drone will descend and crash. See [LOITER_MODE_CRASH_REPORT.md](LOITER_MODE_CRASH_REPORT.md) for detailed analysis.

---

## 📋 Test Results Template

Use this template to record your test results:

```markdown
## Test Session - [Date]

**Environment:**
- Drone: [Model/SITL]
- Autopilot: [ArduPilot/PX4 version X.X.X]
- MCP Version: v1.2.3
- Location: [Indoor/Outdoor/SITL]
- Weather: [Clear/Windy/Overcast]
- Temperature: [XX°C / XX°F]

**Tests Completed:**
- [ ] Quick Test (5 min)
- [ ] Comprehensive Test (20 min)
- [ ] Granular Test (45 min)
- [ ] Individual Feature Tests

**Results:**
- Parameters tested: ✅ Pass / ❌ Fail [details]
- Navigation accuracy: ✅ Pass / ⚠️ GPS drift / ❌ Fail [details]
- Set yaw: ✅ Pass / ❌ Fail [details]
- Reposition: ✅ Pass / ❌ Fail [details]
- Upload mission: ✅ Pass / ❌ Fail [details]
- Download mission: ✅ Pass / ⚠️ Not supported / ❌ Fail [details]
- Set waypoint: ✅ Pass / ❌ Fail [details]
- hold_mission_position: ✅ Pass / ❌ Fail [details]
- resume_mission (enhanced): ✅ Pass / ❌ Fail [details]
- is_mission_finished (enhanced): ✅ Pass / ❌ Fail [details]

**Flight Statistics:**
- Total flight time: [X minutes]
- Max altitude: [X meters]
- Total distance: [X meters]
- Battery consumed: [X%]
- Number of waypoints flown: [X]

**Issues Encountered:**
1. [Issue description]
   - Expected: [what should happen]
   - Actual: [what did happen]
   - Workaround: [how resolved, if applicable]

**Safety Events:**
- Battery warnings: [Yes/No - at what %?]
- GPS loss: [Yes/No]
- Manual override needed: [Yes/No - why?]
- Emergency landing: [Yes/No - why?]

**Notes:**
[Additional observations, recommendations, or concerns]

**Overall Assessment:**
- System stability: Excellent / Good / Fair / Poor
- Production ready: Yes / No
- Recommendation: [next steps]
```

---

## 🛠️ Customizing Tests

### Adjust GPS Coordinates

Replace base coordinates with your location:

```python
# Example coordinates (Irvine, CA)
BASE_LAT = 33.6459
BASE_LON = -117.8427

# Your location:
BASE_LAT = XX.XXXX
BASE_LON = -XXX.XXXX
```

### GPS Offset Calculations

Use these approximations:

```python
# Latitude (consistent globally):
# 0.00001° ≈ 1.1 meters
# 0.0001° ≈ 11 meters
# 0.001° ≈ 111 meters

# Longitude (varies by latitude):
# At equator (0°): 0.0001° ≈ 11 meters
# At 30° latitude: 0.0001° ≈ 9.5 meters
# At 45° latitude: 0.0001° ≈ 7.8 meters
# At 60° latitude: 0.0001° ≈ 5.5 meters

# Example: 30m north at any latitude
new_lat = current_lat + 0.00027

# Example: 30m east at 33° latitude
new_lon = current_lon + 0.00033
```

### Modify Mission Patterns

Common mission patterns:

**Square:**
```python
waypoints = [
  {"latitude_deg": lat, "longitude_deg": lon, "relative_altitude_m": 15},
  {"latitude_deg": lat + 0.0001, "longitude_deg": lon, "relative_altitude_m": 15},
  {"latitude_deg": lat + 0.0001, "longitude_deg": lon + 0.0001, "relative_altitude_m": 15},
  {"latitude_deg": lat, "longitude_deg": lon + 0.0001, "relative_altitude_m": 15}
]
```

**Vertical Stack (same position, different altitudes):**
```python
waypoints = [
  {"latitude_deg": lat, "longitude_deg": lon, "relative_altitude_m": 10},
  {"latitude_deg": lat, "longitude_deg": lon, "relative_altitude_m": 20},
  {"latitude_deg": lat, "longitude_deg": lon, "relative_altitude_m": 30}
]
```

**Grid Survey (parallel lines):**
```python
# 3 parallel lines, 20m apart
waypoints = [
  # Line 1
  {"latitude_deg": lat, "longitude_deg": lon, "relative_altitude_m": 15},
  {"latitude_deg": lat + 0.0009, "longitude_deg": lon, "relative_altitude_m": 15},
  # Line 2
  {"latitude_deg": lat + 0.0009, "longitude_deg": lon + 0.00022, "relative_altitude_m": 15},
  {"latitude_deg": lat, "longitude_deg": lon + 0.00022, "relative_altitude_m": 15},
  # Line 3
  {"latitude_deg": lat, "longitude_deg": lon + 0.00044, "relative_altitude_m": 15},
  {"latitude_deg": lat + 0.0009, "longitude_deg": lon + 0.00044, "relative_altitude_m": 15}
]
```

---

## ⏱️ Performance Benchmarks

Expected execution times:

| Test Type | Duration | Complexity | Best For |
|-----------|----------|------------|----------|
| **Quick Test** | 5 minutes | Low | First-time setup verification |
| **Comprehensive Test** | 15-20 minutes | Medium | Full system validation |
| **Granular Test** | 30-45 minutes | High | Production readiness |
| **Individual Tests** | 2-3 min each | Low-Med | Debugging specific features |

**Factors affecting duration:**
- GPS lock time (0-60 seconds)
- Battery level checks (5-10 seconds each)
- Takeoff/landing (10-30 seconds)
- Waypoint travel time (depends on distance)
- Mission execution (depends on waypoint count and distance)

---

## 📚 Additional Resources

### Documentation
- [README.md](README.md) - Main documentation and feature overview
- [STATUS.md](STATUS.md) - Complete tool list and version history
- [CHATGPT_SETUP.md](CHATGPT_SETUP.md) - ChatGPT integration setup
- [FLIGHT_LOGS.md](FLIGHT_LOGS.md) - Flight logging system documentation
- [MISSION_PAUSE_FIX.md](MISSION_PAUSE_FIX.md) - Mission control improvements (v1.2.2)
- [LOITER_MODE_CRASH_REPORT.md](LOITER_MODE_CRASH_REPORT.md) - Critical safety analysis (v1.2.3)

### Testing Guides
- [TESTING.md](TESTING.md) - Main testing guide (consolidated manual testing procedures)
- [TESTING_FIXES.md](TESTING_FIXES.md) - Detailed fixes from real-world testing

### Examples
- [examples/](examples/) - Example Python scripts
- [examples/README.md](examples/README.md) - Example usage guide

### External Resources
- [ArduPilot Documentation](https://ardupilot.org/copter/)
- [PX4 Documentation](https://docs.px4.io/)
- [MAVSDK Documentation](https://mavsdk.mavlink.io/)
- [MAVLink Protocol](https://mavlink.io/)

---

## 🤝 Contributing Test Results

Found an issue or have suggestions?

**How to report:**
1. Open an issue at: https://github.com/PeterJBurke/droneserver/issues
2. Include your test results (use template above)
3. Provide MCP server logs: `sudo journalctl -u droneserver --since "1 hour ago"`
4. Describe your setup:
   - Drone model
   - Autopilot type and version
   - Location (SITL/real drone)
   - Test that failed

**What to include in logs:**
```bash
# Get MCP server logs (last 100 lines):
sudo journalctl -u droneserver -n 100

# Get flight logs (if available):
cat ~/droneserver/flight_logs/flight_*.log

# Get autopilot logs (SITL):
# Look in terminal where you started SITL
```

**Quality reports include:**
- Specific test that failed
- Expected vs actual behavior
- Steps to reproduce
- Firmware version
- Any error messages

---

## 🔍 Advanced Testing

### Testing with Real Hardware

**Additional considerations:**
- Pre-flight mechanical check (props, motors, frame)
- Compass calibration before first flight
- RC transmitter failsafe configured
- Emergency landing zone identified
- Spotter for visual tracking

**Hardware-specific issues:**
- Vibration affecting sensors
- Compass interference from electronics
- GPS multipath in urban environments
- Wind affecting position hold
- Battery voltage sag under load

### Testing in Simulator (SITL)

**Limitations:**
- Battery monitoring often inaccurate
- No physical constraints (can "fly through" ground)
- Perfect GPS (no multipath or interference)
- No wind simulation (unless configured)
- GPS coordinates may need adjustment for your location

**Advantages:**
- Safe to crash
- Instant reset
- Perfect conditions
- Unlimited battery
- Fast testing iteration

### Performance Testing

**Metrics to track:**
- Command latency (time from call to execution)
- Position accuracy (actual vs target position)
- Altitude hold accuracy (±X meters)
- Heading accuracy (±X degrees)
- Battery consumption rate
- GPS drift over time

**Stress Tests:**
- Rapid command sequences
- Long missions (50+ waypoints)
- Emergency procedures (low battery, GPS loss)
- Multiple pause/resume cycles
- Extended hold positions (5+ minutes)

---

## 📋 v1.2.1 Testing Improvements

Based on comprehensive real-world testing, v1.2.1 includes:

1. **✅ Better Mission Upload Validation**
   - Clear error messages showing exactly what's wrong with waypoint format
   - Type checking for each waypoint
   - Coordinate validation (lat/lon ranges, altitude >= 0)
   - Helpful examples in error responses

2. **✅ GPS Navigation Improvements**
   - Clear error messages for navigation failures
   - Provides relative movement alternatives for precision tasks
   - Shows firmware requirements in error message

3. **✅ Battery Monitoring Fallback**
   - Detects when percentage is uncalibrated (0% with good voltage)
   - Provides voltage-based estimates using LiPo curves
   - Suggests setting `BATT_CAPACITY` parameter

4. **✅ Firmware Compatibility Matrix**
   - Clear documentation of which features need which firmware versions
   - Workarounds provided for unsupported features

## 📋 v1.2.2/v1.2.3 Mission Control Improvements

**v1.2.2 Enhancements:**
1. **✅ hold_mission_position Tool**
   - Safe alternative to pause_mission
   - Uses GUIDED mode (not LOITER)
   - Maintains altitude without RC input
   
2. **✅ Enhanced resume_mission**
   - Returns waypoint tracking info
   - Verifies mode transition to AUTO/MISSION
   - Reports mode_transition_ok status

3. **✅ Enhanced is_mission_finished**
   - Shows current_waypoint / total_waypoints
   - Reports progress_percentage
   - Displays current flight_mode

**v1.2.3 Critical Safety Fix:**
1. **🔴 pause_mission DEPRECATED**
   - Immediately returns error with detailed explanation
   - Directs users to hold_mission_position
   - Explains LOITER mode risks
   - References crash report for technical details

See [MISSION_PAUSE_FIX.md](MISSION_PAUSE_FIX.md) for migration guide and [LOITER_MODE_CRASH_REPORT.md](LOITER_MODE_CRASH_REPORT.md) for crash analysis.

---

[← Back to Testing Guide](TESTING.md)

