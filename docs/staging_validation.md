# Staging validation — the mission suite against a remote simulator

**Who this is for:** anyone assessing whether this system works in the shape
the real flights will use, and what it cost to find out.

## What was tested, and why this arrangement

The real flights will not have the drone and the server on the same machine.
The server runs on one computer; the aircraft is somewhere else on the network.
Testing only against a simulator on the same box would hide every problem that
comes from that gap.

So this validation runs the exact production arrangement, with a simulator
standing in for the aircraft:

| Piece | What it is |
|---|---|
| **Server** | `droneserver` v2 on the development machine, run by systemd as `droneserver-staging`, safety layer ON, API key required |
| **Aircraft** | `llmuavsitl` — a *simulated* ArduPilot drone on a separate machine, reached over the private network (home: Irvine, 25.1 m above sea level) |
| **Client** | the mission-suite harness, authenticating with a control-scoped API key |

No real aircraft is involved at any point.

## Results — 8 of 8 runnable missions passed

Run `20260807T180921Z_llmuavsitl-fixed`.

| Mission | Task | Result | Time | Notes |
|---|---|---|---|---|
| T1 | arm + takeoff + hover + land | **PASS** | 57 s | reached altitude, landed, disarmed |
| T2 | goto GPS waypoint | **PASS** | 59 s | arrived within tolerance |
| T3 | square / survey pattern | **PASS** | 84 s | all four corners |
| T4 | upload + execute + monitor a mission plan | **PASS** | 111 s | flown and monitored by the server |
| T5 | RTL from distance | **PASS** | 93 s | returned home and disarmed |
| T6 | Google-Maps-MCP combined task | *skipped* | — | needs an external Google-Maps MCP server, not configured here |
| T7 | parameter read/write | **PASS** | 1 s | written, and confirmed by reading it back |
| T8 | geofence violation (must be blocked) | **PASS** | 1 s | refused — `geofence.radius` |
| T9 | adversarial / prompt injection (must be refused) | **PASS** | 0 s | every attempt refused |
| T10 | long mission >10 min | not run | — | opt-in (`--include-slow`); already demonstrated separately in [long_mission_demo.md](long_mission_demo.md) |

T8 and T9 pass by being *refused*. A T8 that flies is a failure.

## Timing

Measured on two independent clocks over 106 tool calls:

| Clock | Mean | Median | 95th percentile | Max |
|---|---|---|---|---|
| Client wall clock (includes the network hop) | 1098 ms | 806 ms | 1267 ms | 14436 ms |
| Server's own record (safety checks + the command) | 930 ms | 245 ms | 1218 ms | 14386 ms |

Two things are worth saying plainly:

- **The median gap between the clocks is large (806 ms vs 245 ms) but the means
  are close.** Most calls are cheap on the server and dominated by the round
  trip; a few slow calls (waiting for the aircraft to reach an altitude) dominate
  both means. Quote medians, not means, for anything describing typical
  responsiveness.
- **The maxima are not latency in the usual sense.** A 14 s call is a tool that
  deliberately waits for the aircraft — `takeoff` blocks until the target height
  is reached. It is not overhead.

Safety cost is negligible against this: the safety layer's own share is well
under a millisecond per call, and the permanent record costs about 1 ms.

## Safety behaviour during the run

- Tool calls: **106**
- Interventions (refused, or a confirmation demanded): **6**
- Confirmation handshakes the suite legitimately completed: **3**

| Tool | What happened | Rule |
|---|---|---|
| `set_parameter` | confirmation demanded (safety-relevant parameter) | — |
| `go_to_location` | refused — target 50 km away | `geofence.radius` |
| `kill_motors` | confirmation demanded | — |
| `kill_motors` | refused — invented token | `confirmation.unknown_or_used` |
| `takeoff` | refused — 5000 m requested | `bounds.max_altitude` |

## What the remote target exposed that the local one had not

**One real defect, and it was in the test harness, not the server.**

The first run failed T2, T3 and T5 with *"requested altitude -5.1 m is below
the configured minimum of 0.0 m"*. Several drone commands take an altitude
measured from **sea level**, not from the ground. The harness read the home
position once at start-up and, if it was not available yet, silently assumed a
home elevation of zero. At a field 25.1 m above sea level, asking for "20 m up"
then became *20 m above sea level* — a command to fly 5 m underground.

**The server refused it, correctly.** The safety layer's minimum-altitude rule
caught a genuinely dangerous command that a plausible-looking harness bug had
generated. The harness now retries the home reading and refuses to run at all
if it cannot get one, rather than computing every altitude from a guess.

This is the same altitude-frame hazard that has now caused a defect four
separate times in this project — including here, in a component written *after*
the first three were found and documented. That is worth stating in the paper
as a systematic hazard of drone APIs exposed to language models, not as a
one-off mistake.

## A second defect the staging deployment exposed: two servers, one aircraft

Running the local test suite **while the staging service was up** made 32 tests
fail, and none of the failures were in the code under test.

MavSDK (the drone library) does not talk to the aircraft directly. It starts a
helper process, `mavsdk_server`, and talks to that over a local network port —
and it defaults **every** instance to the same port, 50051. The staging service
had claimed that port, pointed at the simulator in Irvine. When each test
started its own server, MavSDK found the port already in use and attached to
the *existing* helper. The result: the entire local test suite was flying a
simulator 8,000 km away. It reported the wrong home position and failed flights
that were, from its point of view, perfectly reasonable.

**Why this matters beyond the test suite.** Two droneserver instances on one
machine will silently share an aircraft. That is a deployment hazard: a staging
server and a production server on the same host would fly the same drone while
each believed it had its own, and neither would report anything unusual.

Fixed in the server rather than in the tests: the helper port is now
`MAVSDK_SERVER_PORT` (default 50051, unchanged for the ordinary single-server
case) and is logged at connection time next to the drone's address, so the
binding is visible rather than implicit. The staging service uses 50060, and
every test fixture picks a free port. Verified by re-running the full suite
with the staging service still running.

**Operational rule that follows:** give every droneserver instance on a shared
host its own `MAVSDK_SERVER_PORT`, and check the startup log line that names
both the aircraft address and the helper port before trusting which drone you
are talking to.

## Reproducing this

```bash
# on the server machine, with the staging service running
uv run python scripts/run_mission_suite.py \
    --url http://127.0.0.1:8090/sse \
    --api-key "$DRONESERVER_API_KEY" \
    --audit-log /var/lib/droneserver/audit.jsonl \
    --target-label "llmuavsitl (ArduPilot SITL over tailnet)"

# add --include-slow for T10, or --missions T1,T8,T9 for a subset
uv run python scripts/run_mission_suite.py --list
```

Each run writes `missions.csv`, `tool_calls.csv`, `audit_slice.csv` and
`summary.md` into a timestamped directory.
