# Documentation Index

Every Markdown document in this repository, and what it is for.

**Start at [README.md](README.md).** To reproduce the paper's results, start at
[docs/reproduce.md](docs/reproduce.md).

---

## Start here

| File | What it is |
|---|---|
| [README.md](README.md) | What DroneServer is, the safety layer, the deployment posture, quickstart |
| [docs/reproduce.md](docs/reproduce.md) | End-to-end reproduction: SITL, credentials, the mission suite, the LLM harness, the capture bundles |
| [SECURITY.md](SECURITY.md) | Deployment posture in brief; how to report a vulnerability |
| [CHANGELOG.md](CHANGELOG.md) | Release history |
| [CONTRIBUTING.md](CONTRIBUTING.md) | Issues, PRs, the checks a change must pass |

## Safety

| File | What it is |
|---|---|
| [docs/safety_review.md](docs/safety_review.md) | The safety layer in full, written for someone who has not read the code: tiers, confirmation tokens, geofence, bounds, preconditions, auth, audit log, config surface |
| [src/droneserver/safety/README.md](src/droneserver/safety/README.md) | Implementer's notes alongside the safety package |
| [docs/adversarial_results.md](docs/adversarial_results.md) | Generated — the 29 adversarial/prompt-injection cases and what the guard did with each |
| [docs/estop.md](docs/estop.md) | The emergency-stop path |
| [LOITER_MODE_CRASH_REPORT.md](LOITER_MODE_CRASH_REPORT.md) | v1 incident report: `pause_mission()` entered LOITER and the aircraft descended to impact |
| [MISSION_PAUSE_FIX.md](MISSION_PAUSE_FIX.md) | The fix that followed: `hold_mission_position()` |

## Evaluation and evidence

| File | What it is |
|---|---|
| [docs/llm_in_the_loop.md](docs/llm_in_the_loop.md) | What happens inside one scored trial, and why verdicts come from telemetry and the audit log rather than the model's own claim |
| [docs/capture_topology.md](docs/capture_topology.md) | The four-layer capture pipeline and the failure modes it was built to catch |
| [docs/long_mission_demo.md](docs/long_mission_demo.md) | The long mission surviving a client disconnection — server-side mission state |
| [docs/staging_validation.md](docs/staging_validation.md) | The two-machine server/simulator topology the campaigns ran on |
| [docs/tool_description_experiment.md](docs/tool_description_experiment.md) | Effect of tool-description wording on model behaviour |
| [docs/google_maps_mcp.md](docs/google_maps_mcp.md) | The second MCP server used by mission T6 |

## Coverage and tool reference

| File | What it is |
|---|---|
| [docs/tool_test_coverage.md](docs/tool_test_coverage.md) | Generated — per-tool test and flight evidence across the unit, SITL and scored-campaign layers |
| [docs/coverage_summary.md](docs/coverage_summary.md) | Generated — MavSDK method coverage (223 implemented + 15 documented-N/A of 238 client-side) |
| [docs/tool_groups.md](docs/tool_groups.md) | Why the tools are grouped the way they are |
| [MCP_TOOLS_MAVSDK.md](MCP_TOOLS_MAVSDK.md) | v1-era mapping of MCP tools to MAVSDK methods — superseded by the generated coverage docs |
| [MAVSDK_METHODS.md](MAVSDK_METHODS.md) | v1-era MAVSDK API reference — superseded by `docs/coverage_matrix.csv` |
| [MAVLINK_COMMANDS.md](MAVLINK_COMMANDS.md) | MAVLink `MAV_CMD` reference |

## Deployment and operations

| File | What it is |
|---|---|
| [SERVICE_SETUP.md](SERVICE_SETUP.md) | Running the server under systemd |
| [LIVE_SERVER_UPDATE.md](LIVE_SERVER_UPDATE.md) | Updating a running deployment |
| [RESTART_INSTRUCTIONS.md](RESTART_INSTRUCTIONS.md) | Restarting, and reading the logs |
| [CHATGPT_SETUP.md](CHATGPT_SETUP.md) | Driving the server from an interactive MCP chat client, and why hosted web connectors are out of scope |
| [LMSTUDIO_SETUP.md](LMSTUDIO_SETUP.md) | LM Studio's `mcp.json` for this server |
| [deploy/farm/README.md](deploy/farm/README.md) | The 10-lane SITL farm used for the campaigns |

## Testing

| File | What it is |
|---|---|
| [tests/integration/README.md](tests/integration/README.md) | The SITL fixture chain and how to run the integration suite |
| [TESTING.md](TESTING.md) | v1-era manual test procedures driven by chat prompts |
| [TESTING_REFERENCE.md](TESTING_REFERENCE.md) | Troubleshooting reference, GPS maths, firmware compatibility matrix |

## Flight behaviour and logs

| File | What it is |
|---|---|
| [FLIGHT_MODES.md](FLIGHT_MODES.md) | ArduPilot flight modes and which tools trigger which transitions |
| [FLIGHT_LOGS.md](FLIGHT_LOGS.md) | The flight logging system |
| [LOG_COLORS.md](LOG_COLORS.md) | The colour scheme in the server's console output |

## History

| File | What it is |
|---|---|
| [STATUS.md](STATUS.md) | The v1 status and roadmap document, kept as a historical record. Superseded by README.md and CHANGELOG.md |

## Examples

| File | What it is |
|---|---|
| [examples/README.md](examples/README.md) | The example agent |

---

Documents marked **Generated** are produced by a script and should not be
hand-edited — see [CONTRIBUTING.md](CONTRIBUTING.md).
