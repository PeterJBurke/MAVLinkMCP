#!/bin/bash
# Kimi K3 retry - OpenRouter arm completion (Peter's request 2026-08-19).
#
# The two 2026-08-11 kimi-k3 attempts died to broker faults (no scored tally,
# capture incomplete). A 2026-08-19 probe shows the broker serving the model
# again (provider Phala, clean completion), so this re-runs the full OR-arm
# protocol for the one missing row: N=1, T1-T9 minus T6, ArduPilot SITL,
# suite-default fence (RUN AFTER the T6-N5 work restores staging to 1000 m).
# Label matches the arm's orclean- convention so discovery/consolidation
# pick it up as the OR arm's row.
set -u
cd /root/droneserver || exit 2
set -a; . /root/llmuav.env; set +a
export DRONESERVER_API_KEY=$(grep "^SAFETY_API_KEYS=" /etc/droneserver/staging.env | cut -d= -f2- | cut -d, -f1 | cut -d: -f2)
export DRONESERVER_RECORDER_API_KEY=$(cat /root/llmuav-recorder.key)
echo "############ kimi-k3 OR retry ($(date -u +%FT%TZ)) ############"
timeout 21600 /root/.local/bin/uv run python scripts/run_llm_missions.py \
    --missions T1,T2,T3,T4,T5,T7,T8,T9 --trials 1 \
    --model "moonshotai/kimi-k3" \
    --endpoint-only "Moonshot AI" \
    --budget-usd 100 \
    --url http://127.0.0.1:8090/sse \
    --audit-log /var/lib/droneserver/audit.jsonl \
    --target-label "ArduPilot SITL (llmuavsitl)" \
    --label "orclean-moonshotai_kimi-k3" \
    --link-recovery-command "systemctl restart droneserver-staging" \
    --capture --mavlink-endpoint udpin:127.0.0.1:14655 \
    --telemetry-address "udpin://127.0.0.1:14541" \
    --firmware ArduCopter --firmware-version "ArduCopter V4.7.0-dev (c683d8c1) (SITL)" \
    --sitl-host llmuavsitl \
    --dataflash-remote llmuavsitl:/home/dronepilot/ardupilot/ArduCopter/logs \
    --require-complete-capture \
    2>&1 | grep -E -A2 "PASS |FAIL |VOID|LINK |BUDGET|PROVIDER|spend:|spend on|capture:|degraded|ERROR|Error|Traceback"
echo "=== kimi retry finished $(date -u +%FT%TZ) ==="
