# Connecting ChatGPT to MAVLink MCP Server

Complete guide to control your drone using natural language through ChatGPT's web interface.

---

## Prerequisites

✅ **You must have:**
1. ChatGPT Plus or Pro subscription
2. Developer Mode enabled in ChatGPT (you mentioned this is done)
3. This MAVLink MCP repository set up on a server
4. Your drone configured in `.env` file

---

## Architecture Overview

```
┌──────────────┐         ┌──────────────────┐         ┌──────────┐
│   ChatGPT    │         │  MAVLink MCP     │         │  Drone   │
│ (Web Browser)│◄───────►│  HTTP Server     │◄───────►│          │
│              │  SSE    │  (Port 8080)     │ MAVLink │ TCP/UDP  │
└──────────────┘         └──────────────────┘         └──────────┘
```

**Flow:**
1. You type natural language command in ChatGPT
2. ChatGPT connects to your MCP server via HTTP/SSE
3. MCP server translates to MAVLink commands
4. Commands sent to drone via TCP/UDP
5. Drone responds with telemetry
6. ChatGPT displays results in natural language

---

## Step 1: Configure Your Server

### A. Update `.env` File

Add HTTP server configuration to your `.env`:

```bash
nano .env
```

Add these lines (keep existing drone config):

```bash
# Existing drone configuration
MAVLINK_ADDRESS=203.0.113.10
MAVLINK_PORT=5678
MAVLINK_PROTOCOL=tcp

# NEW: HTTP Server Configuration for ChatGPT
MCP_HOST=0.0.0.0        # Listen on all interfaces
MCP_PORT=8080           # HTTP port for MCP server
```

**Security Note:** If your server has a firewall, you'll need to open port 8080.

---

## Step 2: Start the HTTP MCP Server

On your Ubuntu server:

```bash
cd ~/MAVLinkMCP

# Make the script executable
chmod +x start_http_server.sh

# Start the HTTP server
./start_http_server.sh
```

**Expected Output:**
```
===========================================================
MAVLink MCP Server - HTTP/SSE Mode
===========================================================
Starting HTTP server on http://0.0.0.0:8080
This server can be connected to from:
  - ChatGPT Developer Mode
  - Claude Desktop
  - Any MCP-compatible web client
===========================================================
INFO - MAVLink MCP Server Starting
INFO - Configuration loaded from .env file:
INFO -   MAVLINK_ADDRESS: 203.0.113.10
INFO -   MAVLINK_PORT: 5678
INFO -   MAVLINK_PROTOCOL: tcp
INFO - Connected to drone at 203.0.113.10:5678!
INFO - GPS lock acquired!
INFO - MCP Server is READY and exposing drone control tools
```

**Keep this terminal running!** The server must stay active for ChatGPT to connect.

---

## Step 3: Get Your Server URL

### A. Find Your Server IP

If running on a cloud instance:
```bash
# Get your public IP
curl ifconfig.me
```

Example output: `203.0.113.10`

### B. Construct Your MCP Server URL

Format: `http://YOUR_SERVER_IP:8080/mcp/sse`

Example: `http://203.0.113.10:8080/mcp/sse`

**Write this down - you'll need it for ChatGPT!**

**Note:** The URL includes:
- Your server's public IP address
- Port 8080 (default MCP server port)
- `/mcp/sse` path (mount point + SSE endpoint)

---

## Step 4: Configure ChatGPT Developer Mode

### A. Open ChatGPT Settings

