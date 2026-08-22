#!/bin/bash
# T6 refly runner for lane 7 (second T6 lane, canonical field home, 15km fence).
# Usage: farm_t6_lane7_row.sh TRIALS MODEL LABEL_SUFFIX
set -u
TRIALS="$1"; MODEL="$2"; LABEL_SUFFIX="$3"
LANE=7

cd /root/droneserver || exit 2
set -a; . /root/llmuav.env; set +a
ENVFILE="/etc/droneserver/lane${LANE}.env"
export DRONESERVER_API_KEY=$(grep "^SAFETY_API_KEYS=" "$ENVFILE" | cut -d= -f2- | cut -d, -f1 | cut -d: -f2)
export DRONESERVER_RECORDER_API_KEY=$(cat /root/llmuav-recorder.key)

PORT=$((8091+LANE))
TAP=$((14650+10*LANE))
TEL=$((14541+10*LANE))
DFDIR="/opt/sitl/lane${LANE}/logs"
LABEL="${LABEL_PREFIX:-t6refly}-lane${LANE}-$(echo "$MODEL" | tr '/.' '__')-${LABEL_SUFFIX}"

# per-trial engine READINESS + LINK-LIVENESS watchdog v3 (2026-08-20 link-defect
# fix, shared with farm_refly_row.sh - see scripts/farm_watchdog_lib.sh for the
# full rationale: v2 restarted sitl@N on engine death but never restarted
# droneserver-lane@N, orphaning its mavsdk_server into a half-dead link;
# ~16.5s action stalls > ArduCopter's ~10s auto-disarm window meant every
# takeoff got rejected. v3 restarts droneserver-lane@N right after any sitl@N
# restart (anchored on our own restart timestamp, not systemd's
# ActiveEnterTimestamp, which could satisfy the old GPS-ready grep with a
# stale pre-restart line) and asserts get_in_air answers <2s before flying.
. /root/droneserver/scripts/farm_watchdog_lib.sh
farm_lane_precheck "$LANE" "$PORT" "$DRONESERVER_API_KEY"
precheck_rc=$?
if [ "$precheck_rc" != "0" ]; then
  echo "############ WATCHDOG/LINK-LIVENESS PRECHECK FAILED for lane $LANE (rc=$precheck_rc) - ABORTING this row ############"
  exit "$precheck_rc"
fi

# --- pre-trial safety precheck (added after this lane's stuck-aircraft
# incident, 2026-08-20): see farm_refly_row.sh for the full rationale.
if ! /root/droneserver/.venv/bin/python3 scripts/safety_precheck.py "http://127.0.0.1:${PORT}/sse" "$DRONESERVER_API_KEY"; then
  echo "############ SAFETY PRECHECK FAILED for lane $LANE - aircraft not confirmed safe, ABORTING this row ############"
  exit 10
fi

echo "############ LANE $LANE T6 $MODEL trials=$TRIALS ($(date -u +%FT%TZ)) ############"
timeout 5400 /root/.local/bin/uv run python scripts/run_llm_missions.py \
    --missions T6 --trials "$TRIALS" --model "$MODEL" \
    --max-turns 200 \
    --max-trial-cost-usd 12 \
    --trial-timeout-s 3600 \
    --geofence-radius-m 15000 \
    --maps-url "https://mapstools.googleapis.com/mcp" \
    --budget-usd "${BUDGET_USD_OVERRIDE:-260}" \
    --url "http://127.0.0.1:${PORT}/sse" \
    --audit-log "/var/lib/droneserver/lane${LANE}/audit.jsonl" \
    --target-label "ArduPilot SITL (llmuavfarm lane${LANE}, T6 field home)" \
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
echo "############ LANE $LANE T6 $MODEL done exit=$rc $(date -u +%FT%TZ) ############"
exit $rc
