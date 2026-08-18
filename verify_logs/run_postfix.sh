#!/bin/bash
set -o pipefail
cd /root/droneserver
set -a; . /etc/droneserver/staging.env; set +a
KEY=$(grep "^SAFETY_API_KEYS=" /etc/droneserver/staging.env | cut -d= -f2 | cut -d, -f1 | cut -d: -f2)
# TELEMETRY-ADDRESS TOPOLOGY NOTE (2026-08-18) -- READ BEFORE THE NEXT RUN.
# The --telemetry-address below ("udp://:14540") binds EVERY source on that
# port, and the harness now REFUSES it at startup. That address is what let a
# second, idle SITL feed the MavSDK telemetry recorder and put two aircraft
# into single telemetry.csv rows across 472 trials -- see
# /root/LLMUAV/Research/PX4-TELEMETRY-CONTAMINATION-VERIFICATION_2026-08-18.md
# and llm_runs/CHANGELOG-TELEMETRY-CLEAN.md. Resolve it ONE of two ways; the
# choice is a statement about the network, not a preference:
#   (a) PREFERRED -- give this firmware's recorder its OWN mirror port
#       ('mavlink_relay.py --mirror' is now repeatable, so one port feeds the
#       MAVLink tap and another feeds the recorder) and point
#       --telemetry-address at it. scripts/run_local_arm.sh already does this
#       with :14650, "fed only by llmuavsitl".
#   (b) add --allow-shared-telemetry-bind, which asserts that exactly ONE
#       autopilot can reach this port. capture/verify.py's "telemetry.csv
#       single-vehicle" check fails the bundle if that turns out to be false.
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
