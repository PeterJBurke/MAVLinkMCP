"""MCP tool registration.

Importing this package imports every tool module, which registers the tools on
the shared :data:`droneserver.app.mcp` instance as a side effect of their
``@mcp.tool()`` decorators.

Grouping (v1 parity - 45 tools):
- ``action``:    flight control & navigation (15)
- ``telemetry``: state read-outs (17)
- ``mission``:   mission upload/monitor/control (10)
- ``param``:     autopilot parameters (3)
"""

from droneserver.tools import action, mission, param, telemetry

__all__ = ["action", "mission", "param", "telemetry"]
