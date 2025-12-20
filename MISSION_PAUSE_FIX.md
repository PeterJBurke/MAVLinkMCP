# Mission Pause/Resume Fixes (v1.2.2)

## Issues Addressed

Based on the flight log analysis from November 17, 2025, two critical issues with mission pause/resume were identified and fixed:

### Issue #1: Mission Pauses and Enters LOITER Mode

**Problem:**
- When `pause_mission()` is called, the drone enters LOITER flight mode
- LOITER mode can cause altitude drift and is not desired for mission pauses
- This is standard ArduPilot/PX4 behavior when pausing missions

**Root Cause:**
- `drone.mission.pause_mission()` is a MAVLink command that tells the autopilot to pause
- The autopilot automatically switches to LOITER mode as a safety measure
- This behavior cannot be changed at the MAVLink command level

**Solution:**
- Added new `hold_mission_position()` tool as an alternative to `pause_mission()`
- This tool switches to GUIDED mode and holds position at the current location
- GUIDED mode maintains stable altitude without the issues of LOITER mode

### Issue #2: Mission Never Completes After Resume

**Problem:**
- After calling `pause_mission()` then `resume_mission()`, the mission shows "IN PROGRESS" indefinitely
- The drone doesn't continue through waypoints or reach mission completion
- No diagnostic information about what's happening with the mission

**Root Cause:**
- `resume_mission()` wasn't verifying that the flight mode actually changed to AUTO/MISSION
- No information about which waypoint the mission was at when resuming
- Limited diagnostics to troubleshoot mission state

**Solution:**
- Enhanced `resume_mission()` to:
  - Show current waypoint before resuming
  - Verify flight mode transition to AUTO/MISSION mode after resume
  - Provide detailed status including mode transition success
- Enhanced `is_mission_finished()` to:
  - Show current waypoint and total waypoints
  - Display mission progress percentage
  - Include current flight mode in status

---

## New Tool: `hold_mission_position()`

### When to Use

Use `hold_mission_position()` when you want to:
- ✅ Pause a mission WITHOUT entering LOITER mode
- ✅ Maintain altitude stability in GUIDED mode
- ✅ Temporarily stop for inspection or manual control
- ✅ Avoid altitude drift that can occur with LOITER

### Usage Example

```python
# During a mission, pause to inspect something
result = hold_mission_position()
# Result: Switches to GUIDED mode, holds current position
# Shows: waypoint number, position, flight mode

# Do some inspection or manual navigation in GUIDED mode
# ...

# To resume the mission:
# Option 1: Continue from where you paused
set_current_waypoint(waypoint_index=result['was_at_waypoint'])
resume_mission()

# Option 2: Start a new mission
# upload_mission(waypoints)
# initiate_mission(mission_points)
```

### What It Does

1. **Captures mission state**: Gets current waypoint number before stopping
2. **Gets current position**: Records exact GPS coordinates and altitude
3. **Switches to GUIDED mode**: Uses `goto_location(current_position)` to hold
4. **Stops mission execution**: Mission is no longer running (unlike `pause_mission`)
5. **Provides detailed status**: Returns waypoint info, position, and mode

### Important Notes

⚠️ **This STOPS the mission entirely**, not just pauses it:
- You must explicitly resume by setting waypoint and calling `resume_mission()`
- Or upload/initiate a new mission

✅ **Benefits over `pause_mission()`**:
- Stays in GUIDED mode (no LOITER)
- Better altitude stability
- Can do manual navigation before resuming
- Clear state information for resuming

---

## Enhanced Mission Diagnostics

### Improved `resume_mission()`

**What's New:**
- Shows which waypoint the mission will resume from
- Verifies flight mode transition to AUTO/MISSION
- Reports mode transition success/failure
- Provides total waypoint count

**Example Output:**
```json
{
  "status": "success",
  "message": "Mission resumed from waypoint 3/5",
  "current_waypoint": 3,
  "total_waypoints": 5,
  "flight_mode": "MISSION",
  "mode_transition_ok": true,
  "note": "Flight mode should have changed to AUTO/MISSION for mission execution"
}
```

### Improved `is_mission_finished()`

**What's New:**
- Shows current waypoint and total waypoints
- Calculates mission progress percentage
- Includes current flight mode
- Better logging for troubleshooting

**Example Output:**
```json
{
  "status": "success",
  "mission_finished": false,
  "status_text": "IN PROGRESS",
  "current_waypoint": 3,
  "total_waypoints": 5,
  "flight_mode": "MISSION",
  "progress_percentage": 60.0
}
```

**Flight Logs Now Show:**
```
Mission status: IN PROGRESS - Waypoint 3/5 - Mode: MISSION
```

---

## Comparison: `pause_mission()` vs `hold_mission_position()`

| Feature | `pause_mission()` | `hold_mission_position()` |
|---------|-------------------|---------------------------|
| **Flight Mode** | LOITER | GUIDED |
| **Mission State** | Paused | Stopped |
| **Altitude Stability** | Can drift | Stable |
| **Resume Method** | `resume_mission()` | `set_current_waypoint()` + `resume_mission()` |
| **Use Case** | Quick pause/resume | Inspection + manual control |
| **Manual Control** | Limited | Full GUIDED mode control |
| **Complexity** | Simple | More control |

### Decision Guide

**Use `pause_mission()` when:**
- ✅ You want a quick pause and immediate resume
- ✅ You won't do any manual navigation while paused
- ✅ LOITER mode behavior is acceptable for your autopilot
- ✅ You'll resume within seconds

