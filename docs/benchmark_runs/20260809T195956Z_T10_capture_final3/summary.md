# Mission suite run

- Run at: 2026-08-09T20:12:52.760121+00:00
- Target: `ArduPilot SITL (llmuavsitl)`
- Client: `authenticated`
- Missions run: **1** (0 skipped)
- Passed: **1/1**

| Mission | Task | Verdict | Duration (s) | Reason |
|---|---|---|---|---|
| T10 | long mission >10 min | PASS | 770.7 | 12.3 min mission monitored server-side |

## Latency

Two independent clocks. The client wall clock includes the network hop and MCP
framing; the server's audit latency is the guard plus the tool itself. The gap
between them is the network cost of putting the drone on the other side of a link.

| Clock | Calls | Mean (ms) | Median (ms) | p95 (ms) | Max (ms) |
|---|---|---|---|---|---|
| client wall clock | 57 | 99.8 | 76.4 | 182.0 | 568.8 |
| server audit latency_ms | 77 | 7.8 | 1.5 | 2.8 | 502.1 |

## Safety

- Tool calls made: **57**
- Safety interventions (rejected / confirmation required): **0**
- Confirmation round-trips the suite had to complete: **0**
