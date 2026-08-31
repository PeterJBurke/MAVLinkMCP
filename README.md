# DroneServer

An [MCP](https://modelcontextprotocol.io/) server that lets any MCP-capable
large language model fly a MAVLink aircraft — ArduPilot or PX4, simulated or
real — and a server-side safety layer that assumes the model is an untrusted
commander.

The model calls named tools (`takeoff`, `go_to_location`, `kill_motors`); the
server turns each one into MAVLink and returns telemetry. Nothing reaches the
aircraft without passing the safety layer first, and the model cannot switch
that layer off.

This repository is the software artifact for the paper described under
[Citing this work](#citing-this-work). It also carries the benchmark harness
that produced the paper's numbers, so the results can be reproduced rather than
taken on trust.

---

## The safety layer

The interface is designed on the premise that the commanding model may be
wrong, may have been talked into something by its own context, or may simply
hallucinate a justification. Everything below is enforced on the server, in
front of the tool body, and is on by default.

| Mechanism | What it does |
|---|---|
| **Criticality tiers** | Every tool is `read_only`, `normal`, `critical`, or `emergency`. The tier decides what is required before the tool runs. A tool with no tier entry is treated as `critical`, so a newly added tool cannot slip in unclassified. |
| **Confirmation handshake** | A `critical` tool (`kill_motors`, `vehicle_power`, `autopilot_shell`, …) does not execute on the first call. It returns a one-time token plus a plain statement of the consequence; only a second call quoting that exact token executes. Tokens are single-use, tool-bound, argument-bound, and expire. |
| **Independent geofence** | A polygon, altitude ceiling, and home-radius enforced by the server itself, not delegated to the autopilot's fence. Destinations are resolved from the aircraft's live position, and continuous-motion commands are projected forward to where they would end up before being allowed. |
| **Parameter bounds and state preconditions** | Altitude, speed, distance-from-home, coordinate sanity, mission size; plus rules about the vehicle's state ("you cannot navigate before taking off"). |
| **Authentication and scopes** | API keys carry a scope: `telemetry` (read only), `control` (fly it), `admin`. A telemetry-scoped client is refused before a confirmation token is ever issued. |
| **Rate limiting** | Per-client, with a separate and smaller budget for `critical` calls, so a looping model cannot flood the aircraft. |
| **Append-only audit log** | One line per tool call: who, what, allowed or refused, which rule fired, and how long it took. Never edited, only appended. Each record also carries the guard flags in force, so a guardrails-off run is self-documenting. |
| **Fail closed** | If a check itself raises, the command is refused (`guard.internal_error`), not passed through. Bad safety configuration stops the server from starting rather than being discovered mid-flight. |

Full detail, including the tier table and every rule name, is in
[`docs/safety_review.md`](docs/safety_review.md). The emergency-stop path is in
[`docs/estop.md`](docs/estop.md).

Adversarial behaviour — prompt injection in tool arguments, forged and replayed
confirmation tokens, scope escalation, out-of-fence waypoints — is exercised
against a live SITL aircraft through the real MCP path;
[`docs/adversarial_results.md`](docs/adversarial_results.md) is the generated
case-by-case table.

## Deployment posture

The server is intended to run reachable only over a private WireGuard/Tailscale
tailnet, on a host with **zero publicly reachable ports**: firewall
default-deny, and, where containers are involved, `DOCKER-USER` rules, because
published container ports bypass `ufw`.

**No public tunnel — ngrok or otherwise — is part of this deployment.** The v1
documentation that instructed the reader to stand one up has been removed; see
[`SECURITY.md`](SECURITY.md) for the posture in brief and for how to report a
vulnerability.

An MCP client reaches the server at its tailnet address (or on loopback when
the client runs on the same host), presenting a scoped API key.

## Quickstart

```bash
git clone https://github.com/PeterJBurke/droneserver.git
cd droneserver
uv sync
cp .env.example .env      # point MAVLINK_* at your aircraft or simulator
uv run python -m droneserver.server --transport stdio
```

Python 3.11 or newer is required. `uv` is [astral-sh/uv](https://github.com/astral-sh/uv).

**To reproduce the paper's results — SITL setup, credentials, the mission suite,
the LLM-in-the-loop harness, and the capture bundles — start at
[`docs/reproduce.md`](docs/reproduce.md).** That page is the entry point for a
reviewer or replicator; it covers both supported ways of driving the server
(an interactive MCP chat client, and the scripted harness that produced every
number in the paper).

## What the interface covers

- **98 registered MCP tools.** The full per-tool table, with what evidence
  backs each one, is [`docs/tool_test_coverage.md`](docs/tool_test_coverage.md)
  (generated by `scripts/tool_test_coverage.py`); the grouping rationale is
  [`docs/tool_groups.md`](docs/tool_groups.md).
- **MavSDK client-side coverage: 223 implemented and 15 documented-N/A of 238
  methods**, across 33 plugins — 0 missing. Drone-side `*_server` plugin
  methods (92) are out of scope for a ground-side interface. Generated matrix:
  [`docs/coverage_summary.md`](docs/coverage_summary.md),
  [`docs/coverage_matrix.csv`](docs/coverage_matrix.csv).
- **Server-side mission state**, so a long mission survives the client
  disconnecting — see [`docs/long_mission_demo.md`](docs/long_mission_demo.md).

## Tests

Current suite state on this branch:

| Layer | Count | How to run |
|---|--:|---|
| Unit (no aircraft) | 937 | `uv run pytest` |
| SITL integration (docker ArduPilot) | 116 | `uv run pytest -m "sitl and not longmission" tests/integration` |
| Adversarial / prompt-injection cases | 29 of 29 as specified | `uv run pytest -m sitl tests/integration/test_adversarial_sitl.py` |

Four of the unit modules are whole-registry invariants: they assert that the
tier table, the safety coverage, and the tool registry agree with each other
exactly, so adding a tool without classifying it fails the suite.

CI (`.github/workflows/ci.yml`) runs ruff, ruff format, mypy, and the unit
suite on every push. The SITL suite runs nightly
(`.github/workflows/sitl-nightly.yml`) against a pinned ArduCopter 4.5.7 docker
image, because GitHub runners cannot reach the project's tailnet.

The reported unit-only line coverage is low by construction and deliberately
un-gated: most of the codebase is drone-tool bodies that can only execute
against a simulator, so a threshold on a figure that does not measure what it
appears to would be worse than none.

## Documentation

| | |
|---|---|
| [`docs/reproduce.md`](docs/reproduce.md) | End-to-end reproduction — start here |
| [`docs/safety_review.md`](docs/safety_review.md) | The safety layer, written for a non-implementer |
| [`docs/llm_in_the_loop.md`](docs/llm_in_the_loop.md) | What happens inside one scored trial |
| [`docs/capture_topology.md`](docs/capture_topology.md) | The capture pipeline and the failure modes it was built to catch |
| [`docs/tool_test_coverage.md`](docs/tool_test_coverage.md) | Per-tool test and flight evidence |
| [`docs/coverage_summary.md`](docs/coverage_summary.md) | MavSDK method coverage |
| [`docs/adversarial_results.md`](docs/adversarial_results.md) | Adversarial case results |
| [`docs/estop.md`](docs/estop.md) | Emergency stop |
| [`SECURITY.md`](SECURITY.md) | Deployment posture; reporting a vulnerability |
| [`SERVICE_SETUP.md`](SERVICE_SETUP.md) | systemd deployment |
| [`CHANGELOG.md`](CHANGELOG.md) | Release history |
| [`CONTRIBUTING.md`](CONTRIBUTING.md) | Issues, PRs, required checks |

## Operating safely

This software has been tested in simulation. Flying a real aircraft with a
language model in the loop is the operator's decision and the operator's
responsibility: keep visual line of sight, keep a manual RC override live,
verify GPS lock and battery before arming, fly clear of people, and configure
the geofence for the site you are actually at. The safety layer refuses
commands; it does not make an aircraft safe.

## Citing this work

The preprint describing this system is arXiv:2601.15486 (v2; under review).
This paragraph will be replaced with the full journal citation on acceptance.

```bibtex
@misc{droneserver2026,
  title  = {A Safe and Secure, LLM-Agnostic, MAVLink-Based Drone Command and
            Control Interface and Agentic Harness Using the Model Context Protocol},
  note   = {arXiv:2601.15486, v2; under review},
  year   = {2026}
}
```

## License

MIT — see [LICENSE](LICENSE).

## Acknowledgments

- Original project by [Ion Gabriel](https://github.com/ion-g-ion/MAVLinkMCP)
- Built with [MAVSDK](https://mavsdk.mavlink.io/)
- Uses the [Model Context Protocol](https://modelcontextprotocol.io/)
