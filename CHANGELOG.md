# Changelog

Notable changes per tagged release. Dates are the tag's commit date.

---

## v2.0.2 — 2026-09-04

Tooling and generated-data corrections on top of v2.0.1. No API change: no tool
was added, removed, or altered, and no safety rule changed. This is the first
tag on the v2 line to be published as a GitHub Release.

- **Coverage matrix corrected back to the true 223/238.** The AST matcher in
  `scripts/generate_coverage_matrix.py` cannot see `telemetry.in_air` and
  `telemetry.landed_state`, because since FIX 15 they are dispatched
  dynamically — `getattr(drone.telemetry, topic)` in
  `telemetry/ground_stream.py` — rather than called by name. Both are now
  curated entries in `docs/coverage_overrides.csv` with that reason recorded,
  and the checked-in matrix has been regenerated, which also clears nine rows
  of drift that had accumulated in `docs/coverage_matrix.csv` since 071aa37.
  The published figure — 223 implemented, 15 documented-N/A, 238 of 238
  client-side MavSDK methods addressed, 0 missing — is once again what the
  generator produces.
- **Two generators so the paper cannot drift from this repo.**
  `scripts/adversarial_case_table.py` reads case id, category, attack and
  expectation out of the AST of `tests/integration/test_adversarial_sitl.py`
  (expanding parametrized injection cases the way pytest does), joins them
  against the observed status and rule id in `docs/adversarial_results.md` —
  the artifact the suite writes while running against live SITL — and exits
  non-zero if the two disagree about which cases exist or about the headline
  count. `scripts/mission_prompt_table.py` renders the ten mission prompts
  through the harness's own `mission_prompts()` call, and `--verify` checks
  that rendering byte-for-byte against the first operator message of every
  recorded trial transcript. Both emit a LaTeX longtable plus a macro block,
  so no row in the manuscript's case or prompt tables is typed by hand.
- **Release hygiene.** Package version bumped to 2.0.2 in `pyproject.toml` and
  `droneserver.__version__`, which had both been left at 2.0.1.
  `CONTRIBUTING.md` no longer directs pull requests at a `v-next` development
  branch, which does not exist — `main` is the branch to target. The stale
  unit-coverage figure in the CI workflow comment was corrected.

CI is green on this commit: ruff check, ruff format, mypy, and 937 unit tests
passing (run 33752950492), and the SITL integration suite passing on the same
commit — 107 passed, 8 skipped, 1 deselected of the 116 collected
(run 33752950437).

## v2.0.1 — 2026-08-31

The release the paper cites. Same system as v2.0.0; this tag exists because
v2.0.0 predated two fixes and one tool the paper depends on, and an archived
snapshot of v2.0.0 would not contain them.

- **FIX 16 — a transport may not cancel the trial that is using it.** During
  teardown of an MCP session, an anyio cancel scope unwinding the transport was
  indistinguishable from a genuine cancellation of the trial, so a normal
  disconnect could be recorded as an aborted run. The two are now told apart by
  comparing `asyncio.Task.cancelling()` counts across the teardown, in
  `droneserver.llm.mcp_session`.
- **FIX 17 — the Python floor the package promised but never supported.** The
  package advertised `requires-python >= 3.10`, which was never true:
  `Task.cancelling()`, the measurement FIX 16 rests on, does not exist before
  3.11, and `asyncio.TimeoutError` was only unified with the builtin in 3.11 —
  so on 3.10 no LLM session opened at all and the ~23 telemetry timeout handlers
  caught nothing. The floor, the classifiers, the mypy `python_version` and the
  ruff `target-version` all move to 3.11; `tests/test_python_floor_is_honest.py`
  pins it. No `# type: ignore` was added and no mypy setting loosened.
- **Appendix D coverage tooling.** `scripts/tool_test_coverage.py` derives, per
  registered tool, whether the unit suite drives it, whether the SITL suite calls
  it against a real autopilot, and how many times a model chose it in the scored
  campaigns — writing `docs/tool_test_coverage.{csv,md,json}` and, with `--tex`,
  the numeric macros the paper's Appendix D prints. No count in that appendix is
  typed by hand.
