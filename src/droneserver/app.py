"""The FastMCP application instance.

Kept in its own module so tool modules can ``from droneserver.app import mcp``
without circular imports; ``droneserver.tools`` imports the tool modules for
their registration side effects.

Every tool is registered through :class:`SafeFastMCP`, which wraps it in the
Phase 3 safety pipeline (:mod:`droneserver.safety.middleware`). There is no
registration path that bypasses the guard - that is the point: a tool added
without touching the safety code is still validated, tiered, and audited.
"""

from mcp.server.fastmcp import FastMCP

from droneserver.mavlink.connection import app_lifespan
from droneserver.safety.middleware import guard


class SafeFastMCP(FastMCP):
    """FastMCP whose ``@tool()`` decorator applies the safety guard."""

    def tool(self, *args, **kwargs):
        decorate = super().tool(*args, **kwargs)

        def register(fn):
            return decorate(guard(fn))

        return register


mcp = SafeFastMCP("MAVLink MCP", lifespan=app_lifespan)
