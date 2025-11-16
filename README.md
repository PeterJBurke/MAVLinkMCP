# MAVLink MCP Server

A Python-based Model Context Protocol (MCP) server for AI-powered drone control. Connect LLMs to MAVLink-enabled drones (PX4, ArduPilot) for natural language flight control.

## Features

- 🤖 **AI-Powered Control**: Use natural language to command drones via GPT-4, Claude, or other LLMs
- 🚁 **MAVLink Compatible**: Works with PX4, ArduPilot, and other MAVLink drones
- 🔧 **MCP Protocol**: Standard Model Context Protocol for tool integration
- 📡 **Network/Serial Support**: Connect via UDP, TCP, or serial ports
- 🛡️ **Safe Configuration**: Secure handling of connection credentials
- 💬 **ChatGPT Integration**: Direct control from ChatGPT web interface (see below)

## 🌐 Control Your Drone with ChatGPT (NEW!)

**Want to fly your drone by chatting with ChatGPT?** 

You can now control your drone using natural language through the ChatGPT web interface!

**Example conversation:**
```
You: "Arm the drone and take off to 15 meters"
ChatGPT: "Arming... Armed! Taking off to 15 meters... 
         Altitude: 5m... 10m... 15m. Hovering at 15 meters."

You: "What's the battery level?"
ChatGPT: "Battery is at 92%"

You: "Perfect. Land the drone safely"
ChatGPT: "Landing... 10m... 5m... 1m... Landed successfully!"
```

**Setup Steps:**
1. Enable **Developer Mode** in ChatGPT settings (ChatGPT Plus/Pro required)
2. Start the HTTP MCP server: `./start_http_server.sh`
3. Add the MCP connector in ChatGPT with your server URL
4. Start flying with natural language!

📖 **[Complete ChatGPT Setup Guide →](CHATGPT_SETUP.md)**

### 🚀 Production Deployment with systemd Services

For production deployments, you can run the MCP server and ngrok as systemd services that:
- ✅ **Auto-start on boot** - Server starts automatically when system boots
- ✅ **Auto-restart on failure** - Automatically recovers from crashes
- ✅ **Centralized logging** - View logs with `journalctl`
- ✅ **Easy management** - Control with `systemctl` commands

**Quick Install:**
```bash
sudo ./install_services.sh
sudo systemctl enable mavlinkmcp ngrok
sudo systemctl start mavlinkmcp ngrok
```

📖 **[Complete Service Setup Guide →](SERVICE_SETUP.md)**

### 🔄 Updating Your Running Server

If you're already running the services and need to pull updates from GitHub:

```bash
# On your server
cd ~/MAVLinkMCP

# Pull latest code
git pull origin main

# Update dependencies (if needed)
uv sync

# Restart the service to load new code
sudo systemctl restart mavlinkmcp

# Check status
sudo systemctl status mavlinkmcp
```

**Note:** The ngrok service doesn't need to be restarted unless its configuration changed. Your ngrok URL remains the same.

📖 **[Complete Update Guide →](LIVE_SERVER_UPDATE.md)**

### 📊 Monitoring Your Services

Check the status and logs of your running services:

#### **Quick Status Check**

```bash
# Check both services at once
sudo systemctl status mavlinkmcp ngrok

# Check individual services
sudo systemctl status mavlinkmcp
sudo systemctl status ngrok
```

#### **View Live Logs (Real-Time)**

```bash
# Watch MCP server logs in real-time
sudo journalctl -u mavlinkmcp -f

# Watch ngrok logs in real-time
sudo journalctl -u ngrok -f

# Watch both services simultaneously
sudo journalctl -u mavlinkmcp -u ngrok -f
```

Press `Ctrl+C` to stop following logs.

#### **View Recent Logs**

```bash
# Last 50 lines from MCP server
sudo journalctl -u mavlinkmcp -n 50

# Last 50 lines from ngrok
sudo journalctl -u ngrok -n 50

# Last hour of logs
sudo journalctl -u mavlinkmcp --since "1 hour ago"

# Logs from today
sudo journalctl -u mavlinkmcp --since today
```

