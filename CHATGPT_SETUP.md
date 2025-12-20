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
cd ~/droneserver

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

### 🚀 Alternative: Run as systemd Service (Production)

For production deployments, you can install the MCP server and ngrok as systemd services that auto-start on boot and restart on failure:

```bash
sudo ./install_services.sh
sudo systemctl enable droneserver ngrok
sudo systemctl start droneserver ngrok
```

This is recommended for persistent deployments. See **[SERVICE_SETUP.md](SERVICE_SETUP.md)** for complete instructions.

If using services, skip to **Step 4** after installation.

---

## Step 3: Set Up HTTPS with ngrok (Required for ChatGPT)

⚠️ **IMPORTANT:** ChatGPT requires **HTTPS** for security. You cannot use plain `http://` URLs.

ChatGPT will reject URLs like `http://YOUR_IP:8080/mcp/sse` with an "unsafe URL" error.

**Solution:** Use **ngrok** to create a secure HTTPS tunnel to your server.

### A. Install ngrok

**On your Ubuntu server:**

```bash
# Download and install ngrok
curl -s https://ngrok-agent.s3.amazonaws.com/ngrok.asc \
  | sudo tee /etc/apt/trusted.gpg.d/ngrok.asc >/dev/null \
  && echo "deb https://ngrok-agent.s3.amazonaws.com buster main" \
  | sudo tee /etc/apt/sources.list.d/ngrok.list \
  && sudo apt update \
  && sudo apt install ngrok
```

### B. Get Your ngrok Auth Token

