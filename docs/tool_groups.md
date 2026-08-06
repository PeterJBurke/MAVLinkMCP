# Tool grouping rationale

The server exposes **grouped tools** rather than a 1:1 mirror of the ~240
client-side MavSDK methods (paper §4.4). This document records the rationale
per group — the design decisions reviewers asked us to make explicit.

## Grouping principles

1. **One tool = one pilot intent.** Tools map to things an operator would ask
   for ("take off", "fly north at 2 m/s", "upload a fence"), not to SDK
   plumbing. This keeps the tool list meaningful to an LLM choosing among
   tools mid-conversation.
2. **Flat, self-describing schemas beat conditional mega-tools.** Where two
   SDK methods share one intent and one parameter shape, they merge into a
   single tool (e.g. attitude angle vs. rate via a `mode` parameter). Where
   parameters would become conditionally-required unions (only valid for some
   values of an `action` argument), we split tools instead — LLMs fill flat
   schemas far more reliably.
3. **Safety semantics live in the tool, not the caller.** Anything the SDK
   leaves implicit but an autonomous caller can get wrong (streamed setpoints
   that never expire, fences that upload but don't enforce) is made explicit
   in the tool contract: parameters, descriptions, and server-side guards.
4. **Context economy.** Every tool costs prompt tokens in every conversation.
   Merging is preferred whenever it does not violate 1-3.

## v1 groups (retro-documented)

- **action** (15 tools): arming, takeoff/land, kill, RTL, guided navigation
  (goto/relative/reposition/yaw/speed), flight-mode setting, and flight
  monitoring. One tool per command intent; monitoring tools (`check_arrival`,
  `monitor_flight`) exist so the LLM can poll long-running motion instead of
  blocking a tool call.
- **telemetry** (17 tools): read-only state, one tool per telemetry topic
  (position, battery, health, GPS, attitude, ...). Kept separate rather than
  one `get_telemetry(topic)` tool so each response schema is stable and
  documented; read-only grouping also maps cleanly onto future per-scope
  authorization (telemetry-only API keys).
- **mission** (10 tools): upload/download/start/pause/resume/clear and
  progress queries — the autopilot's mission state machine surfaced 1:1,
  because mission steps are distinct intents with distinct failure modes.
- **param** (3 tools): get/set/list autopilot parameters. The escape hatch
  for firmware-specific configuration (e.g. `FENCE_ENABLE`).

## geofence (v2, 2 tools)

Covers 2/2 MavSDK `geofence` methods.

| Tool | MavSDK methods |
|---|---|
| `upload_geofence(polygons, circles)` | `upload_geofence` |
| `clear_geofence()` | `clear_geofence` |

**Why two tools, not one `geofence(action=...)`:** the two intents share no
parameters — merging them would make the geometry arguments conditionally
required (invalid when `action="clear"`), the exact schema shape LLMs
mis-fill most often (principle 2). Keeping `clear_geofence` separate also
lets the Phase 3 criticality tiers treat it as safety-relevant (removing
containment) independently of uploads.

**Safety semantics made explicit (principle 3):** on ArduPilot an uploaded
fence is inert until `FENCE_ENABLE=1`; the tool result and description say so
and point at `set_parameter`. Geometry validation (≥3 points, lat/lon/radius
ranges, inclusion/exclusion vocabulary) happens server-side with actionable
error messages.

## offboard (v2, 8 tools)

Covers 13/13 MavSDK `offboard` methods:

| Tool | MavSDK methods |
|---|---|
| `offboard_control(action=start\|stop\|status)` | `start`, `stop`, `is_active` |
| `offboard_set_position_ned(..., velocity?, acceleration?)` | `set_position_ned`, `set_position_velocity_ned`, `set_position_velocity_acceleration_ned` |
| `offboard_set_position_global` | `set_position_global` |
| `offboard_set_velocity_ned` | `set_velocity_ned` |
| `offboard_set_velocity_body` | `set_velocity_body` |
| `offboard_set_attitude(mode=angle\|rate)` | `set_attitude`, `set_attitude_rate` |
| `offboard_set_acceleration_ned` | `set_acceleration_ned` |
| `offboard_set_actuator_control` | `set_actuator_control` |

**The one-shot vs. streaming problem.** Offboard setpoints are streams: the
autopilot expects a continuous feed, and mavsdk_server re-sends the *last*
setpoint indefinitely. An MCP tool call is one-shot. The design resolves this
by making persistence explicit and guarded:

- *Set-then-start*: setpoint tools only stage/stream data; motion begins at
  `offboard_control("start")` (and `start` refuses to run before any setpoint
  was staged — clearer than the raw SDK error).
- *Stale-setpoint watchdog* (`droneserver.safety.offboard_watchdog`): motion
  setpoints (velocity/attitude/acceleration/actuator) take a
  `stale_timeout_s` (default 15 s, bounded 1–120 s). If the LLM never follows
  up, the server auto-brakes to a zero-velocity hover at the current heading
  and records it; `offboard_control("status")` exposes the watchdog state.
  Position setpoints are self-terminating (the vehicle stops at the target),
  so they clear the watchdog instead.

**Why 8 tools, not one `offboard_setpoint(type=...)`:** 10 setpoint kinds
share almost no parameters — a single tool would be a union schema with ~20
mostly-invalid fields per call (principle 2). Merges were made exactly where
shapes coincide: the three position-NED variants collapse into one tool with
optional feed-forward objects; attitude angle/rate share 4 identically-shaped
parameters and differ only in units, expressed as `mode`. Frames stay
separate (`_ned` vs `_body`, local vs global) because their axis semantics
differ — a wrong-frame guess is a flight-safety error, not a retry.

**Firmware honesty:** all of this was exercised on ArduCopter 4.5.7 SITL
(MavSDK maps offboard start to GUIDED; stop → Loiter/Hold). Per-method
observations, including the accepted-but-inert `set_actuator_control`, are in
the coverage matrix `firmware_notes` column (source:
`docs/firmware_notes.csv`). PX4 verification is pending the PX4 SITL.
