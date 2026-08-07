# Emergency stop — how to make the drone stop, and what to trust

**Who this is for:** anyone who may need to stop this aircraft — a safety
pilot, an operator, or a reviewer checking that a language model flying a drone
can always be overruled. You do not need to have read the code.

## The one-paragraph version

There are four independent ways to stop the drone. They are ranked by how
little has to be working for them to succeed. The radio transmitter in a
pilot's hands needs only the radio link. The software "emergency stop" — the
one the AI itself can call — needs the network, the server, and the AI to all
be behaving, which is exactly what you cannot assume in an emergency. **So the
software stop is the innermost and weakest ring, not the primary one.** Never
plan a flight assuming it will be available.

## Terms used here

- **MAVLink** — the radio/network language ground software and autopilots speak
  to each other. Everything in this document ultimately travels over MAVLink.
- **Autopilot / flight controller** — the small computer on the aircraft that
  actually flies it (running ArduPilot or PX4 firmware). It keeps flying even
  if everything else disappears.
- **GCS (ground control station)** — conventional pilot software such as
  QGroundControl or Mission Planner, talking to the autopilot over MAVLink.
- **Companion computer** — a small computer carried on the aircraft that
  relays commands from the network to the autopilot.
- **MCP server (this project)** — the service that exposes drone commands as
  "tools" an AI model can call.
- **Failsafe** — behaviour the autopilot performs on its own when something is
  lost (radio, GPS, the ground link), e.g. return home and land.
- **Disarm** — switch the motors off. On the ground: routine. In the air: the
  aircraft falls.
- **Kill / motor emergency stop** — cut motor output immediately, in any flight
  mode. Also makes the aircraft fall. It is *not* a "land now" button.

## The four rings

Outermost is most trustworthy.

| Ring | How you stop the drone | What must still be working | Overrules |
|---|---|---|---|
| 1 | **Radio transmitter** — pilot takes over | the radio link only | everything below |
| 2 | **GCS** (QGroundControl / Mission Planner) | the GCS↔aircraft MAVLink link | the server and the AI |
| 3 | **Cut the link / stop the service** | shell access to the server or companion | the AI |
| 4 | **`emergency_stop` tool** (the AI calls it) | network + server + a cooperative AI | nothing |

---

## Ring 1 — the pilot's transmitter (primary)

For any flight outside a net or cage, a safety pilot with a bound transmitter
is the primary override. Flip the mode switch to a pilot-flown mode —
**Stabilize, AltHold or Loiter** on ArduPilot; **Position, Altitude or Manual**
on PX4 — and the autopilot follows the sticks instead of whatever command was
in progress.

Check before every flight:

- Transmitter on, bound to the aircraft, and **in range** before arming.
- A mode switch is configured, and the pilot has *practised* the flip.
- **Throttle stick at mid-position before switching into an altitude-holding
  mode.** This is not a detail. It is the hazard that caused a real crash in
  this project: modes such as LOITER and AltHold take their altitude target
  from the throttle stick, and a transmitter lying on a table with the stick
  down commands a descent. The aircraft descended from 25 m into the ground.
  That incident is why this server's `pause_mission` holds position in GUIDED
  mode (which needs no stick input) instead of switching to LOITER.
- Radio-loss failsafe configured and tested (`FS_THR_ENABLE` on ArduPilot).

### Kill switches — know what they actually do

| Firmware | How it is set up | What happens |
|---|---|---|
| ArduPilot | an RC channel with `RCx_OPTION = 31` (Motor Emergency Stop) | motors stop immediately, in any mode, and stay stopped while the switch is held |
| PX4 | "Kill switch" in the RC setup | motors stop immediately |

On both: **motor kill is not the same as disarm, and neither is a landing.**
The aircraft drops. Use it only when a falling aircraft is safer than a flying
one — a flyaway heading toward people, or a tangled airframe.

## Ring 2 — the ground control station

Keep QGroundControl (or Mission Planner) connected for any non-trivial flight.
From it you can, without the MCP server's cooperation:

- change flight mode (Land, Return-to-Launch, Loiter),
- command Return-to-Launch,
- disarm once on the ground,
- trigger flight termination, if configured.

This works even if the server is wedged, the AI is looping, or the network to
the server is down, because the GCS talks to the autopilot directly.

**One caveat:** MAVLink allows several controllers at once, and the autopilot
obeys whichever spoke last. If you are flying manually from the GCS while an AI
is still issuing commands, the two will fight. Stop the server (Ring 3) rather
than race it.

## Ring 3 — take the server out of the loop

If the AI is misbehaving but the aircraft is stable, remove the server rather
than argue with it:

```bash
systemctl stop droneserver          # or: docker stop <container>
```

Two things then protect you, and it is worth knowing which is which:

- **This server's own watchdog.** In "offboard" flight the aircraft follows a
  continuously repeated instruction such as *keep moving north at 2 m/s*. If
  that instruction is not refreshed within a timeout (15 s by default), the
  server commands a stationary hover. See
  `src/droneserver/safety/offboard_watchdog.py`.
- **The autopilot's own ground-link failsafe.** If the link to the ground
  disappears entirely, the autopilot acts by itself — usually Return-to-Launch
  or Land (`FS_GCS_ENABLE` on ArduPilot, `NAV_DLL_ACT` on PX4). Confirm that
  parameter is set the way you expect **before** you rely on it.

## Ring 4 — the `emergency_stop` tool

This is the stop the AI itself can invoke.

```
emergency_stop(mode="land")   # DEFAULT, SAFEST: stop offboard control, land here
emergency_stop(mode="rtl")    # fly home, then land
emergency_stop(mode="kill")   # CUT MOTORS — the aircraft falls
```

Design decisions, and why:

- **It needs no confirmation step.** Every other dangerous command in this
  server requires a two-call handshake: the first call returns a one-time token
  plus a plain statement of the consequence, and only a second call quoting
  that token executes. Requiring that during an emergency would itself be
  dangerous, so this tool is exempt.
- **It is exempt from rate limiting** for the same reason.
- **It still requires a control-scoped API key, and it is always logged.**
- `land` and `rtl` first stop the repeating offboard instruction and cancel the
  watchdog — otherwise a stale instruction would keep commanding the aircraft
  while the stop was in progress.
- `kill` is the software equivalent of the RC kill switch, with all the same
  consequences.

If `emergency_stop` returns a failure, its response says to escalate. **Escalate
to Ring 1.**

## Pre-flight checklist (real hardware)

- [ ] Safety pilot present, transmitter bound, mode switch practised
- [ ] Kill switch assigned, and its meaning understood (motors off, not land)
- [ ] Throttle stick at mid-position before any altitude-hold mode is selected
- [ ] GCS connected and showing live telemetry
- [ ] Radio-loss and ground-link-loss failsafes set and tested
- [ ] Geofence set on **both** layers — the server's own fence
      (`SAFETY_GEOFENCE_*`) and the autopilot's (`FENCE_ENABLE=1`); see
      [safety_review.md](safety_review.md) §5 for why two are used
- [ ] `emergency_stop` exercised in simulation against this exact configuration
- [ ] Audit log path writable; confirm a record appears for a test call
