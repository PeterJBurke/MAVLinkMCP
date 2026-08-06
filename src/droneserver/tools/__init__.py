"""MCP tool registration.

Importing this package imports every tool module, which registers the tools on
the shared :data:`droneserver.app.mcp` instance as a side effect of their
``@mcp.tool()`` decorators.

Grouping (see docs/tool_groups.md for the per-group rationale):
- ``action``:      flight control & navigation (15, v1)
- ``telemetry``:   state read-outs (17, v1)
- ``mission``:     mission upload/monitor/control (10, v1)
- ``param``:       autopilot parameters (3, v1)
- ``geofence``:    fence upload/clear (2, v2)
- ``offboard``:    continuous setpoint control (8, v2)
- ``camera``:      capture/settings/storage/zoom/tracking (6, v2)
- ``gimbal``:      discovery/control/pointing (3, v2)
- ``mission_raw``: QGC plan import, rally, raw protocol (4, v2)
- ``logs``:        onboard flight logs (1, v2)
"""

from droneserver.tools import (
    action,
    camera,
    geofence,
    gimbal,
    logs,
    mission,
    mission_raw,
    offboard,
    param,
    telemetry,
)

__all__ = [
    "action",
    "camera",
    "geofence",
    "gimbal",
    "logs",
    "mission",
    "mission_raw",
    "offboard",
    "param",
    "telemetry",
]
