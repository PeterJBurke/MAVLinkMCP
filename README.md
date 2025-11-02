# MAVLink MCP Server

A Python-based Model Context Protocol (MCP) server for AI-powered drone control. Connect LLMs to MAVLink-enabled drones (PX4, ArduPilot) for natural language flight control.

## Features

- 🤖 **AI-Powered Control**: Use natural language to command drones via GPT-4, Claude, or other LLMs
- 🚁 **MAVLink Compatible**: Works with PX4, ArduPilot, and other MAVLink drones
- 🔧 **MCP Protocol**: Standard Model Context Protocol for tool integration
- 📡 **Network/Serial Support**: Connect via UDP, TCP, or serial ports
- 🛡️ **Safe Configuration**: Secure handling of connection credentials

## Prerequisites

- **Python 3.10+**
- **uv** package manager ([install here](https://github.com/astral-sh/uv))
- **MAVLink-compatible drone or simulator** with network access (IP address and port)
  - Connection details will be configured in `.env` file
- **OpenAI or Anthropic API key** (for AI agent control)

## Quick Start

### 1. Install uv (if not already installed)

```bash
curl -LsSf https://astral.sh/uv/install.sh | sh
```

### 2. Clone and Install

```bash
git clone https://github.com/PeterJBurke/MAVLinkMCP.git
cd MAVLinkMCP
uv sync
```

### 3. Configure Drone Connection

Copy the example configuration:
```bash
cp .env.example .env
```

Edit `.env` with your drone's IP and port:
```bash
MAVLINK_ADDRESS=your.drone.ip.address
MAVLINK_PORT=14540
```

### 4. Run the AI Agent

```bash
uv run examples/example_agent.py
```

Then use natural language commands:
- "Arm the drone"
- "Take off to 5 meters"
- "What is the current position?"
- "Land the drone"

## Usage

### Method 1: AI Agent (Recommended)

The AI agent provides natural language control:

```bash
# Configure API keys in examples/fastagent.secrets.yaml
uv run examples/example_agent.py
```

### Method 2: Direct MCP Server

For integration with other MCP clients:

```bash
uv run src/server/mavlinkmcp.py
```

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

- **[Deployment Guide](DEPLOYMENT_GUIDE.md)** - Complete setup and usage instructions
- **[Examples README](examples/README.md)** - Example agent documentation
- **[MCP Protocol](https://modelcontextprotocol.io/)** - Model Context Protocol docs
- **[MAVSDK](https://mavsdk.mavlink.io/)** - MAVLink SDK documentation

## Available Tools

The MCP server exposes these tools for drone control:

| Category | Tools |
|----------|-------|
| **Flight Control** | `arm_drone`, `takeoff`, `land`, `move_to_relative` |
| **Telemetry** | `get_position`, `get_flight_mode`, `get_imu`, `print_status_text` |
| **Missions** | `initiate_mission`, `print_mission_progress` |

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

⚠️ **IMPORTANT**: Always follow drone safety protocols:

- Maintain visual line of sight
- Verify GPS lock before flight
- Have manual RC override ready
- Test in open areas away from people
- Check local regulations and weather
- Start with low altitudes (3-5m)

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

## Support

- 📖 [Full Deployment Guide](DEPLOYMENT_GUIDE.md)
- 🐛 [Report Issues](https://github.com/PeterJBurke/MAVLinkMCP/issues)
- 💬 [Discussions](https://github.com/PeterJBurke/MAVLinkMCP/discussions)