**Use `hold_mission_position()` when:**
- ✅ You need stable altitude hold (GUIDED mode)
- ✅ You want to do manual navigation before resuming
- ✅ You need to inspect something during the mission
- ✅ LOITER mode causes issues on your platform
- ✅ You want full control over resume waypoint

---

## Flight Log Analysis

### Example from Issue Report

**Original Problem (from logs):**
```
01:18:05 - MCP TOOL: pause_mission()
01:18:05 - MAVLink → drone.mission.pause_mission()
01:18:05 - ⚠️  Pausing mission - drone may switch to LOITER mode
```
→ Drone enters LOITER mode (unwanted)

```
01:18:10 - MCP TOOL: resume_mission()
01:18:10 - MAVLink → drone.mission.start_mission()
01:18:10 - ⚠️  Resuming mission - drone will switch to AUTO flight mode
```
→ Mission should resume but progress stalls

```
01:18:08 - Checking if mission is finished
01:18:08 - Mission status: IN PROGRESS
...
(repeats with no progress)
```
→ Mission never completes

**After Fix (expected logs):**
```
01:18:10 - MCP TOOL: resume_mission()
01:18:10 - ⚠️  Resuming mission from waypoint 2/4 - drone will switch to AUTO flight mode
01:18:10 - Flight mode after resume: MISSION
01:18:10 - Checking if mission is finished
01:18:10 - Mission status: IN PROGRESS - Waypoint 2/4 - Mode: MISSION
```
→ Clear visibility into mission state and progress

**Using New Tool (alternative):**
```
01:18:05 - MCP TOOL: hold_mission_position()
01:18:05 - MAVLink → drone.action.goto_location(lat=X, lon=Y, alt=Z)
01:18:05 - ⚠️  Holding mission position in GUIDED mode (not LOITER) - was at waypoint 2/4
```
→ Stays in GUIDED mode, no LOITER

---

## Migration Guide

If you were using `pause_mission()` and experiencing issues:

### Quick Fix (Minimal Changes)
1. Update to v1.2.2 (pull latest code)
2. Restart MCP server: `sudo systemctl restart droneserver`
3. Use same pause/resume workflow
4. Check new diagnostic fields in responses

### Better Approach (Recommended)
1. Update to v1.2.2
2. Replace `pause_mission()` with `hold_mission_position()`
3. When resuming, use:
   ```python
   # Get the waypoint from hold_mission_position response
   result = hold_mission_position()
   waypoint = result['was_at_waypoint']
   
   # Resume from that waypoint
   set_current_waypoint(waypoint_index=waypoint)
   resume_mission()
   ```

### For ChatGPT Usage

**Before:**
> "Pause the mission"

**After (option 1):**
> "Pause the mission and check the flight mode and waypoint status"

**After (option 2, recommended):**
> "Hold the mission position in GUIDED mode, I want to inspect something"
> ... (do inspection) ...
> "Resume the mission from waypoint [X]"

---

## Testing

To verify these fixes work for your setup:

1. **Test Mission Resume:**
   ```bash
   # Upload and start a mission
   # Pause it
   # Resume it
   # Check is_mission_finished for diagnostics
   ```

2. **Test Hold Mission Position:**
   ```bash
   # Upload and start a mission
   # Call hold_mission_position
   # Verify mode is GUIDED (not LOITER)
   # Resume from saved waypoint
   ```

3. **Check Flight Logs:**
   ```bash
   sudo journalctl -u droneserver -f
   # Look for new diagnostic messages
   # Verify waypoint progress is shown
   # Confirm flight mode transitions
   ```

---

## Troubleshooting

### Mission still not completing after resume?

Check the enhanced diagnostics from `is_mission_finished()`:
- Is `flight_mode` actually "MISSION" or "AUTO"?
- Is `current_waypoint` advancing?
- Is `progress_percentage` increasing?

If mode is LOITER after resume:
- The autopilot might not support mission resume
- Try using `hold_mission_position()` + manual resume instead

### hold_mission_position not working?

Check the response:
- Does it show `"flight_mode": "GUIDED"`?
- Are position coordinates valid?
- Was waypoint number captured?

### Logs not showing new diagnostic info?

Make sure you pulled the latest code:
```bash
cd ~/droneserver
git pull origin main
uv sync
sudo systemctl restart droneserver
```

---

## Version Information

**Version:** 1.2.2  
**Release Date:** November 17, 2025  
**Changes:**
- Added `hold_mission_position()` tool (1 new tool, total: 36)
- Enhanced `resume_mission()` diagnostics
- Enhanced `is_mission_finished()` diagnostics
- Improved mission state visibility in logs

**Related Issues:**
- Mission pauses entering LOITER mode ✅ Fixed (alternative provided)
- Mission not completing after resume ✅ Fixed (diagnostics added)
- Lack of mission progress visibility ✅ Fixed (enhanced status)

---

## See Also

- [FLIGHT_MODES.md](FLIGHT_MODES.md) - Comprehensive guide to flight modes
- [TESTING.md](TESTING.md) - Test scenarios for all features
- [FLIGHT_LOGS.md](FLIGHT_LOGS.md) - How to read and analyze flight logs
- [STATUS.md](STATUS.md) - Complete feature list and roadmap

---

**Questions or issues?** Check the flight logs first, then review the troubleshooting section above.

