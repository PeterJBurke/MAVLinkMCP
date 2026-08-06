"""
Simple MCP Client for MAVLink Drone Control

This example shows how to interact with the MAVLink MCP server
using the mcp-agent package.
"""

import asyncio

from mcp_agent.app import MCPApp

# Create the application
app = MCPApp(name="MAVLink Drone Controller", description="AI-powered drone control interface")


async def main():
    """
    Main function to run the drone control agent.

    Note: The MAVLink MCP server is automatically started by the configuration
    in examples/fastagent.config.yaml
    """
    print("MAVLink Drone Control Agent")
    print("=" * 50)
    print("\nConnecting to MAVLink MCP server...")
    print("The server will connect to your drone using settings from .env")
    print("\nAvailable commands:")
    print("  - arm_drone()")
    print("  - takeoff(altitude)")
    print("  - get_position()")
    print("  - move_to_relative(lr, fb, altitude, yaw)")
    print("  - land()")
    print("  - get_flight_mode()")
    print("\nType 'help' for more commands or 'exit' to quit.")
    print("=" * 50)

    # For now, this is a basic example
    # The full agent integration with LLM is configured via fastagent.config.yaml
    # and launched separately

    print("\nTo use the full AI-powered interface:")
    print("1. Ensure .env is configured with your drone's IP and port")
    print("2. Ensure examples/fastagent.secrets.yaml has your API key")
    print("3. The MCP server will auto-start when the agent connects")

    await asyncio.sleep(1)
    print("\nExample setup complete!")


if __name__ == "__main__":
    asyncio.run(main())
