"""MCP tool registration.

Importing this package imports every tool module, which registers the tools on
the shared :data:`droneserver.app.mcp` instance as a side effect of their
``@mcp.tool()`` decorators.

Grouping (see docs/tool_groups.md for the per-group rationale):
- ``action``:      flight control & navigation (v1 + v2 completion)
- ``telemetry``:   state read-outs (v1 + extended/rate v2)
- ``mission``:     mission upload/monitor/control (v1 + v2 completion)
- ``param``:       autopilot parameters
- ``geofence``:    fence upload/clear
- ``offboard``:    continuous setpoint control
- ``camera``:      capture/settings/storage/zoom/tracking
- ``gimbal``:      discovery/control/pointing
- ``mission_raw``: QGC plan import, rally, raw protocol
- ``logs``:        onboard flight logs
- ``system``:      info/identification/status-text/mavlink-timeout
- ``safety_ops``:  calibration + failure injection
- ``peripherals``: manual control, follow-me, gripper/winch, transponder,
                   tune, mocap, RTK
- ``filesystem``:  MAVLink FTP + (tier-critical) autopilot shell
- ``emergency``:   emergency_stop (tier EMERGENCY, see docs/estop.md)
"""

from droneserver.tools import (
    action,
    camera,
    emergency,
    filesystem,
    geofence,
    gimbal,
    logs,
    mission,
    mission_raw,
    offboard,
    param,
    peripherals,
    safety_ops,
    system,
    telemetry,
)

__all__ = [
    "action",
    "camera",
    "emergency",
    "filesystem",
    "geofence",
    "gimbal",
    "logs",
    "mission",
    "mission_raw",
    "offboard",
    "param",
    "peripherals",
    "safety_ops",
    "system",
    "telemetry",
]