#### **Get Your ngrok URL**

```bash
# Get the HTTPS URL for ChatGPT
curl -s http://localhost:4040/api/tunnels | grep -o 'https://[^"]*ngrok[^"]*'

# Or view full ngrok info
curl -s http://localhost:4040/api/tunnels | python3 -m json.tool
```

#### **Verify Drone Connection**

```bash
# Check if drone is connected
sudo journalctl -u mavlinkmcp -n 100 | grep -E "Connected to drone|GPS LOCK|READY"

# Check current telemetry
sudo journalctl -u mavlinkmcp -n 20
```

#### **Health Check All Services**

```bash
# One-line health check
systemctl is-active mavlinkmcp ngrok && echo "✅ All services running"

# Detailed check with ports
sudo netstat -tulpn | grep -E "8080|4040"
```

---

## Prerequisites

- **Python 3.12+** (comes with Ubuntu 24.04)
- **Ubuntu 24.04** cloud instance or server
- **uv** package manager ([install here](https://github.com/astral-sh/uv))
- **MAVLink-compatible drone or simulator** with network access (IP address and port)
  - Connection details will be configured in `.env` file
- **OpenAI or Anthropic API key** (for AI agent control)

### 🔌 Autopilot Firmware Compatibility

MAVLink MCP works with all major autopilots, but some advanced features require recent firmware versions:

| Feature Category | ArduPilot | PX4 | Notes |
|-----------------|-----------|-----|-------|
| **Core Flight Control** | ✅ All versions | ✅ All versions | Arm, takeoff, land, move, RTL |
| **Basic Navigation** | ✅ All versions | ✅ All versions | GPS, waypoints, hold |
| **Parameter Management** | ✅ All versions | ✅ All versions | Read/write all parameters |
| **Mission Upload/Download** | ✅ 3.5+ | ✅ 1.10+ | Upload works everywhere |
| **Advanced Navigation** | ⚠️ 4.0+ | ⚠️ 1.13+ | `orbit_location` requires newer firmware |
| **Yaw Control** | ✅ All versions | ✅ All versions | Works on all versions |
| **Battery Monitoring** | ⚠️ Calibration needed | ⚠️ Calibration needed | Set `BATT_CAPACITY` for accurate readings |

**✅ Recommended for Full v1.2.0 Support:**
- **ArduPilot Copter:** 4.0.0 or newer
- **PX4:** 1.13.0 or newer  
- **SITL Simulators:** Latest stable versions

**⚠️ Limited Support:**
- Older firmware (ArduPilot 3.x, PX4 1.10-1.12) works but `orbit_location` may fail
- The server automatically detects unsupported features and provides workarounds

**🔍 Check Your Firmware:**
- Use `get_telemetry` or `get_health` tools to check autopilot version
- See [TESTING_GUIDE.md](TESTING_GUIDE.md) for firmware-specific limitations and workarounds

## Quick Start

### 1. Set Up Your System

**If starting from a fresh Ubuntu 24.04 instance** (AWS, Digital Ocean, Linode, etc.):

```bash
# Update package list
sudo apt update

# Upgrade packages (non-interactive, no prompts)
sudo DEBIAN_FRONTEND=noninteractive apt upgrade -y -o Dpkg::Options::="--force-confdef" -o Dpkg::Options::="--force-confold"

# Install essential dependencies
sudo apt install -y python3 python3-pip python3-venv git curl build-essential

# Verify Python version (Ubuntu 24.04 comes with Python 3.12)
python3 --version
```

**Expected output:** `Python 3.12.x`

**Install uv package manager:**

```bash
curl -LsSf https://astral.sh/uv/install.sh | sh
```

**Restart your shell or load the environment:**

```bash
source $HOME/.bashrc
# Or if using zsh: source $HOME/.zshrc
```

**Verify uv installation:**

```bash
uv --version
```

### 2. Clone and Install

```bash
git clone https://github.com/PeterJBurke/MAVLinkMCP.git
cd MAVLinkMCP

# Install all dependencies
uv sync

# If you get module import errors, try removing the lock file and reinstalling
# rm uv.lock
# uv sync --upgrade
```

### 3. Configure Connection and API Keys

**a) Set up drone connection:**

Copy the example configuration:
```bash
cp .env.example .env
```

Edit `.env` with your drone's IP and port:
```bash
nano .env
```

Set your drone's connection details:
```bash
MAVLINK_ADDRESS=<your-drone-ip>
MAVLINK_PORT=14540  # Or your drone's specific port
MAVLINK_PROTOCOL=tcp  # tcp, udp, or serial
```

**Protocol Selection:**
- **TCP:** Use for remote/cloud drones or network connections (most common)
- **UDP:** Use for local simulators (PX4 SITL, Gazebo)
- **Serial:** Use for direct USB/serial connections

> **Tip:** If QGROUNDCONTROL connects successfully to your drone, use the same protocol it uses (usually TCP for network connections).

**Save and exit:** Press `Ctrl+X`, then `Y`, then `Enter`

**b) Add your API key:**

