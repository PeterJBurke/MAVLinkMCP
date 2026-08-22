#!/bin/bash
# PX4 T4-refly-manifest runner, farm lanes 8/9 (mirrors scripts/px4_t4_refly.sh,
# adapted per lane per PX4-CONDUCTOR briefing 2026-08-19/20).
#
# Usage:
#   LANE=8 MODELS="gemini-3.5-flash-lite" MISSIONS="T2,T3,T4,T5" TRIALS=5 \
#     BUDGET=260 LABEL_PREFIX=px4refly bash px4refly_farm.sh
set -u
cd /root/droneserver || exit 2
set -a; . /root/llmuav.env; set +a

LANE="${LANE:?set LANE=8 or 9}"
MODELS="${MODELS:?set MODELS}"
MISSIONS="${MISSIONS:?set MISSIONS}"
TRIALS="${TRIALS:?set TRIALS}"
BUDGET="${BUDGET:?set BUDGET}"
LABEL_PREFIX="${LABEL_PREFIX:-px4refly}"
PER_MODEL_TIMEOUT="${PER_MODEL_TIMEOUT:-5400}"
# Turn-limit-raise re-runs (Plan 35 S1(d)): pass MAX_TURNS=150 and this scales
# max-tool-calls/trial-timeout-s proportionally (150/90 = 1.667x) unless
# explicitly overridden.
MAX_TURNS="${MAX_TURNS:-90}"
MAX_TOOL_CALLS="${MAX_TOOL_CALLS:-$(python3 -c "print(round(250*$MAX_TURNS/90))")}"
TRIAL_TIMEOUT_S="${TRIAL_TIMEOUT_S:-$(python3 -c "print(round(1800*$MAX_TURNS/90))")}"

case "$LANE" in
  8) MCP_PORT=8099; MAV_TAP=14730; MAV_REC=14621; VEHICLE_SYSID=9 ;;
  9) MCP_PORT=8100; MAV_TAP=14740; MAV_REC=14631; VEHICLE_SYSID=10 ;;
  *) echo "bad LANE=$LANE"; exit 2 ;;
esac

export DRONESERVER_API_KEY=$(grep "^SAFETY_API_KEYS=" /etc/droneserver/lane${LANE}.env | cut -d= -f2- | cut -d, -f1 | cut -d: -f2)
export DRONESERVER_RECORDER_API_KEY=$(cat /root/llmuav-recorder.key)

TODAY_UTC=$(date -u +%Y-%m-%d)
DATAFLASH_DIR="/opt/sitl/lane${LANE}/log/${TODAY_UTC}"

echo "=== PX4 refly-manifest, lane $LANE, $(date -u +%FT%TZ) ==="
echo "missions=$MISSIONS trials=$TRIALS budget=\$$BUDGET models: $MODELS"
echo "dataflash dir: $DATAFLASH_DIR"
echo

REFUSED=""
for model in $MODELS; do
  label="${LABEL_PREFIX}-$(echo "$model" | tr '/.' '__')"
  echo "############ lane$LANE $model  ($(date -u +%FT%TZ))  [cap \$$BUDGET] ############"
  timeout "$PER_MODEL_TIMEOUT" /root/.local/bin/uv run python scripts/run_llm_missions.py \
      --missions "$MISSIONS" --trials "$TRIALS" --model "$model" \
      --budget-usd "$BUDGET" \
      --url "http://127.0.0.1:${MCP_PORT}/sse" \
      --audit-log "/var/lib/droneserver/lane${LANE}/audit.jsonl" \
      --target-label "PX4 SITL lane${LANE} (llmuavfarm)" \
      --label "$label" \
      --out benchmark_runs \
      --link-recovery-command "systemctl restart droneserver-lane@${LANE}" \
      --capture --mavlink-endpoint "udpin:127.0.0.1:${MAV_TAP}" \
      --telemetry-address "udpin://127.0.0.1:${MAV_REC}" \
      --firmware PX4 --firmware-version "PX4 v1.16.2" \
      --sitl-host "llmuavfarm-lane${LANE}" \
      --dataflash-dir "$DATAFLASH_DIR" \
      --vehicle-sysid "$VEHICLE_SYSID" \
      --max-turns "$MAX_TURNS" --max-tool-calls "$MAX_TOOL_CALLS" --trial-timeout-s "$TRIAL_TIMEOUT_S" \
      --param-name MPC_XY_CRUISE --param-write-value 8.0 \
      --require-complete-capture \
      2>&1 | grep -E -A2 "^model:|^price:|^budget:|PASS |FAIL |VOID|LINK |BUDGET|PROVIDER|spend:|spend on|capture:|degraded|ERROR|Error|Traceback|passed on"
  rc=${PIPESTATUS[0]}
  case "$rc" in
    0) verdict="all judged missions passed" ;;
    1) verdict="at least one mission FAILED on telemetry evidence" ;;
    2) verdict="NOT STARTABLE" ;;
    3) verdict="PROVIDER REFUSED THE KEY"; REFUSED="$REFUSED $model" ;;
    4) verdict="missions ran but capture bundle DEGRADED" ;;
    124) verdict="TIMED OUT after ${PER_MODEL_TIMEOUT}s" ;;
    *) verdict="unexpected exit" ;;
  esac
  echo "############ end lane$LANE $model (exit $rc: $verdict) $(date -u +%FT%TZ) ############"
  echo
done

echo "=== lane $LANE finished $(date -u +%FT%TZ) ==="
if [ -n "$REFUSED" ]; then
  echo "!!! refused models:$REFUSED"
fi
