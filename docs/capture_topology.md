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
directions verbatim to a UDP port. It is the one part of the capture layer that
is not passive, so `tests/test_mavlink_relay.py` holds it to byte-exact,
in-order delivery through deliberately fragmented writes, and to surviving a
mirror that has died. Note what the mirror is: **both directions copied into
one UDP stream**, in whatever chunks the pumps read, so a datagram boundary is
not a message boundary. pymavlink reassembles across datagrams, but a lost or
reordered datagram costs the tap whatever frames straddled it and nothing in
the tlog says so. Run as `mavlink-relay.service` on llmuavdev;
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

That test compares a birth time from the **simulator's** clock with a trial
start from **this host's**. The harness measures the offset over the same SSH
connection before each fetch (`remote_clock_offset_s`), subtracts it, and
records it as the manifest's `clock_offset_ms` — but keep the two boxes on NTP
anyway. A simulator running a few seconds fast stamps the *previous* flight's
log into this trial's window; that is blocker B-3 reached by another route, and
it is silent. The harness prints a warning when the offset exceeds two seconds
or cannot be measured at all.

### 4. Check the files, not the exit code

`capture_session.py` deliberately swallows recorder start failures so a capture
problem cannot destroy a flight. That means **a run that skipped every recorder
still exits 0**.

Both harnesses now verify each bundle themselves
(`droneserver/capture/verify.py`), record the answer in the manifest as
`capture_status: complete | degraded[...]`, and print `capture: N/M trial(s)
degraded` at the end of a run. **`--require-complete-capture` makes a degraded
bundle exit non-zero (4)** — pass it for any run whose data is meant to be
kept. The checks are the ones whose absence hid the defects on this page, plus
the ones whose absence let a demonstrably incomplete bundle pass anyway:

- `mavlink.jsonl` must carry **both directions**, and when the vehicle is seen
  to arm the ground-station side must contain something other than HEARTBEATs.
  A ground station heartbeats once a second whether or not the tap is on a path
  carrying its commands, so "sent > 0" alone proved nothing.
- `telemetry.csv` must clear a row floor, **carry actual vehicle state** (a
  recorder that never connects still writes perfectly regular empty rows), have
  no gap over 5 s between consecutive rows (ten rows spread over a twelve-minute
  trial used to pass), keep `sample_age_s` fresh (sample-and-hold turns a dead
  link into a stationary aircraft), and reach the end of the trial.
- the manifest must list every file at its true size, list **nothing that is
  not there**, and every `sha256` must re-verify against the bytes on disk.
- `events.jsonl` must parse.
- a trial that armed — per the telemetry, the vehicle's HEARTBEATs **or** the
  derived events — must have retained a dataflash log. Asking all three matters
  because the telemetry is exactly the witness that goes silent when the
  recorder fails, and its silence used to excuse the missing log.

That is a machine check, not a substitute for looking. After the first trial of
any new topology, look at the bundle yourself:

```bash
uv run python - <<'PY'
import collections, json, pathlib
from droneserver.capture.verify import verify_bundle
d = pathlib.Path("benchmark_runs/<run>/T1/trial_1")
check = verify_bundle(d, require_transcript=False)   # hashes are re-computed here
for c in check.checks:
    print(f"{'ok  ' if c.ok else 'FAIL'} {c.name:16} {c.detail}")
sent = collections.Counter(
    json.loads(l)["msg_type"] for l in (d/"mavlink.jsonl").open() if json.loads(l)["direction"] == "sent"
)
print("what the server actually sent:", dict(sent))  # not just HEARTBEAT
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

## The PX4 topology (llmuavpx4, validated 2026-08-12)

The same four rules, wired onto the second firmware. The sim is **PX4 v1.16.2
SITL** on the box `llmuavpx4` (tailnet `100.89.214.49`); on that box
`px4-mavbridge.service` runs MAVProxy as the aggregation point:

```
  px4 SITL (udp 14540) ──▶ MAVProxy (llmuavpx4) ── tcpin  100.89.214.49:5760 ──┐
                                       │                                        │
                          udpin 14550 (GCS)   udpout 100.100.244.74:14540 ──┐   │
                                                (telemetry forward, added)   │   │
  llmuavdev:                                                                 │   │
    droneserver-staging ─TCP 127.0.0.1:5679─▶ [ mavlink-relay-px4 ] ────────────┘
                                                   │            │  (both directions)
                                          TelemetryRecorder     └─UDP 127.0.0.1:14656
                                          udp://:14540 ◀────────┘   MavlinkTap → mavlink.tlog
    logs/<date>/*.ulg  ──scp──▶ retain_remote_dataflash (llmuavpx4:/var/lib/px4-sitl/log)
```

What differs from the ArduPilot stack, and why:

1. **The relay is `mavlink-relay-px4.service`** — same byte-pump, upstream
   `100.89.214.49:5760` (MAVProxy's `tcpin`), mirror `127.0.0.1:14656`. It
   `Conflicts=` the ArduPilot `mavlink-relay.service` because both listen on
   `127.0.0.1:5679`, so `droneserver-staging` needs no env change to swap
   firmwares — only the relay does. Tap endpoint is therefore
   `udpin:127.0.0.1:14656`.

2. **The telemetry recorder needs its own forwarded UDP stream.** MAVProxy's
   `tcpin` serves the relay as its single client; a second MavSDK client on
   `tcpout://…:5760` does not get a working link. So `px4-mavbridge` was given a
   dedicated `--out=udpout:100.100.244.74:14540` forward (a systemd drop-in,
   `10-telemetry-forward.conf`), and the recorder uses the same
   `udp://:14540` listen endpoint the ArduPilot stack uses. Restore by removing
   the drop-in + `daemon-reload` + restart.

3. **PX4 logs are `.ulg`, nested by date** (`log/<YYYY-MM-DD>/HH_MM_SS.ulg`), so
   `--dataflash-remote llmuavpx4:/var/lib/px4-sitl/log` and
   `retain_remote_dataflash` must recurse (it does — `find -maxdepth 3`, added
   for this). No `LOG_FILE_DSRMROT` equivalent is needed: PX4's default
   `SDLOG_MODE=0` already logs from arm to disarm, so each flight is its own
   `.ulg` and a non-arming trial writes none — the same correctness the
   ArduPilot `.BIN` rotation gives.

4. **PX4-specific data-dictionary gaps, disclosed not "fixed":**
   - `ekf_ok` / `geofence_ok` are **blank on every PX4 row**. They are read from
     `SYS_STATUS` only when the autopilot declares the subsystem *present*, and
     PX4 v1.16.2 sets neither the AHRS nor the GEOFENCE present bit
     (present=`0x0200402f`). ArduPilot sets both. `verify_bundle` therefore
     reports these two columns rather than requiring them (see
     `TELEMETRY_FIRMWARE_HEALTH_COLUMNS`); `hdop`/`vdop`, which come off
     `GPS_RAW_INT` on every firmware, remain required and are what prove the
     tap's `raw_source` is wired.
   - `battery_pct` reads `0.0` on PX4 SITL (SIH publishes `BATTERY_STATUS.
     battery_remaining=0` while `SYS_STATUS` says 100%); the column is non-empty
     so the bundle is complete, but the value is not a real state of charge.
     Same disclosure as ArduPilot's `battery_pct` (Plan 23 §4c).
   - **T7's parameter is firmware-specific.** `WPNAV_SPEED` does not exist on
     PX4 (the read times out and T7 fails), so both harnesses take
     `--param-name`; PX4 uses `MPC_XY_CRUISE`. `get_home_position` works on PX4
     with no HOME-on-request quirk, and the server-side geofence (firmware-
     independent) still rejects T8 at range.

### The working PX4 invocation (scripted suite)

```bash
cd /root/droneserver
set -a; . /etc/droneserver/staging.env; set +a
KEY="$(printf '%s' "$SAFETY_API_KEYS" | cut -d, -f1 | cut -d: -f2)"

.venv/bin/python scripts/run_mission_suite.py \
  --url http://127.0.0.1:8090/sse --api-key "$KEY" \
  --missions T1,T2,T3,T4,T5,T6,T7,T8,T9 --trials 5 --label px4_n5 \
  --audit-log /var/lib/droneserver/audit.jsonl \
  --target-label "PX4 SITL (llmuavpx4)" \
  --capture --mavlink-endpoint udpin:127.0.0.1:14656 \
  --telemetry-address "udp://:14540" \
  --firmware PX4 --firmware-version "PX4 v1.16.2" \
  --param-name MPC_XY_CRUISE \
  --sitl-host llmuavpx4 \
  --dataflash-remote llmuavpx4:/var/lib/px4-sitl/log \
  --require-complete-capture
```

The LLM harness (`run_llm_missions.py`, what the N=5 campaign runs) takes the
identical capture flags plus `--param-name MPC_XY_CRUISE`.
