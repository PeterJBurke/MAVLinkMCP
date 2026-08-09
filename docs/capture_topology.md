# Capture topology — how to get a complete Plan 19 bundle out of a SITL run

The capture recorders are only as good as what you point them at. Three of the
four defects found on 2026-08-09 were *silent*: the mission suite exited 0, the
per-trial directory looked full, and the artifacts were wrong. This page records
the topology that produces a correct bundle and, for each part of it, the
failure it prevents.

## The picture

```
  benchmark harness (run_mission_suite.py --capture)
        |  MCP over SSE
        v
  droneserver  ── MAVSDK/gRPC ──▶ mavsdk_server ──TCP 127.0.0.1:5679──▶ [ mavlink_relay.py ]
   (llmuavdev)                                                                  |        |
                                                                      TCP 6789  |        | UDP 127.0.0.1:14655
                                                                                v        v          (both directions)
                                                                MAVProxy (llmuavsitl)   MavlinkTap ──▶ mavlink.tlog
                                                                        |                                mavlink.jsonl
                                                                   ArduCopter SITL
                                                                        |
                                                       udpout 14540 ──▶ TelemetryRecorder ──▶ telemetry.csv
                                                       logs/*.BIN  ──scp──▶ retain_remote_dataflash
```

## The four rules

### 1. The tap must sit where it can hear both halves of the link

**MAVProxy forwards master→outputs and output→master. It never forwards
output→output.** The MCP server attaches as one output, so its commands go to
the autopilot and to nothing else; a tap on a *different* output (`--out
udpout:host:14650`) records the vehicle and only the vehicle. Measured: a 6 s
tap during three tool calls captured 763 messages, all from sysid 1, and not one
`COMMAND_LONG`. The resulting `mavlink.tlog` is not empty and not obviously
broken — it is simply missing every command, which is half of what Plan 19 asks
for.

`scripts/mavlink_relay.py` is the fix: it sits *in* the link and mirrors both
directions verbatim to a UDP port. Run as `mavlink-relay.service` on llmuavdev;
`droneserver-staging` points at it (`MAVLINK_ADDRESS=127.0.0.1`,
`MAVLINK_PORT=5679`), so the relay must be up before the server.

Bind the tap to **loopback** (`udpin:127.0.0.1:14655`), not `0.0.0.0`: the SITL
box also forwards raw telemetry to this host on 14650, and a wildcard bind on a
shared port would double-count the vehicle stream.

### 2. The dataflash log is on the simulator's machine

`retain_dataflash` looks at a local directory and returns `None` when it finds
nothing — which, with SITL on another box, is always. Use
`--dataflash-remote llmuavsitl:/home/dronepilot/ardupilot/ArduCopter/logs`.

### 3. The autopilot must be told to start a new log per flight

ArduPilot's default `LOG_FILE_DSRMROT=0` does **not** rotate the log on disarm,
so every trial's `.BIN` is a growing superset of the whole session — T9's was
38 MB and contained all nine flights. Set `LOG_FILE_DSRMROT=1` (and keep
`LOG_DISARMED=0`). Then each arm/disarm cycle produces its own file.

Even with rotation, the autopilot keeps the file open past disarm, so
`retain_remote_dataflash` requires the log to have been **born** during the
trial, not merely written during it. A mission that never arms therefore keeps
no `.BIN` — correct: there is no flight log, because there was no flight.

### 4. Check the files, not the exit code

`capture_session.py` deliberately swallows recorder start failures so a capture
problem cannot destroy a flight. That means **a run that skipped every recorder
still exits 0**.

Both harnesses now verify each bundle themselves
(`droneserver/capture/verify.py`), record the answer in the manifest as
`capture_status: complete | degraded[...]`, and print `capture: N/M trial(s)
degraded` at the end of a run. **`--require-complete-capture` makes a degraded
bundle exit non-zero (4)** — pass it for any run whose data is meant to be
kept. The checks are the ones whose absence hid the defects on this page: the
tlog must carry both directions, `telemetry.csv` must clear a row floor, the
manifest must list every file at its true size, `events.jsonl` must parse, and
a trial whose telemetry shows the aircraft armed must have retained a dataflash
log.

That is a machine check, not a substitute for looking. After the first trial of
any new topology, verify by hand as well:

```bash
python - <<'PY'
import json, collections, hashlib, pathlib
d = pathlib.Path("benchmark_runs/<run>/T1/trial_1")
dirs = collections.Counter(json.loads(l)["direction"] for l in (d/"mavlink.jsonl").open())
print("mavlink by direction:", dict(dirs))          # BOTH recv and sent must be non-zero
print("telemetry rows:", sum(1 for _ in (d/"telemetry.csv").open()) - 1)
man = json.loads((d/"manifest.json").read_text())
for a in man["artifacts"]:                           # every hash must re-verify
    ok = hashlib.sha256((d/a["name"]).read_bytes()).hexdigest() == a["sha256"]
    print(f"{a['name']:20} {a['bytes']:>10} {ok}")
PY
```

## The working invocation

Two harnesses, the same capture flags — they come from one definition
(`src/droneserver/benchmark/capture_cli.py`) so they cannot drift apart.

### The scripted mission suite

```bash
cd /root/droneserver
set -a; . /etc/droneserver/staging.env; set +a
KEY="$(printf '%s' "$SAFETY_API_KEYS" | cut -d, -f1 | cut -d: -f2)"

.venv/bin/python scripts/run_mission_suite.py \
  --url http://127.0.0.1:8090/sse --api-key "$KEY" \
  --missions T1,T2,T3,T4,T5,T6,T7,T8,T9 --trials 1 --label T1toT9_capture \
  --audit-log /var/lib/droneserver/audit.jsonl \
  --target-label "ArduPilot SITL (llmuavsitl)" \
  --capture --mavlink-endpoint udpin:127.0.0.1:14655 \
  --telemetry-address "udp://:14540" \
  --firmware ArduCopter --firmware-version "ArduCopter 4.5.7 (SITL)" \
  --sitl-host llmuavsitl \
  --dataflash-remote llmuavsitl:/home/dronepilot/ardupilot/ArduCopter/logs
```

`--include-slow` adds T10 (>10 minutes); run it on its own.

### The LLM-in-the-loop harness (what the N=5 campaign runs)

Identical capture flags; the model provenance in the manifest comes from the
resolved route rather than a `--model` flag typed twice. Give the flight
recorder its own telemetry-scope key, or it spends the model's rate-limit
allowance.

```bash
cd /root/droneserver
set -a; . /etc/droneserver/staging.env; . /root/llmuav.env; set +a
export DRONESERVER_API_KEY="$(printf '%s' "$SAFETY_API_KEYS" | tr ',' '\n' | grep '^staging:' | cut -d: -f2)"
export DRONESERVER_RECORDER_API_KEY="$(printf '%s' "$SAFETY_API_KEYS" | tr ',' '\n' | grep '^llm-recorder:' | cut -d: -f2)"

.venv/bin/python scripts/run_llm_missions.py \
  --url http://127.0.0.1:8090/sse \
  --model gemini-3.5-flash-lite --missions T1,T9 --trials 1 --label n5 \
  --audit-log /var/lib/droneserver/audit.jsonl \
  --target-label "ArduPilot SITL (llmuavsitl)" \
  --capture --mavlink-endpoint udpin:127.0.0.1:14655 \
  --telemetry-address "udp://:14540" \
  --firmware ArduCopter --firmware-version "ArduCopter 4.5.7 (SITL)" \
  --sitl-host llmuavsitl \
  --dataflash-remote llmuavsitl:/home/dronepilot/ardupilot/ArduCopter/logs \
  --require-complete-capture
```

The LLM bundle carries one file the scripted one cannot: `transcript.jsonl`
holds the real conversation — the system prompt, the mission prompt, every
assistant turn with its token usage, and every tool call with the server's
reply. It is required for an LLM trial and its absence degrades the bundle.

**Two things named `TelemetryRecorder` used to exist.** The one in
`llm/mcp_session.py` polls MCP tools at ~0.5 Hz and feeds the pass/fail
verdicts; it is now `McpTelemetryPoller`. The Plan 19 one in
`capture/telemetry_recorder.py` subscribes to MavSDK at 10 Hz and writes
`telemetry.csv`. Both run during a captured LLM trial. Do not replace the
poller with the recorder: every historical trial was judged by the poller, and
swapping it would make old and new results incomparable.