1. **Sign up for ngrok** (free): [https://ngrok.com/signup](https://ngrok.com/signup)
2. **Log in** and go to: [https://dashboard.ngrok.com/get-started/your-authtoken](https://dashboard.ngrok.com/get-started/your-authtoken)
3. **Copy your auth token** (looks like: `2abc123def456ghi789jkl`)

### C. Configure ngrok

```bash
# Add your auth token (replace with your actual token)
ngrok config add-authtoken YOUR_NGROK_TOKEN
```

### D. Start ngrok Tunnel

**Open a NEW terminal/SSH session** (keep your MCP server running in the first one):

```bash
# Create HTTPS tunnel to your MCP server
ngrok http 8080
```

**You'll see output like:**
```
ngrok

Session Status                online
Account                       your-email@example.com (Plan: Free)
Version                       3.x.x
Region                        United States (us)
Web Interface                 http://127.0.0.1:4040
Forwarding                    https://abc123xyz.ngrok-free.app -> http://localhost:8080

Connections                   ttl     opn     rt1     rt5     p50     p90
                              0       0       0.00    0.00    0.00    0.00
```

### E. Get Your HTTPS URL

Look for the **Forwarding** line with `https://`:

```
Forwarding                    https://abc123xyz.ngrok-free.app -> http://localhost:8080
```

**Copy the HTTPS URL:** `https://abc123xyz.ngrok-free.app`

**Your complete ChatGPT MCP URL is:**
```
https://abc123xyz.ngrok-free.app/sse
```

⚠️ **Note:** Use `/sse` endpoint, NOT `/mcp/sse` (the FastMCP server defaults to `/sse` for SSE transport)

⚠️ **Keep both terminals running:**
- Terminal 1: MCP server (`./start_http_server.sh`)
- Terminal 2: ngrok tunnel (`ngrok http 8080`)

---

### ngrok Free Tier Notes

✅ **Included:**
- HTTPS support
- Up to 1 free domain
- Basic tunneling features

⚠️ **Limitations:**
- URL changes each time you restart ngrok
- 40 connections/minute rate limit
- Session timeout after ~8 hours

**For production:** Consider ngrok paid plans ($8/month) for:
- Custom domain (e.g., `drone.yourdomain.com`)
- Reserved URLs that don't change
- No rate limits
- No session timeouts

---

## Step 4: Configure ChatGPT Developer Mode

### A. Open ChatGPT Agent Builder

⚠️ **IMPORTANT:** You need to access the **Agent Builder** interface, not regular ChatGPT settings.

**Direct Link (Recommended):**
```
https://platform.openai.com/chat/edit?models=gpt-4.1
```

**Alternative Path:**
1. Go to [https://platform.openai.com/](https://platform.openai.com/)
2. Navigate to **Agent Builder** or **Chat** section
3. Select **gpt-4.1** model
4. Look for **Tools** or **Custom Tools** configuration

💡 **Note:** The Agent Builder interface allows you to add custom MCP tools to your ChatGPT sessions. This is different from the general ChatGPT settings.

### B. Add MCP Connector

In the Agent Builder interface:

1. Click **"Add Tool"** or **"Add Connector"** button
2. Select **"Custom Tool"** or **"MCP Server"** option
3. Fill in the details:

**Connector Configuration:**
```
Name: droneserver
Description: MAVLink drone control via MCP protocol
Server URL: https://YOUR_NGROK_URL.ngrok-free.app/sse
Type: MCP Server (SSE)
```

**Example (use YOUR actual ngrok URL):**
```
Name: droneserver
Description: AI-powered drone flight control
Server URL: https://abc123xyz.ngrok-free.app/sse
```

⚠️ **CRITICAL NOTES:**
- Replace `abc123xyz.ngrok-free.app` with your actual ngrok URL from Step 3!
- Use `/sse` endpoint, NOT `/mcp/sse` (the server defaults to `/sse`)
- Must use HTTPS (ngrok provides this automatically)
- Name should be lowercase, no spaces (e.g., `droneserver`)

3. Click **Save** or **Add Tool**

### C. Verify Connection

ChatGPT should show:
- ✅ **Connected** status next to the connector
- List of available tools/functions
- Green indicator showing active connection

If you see **"Connection Error"**, check:
- MCP server is running (`./start_http_server.sh`)
- ngrok tunnel is running (`ngrok http 8080`)
- URL is correct HTTPS format: `https://xxx.ngrok-free.app/sse`
- URL includes `/sse` at the end (NOT `/mcp/sse`)
- You're using the HTTPS URL from ngrok, not http://
- You're using the Agent Builder interface at `https://platform.openai.com/chat/edit?models=gpt-4.1`

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

**ChatGPT:** "The drone is currently in GUIDED mode (ArduPilot's external control mode), which allows programmatic control."

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

### Issue 1: "Cannot connect to MCP server" or "Unsafe URL"

**Solutions:**

1. **Verify HTTPS URL with correct path:**
   - ❌ Wrong: `http://64.225.115.101:8080/sse` (HTTP not allowed)
   - ❌ Wrong: `https://abc123xyz.ngrok-free.app/mcp/sse` (wrong path)
   - ✅ Correct: `https://abc123xyz.ngrok-free.app/sse`
   
   ChatGPT requires HTTPS! You must use ngrok, and the correct endpoint is `/sse`.

2. **Check MCP server is running:**
   ```bash
   # On your server
   ps aux | grep droneserver_http
   ```

3. **Check ngrok tunnel is running:**
   ```bash
   # On your server
   ps aux | grep ngrok
   
   # Or view ngrok status
   curl http://localhost:4040/api/tunnels
   ```

4. **Restart ngrok if needed:**
   ```bash
   # Kill existing ngrok
   pkill ngrok
   
   # Start new tunnel
   ngrok http 8080
   
   # Copy the NEW https:// URL
   ```

---

### Issue 2: "Server URL not responding"

**Solutions:**

1. **Use ngrok HTTPS URL, not direct IP:**
   - ❌ Wrong: `http://64.225.115.101:8080/sse`
   - ❌ Wrong: `https://64.225.115.101:8080/sse`
   - ✅ Correct: `https://abc123xyz.ngrok-free.app/sse`

2. **Ensure correct path:**
   - ❌ Wrong: `https://abc123xyz.ngrok-free.app`
   - ❌ Wrong: `https://abc123xyz.ngrok-free.app/mcp/sse` (old path)
   - ✅ Correct: `https://abc123xyz.ngrok-free.app/sse`

3. **Get fresh ngrok URL:**
   ```bash
   # ngrok URLs change on restart
   # View your current URL:
   curl http://localhost:4040/api/tunnels | grep public_url
   ```

---

### Issue 3: "Tools not appearing in ChatGPT"

**Solutions:**

1. **Start a new conversation** - Tools only appear in new chats
2. **Reconnect the connector** in Developer Mode settings
3. **Check both servers are running:**
   ```bash
   # Check MCP server
   ps aux | grep droneserver_http
   
   # Check ngrok
   ps aux | grep ngrok
   ```
4. **Verify MCP server shows tools** in startup logs
5. **Check ngrok web interface** for requests:
   - Open browser to: `http://YOUR_SERVER_IP:4040`
   - See if ChatGPT is making requests

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
   - Your MCP server is accessible via ngrok's public HTTPS URL
   - Anyone with the ngrok URL can control your drone
   - ngrok provides some security, but the URL should be kept private

2. **Authentication:**
   - Current setup has NO authentication
   - ngrok URLs are long and random, providing some obscurity
   - Don't share your ngrok URL publicly

3. **ngrok Security Features:**
   - **Free tier:** Basic password protection available
   - **Paid tier:** IP restrictions, OAuth, webhook verification
   
   **Add password protection (free):**
   ```bash
   ngrok http 8080 --basic-auth "username:password"
   ```
   
   Then configure auth in ChatGPT connector settings.

4. **Best Practices:**
   - ✅ Use ngrok's random URLs (hard to guess)
   - ✅ Rotate ngrok URLs regularly (restart ngrok)
   - ✅ Monitor ngrok web interface (http://localhost:4040) for suspicious activity
   - ✅ Keep your ngrok auth token private
   - ❌ Don't post your ngrok URL on social media
   - ❌ Don't commit ngrok URLs to git

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

See [STATUS.md](STATUS.md) for current capabilities and development roadmap!

---

## Support

- 🐛 [Report Issues](https://github.com/PeterJBurke/droneserver/issues)
- 💬 [Discussions](https://github.com/PeterJBurke/droneserver/discussions)
- 📖 [Main README](README.md)
- 📊 [Status & Roadmap](STATUS.md)
- 🔧 [Service Setup](SERVICE_SETUP.md)
- 🔄 [Update Guide](LIVE_SERVER_UPDATE.md)

---

**Happy Flying with ChatGPT! 🚁🤖**

Remember: Always maintain visual line of sight and follow all aviation safety regulations!

