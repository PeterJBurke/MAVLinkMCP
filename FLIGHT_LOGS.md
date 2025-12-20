# Flight Logs

## Overview

The MAVLink MCP Server automatically creates detailed flight log files for every session. These logs record all MCP tool calls and the actual MAVLink commands sent to the drone, making it easy to:

- **Review flight operations** post-flight
- **Debug issues** by seeing exactly what commands were sent
- **Verify command sequences** for safety and compliance
- **Audit drone operations** for record-keeping
- **Learn MAVLink protocols** by seeing the actual commands

## Log File Location

Flight logs are stored in:
```
<repository_root>/flight_logs/
```

Each log file is named with a timestamp:
```
flight_20250117_143025.log
```

Format: `flight_YYYYMMDD_HHMMSS.log`

## Log File Format

Each log file contains:

### Header
```
================================================================================
MAVLink MCP Flight Log
Started: 2025-01-17 14:30:25
================================================================================
```

### Log Entries
Each entry is timestamped with millisecond precision:

```
[14:30:27.123] MCP_TOOL: arm_drone()
[14:30:27.156] MAVLink_CMD: drone.action.arm()
[14:30:28.234] MCP_TOOL: takeoff(takeoff_altitude=15.0)
[14:30:28.267] MAVLink_CMD: drone.action.set_takeoff_altitude(altitude=15.0)
[14:30:28.298] MAVLink_CMD: drone.action.takeoff()
[14:30:30.456] MCP_TOOL: go_to_location(latitude_deg=33.645, longitude_deg=-117.842, absolute_altitude_m=50.0, yaw_deg=nan)
[14:30:30.489] MAVLink_CMD: drone.action.goto_location(lat=33.645000, lon=-117.842000, alt=50.0, yaw=nan)
```

## Entry Types

### `MCP_TOOL`
Records when a high-level MCP tool is called by ChatGPT or other AI agent:
```
[HH:MM:SS.mmm] MCP_TOOL: <tool_name>(<parameters>)
```

Examples:
- `MCP_TOOL: arm_drone()`
- `MCP_TOOL: takeoff(takeoff_altitude=20.0)`
- `MCP_TOOL: set_parameter(name=RTL_ALT, value=3000.0, param_type=auto)`

### `MAVLink_CMD`
Records the actual MAVLink/MAVSDK command sent to the drone:
```
[HH:MM:SS.mmm] MAVLink_CMD: <command>(<parameters>)
```

Examples:
- `MAVLink_CMD: drone.action.arm()`
- `MAVLink_CMD: drone.action.goto_location(lat=33.645000, lon=-117.842000, alt=50.0, yaw=0.0)`
- `MAVLink_CMD: drone.param.set_param_float(name=RTL_ALT, value=3000.0)`

## Relationship Between MCP_TOOL and MAVLink_CMD

One MCP tool call may generate **multiple MAVLink commands**:

```
[14:30:28.234] MCP_TOOL: takeoff(takeoff_altitude=15.0)
[14:30:28.267] MAVLink_CMD: drone.action.set_takeoff_altitude(altitude=15.0)
[14:30:28.298] MAVLink_CMD: drone.action.takeoff()
```

This shows that the `takeoff` tool sends two commands to the drone:
1. Set the takeoff altitude
2. Execute the takeoff

## Common MAVLink Commands

### Basic Flight Operations
- `drone.action.arm()` - Enable motors
- `drone.action.disarm()` - Disable motors
- `drone.action.takeoff()` - Initiate takeoff
- `drone.action.land()` - Land at current position
- `drone.action.return_to_launch()` - Return home and land

### Navigation
- `drone.action.goto_location(lat, lon, alt, yaw)` - Fly to GPS coordinates

### Missions
- `drone.mission.upload_mission(waypoint_count)` - Upload waypoints to drone
- `drone.mission.start_mission()` - Start/resume mission execution
- `drone.mission.pause_mission()` - Pause current mission
- `drone.mission.clear_mission()` - Remove all waypoints
- `drone.mission.download_mission()` - Download current mission
- `drone.mission.set_current_mission_item(waypoint_index)` - Jump to waypoint
- `drone.mission.is_mission_finished()` - Check mission status

