# Color-Coded Journalctl Logs

Your MAVLink MCP server logs now use ANSI color codes to make different log types instantly recognizable!

---

## 🎨 Color Scheme

| Log Type | Color | Emoji | Example |
|----------|-------|-------|---------|
| **MCP Tool Calls** | 🟢 GREEN | 🔧 | `🔧 MCP TOOL: arm_drone()` |
| **MAVLink Commands** | 🔵 CYAN | 📡 | `📡 MAVLink → drone.action.arm()` |
| **Tool Errors** | 🔴 RED | ❌ | `❌ TOOL ERROR - Failed to arm: ...` |
| **Warnings** | 🟡 YELLOW | ⚠️ | `⚠️ EMERGENCY MOTOR KILL ACTIVATED` |
| **Standard Info** | ⚪ WHITE | ℹ️ | `✓ Drone armed successfully` |

**Note:** HTTP request logs are suppressed by default for cleaner output. Enable with `MAVLINK_VERBOSE=1` in `.env` if needed.

---

## 📊 Example Log Output

Here's what a typical flight sequence looks like with color coding (clean, readable):

```
02:30:10 | INFO    | 🔧 MCP TOOL: get_health()                                    [GREEN]
02:30:10 | INFO    | Health check complete - all systems nominal

02:30:12 | INFO    | 🔧 MCP TOOL: arm_drone()                                     [GREEN]
02:30:12 | INFO    | Arming drone...
02:30:12 | INFO    | 📡 MAVLink → drone.action.arm()                              [CYAN]
02:30:13 | INFO    | ✓ Drone armed successfully

02:30:15 | INFO    | 🔧 MCP TOOL: takeoff(takeoff_altitude=10.0)                 [GREEN]
02:30:15 | INFO    | 📡 MAVLink → drone.action.set_takeoff_altitude(altitude=10.0) [CYAN]
02:30:15 | INFO    | 📡 MAVLink → drone.action.takeoff()                         [CYAN]
02:30:15 | INFO    | ✓ Takeoff initiated to 10.0m

02:30:20 | INFO    | 🔧 MCP TOOL: go_to_location(latitude_deg=33.646, longitude_deg=-117.843, ...) [GREEN]
02:30:20 | INFO    | 📡 MAVLink → drone.action.goto_location(lat=33.646, lon=-117.843, ...) [CYAN]

02:30:25 | INFO    | 🔧 MCP TOOL: set_yaw(yaw_deg=90.0, yaw_rate_deg_s=30.0)    [GREEN]
02:30:25 | INFO    | 📡 MAVLink → drone.action.goto_location(...)                [CYAN]
02:30:25 | INFO    | ✓ Yaw set to 90.0° (E)

02:30:30 | INFO    | 🔧 MCP TOOL: land()                                         [GREEN]
02:30:30 | INFO    | 📡 MAVLink → drone.action.land()                            [CYAN]

02:30:35 | INFO    | 🔧 MCP TOOL: disarm_drone()                                 [GREEN]
02:30:35 | INFO    | 📡 MAVLink → drone.action.disarm()                          [CYAN]
02:30:35 | ERROR   | ❌ TOOL ERROR - Failed to disarm: motors still spinning     [RED]
```

**Clean and focused on drone operations!** No HTTP noise. 🎉

---

## 🔍 Viewing Colored Logs

**IMPORTANT:** journalctl strips ANSI colors by default! Use `--output=cat` to see colors.

### Watch live logs WITH colors:
```bash
sudo journalctl -u droneserver -f --output=cat
```

### Watch live logs WITHOUT colors (but with systemd prefix):
```bash
sudo journalctl -u droneserver -f
```

### View last 50 lines with colors:
```bash
sudo journalctl -u droneserver -n 50 --output=cat --no-pager
```

### View last 50 lines in less (scrollable, with colors):
```bash
sudo journalctl -u droneserver -n 50 --output=cat | less -R
```

### Filter by log type (with colors):

**Only MAVLink commands (cyan):**
```bash
sudo journalctl -u droneserver -f --output=cat | grep "📡 MAVLink"
```

**Only MCP tool calls (green):**
```bash
sudo journalctl -u droneserver -f --output=cat | grep "🔧 MCP TOOL"
```

