"""The FastMCP application instance.

Kept in its own module so tool modules can ``from droneserver.app import mcp``
without circular imports; ``droneserver.tools`` imports the tool modules for
their registration side effects.
"""

from mcp.server.fastmcp import FastMCP

from droneserver.mavlink.connection import app_lifespan

mcp = FastMCP("MAVLink MCP", lifespan=app_lifespan)
