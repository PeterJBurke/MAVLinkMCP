#!/bin/bash
# Kimi K3 opportunistic re-retry probe/run for one farm lane (item C).
# Usage: farm_kimi_probe.sh LANE MISSIONS
set -u
LANE="$1"; MISSIONS="$2"

cd /root/droneserver || exit 2
set -a; . /root/llmuav.env; set +a
ENVFILE="/etc/droneserver/lane${LANE}.env"
export DRONESERVER_API_KEY=$(grep "^SAFETY_API_KEYS=" "$ENVFILE" | cut -d= -f2- | cut -d, -f1 | cut -d: -f2)
export DRONESERVER_RECORDER_API_KEY=$(cat /root/llmuav-recorder.key)

PORT=$((8091+LANE))
TAP=$((14650+10*LANE))
TEL=$((14541+10*LANE))
DFDIR="/opt/sitl/lane${LANE}/logs"
LABEL="orclean-lane${LANE}-moonshotai_kimi-k3"

# readiness watchdog v2
watchdog_ok=0
for attempt in 1 2 3; do
  if pgrep -f "bin/arducopter.*-I${LANE} " >/dev/null 2>&1; then
    watchdog_ok=1
    break
  fi
  echo "############ WATCHDOG: lane $LANE arducopter engine is DOWN (attempt $attempt) - restarting sitl@${LANE} ############"
  systemctl restart "sitl@${LANE}"
  sleep 3
done
if [ "$watchdog_ok" != "1" ]; then
  echo "############ WATCHDOG: lane $LANE arducopter engine did NOT come back after 3 restarts - ABORTING ############"
  exit 9
fi
proc_start_ts=$(systemctl show "sitl@${LANE}" -p ActiveEnterTimestamp --value)
ready=0
for i in $(seq 1 45); do
  if journalctl -u "sitl@${LANE}" --since "$proc_start_ts" --no-pager 2>/dev/null | grep -q "is using GPS"; then
    ready=1
    break
  fi
  sleep 1
done
if [ "$ready" = "1" ]; then
  echo "############ WATCHDOG: lane $LANE EKF/GPS ready ############"
else
  echo "############ WATCHDOG: lane $LANE did NOT confirm GPS-ready within 45s - proceeding anyway ############"
fi

echo "############ LANE $LANE kimi-k3 $MISSIONS ($(date -u +%FT%TZ)) ############"
timeout 3600 /root/.local/bin/uv run python scripts/run_llm_missions.py \
    --missions "$MISSIONS" --trials 1 \
    --model "moonshotai/kimi-k3" \
    --endpoint-only "Moonshot AI" \
    --budget-usd 100 \
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
    2>&1 | grep -E -A2 "PASS |FAIL |VOID|LINK |BUDGET|PROVIDER|spend:|spend on|capture:|degraded|ERROR|Error|Traceback|429|rate.limit"
rc=${PIPESTATUS[0]}
echo "############ LANE $LANE kimi-k3 done exit=$rc $(date -u +%FT%TZ) ############"
exit $rc