Create the secrets file:
```bash
nano examples/fastagent.secrets.yaml
```

Add your OpenAI or Anthropic API key:
```yaml
openai:
    api_key: sk-your-actual-openai-key-here
```

Or for Claude:
```yaml
anthropic:
    api_key: sk-ant-your-anthropic-key-here
```

**Save and exit:** Press `Ctrl+X`, then `Y`, then `Enter`

### 4. Control Your Drone

**Choose your control method:**

#### **Option A: Interactive CLI (Easiest - Direct Control)**

Use the interactive command-line controller for direct, human-friendly control:

```bash
uv run examples/interactive_client.py
```

**Example commands:**
```
Command> arm              # Arm the drone
Command> takeoff 10       # Take off to 10 meters
Command> position         # Get GPS position
Command> battery          # Check battery
Command> land             # Land the drone
Command> help             # Show all commands
```

**Available commands:**
- `connect` - Connect to drone
- `arm` / `disarm` - Arm/disarm motors
- `takeoff [altitude]` - Take off (default: 10m)
- `land` - Land the drone
- `position` - Get GPS coordinates
- `battery` - Check battery level
- `mode` - Get flight mode
- `help` - Show all commands
- `quit` / `exit` - Exit

---

#### **Option B: MCP Server (For AI Agents)**

If you want to control your drone through AI (Claude Desktop, etc.), run the MCP server:

```bash
uv run src/server/mavlinkmcp.py
```

**⚠️ Important:** The MCP server uses JSON-RPC protocol. Don't type commands into it directly. Instead:
1. Run it in the background or separate terminal
2. Connect to it with Claude Desktop or another MCP-compatible client
3. Chat naturally with the AI to control your drone

## Usage

### Running the MCP Server

The MAVLink MCP server exposes drone control as MCP tools that can be used by AI agents or other MCP clients:

```bash
# Run the MCP server (connects to your drone via .env settings)
uv run src/server/mavlinkmcp.py
```

The server will:
1. Load drone connection settings from `.env`
2. Connect to your drone at the specified IP and port
3. Wait for GPS lock
4. Expose drone control tools via the MCP protocol

### Integrating with AI Agents

The MCP server can be integrated with various AI frameworks:

- **MCP-compatible AI agents** - Connect any MCP client to the server
- **Custom Python clients** - Use the `mcp-agent` package to build custom integrations
- **LLM applications** - Expose drone control as tools/functions to LLMs

**Note:** The `mcp-agent` package API has changed significantly. We're working on updated examples for the new API. For now, you can run the server directly and integrate it with your preferred MCP client.

### Quick Launch Scripts

Test connection:
```bash
./test_connection.sh
```

Start agent with auto-configuration:
```bash
./start_agent.sh
```

## Documentation

### Setup & Deployment Guides
- **[ChatGPT Setup Guide](CHATGPT_SETUP.md)** - Control drone with ChatGPT web interface
- **[Service Setup Guide](SERVICE_SETUP.md)** - Production deployment with systemd
- **[Server Update Guide](LIVE_SERVER_UPDATE.md)** - Update your running server