- **CI green end to end** for the first time since 2026-08-14: ruff check, ruff
  format, mypy and the unit suite (937 tests).
- **Release hygiene.** README rewritten for v2. `SECURITY.md`, `CONTRIBUTING.md`
  and this file added. Every v1 instruction to stand up a public ngrok tunnel
  removed from the tree — including `ngrok.service` — because the deployment the
  paper describes uses no public tunnel anywhere and has zero public ports.

Supersedes v2.0.0 as the archive target for the paper.

## v2.0.0 — 2026-08-21

The v2 upgrade. Breaking changes relative to v1 throughout.

- **98 MCP tools**, up from 41.
- **Near-complete MavSDK coverage:** 223 of 238 client-side methods implemented,
  the remaining 15 documented as not-applicable with reasons — 238/238 addressed,
  0 missing. Drone-side `*_server` plugin methods (92) are out of scope.
- **Server-side safety layer**, treating the commanding model as untrusted:
  criticality tiers, single-use confirmation handshakes for critical tools, an
  independent server-side geofence, parameter bounds, state preconditions,
  scoped API keys, rate limits, an append-only audit log, and fail-closed
  validation. The model cannot switch it off. Adversarial suite 29/29.
- **Server-side mission state**, so a mission survives the client disconnecting
  — demonstrated by a 37.8-minute mission surviving a 4-minute disconnection.
- **Four-layer capture pipeline:** 10 Hz telemetry, a bidirectional MAVLink wire
  tap, the audit-log slice, and the full LLM transcript, sealed per trial in a
  manifest with a sha256 of every file.
- **LLM-in-the-loop harness** (`scripts/run_llm_missions.py`,
  `scripts/run_mission_suite.py`, `scripts/run_n5_campaign.sh`): any provider,
  the same MCP server and safety layer, with verdicts computed from recorded
  telemetry and the audit log rather than from the model's own claim of success.
- **921 unit and 116 SITL integration tests** at the freeze, under CI.
- FIX 1–15, including the T6 return-honesty contract and the state-ownership
  fixes (13/14/15).

---

## v1.4.0 — 2025-12-10

Complete flight lifecycle management: auto-land waits for confirmed touchdown
before returning `mission_complete`, 30-second progress updates the LLM cannot
override, and landing detection requiring ON_GROUND + not in_air + altitude < 2 m
held for 3 s.

## v1.2.4 — 2025-11-16

Switched mission handling to MAVSDK's `mission_raw` for ArduPilot compatibility;
the high-level `Mission` class validated too strictly and failed on download.

## v1.2.3 — 2025-11-16

Critical safety fix: deprecated `pause_mission()`, which caused a crash in flight
testing by entering LOITER — a mode that does not hold current altitude in
ArduPilot — and descending from 25 m to ground impact. `hold_mission_position()`
is the safe alternative.

## v1.2.2 — 2025-11-16

Added `hold_mission_position()` as a GUIDED-mode alternative to `pause_mission()`,
and added mode-transition diagnostics to `resume_mission()`.

## v1.2.1 — 2025-11-16

Error handling and autopilot-compatibility patch: mission-upload validation
messages, orbit capability detection, battery fallback for uncalibrated systems,
and a firmware compatibility matrix.

## v1.2.0 — 2025-11-16

Ten new tools to 35 total, including parameter management (`get_parameter`,
`set_parameter`, `list_parameters`).

## v1.1.5 — 2025-11-15

Documentation consolidation: four redundant historical files removed, the roadmap
folded into `STATUS.md`.

## v1.0.0 — 2025-11-11

First stable release. MAVLink drone control over MCP, HTTP/SSE transport, TCP/UDP
connections to ArduPilot SITL, GPS navigation with automatic GUIDED mode, and
real-time telemetry.
