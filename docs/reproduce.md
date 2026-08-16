# Reproducing the paper's results end to end

**Who this is for:** a reviewer or replicator who wants to run the actual
evaluation — not chat with a model over MCP by hand, but execute the same
scripts that produced the paper's numbers, against a simulator you control.

This page answers a specific reviewer request: open-source scripts that call
the LLM and the MCP server and run the test suite end to end, as opposed to
"set up the MCP server and drive it manually from a chat client." Both modes
of use are real and both are documented (see [Two ways to operate the
system](#two-ways-to-operate-the-system) below), but this page is about the
scripted, reproducible one.

## What you need

| Piece | What it is | Where |
|---|---|---|
| This repository | the MCP server, the safety layer, and the benchmark harness | `github.com/PeterJBurke/droneserver`, branch `v2-upgrade` |
| A SITL simulator | a virtual ArduPilot (or PX4) aircraft to fly against | two options below — pick one |
| Provider API key(s) | credentials for whichever LLM(s) you want to fly | one env var per provider, see below |
| `uv` | Python package/venv manager this project uses | `astral-sh/uv` |

No real aircraft, no cloud account beyond an LLM provider, and no part of
this project's own infrastructure is required.

### 1. Clone and install

```bash
git clone https://github.com/PeterJBurke/droneserver.git
cd droneserver
git checkout v2-upgrade
uv sync
```

### 2. Get a SITL aircraft running

Two documented paths, both real and both used by this project — pick
whichever matches what you're trying to reproduce.

**(a) Self-contained, single-machine — the path CI uses.** A throwaway
ArduPilot SITL in Docker, pinned to a known firmware build, no toolchain
required:

```bash
docker build -t droneserver-sitl-arducopter:4.5.7 docker/ardupilot-sitl
docker run -d --rm -p 127.0.0.1:5760:5760 -p 127.0.0.1:5762:5762 \
    droneserver-sitl-arducopter:4.5.7
```

This is exactly what `.github/workflows/sitl-nightly.yml` builds, and what
`tests/integration/conftest.py` builds and drives automatically for the
integration test suite (`uv run pytest -m "sitl and not longmission"
tests/integration`) — see `tests/integration/README.md` for the full
fixture chain. It is the fastest way to check the server and the scripts
work at all, and it is enough to run `run_mission_suite.py` and
`run_llm_missions.py` against a local, ephemeral aircraft.

**(b) A persistent SITL host — the topology the paper's campaigns actually
used.** The scored evaluation in the paper ran the server and the simulator
as two separate machines on a private network (matching a real deployment,
where the aircraft is never on the same box as the server), with the
simulator itself set up by
[`CreateSITLenv`](https://github.com/PeterJBurke/CreateSITLenv). Use this
path to reproduce the paper's own measurement conditions, including network
round-trip latency between the server and the vehicle; see
`docs/staging_validation.md` and `docs/capture_topology.md` for the exact
arrangement and the capture-pipeline wiring used against it.

### 3. Configure credentials

The MCP server itself needs an API key (`SAFETY_API_KEYS` server-side;
clients present it as `DRONESERVER_API_KEY`). The LLM-in-the-loop harness
additionally wants a second, telemetry-scoped key for its passive flight
recorder (`DRONESERVER_RECORDER_API_KEY`) so the recorder doesn't share the
flying client's rate-limit bucket. See `docs/safety_review.md` for how
`SAFETY_API_KEYS` is structured.

For the model(s) you want to fly, set the matching provider key — only the
ones you use are required:

| Provider | Env var |
|---|---|
| OpenAI | `OPENAI_API_KEY` |
| Anthropic | `ANTHROPIC_API_KEY` |
| Google (Gemini) | `GEMINI_API_KEY` |
| xAI (Grok) | `XAI_API_KEY` |
| Mistral | `MISTRAL_API_KEY` |
| DeepSeek | `DEEPSEEK_API_KEY` |
| OpenRouter (routes many models through one key) | `OPENROUTER_API_KEY` |
| Local LM Studio endpoint | `LMSTUDIO_API_KEY` (any value; the endpoint itself is unauthenticated) |
| Google Maps MCP (only needed for mission T6) | `GOOGLE_MAPS_API_KEY` |

Then price the models — the harness refuses to fly a model it has no price
for, on purpose, so cost is never silently unmetered:

```bash
uv run python scripts/update_model_prices.py
```

## The scripts

Three scripts cover the reproducer's path end to end. All three are plain
Python/shell, take no proprietary dependency, and are the actual scripts
that produced the paper's data — not a simplified stand-in.

### `scripts/run_mission_suite.py` — the scripted, deterministic suite (T1–T10)

No LLM in the loop. A fixed Python client calls the server's MCP tools in a
predetermined order for each of the ten standardized missions, so this
script checks that the server, the safety layer, and the simulator link
work at all, independent of any model. Its output is the harness's own
ground truth for verifying the LLM-driven runs against.

```bash
uv run python scripts/run_mission_suite.py \
    --url http://127.0.0.1:8090/sse \
    --api-key "$DRONESERVER_API_KEY" \
    --audit-log /var/lib/droneserver/audit.jsonl \
    --target-label "my SITL aircraft"

# list the ten missions without flying anything
uv run python scripts/run_mission_suite.py --list
```

(`--include-slow` adds T10, the >10-minute long mission; it's excluded by
default because of its length, not its importance.)

### `scripts/run_llm_missions.py` — the LLM-in-the-loop harness

This is the script the reviewer request is about: it hands a chosen
model's real API the server's actual MCP tool schemas and a mission
in one sentence of plain English, then lets the model decide which tools to
call, in which order, with which arguments — and records everything. It
supports any provider in the table above, or an OpenAI-wire-compatible
local endpoint (LM Studio, etc.), via `--model provider:model-name` or a
bare model name that resolves automatically.

```bash
# one mission, one trial, with the paired command+recorder keys
uv run python scripts/run_llm_missions.py \
    --missions T1 --model gpt-5.2 \
    --url http://127.0.0.1:8090/sse \
    --api-key "$DRONESERVER_API_KEY" \
    --recorder-api-key "$DRONESERVER_RECORDER_API_KEY" \
    --target-label "my SITL aircraft"

# the flying part of the suite plus the two guardrail (refusal) tasks
uv run python scripts/run_llm_missions.py \
    --missions T1,T2,T3,T4,T5,T7,T8,T9 --model gpt-5.2 \
    --url http://127.0.0.1:8090/sse --api-key "$DRONESERVER_API_KEY"

# read the mission prompts, or list endpoints available for a model,
# without flying anything
uv run python scripts/run_llm_missions.py --list
uv run python scripts/run_llm_missions.py --list-endpoints qwen3-max
```

See `docs/llm_in_the_loop.md` for what happens inside one trial (the
harness's own connection checks the aircraft first; a separate,
read-only recorder connection logs telemetry throughout; the model gets
only the tools and the mission sentence) and for the rule that makes the
results evidence rather than self-report: **verdicts are computed from the
recorded flight telemetry and the server's audit log, never from the
model's own claim of success.** The model's closing `MISSION COMPLETE` /
`MISSION ABORTED` line is captured too, and stored beside the
telemetry-derived verdict — so a model that claims success it did not
achieve is measured as having done so, not believed.

### `scripts/run_n5_campaign.sh` — the full statistical campaign

A thin shell wrapper around `run_llm_missions.py` that drives the paper's
whole model matrix, five trials per mission per model, one model at a time
(they share a simulated aircraft), with full capture enabled on every
trial:

```bash
# the default campaign: T1-T9, 5 trials, the paper's direct-API model list
bash scripts/run_n5_campaign.sh

# override the model list
MODELS="claude-sonnet-5 gpt-5.2" bash scripts/run_n5_campaign.sh

# a short rehearsal before committing to the full campaign
MISSIONS=T1,T2 TRIALS=1 bash scripts/run_n5_campaign.sh
```

Read the script itself (`scripts/run_n5_campaign.sh`) before running it —
it sources credentials from this project's own deployment paths
(`/etc/droneserver/staging.env`, `/root/llmuav.env`) and enforces per-key
spend ceilings tuned to this project's campaign; a reproducer pointing it
at their own SITL and keys should adjust those paths and, if desired, the
budget caps (`--budget-usd`, exposed by `run_llm_missions.py`) rather than
run it unmodified against production credentials.

## What a reproducer gets out of each trial

With `--capture` enabled (on by default in `run_n5_campaign.sh`; opt-in via
the flag for the other two scripts), every trial writes a self-contained,
model/mission/trial-keyed capture bundle: 10 Hz telemetry (`telemetry.csv`),
the bidirectional MAVLink wire tap (`mavlink.tlog` / `mavlink.jsonl`), the
server's append-only audit log slice (`audit_slice.jsonl`/`.csv`), the full
LLM transcript (`transcript.jsonl`), the autopilot's own dataflash log, and
a `manifest.json` recording versions, simulation parameters, and a sha256
of every file in the bundle — plus a `summary.md` and per-run CSVs
(`missions.csv`, `tool_calls.csv`) rolling the trials up. See
`docs/capture_topology.md` for the full pipeline and the failure modes it
was built to catch, and the paper's reproducibility appendix for how each
figure is regenerated from exactly this bundle. As stated above: pass/fail
verdicts in every one of these outputs come from the telemetry and audit
record, not from the model's own narration.

## Two ways to operate the system

The scripts on this page are one of two supported ways to drive the
server, and the paper documents both because reviewers specifically asked
for the scripted one:

- **Interactive web-chat.** Connect an MCP-capable chat client (Claude
  Desktop, or ChatGPT's Developer Mode connector) to the running server and
  fly it conversationally. This is the mode shown in the v1 paper's
  figures and in `CHATGPT_SETUP.md` / `LMSTUDIO_SETUP.md` in this repo. A
  human reads every reply and decides what to ask for next.
- **Scripted Python API — this page.** `run_llm_missions.py` calls a
  chosen provider's model API programmatically, turn by turn, through the
  same MCP server, the same 98 tools, and the same server-side safety
  layer — with no human in the per-turn loop. This is what makes N=5
  statistical campaigns possible, and it is what every number in the
  paper's results section comes from.

Both modes terminate at the identical `droneserver` MCP instance; only the
client issuing the tool calls differs.
