#!/bin/bash
set -o pipefail
cd /root/droneserver
set -a; . /etc/droneserver/staging.env; set +a
KEY=$(grep "^SAFETY_API_KEYS=" /etc/droneserver/staging.env | cut -d= -f2 | cut -d, -f1 | cut -d: -f2)
exec .venv/bin/python -u scripts/run_mission_suite.py \
  --url http://127.0.0.1:8090/sse --api-key "$KEY" \
  --missions T1,T2,T3,T4,T5,T7,T8,T9 --trials 1 --label postfix_verify \
  --audit-log /var/lib/droneserver/audit.jsonl \
  --target-label "ArduPilot SITL (llmuavsitl)" \
  --capture --mavlink-endpoint udpin:127.0.0.1:14655 \
  --telemetry-address "udp://:14540" \
  --firmware ArduCopter --firmware-version "ArduCopter 4.5.7 (SITL)" \
  --sitl-host llmuavsitl \
  --dataflash-remote llmuavsitl:/home/dronepilot/ardupilot/ArduCopter/logs \
  --require-complete-capture