1. Go to [https://chat.openai.com/](https://chat.openai.com/)
2. Click your **profile icon** (bottom left)
3. Select **Settings**
4. Navigate to **Developer** section

### B. Add MCP Connector

In Developer Mode settings:

1. Click **"Add Connector"** or **"New MCP Server"**
2. Fill in the details:

**Connector Configuration:**
```
Name: MAVLink Drone Controller
Description: Control MAVLink drones with natural language
Server URL: http://YOUR_SERVER_IP:8080/mcp/sse
Type: MCP Server (SSE)
```

**Example:**
```
Name: MAVLink Drone Controller
Description: AI-powered drone flight control
Server URL: http://203.0.113.10:8080/mcp/sse
Type: MCP Server (SSE)
```

3. Click **Save** or **Connect**

### C. Verify Connection

ChatGPT should show:
- ✅ **Connected** status next to the connector
- List of available tools/functions
- Green indicator showing active connection

If you see **"Connection Error"**, check:
- Server is running (`./start_http_server.sh`)
- Firewall allows port 8080
- URL is correct (including `/sse` at the end)
- Server IP is accessible from your location

---

## Step 5: Control Your Drone with Natural Language!

### Starting a New Chat

1. Start a **new conversation** in ChatGPT
2. ChatGPT should automatically detect the MCP connector
3. You'll see a message like: "MAVLink Drone Controller tools are available"

### Example Commands

#### **Pre-Flight Checks**

**You:** "Check if the drone is connected and show me its current position"

**ChatGPT:** "The drone is connected at 203.0.113.10:5678. Current position: Latitude 33.6459°, Longitude -117.8428°, Altitude 0.0m"

---

#### **Arming and Takeoff**

**You:** "Arm the drone and take off to 10 meters"

**ChatGPT:** "I'll arm the drone and initiate takeoff to 10 meters... Done! The drone is now armed and taking off. Current altitude: 9.8m"

---

#### **Position Queries**

**You:** "What's the drone's current altitude and battery level?"

**ChatGPT:** "Current altitude: 10.2m above home position. Battery: 85%"

---

#### **Movement**

**You:** "Move the drone 5 meters forward"

**ChatGPT:** "Moving drone 5 meters forward... Command sent. The drone is now moving to the new position."

---

#### **Landing**

**You:** "Land the drone safely"

**ChatGPT:** "Initiating landing sequence... The drone is descending. Current altitude: 8.5m... 5.2m... 2.1m... 0.1m. Landed successfully!"

---

#### **Flight Mode**

**You:** "What flight mode is the drone in?"

**ChatGPT:** "The drone is currently in OFFBOARD mode, which allows programmatic control."

---

## Step 6: Advanced Usage

### Multi-Step Operations

**You:** "I want to fly a simple pattern: take off to 15 meters, hover for 5 seconds, then land"

**ChatGPT:** "I'll execute this flight pattern for you:
1. Taking off to 15 meters... Reached 15m
2. Hovering for 5 seconds... Done
3. Landing... Altitude: 10m... 5m... 1m... 0m. Landed safely!"

### Safety Queries

**You:** "Is it safe to fly right now? Check all pre-flight systems"

**ChatGPT:** "Let me check the drone's status:
- ✅ Connection: Active
- ✅ GPS Lock: Acquired (12 satellites)
- ✅ Battery: 92%
- ✅ Flight Mode: Ready
- ✅ Position: Valid home position set
All systems nominal - safe to fly!"

### Mission Planning

**You:** "Create a mission to fly to coordinates 33.646°N, 117.843°W at 20 meters altitude, then return home"

**ChatGPT:** "I'll create that waypoint mission... Mission uploaded with 2 waypoints. Starting mission now... Waypoint 1 reached... Waypoint 2 reached... Returning to launch..."

---

## Troubleshooting

### Issue 1: "Cannot connect to MCP server"

**Solutions:**

1. **Check server is running:**
   ```bash
   # On your server
   ps aux | grep mavlinkmcp_http
   ```

2. **Verify port is open:**
   ```bash
   # On your server
   sudo netstat -tulpn | grep 8080
   ```

3. **Test with curl:**
   ```bash
   curl http://YOUR_SERVER_IP:8080/mcp/sse
   ```

4. **Check firewall:**
   ```bash
   # Allow port 8080
   sudo ufw allow 8080/tcp
   ```

---

### Issue 2: "Server URL not responding"

**Solutions:**

1. **Use public IP, not localhost:**
   - ❌ Wrong: `http://localhost:8080/mcp/sse`
   - ✅ Correct: `http://203.0.113.10:8080/mcp/sse`

2. **Ensure correct path:**
   - ❌ Wrong: `http://203.0.113.10:8080`
   - ❌ Wrong: `http://203.0.113.10:8080/sse`
   - ✅ Correct: `http://203.0.113.10:8080/mcp/sse`

3. **Check HTTPS vs HTTP:**
   - Use `http://` not `https://` (unless you've set up SSL)

---

### Issue 3: "Tools not appearing in ChatGPT"

**Solutions:**

1. **Start a new conversation** - Tools only appear in new chats
2. **Reconnect the connector** in Developer Mode settings
3. **Check server logs** for connection attempts
4. **Verify MCP server shows tools** in startup logs

---

### Issue 4: "Drone commands not executing"

**Solutions:**

1. **Check drone connection** in server logs
2. **Verify .env configuration:**
   ```bash
   cat .env
   ```
3. **Test with interactive client first:**
   ```bash
   uv run examples/interactive_client.py
   ```
4. **Check GPS lock** - many commands require GPS

---

## Security Considerations

⚠️ **IMPORTANT SECURITY NOTES:**

1. **Network Exposure:**
   - Your MCP server is accessible over the network
   - Anyone with the URL can control your drone
   - Use a firewall and restrict access by IP if possible

2. **Authentication:**
   - Current setup has NO authentication
   - Consider adding authentication for production use
   - Use VPN or SSH tunnel for secure access

3. **Recommended Security:**
   ```bash
   # Option 1: Only allow from specific IP (ChatGPT's IP)
   sudo ufw allow from CHATGPT_IP to any port 8080
   
   # Option 2: Use SSH tunnel (most secure)
   ssh -L 8080:localhost:8080 user@YOUR_SERVER_IP
   # Then use: http://localhost:8080/sse in ChatGPT
   ```

---

## Example Full Flight Session

Here's a complete conversation example:

**You:** "Hi! I'd like to fly my drone. Can you check if it's ready?"

**ChatGPT:** "I'll check the drone's status for you... The drone is connected at 203.0.113.10:5678. All systems are operational:
- GPS: Locked with 14 satellites
- Battery: 98%
- Position: Home position set at 33.6459°N, 117.8428°W
- Flight mode: Ready
Ready for flight!"

**You:** "Great! Arm it and take off to 12 meters"

**ChatGPT:** "Arming the drone... Armed successfully! Initiating takeoff to 12 meters... The drone is ascending... Current altitude: 5m... 10m... 12m. Hovering at 12 meters."

**You:** "What can I see from here? Show me the current position"

**ChatGPT:** "Current position: Latitude 33.6459°N, Longitude 117.8428°W, Altitude 12.0m above home. You're hovering 12 meters directly above the takeoff point."

**You:** "Perfect. Now bring it back and land"

**ChatGPT:** "Initiating landing sequence... Descending... 10m... 7m... 5m... 2m... 0.5m... Touchdown! Drone has landed safely."

**You:** "Thanks! That was perfect"

**ChatGPT:** "You're welcome! The drone is safely on the ground. Would you like to disarm it?"

**You:** "Yes please"

**ChatGPT:** "Disarming the drone... Done! The drone is now disarmed and safe."

---

## Next Steps

### Want More Control?

1. **Add custom waypoints** - "Fly to multiple coordinates in sequence"
2. **Create patterns** - "Fly in a square pattern at 15 meters"
3. **Automated missions** - "Survey this area in a grid pattern"
4. **Emergency commands** - "Return to home immediately"

### Want Better Security?

1. Set up HTTPS with SSL certificate
2. Add authentication middleware
3. Use SSH tunneling for secure connection
4. Implement rate limiting

### Want More Features?

See [STATUS.md](STATUS.md) for roadmap and possible enhancements!

---

## Support

- 🐛 [Report Issues](https://github.com/PeterJBurke/MAVLinkMCP/issues)
- 💬 [Discussions](https://github.com/PeterJBurke/MAVLinkMCP/discussions)
- 📖 [Main README](README.md)
- 📊 [Project Status](STATUS.md)

---

**Happy Flying with ChatGPT! 🚁🤖**

Remember: Always maintain visual line of sight and follow all aviation safety regulations!

