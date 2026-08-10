#!/bin/bash
# Bounded LLM-in-the-loop verification run. NOT the campaign.
set -u
cd /root/droneserver || exit 2
set -a; . /root/llmuav.env; set +a
export DRONESERVER_API_KEY=$(grep "^SAFETY_API_KEYS=" /etc/droneserver/staging.env | cut -d= -f2- | cut -d, -f1 | cut -d: -f2)
export DRONESERVER_RECORDER_API_KEY=$(cat /root/llmuav-recorder.key)

MODEL="$1"; MISSIONS="$2"; LABEL="$3"; BUDGET="$4"
exec .venv/bin/python -u scripts/run_llm_missions.py \
    --missions "$MISSIONS" --trials 1 --model "$MODEL" \
    --url http://127.0.0.1:8090/sse \
    --audit-log /var/lib/droneserver/audit.jsonl \
    --target-label "ArduPilot SITL (llmuavsitl)" \
    --label "$LABEL" \
    --budget-usd "$BUDGET" \
    --capture --mavlink-endpoint udpin:127.0.0.1:14655 \
    --telemetry-address "udp://:14540" \
    --firmware ArduCopter --firmware-version "ArduCopter 4.5.7 (SITL)" \
    --sitl-host llmuavsitl \
    --dataflash-remote llmuavsitl:/home/dronepilot/ardupilot/ArduCopter/logs \
    --require-complete-capture
