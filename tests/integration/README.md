# SITL integration tests

End-to-end tests that run the real droneserver against a **throwaway ArduPilot
SITL in docker** and drive it through the MCP protocol, exactly like an LLM
client would.

## Prerequisites

- docker (daemon running, current user allowed to use it)
- network access on first run (the image downloads the pinned SITL binary)
- dev dependencies installed: `uv sync`

## Running

Integration tests are marked `@pytest.mark.sitl` and **excluded from the
default `pytest` run and from CI** (SITL-in-CI is a later phase):

```bash
# the routine SITL sweep (~13 min) - excludes the long demonstration
uv run pytest -m "sitl and not longmission" tests/integration

# unit tests only (sitl and longmission both excluded by default addopts)
uv run pytest

# the >10-minute demonstration mission on its own (~40 min, writes
# docs/long_mission_demo.md)
uv run pytest -m longmission tests/integration/test_long_mission_demo.py
```

> **Note:** the demonstration is marked BOTH `sitl` and `longmission`, so a
> bare `-m sitl` will pull it in and add ~40 minutes. Use
> `-m "sitl and not longmission"` for routine sweeps.

First run builds the docker image (`droneserver-sitl-arducopter:4.5.7`);
subsequent runs reuse it. Expect roughly 30-90 s for the SITL to become
flight-ready (EKF converged, prearm checks passing) per test module.

## How it works

Module-scoped fixture chain in `conftest.py` (fresh SITL per test module):

1. `sitl` - builds/starts the container from `docker/ardupilot-sitl/Dockerfile`
   (ArduCopter 4.5.7 prebuilt SITL binary from firmware.ardupilot.org, home at
   the CMAC test field, MAVLink on tcp 5760/5762 mapped to ephemeral localhost
   ports). Waits via pymavlink on the second port for: heartbeat, 3D GPS fix,
   EKF absolute position, and the autopilot prearm checks reporting healthy.
2. `droneserver_url` - starts `python -m droneserver.server` as a subprocess
   with `MAVLINK_ADDRESS/PORT/PROTOCOL` pointed at the container and an
   isolated `FLIGHT_LOG_DIR`; server log lands in the pytest tmp dir.
3. `drone_tools` - `MCPToolClient` (see `mcp_client.py`): a synchronous MCP
   client helper that opens a short-lived HTTP/SSE session per tool call and
   returns the tool's dict result. The fixture polls `get_armed` until the
   server's background MAVLink connection is up.

Phase 2 tool tests should take `drone_tools` and call tools directly:

```python
result = drone_tools.call("takeoff", takeoff_altitude=10.0, timeout=180)
assert result["status"] == "success"
```

## Gotchas

- ArduPilot serial-over-TCP accepts **one client per port**: 5760 belongs to
  the droneserver, 5762 to the readiness probe. Don't attach anything else.
- The container passes `--serial0 tcp:0` explicitly because the binary default
  is `tcp:0:wait`, which would block the whole simulation (and EKF
  convergence) until a client connects.
- ArduPilot streams no telemetry until asked: the probe sends
  `REQUEST_DATA_STREAM` (and re-sends it if the first request is lost during
  boot).
- These tests only ever talk to the local throwaway container. Never point
  this harness at a real drone, the persistent tailnet SITLs, or production.
