#!/bin/bash
# Generic single-row refly runner for one AP farm lane.
# Usage: farm_refly_row.sh LANE MISSIONS TRIALS MODEL MAX_TURNS MAX_TRIAL_COST_USD BUDGET_USD LABEL_SUFFIX [INCLUDE_SLOW]
set -u
LANE="$1"; MISSIONS="$2"; TRIALS="$3"; MODEL="$4"; MAX_TURNS="$5"
MAX_TRIAL_COST_USD="$6"; BUDGET_USD="$7"; LABEL_SUFFIX="$8"; INCLUDE_SLOW="${9:-0}"

cd /root/droneserver || exit 2
set -a; . /root/llmuav.env; set +a
ENVFILE="/etc/droneserver/lane${LANE}.env"
export DRONESERVER_API_KEY=$(grep "^SAFETY_API_KEYS=" "$ENVFILE" | cut -d= -f2- | cut -d, -f1 | cut -d: -f2)
export DRONESERVER_RECORDER_API_KEY=$(cat /root/llmuav-recorder.key)

PORT=$((8091+LANE))
TAP=$((14650+10*LANE))
TEL=$((14541+10*LANE))
DFDIR="/opt/sitl/lane${LANE}/logs"
LABEL="refly-lane${LANE}-$(echo "$MODEL" | tr '/.' '__')-${LABEL_SUFFIX}"

EXTRA=()
if [ "$INCLUDE_SLOW" = "1" ]; then EXTRA+=(--include-slow); fi

# --- per-trial engine READINESS + LINK-LIVENESS watchdog (v3, 2026-08-20
# link-defect fix) ---
# v1 only checked the arducopter PROCESS existed and slept a flat 8s. That
# caught the farm-wide idle die-off fine, but 8s is nowhere near ArduCopter's
# real boot time (GPS 3D fix + EKF origin took ~40s on a clean boot) and two
# lanes proved it: trials launched <45s after a v1-triggered restart hit a raw
# MAVSDK "takeoff failed: FAILED: 'Failed'" immediately after arming (lane1
# tax61, lane2 tax41, lane4 tax30 t1) - a genuine control-path infra failure,
# not a model result. v2 restarted if the process was dead, then ALWAYS
# polled journalctl for THIS instance's own "using GPS" line before releasing
# the trial - but never restarted droneserver-lane@N, so its mavsdk_server
# stayed connected to the dead/replaced SITL instance and went half-dead
# (~16.5s action stalls > ArduCopter's ~10s auto-disarm window -> every
# takeoff rejected). v2's GPS-ready grep was also vulnerable to matching a
# STALE line already in the journal (anchored on systemd's
# ActiveEnterTimestamp, not the instant we issued the restart).
# v3 (scripts/farm_watchdog_lib.sh, shared with farm_t6_lane7_row.sh): after
# any sitl@N restart, waits for a FRESH "is using GPS" line anchored on our
# own restart timestamp, THEN restarts droneserver-lane@N and waits for /sse
# 200, THEN asserts get_in_air answers <2s (the exact probe that dies on an
# orphaned link) - one full recovery cycle on failure, then hard-stop.
. /root/droneserver/scripts/farm_watchdog_lib.sh
farm_lane_precheck "$LANE" "$PORT" "$DRONESERVER_API_KEY"
precheck_rc=$?
if [ "$precheck_rc" != "0" ]; then
  echo "############ WATCHDOG/LINK-LIVENESS PRECHECK FAILED for lane $LANE (rc=$precheck_rc) - ABORTING this row, not spending a trial ############"
  exit "$precheck_rc"
fi

# --- pre-trial safety precheck (added after the lane7 stuck-aircraft incident,
# 2026-08-20): confirm the aircraft is disarmed and on the ground - a prior
# trial's crash can leave it armed/airborne, which the EKF/GPS check above does
# not catch. Attempts RTL + polls up to ~90s; aborts this row rather than
# starting a fresh trial against an aircraft already in the air.
if ! /root/droneserver/.venv/bin/python3 scripts/safety_precheck.py "http://127.0.0.1:${PORT}/sse" "$DRONESERVER_API_KEY"; then
  echo "############ SAFETY PRECHECK FAILED for lane $LANE - aircraft not confirmed safe, ABORTING this row ############"
  exit 10
fi

echo "############ LANE $LANE $MODEL $MISSIONS trials=$TRIALS turns=$MAX_TURNS ($(date -u +%FT%TZ)) ############"
timeout 5400 /root/.local/bin/uv run python scripts/run_llm_missions.py \
    --missions "$MISSIONS" --trials "$TRIALS" --model "$MODEL" \
    "${EXTRA[@]}" \
    --max-turns "$MAX_TURNS" \
    --max-trial-cost-usd "$MAX_TRIAL_COST_USD" \
    --budget-usd "$BUDGET_USD" \
    --url "http://127.0.0.1:${PORT}/sse" \
    --audit-log "/var/lib/droneserver/lane${LANE}/audit.jsonl" \
    --target-label "ArduPilot SITL (llmuavfarm lane${LANE})" \
    --label "$LABEL" \
    --link-recovery-command "systemctl restart droneserver-lane@${LANE}" \
    --capture --mavlink-endpoint "udpin:127.0.0.1:${TAP}" \
    --telemetry-address "udpin://127.0.0.1:${TEL}" \
    --firmware ArduCopter --firmware-version "ArduCopter V4.7.0-dev (c683d8c1) (SITL)" \
    --sitl-host "llmuavfarm-lane${LANE}" \
    --dataflash-dir "$DFDIR" \
    --require-complete-capture \
    2>&1 | grep -E -A2 "PASS |FAIL |VOID|LINK |BUDGET|PROVIDER|spend:|spend on|capture:|degraded|ERROR|Error|Traceback"
rc=${PIPESTATUS[0]}
echo "############ LANE $LANE $MODEL done exit=$rc $(date -u +%FT%TZ) ############"
exit $rc
