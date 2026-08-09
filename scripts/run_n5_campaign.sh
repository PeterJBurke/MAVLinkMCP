#!/bin/bash
# N=5 benchmark campaign — the statistical core of the paper (Plan 08).
#
#   T1-T9, five trials per mission per model, full Plan-19 capture on every trial.
#   T10 is NOT here: Plan 08 decoupled it to N=1 on 3-4 models (it tests a server
#   property, not a model property). Run T10 separately with --include-slow.
#
# Phase A (this script's default) is the DIRECT-API models only. That is not an
# oversight: Plan 04's measurement protocol requires the headline comparison to
# run on one uniform access path, so the OpenRouter arm is a separate phase and
# is reported as a separate arm (decided 2026-08-09).
#
#   MODELS="..." bash scripts/run_n5_campaign.sh     # override the list
#   MISSIONS=T1,T2 TRIALS=1 bash scripts/run_n5_campaign.sh   # short rehearsal
#
# One model at a time, always: they share a single simulated aircraft.
set -u
cd /root/droneserver
set -a; . /root/llmuav.env; set +a
export DRONESERVER_API_KEY=$(grep "^SAFETY_API_KEYS=" /etc/droneserver/staging.env | cut -d= -f2 | cut -d: -f2)
export DRONESERVER_RECORDER_API_KEY=$(cat /root/llmuav-recorder.key)

MISSIONS="${MISSIONS:-T1,T2,T3,T4,T5,T6,T7,T8,T9}"
TRIALS="${TRIALS:-5}"
# Phase A: direct vendor APIs, uniform access path.
MODELS="${MODELS:-claude-opus-5 claude-sonnet-5 claude-haiku-4-5-20251001 gemini-3.1-pro-preview gemini-3.6-flash gemini-3.5-flash-lite grok-4.5 grok-4.20-0309-reasoning grok-4.20-0309-non-reasoning}"
# 45 trials/model at a few minutes each; generous so a slow model is not truncated.
PER_MODEL_TIMEOUT="${PER_MODEL_TIMEOUT:-21600}"

# --- dead-key handling ------------------------------------------------------
# run_llm_missions.py exits 3 when the provider would not serve the key at all
# (out of credit, or the key rejected) and abandons that model's remaining
# trials. ONE such exit is not proof of a dead key: on 2026-08-08
# gemini-3.1-pro-preview reported "out of credit" and gemini-3.6-flash then ran
# perfectly on the SAME key, so a single message can be about the model (or the
# entitlement) rather than the account. TWO different models of the same
# provider failing that way is a key, not a model - so the rest of that
# provider's models are skipped instead of each burning a startup and a trial
# to learn the same thing.
declare -A PROVIDER_FATAL=()
provider_of() {  # crude but sufficient: the campaign list is direct-API models
  case "$1" in
    claude*)  echo anthropic ;;
    gemini*)  echo google ;;
    grok*)    echo xai ;;
    gpt*|o[134]*) echo openai ;;
    *:*)      echo "${1%%:*}" ;;
    *)        echo unknown ;;
  esac
}

echo "=== N=5 campaign starting $(date -u +%FT%TZ) ==="
echo "missions=$MISSIONS trials=$TRIALS models=$(echo $MODELS | wc -w)"
echo

for model in $MODELS; do
  provider=$(provider_of "$model")
  if [ "${PROVIDER_FATAL[$provider]:-0}" -ge 2 ]; then
    echo "############ SKIP $model - $provider rejected the key on ${PROVIDER_FATAL[$provider]} models already ############"
    echo
    continue
  fi
  label="n5-$(echo "$model" | tr '/.' '__')"
  echo "############ $model  ($(date -u +%FT%TZ)) ############"
  timeout "$PER_MODEL_TIMEOUT" /root/.local/bin/uv run python scripts/run_llm_missions.py \
      --missions "$MISSIONS" --trials "$TRIALS" --model "$model" \
      --url http://127.0.0.1:8090/sse \
      --audit-log /var/lib/droneserver/audit.jsonl \
      --target-label "ArduPilot SITL (llmuavsitl)" \
      --label "$label" \
      --link-recovery-command "systemctl restart droneserver-staging" \
      --capture --mavlink-endpoint udpin:127.0.0.1:14655 \
      --telemetry-address "udp://:14540" \
      --firmware ArduCopter --firmware-version "ArduCopter 4.5.7 (SITL)" \
      --sitl-host llmuavsitl \
      --dataflash-remote llmuavsitl:/home/dronepilot/ardupilot/ArduCopter/logs \
      --require-complete-capture \
      2>&1 | grep -E "^model:|^price:|^budget:|PASS |FAIL |VOID|LINK |BUDGET|PROVIDER|spend:|spend on|capture:|degraded|ERROR|Traceback|passed on"
  rc=${PIPESTATUS[0]}
  if [ "$rc" -eq 3 ]; then
    PROVIDER_FATAL[$provider]=$(( ${PROVIDER_FATAL[$provider]:-0} + 1 ))
    echo "!!! $provider would not serve $model (dead-key count for $provider: ${PROVIDER_FATAL[$provider]})"
  fi
  echo "############ end $model (exit $rc) $(date -u +%FT%TZ) ############"
  echo
done

echo "=== N=5 campaign finished $(date -u +%FT%TZ) ==="
