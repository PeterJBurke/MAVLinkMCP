# Emergency stop — the full override chain

The `emergency_stop` MCP tool is the **innermost and least authoritative** ring
of a four-ring chain. It depends on the MCP server, the network, and the
autopilot link all working. The outer rings do not. **Never plan a flight on
the assumption that the software e-stop will be available.**

Rings, outermost (most authoritative) first:

| Ring | Mechanism | Depends on | Beats |
|---|---|---|---|
| 1 | **RC transmitter takeover** | RC link only | everything below |
| 2 | **Ground control station (QGC/MP)** | GCS ↔ vehicle MAVLink link | server + LLM |
| 3 | **Kill the companion link / stop the service** | shell on the companion or server host | LLM |
| 4 | **`emergency_stop` MCP tool** | MCP server + LLM behaving | nothing |

---

## Ring 1 — RC takeover (primary)

A safety pilot with a bound transmitter is the primary override for any flight
outside a cage. Flip the mode switch to a pilot-controlled mode
(**Stabilize / AltHold / Loiter** on ArduPilot, **Position / Altitude /
Manual** on PX4). The autopilot obeys the RC stick input over any MAVLink
command in progress.

Requirements, checked before every real flight:

- Transmitter on, bound, and **within range** before arming.
- A mode switch is configured and the pilot has *practised* the flip.
- Throttle at **mid-stick** before switching into an altitude-holding mode —
  this is the exact hazard behind the v1 `pause_mission` crash: LOITER/AltHold
  take altitude from the throttle stick, and an unknown stick position on a
  transmitter that was not being held caused a descent to ground impact.
- `FS_THR_ENABLE` / RC-loss failsafe configured and tested.

**Kill switch semantics — know the difference:**

- **ArduPilot**: an RC channel assigned `RCx_OPTION = 31` (Motor Emergency
  Stop) stops the motors immediately, in any mode, latched while active. The
  aircraft **falls**. It is not a "land now" switch.
- **PX4**: `Kill switch` (mapped in the RC setup) does the same — motors off,
  no landing sequence.
- On both, **motor kill is not disarm**. Disarm on the ground is routine;
  disarm/kill in the air is a crash. This is exactly why the safety layer
  escalates `disarm_drone` to CRITICAL only when `in_air` is true.

Use motor kill only when a falling aircraft is safer than a flying one — e.g.
a flyaway heading toward people, or a wrapped/entangled airframe.

## Ring 2 — Ground control station

Keep QGroundControl (or Mission Planner) connected to the same vehicle for any
non-trivial flight. From the GCS you can, without the MCP server's cooperation:

- change flight mode (Land / RTL / Loiter),
- issue Return-to-Launch,
- disarm on the ground,
- trigger flight termination if configured.

Because the GCS talks MAVLink directly to the vehicle, it works even if the MCP
server is wedged, the LLM is looping, or the network to the server is down.
Note MAVLink is multi-master: the GCS and the server can both be connected, and
the **last command wins** — a GCS mode change will override a server command,
and vice versa. Do not leave an LLM issuing setpoints while you fly manually
from the GCS.

## Ring 3 — Cut the link / stop the service

If the LLM is misbehaving but the vehicle is stable, remove the server from the
loop rather than fighting it:

```bash
systemctl stop droneserver        # or: docker stop <container>
```

The offboard **stale-setpoint watchdog** matters here: if the server dies
mid-offboard, `mavsdk_server` stops re-sending setpoints and the autopilot's
own offboard-loss failsafe takes over (PX4: failsafe action; ArduPilot: GUIDED
without a target holds). Within the server, the watchdog brakes to a
zero-velocity hover if a motion setpoint is not refreshed within
`stale_timeout_s` (default 15 s). See `src/droneserver/safety/offboard_watchdog.py`.

Killing the companion-computer link is also effective: the vehicle's
**GCS-loss failsafe** (`FS_GCS_ENABLE` on ArduPilot, `NAV_DLL_ACT` on PX4)
will trigger the configured action — usually RTL or Land. Verify that
parameter is set the way you expect *before* relying on it.

## Ring 4 — the `emergency_stop` tool

```
emergency_stop(mode="land")   # DEFAULT, SAFEST: stop offboard, land here
emergency_stop(mode="rtl")    # fly home, then land
emergency_stop(mode="kill")   # CUT MOTORS — the drone falls
```

Design decisions, and why:

- **No confirmation token.** Every other CRITICAL tool needs a two-call
  round-trip; requiring one during an emergency would be a hazard, so this tool
  is tier `EMERGENCY` and executes on the first call.
- **Exempt from rate limiting**, for the same reason.
- **Still requires `control` scope**, and is **always audited**.
- `land` and `rtl` first stop offboard streaming and cancel the offboard
  watchdog — otherwise a live setpoint would keep commanding the vehicle while
  the stop is in progress.
- `kill` maps to MavSDK `action.kill()` — the software equivalent of the RC
  kill switch, with all the same consequences.

If `emergency_stop` returns `status: "failed"`, its response tells the caller
to escalate to the out-of-band chain immediately. **Escalate to Ring 1.**

## Pre-flight checklist (real hardware)

- [ ] Safety pilot present, transmitter bound, mode switch practised
- [ ] Kill switch assigned and its semantics understood (motors off, not land)
- [ ] GCS connected and showing live telemetry
- [ ] RC-loss and GCS-loss failsafe parameters set and verified
- [ ] Server-side geofence configured for the actual flying site
      (`SAFETY_GEOFENCE_POLYGON`, ceiling, radius) **and** the firmware fence
      enabled (`FENCE_ENABLE=1`) — two independent layers, see
      [safety_review.md](safety_review.md) §5
- [ ] `emergency_stop` exercised in SITL against the same configuration
- [ ] Audit log path writable; verify a record appears for a test call