**Only errors (red):**
```bash
sudo journalctl -u droneserver -f --output=cat | grep "❌"
```

**Only warnings (yellow):**
```bash
sudo journalctl -u droneserver -f --output=cat | grep "⚠️"
```

**Complete flight sequence (tool → MAVLink):**
```bash
sudo journalctl -u droneserver -f --output=cat | grep -E "MCP TOOL|MAVLink"
```

**Everything (tool + MAVLink + errors):**
```bash
sudo journalctl -u droneserver -f --output=cat | grep -E "🔧|📡|❌"
```

---

## 🎯 Why This Helps

### 1. **Instant Visual Identification**
- See at a glance what type of log entry you're looking at
- No need to read the full line to know if it's a command, tool, or error

### 2. **Easier Debugging**
- **GREEN** tool calls show what ChatGPT requested
- **CYAN** MAVLink commands show what was actually sent to the drone
- **RED** errors immediately stand out
- Follow the flow: Tool → MAVLink → Result

### 3. **Production Monitoring**
- Errors are impossible to miss (bright red)
- Critical warnings in yellow catch your attention
- Easy to scan hundreds of lines quickly

### 4. **Professional Appearance**
- Looks like modern monitoring tools (Kubernetes, Docker, etc.)
- Color-coded logs are industry standard

---

## 🔧 Technical Details

**ANSI Color Codes Used:**
```python
class LogColors:
    RESET = '\033[0m'
    RED = '\033[91m'      # Errors
    GREEN = '\033[92m'    # Tool calls
    YELLOW = '\033[93m'   # Warnings
    CYAN = '\033[96m'     # MAVLink commands
```

**Applied to:**
- `log_tool_call()` - All MCP tool invocations
- `log_mavlink_cmd()` - All MAVLink commands sent to drone
- `logger.error()` - All error messages
- `logger.warning()` - Critical warnings

---

## 📝 Example Debugging Scenario

**Problem:** Drone won't arm

**Without colors:**
```
02:30:10 INFO Processing request of type CallToolRequest
02:30:10 INFO MCP TOOL: arm_drone()
02:30:10 INFO Arming drone...
02:30:10 INFO MAVLink → drone.action.arm()
02:30:11 ERROR Failed to arm: preflight checks failed
```
All lines look the same - hard to quickly identify the error.

**With colors:**
```
02:30:10 | INFO    | Processing request of type CallToolRequest
02:30:10 | INFO    | 🔧 MCP TOOL: arm_drone()                    [GREEN - stands out]
02:30:10 | INFO    | Arming drone...
02:30:10 | INFO    | 📡 MAVLink → drone.action.arm()             [CYAN - stands out]
02:30:11 | ERROR   | ❌ TOOL ERROR - Failed to arm: preflight checks failed  [RED - IMMEDIATELY VISIBLE]
```
Error jumps out in red - you see the problem instantly!

---

## 🚀 Update Your Server

To get the new colored logs:

```bash
cd ~/droneserver
git pull origin main
sudo systemctl restart droneserver

# Watch logs WITH colors
sudo journalctl -u droneserver -f --output=cat
```

---

## 🔧 Troubleshooting

### "I don't see any colors - everything is black!"

**Problem:** journalctl strips ANSI color codes by default.

**Solution:** Add `--output=cat` to your journalctl command:
```bash
sudo journalctl -u droneserver -f --output=cat
```

### "How do I check if colors are actually in the logs?"

Run this command to search for ANSI codes:
```bash
sudo journalctl -u droneserver -n 20 | grep -E '\[0m|\[91m|\[92m|\[96m'
```

If you see output with `[91m`, `[92m`, `[96m`, etc., then colors ARE present but journalctl is hiding them.

### "I want the systemd timestamp but also colors"

Unfortunately, journalctl's default output formats strip ANSI codes. You have two options:

**Option 1:** Colors without systemd prefix
```bash
sudo journalctl -u droneserver -f --output=cat
```

**Option 2:** Systemd prefix without colors
```bash
sudo journalctl -u droneserver -f
```

Our logs include their own timestamp (`HH:MM:SS`), so `--output=cat` is recommended for best readability.

---

**Enjoy your beautiful, color-coded logs!** 🎨✨

---

[← Back to Main README](README.md)