### Parameter Management
- `drone.param.get_param_int(name)` - Read integer parameter
- `drone.param.get_param_float(name)` - Read float parameter
- `drone.param.set_param_int(name, value)` - Set integer parameter
- `drone.param.set_param_float(name, value)` - Set float parameter

### Speed Control
- `drone.action.set_maximum_speed(speed_m_s)` - Limit maximum speed

### Emergency
- `drone.action.kill()` - Emergency motor shutdown (crash!)

## Example Flight Log

Here's what a complete flight log might look like:

```
================================================================================
MAVLink MCP Flight Log
Started: 2025-01-17 14:30:25
================================================================================

[14:30:27.123] MCP_TOOL: arm_drone()
[14:30:27.156] MAVLink_CMD: drone.action.arm()
[14:30:29.234] MCP_TOOL: takeoff(takeoff_altitude=15.0)
[14:30:29.267] MAVLink_CMD: drone.action.set_takeoff_altitude(altitude=15.0)
[14:30:29.298] MAVLink_CMD: drone.action.takeoff()
[14:30:45.456] MCP_TOOL: go_to_location(latitude_deg=33.645, longitude_deg=-117.842, absolute_altitude_m=50.0, yaw_deg=nan)
[14:30:45.489] MAVLink_CMD: drone.action.goto_location(lat=33.645000, lon=-117.842000, alt=50.0, yaw=nan)
[14:31:15.678] MCP_TOOL: set_yaw(yaw_deg=90.0, yaw_rate_deg_s=30.0)
[14:31:15.711] MAVLink_CMD: drone.action.goto_location(lat=33.645000, lon=-117.842000, alt=50.0, yaw=90.0)
[14:31:45.890] MCP_TOOL: hold_position()
[14:31:45.923] MAVLink_CMD: drone.action.goto_location(lat=33.645001, lon=-117.842001, alt=50.1)
[14:32:00.123] MCP_TOOL: return_to_launch()
[14:32:00.156] MAVLink_CMD: drone.action.return_to_launch()
[14:32:45.456] MCP_TOOL: disarm_drone()
[14:32:45.489] MAVLink_CMD: drone.action.disarm()
```

## Use Cases

### 1. Post-Flight Review
Review what happened during a flight to understand command sequences:
```bash
cat flight_logs/flight_20250117_143025.log | grep MCP_TOOL
```

### 2. Debugging Failures
When a flight doesn't behave as expected, check the exact MAVLink commands:
```bash
cat flight_logs/flight_20250117_143025.log | grep MAVLink_CMD
```

### 3. Audit Trail
Keep flight logs for compliance, safety audits, or insurance purposes.

### 4. Learning MAVLink
See how high-level MCP tools translate to low-level MAVLink commands:
```bash
grep -A1 "MCP_TOOL: set_yaw" flight_logs/flight_*.log
```

### 5. Performance Analysis
Check timing between commands to identify delays or issues:
```bash
cat flight_logs/flight_20250117_143025.log | grep -E "takeoff|land"
```

## Log Rotation

Flight logs are created:
- **One log file per server session** - A new log file is created each time the MCP server starts
- **Automatically timestamped** - Log files never overwrite each other
- **Not automatically deleted** - You should periodically clean up old logs

### Manual Cleanup
Remove logs older than 30 days:
```bash
find flight_logs/ -name "flight_*.log" -mtime +30 -delete
```

Keep only the last 10 flights:
```bash
cd flight_logs/
ls -t flight_*.log | tail -n +11 | xargs rm --
```

## Integration with Testing

Flight logs are automatically created during testing. After running the test scenarios from `TESTING.md`, you can review the logs to verify:

1. **All expected commands were sent**
2. **Commands were sent in the correct order**
3. **No unexpected commands occurred**
4. **Timing between commands was appropriate**

