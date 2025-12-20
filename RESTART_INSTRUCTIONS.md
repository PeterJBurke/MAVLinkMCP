# How to Restart and View Logs

## 🔄 Restart the MCP Server

After pulling the latest code, restart the service:

```bash
# Pull latest code
cd ~/droneserver
git pull origin main

# Restart the MCP service
sudo systemctl restart droneserver

# Check it started successfully
sudo systemctl status droneserver
```

## 👀 View Live Logs

Now you can see **exactly** what's happening:

```bash
# Watch live logs (Ctrl+C to stop)
sudo journalctl -u droneserver -f
```

## 📊 What You'll See

### Tool Calls
Every time a tool is called, you'll see:
```
2025-11-17 10:30:45 - droneserver - INFO - 🔧 MCP TOOL: takeoff(takeoff_altitude=15.0)
```

### MAVLink Commands
Every command sent to the drone:
```
2025-11-17 10:30:45 - droneserver - INFO - 📡 MAVLink → drone.action.set_takeoff_altitude(altitude=15.0)
2025-11-17 10:30:45 - droneserver - INFO - 📡 MAVLink → drone.action.takeoff()
```

### Flight Mode Warnings
When modes change automatically:
```
2025-11-17 10:30:50 - droneserver - INFO - 🔧 MCP TOOL: initiate_mission(...)
2025-11-17 10:30:50 - droneserver - INFO - ⚠️  Mission starting - drone will switch to AUTO flight mode
2025-11-17 10:30:50 - droneserver - INFO - 📡 MAVLink → drone.mission.start_mission()
```

### hold_position (Stays in GUIDED)
```
2025-11-17 10:31:00 - droneserver - INFO - 🔧 MCP TOOL: hold_position()
2025-11-17 10:31:00 - droneserver - INFO - 📡 MAVLink → drone.action.goto_location(lat=33.6459, lon=-117.8427, alt=50.0)
```
✅ **No mode change - stays in GUIDED, no altitude drift!**

## 🐛 Troubleshooting

### If logs still don't show:
```bash
# Check service is actually running
sudo systemctl status droneserver

# Check for errors in startup
sudo journalctl -u droneserver -n 100

# Restart and watch from the beginning
sudo systemctl restart droneserver && sudo journalctl -u droneserver -f
```

### If you see Python errors:
```bash
# Make sure dependencies are updated
cd ~/droneserver
uv sync
sudo systemctl restart droneserver
```

## 🎯 What Changed

1. **Unbuffered logging** - Logs appear immediately (no delay)
2. **🔧 MCP TOOL markers** - Shows which tool was called
3. **📡 MAVLink markers** - Shows which command was sent
4. **⚠️ Mode warnings** - Warns when flight mode changes

## 📖 More Information

- **Flight modes explained:** [FLIGHT_MODES.md](FLIGHT_MODES.md)
- **Why modes change:** Mission commands (initiate/pause/resume) automatically switch modes
- **Why hold_position is safe:** Now uses goto_location to stay in GUIDED mode

---

**Ready to test!** Restart the service and watch the logs as you fly. 🚁✨

