#!/bin/bash
# Free error-classification probes: no valid provider call is ever made, so no
# tokens are billed. Confirms the harness distinguishes
#   (a) a key the provider rejects  -> FATAL, abandon that model, exit 3
#   (b) a model id that does not exist -> NOT STARTABLE, exit 2
set -u
cd /root/droneserver || exit 2
set -a; . /root/llmuav.env; set +a
export DRONESERVER_API_KEY=$(grep "^SAFETY_API_KEYS=" /etc/droneserver/staging.env | cut -d= -f2- | cut -d, -f1 | cut -d: -f2)
export DRONESERVER_RECORDER_API_KEY=$(cat /root/llmuav-recorder.key)

echo "###### PROBE A: rejected key (anthropic) ######"
ANTHROPIC_API_KEY="sk-ant-invalid-key-for-classification-probe" \
  timeout 600 .venv/bin/python -u scripts/run_llm_missions.py \
    --missions T1 --trials 1 --model claude-haiku-4-5-20251001 \
    --url http://127.0.0.1:8090/sse --label probe_badkey \
    --target-label "ArduPilot SITL (llmuavsitl)" --budget-usd 1
echo "PROBE A exit=$?"

echo "###### PROBE B: model id that does not exist ######"
timeout 300 .venv/bin/python -u scripts/run_llm_missions.py \
    --missions T1 --trials 1 --model claude-does-not-exist-9 \
    --url http://127.0.0.1:8090/sse --label probe_badmodel \
    --target-label "ArduPilot SITL (llmuavsitl)" --budget-usd 1
echo "PROBE B exit=$?"
