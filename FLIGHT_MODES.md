# Flight Modes in MAVLink MCP

## Understanding ArduPilot Flight Modes

MAVLink MCP tools interact with ArduPilot, which automatically switches flight modes based on commands. Understanding these modes is crucial for controlling your drone effectively.

---

## 🎯 Flight Modes Used by MCP Tools

### GUIDED Mode (Primary)
**Used by:** Most movement and navigation tools
- `arm_drone`
- `takeoff`
- `go_to_location`
- `move_to_relative`  
- `hold_position` ✅ **FIXED - now stays in GUIDED**
- `set_yaw`
- `reposition`

**Behavior:**
- Drone accepts real-time position/velocity commands
- Maintains commanded altitude actively
- Best for dynamic, AI-controlled flight
- **Altitude stays stable** - no unexpected descent

**Log Example:**
```
🔧 MCP TOOL: go_to_location(latitude_deg=33.6459, longitude_deg=-117.8427, absolute_altitude_m=50)
📡 MAVLink → drone.action.goto_location(lat=33.6459, lon=-117.8427, alt=50)
```

---

### AUTO Mode (Mission Execution)
**Used by:** Mission execution tools
- `initiate_mission` ⚠️
- `resume_mission` ⚠️
- Mission waypoint navigation

**Behavior:**
- Drone follows pre-programmed waypoint mission
- No real-time control - mission must complete or be paused
- Automatically activated when mission starts
- Returns to GUIDED after mission completes (if configured)

**Automatic Mode Change:**
```
🔧 MCP TOOL: initiate_mission(...)
⚠️  Mission starting - drone will switch to AUTO flight mode
📡 MAVLink → drone.mission.start_mission()
✓ Flight mode automatically changed to AUTO for mission execution
```

---

### LOITER Mode (Problematic for Our Use Case)
**Previously used by:** `hold_position` (FIXED ✅)
- Old `drone.action.hold()` triggered LOITER

**Behavior:**
- Holds position at GPS coordinates
- **Problem:** Uses altitude from mode entry, which may differ from current altitude
- **Result:** Drone often descends when entering LOITER
- **Solution:** We now use `goto_location(current_position)` to stay in GUIDED

**Why It Caused Descent:**
```
Before Fix:
  Drone at 50m in GUIDED → hold_position() → LOITER mode entered
  LOITER thinks target altitude is 45m (from earlier command)
  → Drone descends to 45m ❌

After Fix:
  Drone at 50m in GUIDED → hold_position() → goto_location(current_pos)
  Stays in GUIDED, maintains 50m altitude ✅
```

**Log showing LOITER warning (for pause_mission):**
```
🔧 MCP TOOL: pause_mission()
📡 MAVLink → drone.mission.pause_mission()
⚠️  Pausing mission - drone may switch to LOITER mode
✓ Flight mode may have changed to LOITER (hold position)
```

---

### LAND Mode
**Used by:** Landing tool
- `land` 

**Behavior:**
- Autonomous descent to ground
- Disarms automatically when landed
- Cannot be interrupted (by design for safety)

---

### RTL Mode (Return to Launch)
**Used by:** Emergency return
- `return_to_launch`

**Behavior:**
- Flies to launch position
- Climbs to RTL altitude (parameter: `RTL_ALT`)
- Lands at launch point (if configured)

---

## 🔍 Monitoring Flight Modes

### Check Current Mode
```python
get_flight_mode()
# Returns: {"flight_mode": "GUIDED"}
```

### In Logs
When running `sudo journalctl -u droneserver -f`, you'll see:
```
2025-11-17 10:30:00 - droneserver - INFO - 🔧 MCP TOOL: initiate_mission(...)
2025-11-17 10:30:00 - droneserver - INFO - ⚠️  Mission starting - drone will switch to AUTO flight mode
2025-11-17 10:30:00 - droneserver - INFO - 📡 MAVLink → drone.mission.start_mission()
```

---

## ⚠️ Mode Change Warnings

Tools that change flight modes now log warnings:

| Tool | Mode Change | Warning |
|------|------------|---------|
| `initiate_mission` | GUIDED → AUTO | ⚠️ Mission starting - drone will switch to AUTO flight mode |
| `resume_mission` | Any → AUTO | ⚠️ Resuming mission - drone will switch to AUTO flight mode |
| `pause_mission` | AUTO → LOITER | ⚠️ Pausing mission - drone may switch to LOITER mode |
| `hold_position` | **Stays GUIDED** ✅ | Uses goto_location to prevent mode change |

---

## 🛡️ Best Practices

### For Stable Flight
1. **Use `hold_position` to pause** - Stays in GUIDED mode, no altitude drift
2. **Monitor mode changes** - Check logs for ⚠️ warnings
3. **Avoid mixing mission and manual control** - Finish mission or pause before using go_to_location

### For Mission Execution
1. **Start mission** - Expect AUTO mode
2. **Pause if needed** - Drone will hold in LOITER
3. **Resume** - Back to AUTO mode
4. **After mission** - Drone stays in AUTO or switches to GUIDED (depends on autopilot config)

### For Manual Control
1. **Stay in GUIDED** - All manual movement tools keep you here
2. **Use `hold_position` to pause** - No mode switch
3. **Check altitude frequently** - Especially after any mode change

---

## 🐛 Troubleshooting

### Drone Descending When You Hold Position?
**Before v1.2.1:** `hold_position` used `drone.action.hold()` → LOITER mode → altitude drift
**After v1.2.1:** `hold_position` uses `goto_location(current_position)` → stays GUIDED → stable ✅

**Check your version:**
```bash
sudo journalctl -u droneserver -n 50 | grep "hold_position"
```

**Should see:**
```
🔧 MCP TOOL: hold_position()
📡 MAVLink → drone.action.goto_location(lat=X, lon=Y, alt=Z)
```

### Drone in Wrong Mode After Mission?
**Expected:** After mission completes, drone should be in AUTO or GUIDED
**If stuck:** Use `get_flight_mode` to check, then send any GUIDED command to switch back:
```python
hold_position()  # Will switch to GUIDED and hold
```

### Logs Not Showing in journalctl?
**After this update:** Logs are unbuffered and will show immediately
**Restart service:**
```bash
sudo systemctl restart droneserver
sudo journalctl -u droneserver -f
```

---

## 📊 Mode Transition Diagram

```
DISARMED
   ↓ arm_drone
GUIDED (armed)
   ↓ takeoff
GUIDED (flying)
   ├─ go_to_location → GUIDED (continues)
   ├─ hold_position → GUIDED (continues) ✅
   ├─ initiate_mission → AUTO
   ├─ land → LAND
   └─ return_to_launch → RTL

AUTO (mission running)
   ├─ pause_mission → LOITER
   └─ mission complete → AUTO or GUIDED

LOITER (paused mission)
   └─ resume_mission → AUTO

LAND (landing)
   └─ on ground → DISARMED (auto)

RTL (returning home)
   └─ at home → LAND → DISARMED
```

---

## 🔗 Related Documentation

- [STATUS.md](STATUS.md) - Current features and tools
- [TESTING.md](TESTING.md) - Test scenarios
- [ArduPilot Flight Modes](https://ardupilot.org/copter/docs/flight-modes.html) - Official docs

---

**Last Updated:** November 17, 2025  
**Version:** 1.2.1+ (with hold_position GUIDED mode fix)