Example verification:
```bash
# Check if parameter management test ran
grep "set_parameter" flight_logs/flight_$(date +%Y%m%d)*.log

# Verify mission upload
grep "upload_mission" flight_logs/flight_$(date +%Y%m%d)*.log | wc -l

# Count navigation commands
grep -E "goto_location|set_yaw|reposition" flight_logs/flight_*.log | wc -l
```

## Troubleshooting

### Log file not created
- Check that the `flight_logs/` directory exists and is writable
- Verify the server started successfully
- Look for error messages in the console/journalctl logs

### Missing log entries
- Ensure you're using the updated version of `droneserver.py`
- Check if any errors occurred during command execution
- Verify the tool calls were successful (not rejected)

### Large log files
- This is normal for long flight sessions with many commands
- Consider implementing log rotation if needed
- Compress old logs: `gzip flight_logs/flight_202501*.log`

## Privacy and Security

### What's NOT logged
- Authentication tokens or credentials
- Full mission plans with sensitive locations (only waypoint counts)
- Telemetry data (position, battery, etc.) - only commands are logged
- Error stack traces with sensitive paths

### What IS logged
- All MCP tool names and parameters
- All MAVLink commands sent to the drone
- Timestamps for all operations

### Recommendations
- **Do NOT share flight logs publicly** if they contain sensitive location data
- Review logs before sharing for troubleshooting
- Implement access controls on the `flight_logs/` directory in production
- Consider encrypting logs for sensitive operations

## Advanced Usage

### Parsing Logs Programmatically

Python example:
```python
import re
from datetime import datetime

def parse_flight_log(log_file):
    entries = []
    with open(log_file, 'r') as f:
        for line in f:
            match = re.match(r'\[(\d{2}:\d{2}:\d{2}\.\d{3})\] (MCP_TOOL|MAVLink_CMD): (.+)', line)
            if match:
                time_str, entry_type, command = match.groups()
                entries.append({
                    'time': time_str,
                    'type': entry_type,
                    'command': command
                })
    return entries

# Usage
entries = parse_flight_log('flight_logs/flight_20250117_143025.log')
mcp_tools = [e for e in entries if e['type'] == 'MCP_TOOL']
mavlink_cmds = [e for e in entries if e['type'] == 'MAVLink_CMD']

print(f"Total MCP tool calls: {len(mcp_tools)}")
print(f"Total MAVLink commands: {len(mavlink_cmds)}")
```

### Analyzing Command Sequences

Check if RTL was properly executed:
```bash
grep -B2 -A5 "return_to_launch" flight_logs/flight_*.log
```

Verify mission workflow:
```bash
grep -E "upload_mission|start_mission|pause_mission|resume_mission" flight_logs/flight_*.log
```

### Integration with External Tools

Export logs to CSV for analysis in spreadsheet tools:
```bash
cat flight_logs/flight_20250117_143025.log | \
  grep -E "\[.*\]" | \
  sed 's/\[//g; s/\] /,/; s/: /,/' > flight_analysis.csv
```

## Future Enhancements

Potential improvements for flight logging:

- [ ] Separate log files per actual flight (arm to disarm)
- [ ] Include response codes from MAVLink commands
- [ ] Add GPS coordinates for navigation commands
- [ ] Log telemetry snapshots at key moments
- [ ] Export logs in JSON format
- [ ] Real-time log streaming to external monitoring
- [ ] Automatic log rotation and compression
- [ ] Log severity levels (INFO, WARNING, ERROR)
- [ ] Integration with flight data analysis tools
- [ ] Statistical summaries at end of log

## Support

For issues with flight logging:
1. Check the main README.md for general troubleshooting
2. Review the server logs (journalctl -u droneserver -f)
3. Ensure you're on the latest version (v1.2.0+)
4. Report issues on GitHub with sample log entries

---

**Note**: Flight logging was introduced in version 1.2.0. Ensure you're running the latest version to access this feature.

