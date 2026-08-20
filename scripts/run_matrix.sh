#!/bin/bash
# N=1 validation pass across the approved model matrix.
#
# Five missions per model, chosen for coverage rather than completeness:
#   T1  basic flight                  - does the loop work at all for this provider
#   T5  fly out and return-to-launch   - the mission most sensitive to whether a
#                                        model waits for a command to finish
#   T7  parameter read/write           - exercises the confirmation handshake
#   T8  geofence violation             - must be refused
#   T9  prompt injection               - must be refused
#
# Run one model at a time: they share a single simulated aircraft.
set -u
cd /root/droneserver
set -a; . /root/llmuav.env; set +a
export DRONESERVER_API_KEY=$(grep "^SAFETY_API_KEYS=" /etc/droneserver/staging.env | cut -d= -f2 | cut -d: -f2)
export DRONESERVER_RECORDER_API_KEY=$(cat /root/llmuav-recorder.key)

MISSIONS="${MISSIONS:-T1,T5,T7,T8,T9}"
MODELS="${MODELS:-claude-opus-5 claude-sonnet-5 claude-haiku-4-5-20251001 gemini-3.1-pro-preview gemini-3.6-flash gemini-3.5-flash-lite grok-4.5 grok-4.20-0309-reasoning grok-4.20-0309-non-reasoning}"

for model in $MODELS; do
  label="matrix-$(echo "$model" | tr '/.' '__')"
  echo "############ $model ############"
  timeout 2400 /root/.local/bin/uv run python scripts/run_llm_missions.py \
      --missions "$MISSIONS" --model "$model" \
      --url http://127.0.0.1:8090/sse \
      --audit-log /var/lib/droneserver/audit.jsonl \
      --target-label "llmuavsitl (ArduPilot SITL over tailnet)" \
      --label "$label" \
      --link-recovery-command "systemctl restart droneserver-staging" \
      2>&1 | grep -E "^model:|^price:|^budget:|PASS |FAIL |LINK |BUDGET|spend:|spend on|ERROR|Traceback|HARNESS CRASH|passed on"
  echo "############ end $model (exit $?) ############"
done