### Testing & Examples
- **[Testing Guide](TESTING_GUIDE.md)** - Comprehensive test scenarios for v1.2.0 features 🆕
- **[Examples README](examples/README.md)** - Example agent documentation

### Project Information
- **[Project Status & Roadmap](STATUS.md)** - Current features and future plans

### External Resources
- **[MCP Protocol](https://modelcontextprotocol.io/)** - Model Context Protocol docs
- **[MAVSDK](https://mavsdk.mavlink.io/)** - MAVLink SDK documentation

## Available Tools

The MCP server exposes **35 tools** for complete drone control:

| Category | Count | Key Tools |
|----------|-------|-----------|
| **Flight Control** | 5 | `arm_drone`, `disarm_drone`, `takeoff`, `land`, `hold_position` |
| **Emergency & Safety** | 3 | `return_to_launch`, `kill_motors`, `get_battery` |
| **Navigation** | 8 | `get_position`, `go_to_location`, `orbit_location` 🆕, `set_yaw` 🆕, `reposition` 🆕 |
| **Mission Management** | 9 | `initiate_mission`, `upload_mission` 🆕, `download_mission` 🆕, `set_current_waypoint` 🆕 |
| **Telemetry** | 7 | `get_health`, `get_speed`, `get_attitude`, `get_gps_info`, `get_armed` |
| **Parameter Management** | 3 | `get_parameter`, `set_parameter`, `list_parameters` |

**See [STATUS.md](STATUS.md) for complete tool list and descriptions.**

### Recent Updates
- ✅ **Nov 16, 2025**: v1.2.1 patch - improved error handling based on comprehensive testing (mission validation, orbit detection, battery fallback)
- ✅ **Nov 16, 2025**: v1.2.0 near complete - added 7 tools for advanced navigation & missions (orbit, yaw, reposition, mission mgmt)
- ✅ **Nov 16, 2025**: v1.2.0 development - added 3 parameter management tools (get/set/list params)
- ✅ **Nov 16, 2025**: Documentation cleanup - removed 4 redundant files, consolidated roadmap
- ✅ **Nov 12, 2025**: v1.1.0 released with 15 new tools - critical safety features, health checks, advanced telemetry

## Configuration

Create a `.env` file in the project root:

```bash
# Drone connection details
MAVLINK_ADDRESS=<your-drone-ip>  # Your drone IP address
MAVLINK_PORT=14540               # MAVLink port (default: 14540)
```

For the AI agent, create `examples/fastagent.secrets.yaml`:

```yaml
openai:
    api_key: sk-your-key-here
```

## Safety Guidelines

⚠️ **CRITICAL SAFETY RULES:**

1. **Always maintain visual line of sight** with your drone
2. **Check battery level** before flight
3. **Verify GPS lock** before arming (wait for "global position ok")
4. **Test in open area** away from people, buildings, and obstacles
5. **Have manual RC override ready** at all times
6. **Start with low altitudes** (3-5 meters) for testing
7. **Check local regulations** and flight restrictions
8. **Never fly in bad weather** (wind, rain, low visibility)

### Pre-Flight Checklist:
- [ ] Drone battery charged
- [ ] Propellers secure
- [ ] Clear flight area
- [ ] GPS lock confirmed
- [ ] RC transmitter on and ready
- [ ] Network connection to drone stable
- [ ] Emergency landing plan prepared

### Emergency Procedures:
- **Loss of Connection**: Drone should RTL (Return to Launch) automatically
- **Unstable Flight**: Command immediate landing or use RC override
- **Network Issues**: Use RC transmitter for manual control

## Development

This project uses `uv` for dependency management:

```bash
# Install dependencies
uv sync

# Run the server
uv run src/server/mavlinkmcp.py

# Run the example agent
uv run examples/example_agent.py

# Add a new dependency
uv add package-name
```

## Contributing

Contributions welcome! Please:

1. Fork the repository
2. Create a feature branch
3. Make your changes
4. Submit a pull request

## License

MIT License - see [LICENSE](LICENSE) file for details.

## Acknowledgments

- Original project by [Ion Gabriel](https://github.com/ion-g-ion/MAVLinkMCP)
- Built with [MAVSDK](https://mavsdk.mavlink.io/)
- Uses [Model Context Protocol](https://modelcontextprotocol.io/)

## Getting API Keys

### OpenAI API Key (for GPT-4o-mini, GPT-4o, etc.)

1. **Create an OpenAI account:**
   - Go to [https://platform.openai.com/signup](https://platform.openai.com/signup)
   - Sign up with your email or Google/Microsoft account

2. **Add billing information:**
   - Navigate to [https://platform.openai.com/account/billing](https://platform.openai.com/account/billing)
   - Add a payment method (required for API access)
   - Consider setting usage limits to control costs

3. **Create an API key:**
   - Go to [https://platform.openai.com/api-keys](https://platform.openai.com/api-keys)
   - Click **"Create new secret key"**
   - Give it a name (e.g., "MAVLink MCP")
   - **Copy the key immediately** - you won't be able to see it again!
   - Key format: `sk-proj-...` or `sk-...`

4. **Add to your project:**
   ```bash
   nano examples/fastagent.secrets.yaml
   ```
   ```yaml
   openai:
       api_key: sk-proj-your-actual-key-here
   ```

**Pricing:** Pay-as-you-go. GPT-4o-mini costs ~$0.15 per 1M input tokens. Check current pricing at [https://openai.com/api/pricing/](https://openai.com/api/pricing/)

### Anthropic API Key (for Claude)

1. **Create an Anthropic account:**
   - Go to [https://console.anthropic.com/](https://console.anthropic.com/)
   - Sign up with your email

2. **Add billing:**
   - Navigate to **Settings → Billing**
   - Add a payment method
   - Purchase credits (minimum $5)

3. **Create an API key:**
   - Go to **Settings → API Keys**
   - Click **"Create Key"**
   - Give it a name (e.g., "MAVLink MCP")
   - **Copy the key immediately** - you won't be able to see it again!
   - Key format: `sk-ant-...`

4. **Add to your project:**
   ```bash
   nano examples/fastagent.secrets.yaml
   ```
   ```yaml
   anthropic:
       api_key: sk-ant-your-actual-key-here
   ```

**Pricing:** Prepaid credits. Claude 3.5 Sonnet costs ~$3 per 1M input tokens. Check current pricing at [https://www.anthropic.com/pricing](https://www.anthropic.com/pricing)

### Security Notes

- **Never commit API keys to Git** - the `.gitignore` is configured to protect `fastagent.secrets.yaml`
- **Keep keys private** - treat them like passwords
- **Set spending limits** - both platforms allow budget controls
- **Rotate keys regularly** - especially if you suspect they've been exposed
- **Use separate keys** - for development vs production

## Troubleshooting

### ModuleNotFoundError: No module named 'mcp_agent'

If you see this error when running the agent:
```
ModuleNotFoundError: No module named 'mcp_agent.core.fastagent'
```

**Solution 1 - Get the latest code with the fix:**
```bash
cd MAVLinkMCP

# Pull the latest code with updated imports
git pull origin main

# Reinstall dependencies
uv sync

# Try again
uv run examples/example_agent.py
```

**Solution 2 - If you still have issues:**
```bash
cd MAVLinkMCP

# Remove the lock file
rm uv.lock

# Reinstall with latest versions
uv sync --upgrade

# Verify installation
uv run python -c "import mcp_agent; print('mcp_agent installed successfully')"
```

### Warning about deprecated dev-dependencies

If you see:
```
warning: The `tool.uv.dev-dependencies` field is deprecated
```

This warning is harmless but to fix it, run:
```bash
git pull origin main  # Get latest pyproject.toml with the fix
uv sync
```

### Connection to drone fails

**Problem: "Failed to connect to drone"**

1. **Check drone is reachable:**
   ```bash
   ping <your-drone-ip>
   ```

2. **Verify .env file:**
   ```bash
   cat .env
   # Should show your drone's IP, port, and protocol
   ```

3. **Check port accessibility:**
   ```bash
   nc -zv <your-drone-ip> <port>
   ```

4. **Verify environment variables are loaded:**
   ```bash
   uv run python -c "import os; from dotenv import load_dotenv; load_dotenv(); print(os.getenv('MAVLINK_ADDRESS'), os.getenv('MAVLINK_PORT'), os.getenv('MAVLINK_PROTOCOL'))"
   ```

5. **Check firewall settings** (may need to allow TCP/UDP traffic)

**Problem: "Waiting for global position estimate" (hangs)**

1. Ensure drone has GPS fix (needs clear sky view)
2. Wait 1-2 minutes for GPS satellites to lock
3. Check drone's GPS module is functioning

### Flight Issues

**Problem: "Arming failed"**

Solutions:
1. Check all pre-arm checks pass
2. Verify GPS lock is obtained
3. Ensure drone is in a safe state
4. Check battery voltage is sufficient

**Problem: "Offboard mode failed to start"**

Solutions:
1. Ensure drone is armed first
2. Check drone firmware supports offboard mode
3. Verify RC transmitter is on (some drones require this)

### Getting Debug Logs

Enable detailed logging:
```bash
export MAVSDK_LOG_LEVEL=DEBUG
uv run src/server/mavlinkmcp.py
```

### API Key not working

1. **Verify secrets file exists:**
   ```bash
   ls -la examples/fastagent.secrets.yaml
   ```

2. **Check file contents:**
   ```bash
   cat examples/fastagent.secrets.yaml
   # Should show your API key (keep it private!)
   ```

3. **Verify proper YAML formatting:**
   - Use spaces, not tabs for indentation
   - Ensure proper key structure as shown in the guide

## Testing Without a Real Drone

For development and testing, you can use a **simulated drone** with PX4 SITL (Software In The Loop):

```bash
# Install PX4 SITL simulator (separate terminal)
# Then configure .env for local simulator:
MAVLINK_ADDRESS=127.0.0.1
MAVLINK_PORT=14540
MAVLINK_PROTOCOL=udp
```

For PX4 SITL installation and setup, see:
- [PX4 SITL Documentation](https://docs.px4.io/main/en/simulation/)
- [PX4 User Guide](https://docs.px4.io/main/en/)

## Example Flight Session

Here's a complete example session using the interactive client:

```bash
# Start the interactive client
uv run examples/interactive_client.py

# You see: Connected to drone at tcp://your-drone-ip:port!
# You see: GPS lock acquired!

🎮 Command> position
📍 Position:
   Latitude:  33.645862°
   Longitude: -117.842751°
   Altitude:  0.0m

🎮 Command> arm
🔧 Arming drone...
✅ Drone armed!

🎮 Command> battery
🔋 Battery: 98.5%

🎮 Command> takeoff 10
🚁 Taking off to 10.0m...
✅ Takeoff command sent! Target altitude: 10.0m

# Wait for takeoff to complete (5-15 seconds)

🎮 Command> position
📍 Position:
   Latitude:  33.645862°
   Longitude: -117.842751°
   Altitude:  9.8m

🎮 Command> mode
✈️  Flight Mode: OFFBOARD

🎮 Command> land
🛬 Landing drone...
✅ Landing command sent!

# Monitor descent
🎮 Command> position
📍 Position:
   Altitude:  2.5m

🎮 Command> position
📍 Position:
   Altitude:  0.02m

🎮 Command> quit
👋 Goodbye!
```

## Support and Resources

### Project Resources
- 🐛 [Report Issues](https://github.com/PeterJBurke/MAVLinkMCP/issues)
- 💬 [Discussions](https://github.com/PeterJBurke/MAVLinkMCP/discussions)
- 📊 [Status & Roadmap](STATUS.md)

### External Documentation
- 📖 [MCP Protocol](https://modelcontextprotocol.io/introduction)
- 🚁 [MAVSDK Documentation](https://mavsdk.mavlink.io/main/en/)
- 🛸 [PX4 User Guide](https://docs.px4.io/main/en/)
- 📡 [MAVLink Protocol](https://mavlink.io/en/)
