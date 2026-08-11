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
cd /root/droneserver || exit 2
set -a; . /root/llmuav.env; set +a
# The control-scope key is the FIRST comma-separated entry's value, not the
# second colon-field of the whole line: SAFETY_API_KEYS holds several
# scope:key pairs, so splitting the line on ":" alone silently picks whatever
# happens to sit there and can hand the campaign a telemetry-scope key. Same
# extraction as docs/capture_topology.md.
export DRONESERVER_API_KEY=$(grep "^SAFETY_API_KEYS=" /etc/droneserver/staging.env | cut -d= -f2- | cut -d, -f1 | cut -d: -f2)
export DRONESERVER_RECORDER_API_KEY=$(cat /root/llmuav-recorder.key)

MISSIONS="${MISSIONS:-T1,T2,T3,T4,T5,T6,T7,T8,T9}"
TRIALS="${TRIALS:-5}"
# Phase A: direct vendor APIs, uniform access path.
#
# gpt-5.2 and gemini-robotics-er-2-preview were MISSING from this list until
# 2026-08-10. They are in Plan 04's accepted matrix and both have flown at N=1;
# omitting them would have produced a comparison table with no OpenAI column at
# all - a reviewer comment we would have paid two days of campaign time to earn.
MODELS="${MODELS:-claude-opus-5 claude-sonnet-5 claude-haiku-4-5-20251001 gpt-5.2 gemini-3.1-pro-preview gemini-3.6-flash gemini-3.5-flash-lite gemini-robotics-er-2-preview grok-4.5 grok-4.20-0309-reasoning grok-4.20-0309-non-reasoning}"
# 45 trials/model at a few minutes each; generous so a slow model is not truncated.
PER_MODEL_TIMEOUT="${PER_MODEL_TIMEOUT:-21600}"

# --- per-key spend ceilings (Peter, 2026-08-10) ------------------------------
# The harness guard must bind BEFORE the provider's own cap: it stops cleanly
# between trials and logs "rerun to resume", whereas a provider cap hard-stops
# mid-trial with no warning - the failure that destroyed 80 trials on 08-08.
#
# These caps are CUMULATIVE over each key's whole life on this project (the
# ledger sums every past row for the key), NOT a per-run allowance - so a guard
# has to clear everything already spent on the key PLUS the remaining work.
#
# anthropic 300: ~$160.59 is already counted against this key, and re-flying all
#                three Anthropic models in full for one contiguous N=5
#                (opus 40 + sonnet 40 + haiku 40 ~= $87 corrected) would push the
#                cumulative to ~$248. 175 left only ~$14 of headroom and would
#                have BUDGET-stopped the resumed Opus almost immediately. 300
#                clears the ~$248 with margin. (Raised from 175, 2026-08-11.)
# google     75: deliberately under Peter's $125 provider cap, so we stop first.
# others    100: unchanged, within the standing rule.
budget_for() {
  case "$1" in
    anthropic) echo 300 ;;
    google)    echo 75  ;;
    *)         echo 100 ;;
  esac
}
provider_of() {
  case "$1" in
    claude*)      echo anthropic ;;
    gemini*)      echo google ;;
    grok*)        echo xai ;;
    gpt*|o[134]*) echo openai ;;
    *:*)          echo "${1%%:*}" ;;
    *)            echo unknown ;;
  esac
}

# --- dead-key handling ------------------------------------------------------
# run_llm_missions.py exits 3 when the provider would not serve the key for this
# model (out of credit, or the key rejected) and abandons that model's remaining
# trials itself. That is where the damage is bounded: at most a few trials, not
# the eighty the 2026-08-08 campaign lost.
#
# This script deliberately does NOT skip the rest of that provider's models.
# The temptation is obvious - two refusals on one key look like a dead account -
# but the asymmetry is decisive. Guessing wrong costs a few minutes per model;
# guessing wrong the other way costs an entire provider arm of the paper's data.
# And the evidence says guessing wrong is easy: on 2026-08-08
# gemini-3.1-pro-preview reported "out of credit" while gemini-3.6-flash then
# ran perfectly on the SAME key, and a per-model entitlement gap reads
# identically to a dead key from out here.
#
# Every refusal is counted and reprinted as a block at the end, so a genuinely
# dead key is unmissable to the human reading the log without the script having
# to act on a guess.
REFUSED=""

echo "=== N=5 campaign starting $(date -u +%FT%TZ) ==="
echo "missions=$MISSIONS trials=$TRIALS models=$(echo $MODELS | wc -w)"
echo

for model in $MODELS; do
  label="n5-$(echo "$model" | tr '/.' '__')"
  provider=$(provider_of "$model")
  budget=$(budget_for "$provider")
  echo "############ $model  ($(date -u +%FT%TZ))  [$provider, cap \$$budget] ############"
  timeout "$PER_MODEL_TIMEOUT" /root/.local/bin/uv run python scripts/run_llm_missions.py \
      --missions "$MISSIONS" --trials "$TRIALS" --model "$model" \
      --budget-usd "$budget" \
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
      2>&1 | grep -E -A2 "^model:|^price:|^budget:|PASS |FAIL |VOID|LINK |BUDGET|PROVIDER|spend:|spend on|capture:|degraded|ERROR|Error|Traceback|passed on"
  rc=${PIPESTATUS[0]}
  case "$rc" in
    # -A2 above keeps the two lines after a match, so a Traceback shows the
    # exception line that follows it rather than the useless word alone.
    0) verdict="all judged missions passed" ;;
    1) verdict="at least one mission FAILED on telemetry evidence" ;;
    2) verdict="NOT STARTABLE (bad model, unpinned aggregator, or no price)" ;;
    3) verdict="PROVIDER REFUSED THE KEY - remaining trials abandoned"
       REFUSED="$REFUSED $model" ;;
    4) verdict="missions ran but a Plan 19 capture bundle came out DEGRADED" ;;
    124) verdict="TIMED OUT after ${PER_MODEL_TIMEOUT}s - trials were cut off" ;;
    *) verdict="unexpected exit" ;;
  esac
  echo "############ end $model (exit $rc: $verdict) $(date -u +%FT%TZ) ############"
  echo
done

echo "=== N=5 campaign finished $(date -u +%FT%TZ) ==="
if [ -n "$REFUSED" ]; then
  echo
  echo "!!! the provider would not serve these models on the configured key:"
  for m in $REFUSED; do echo "!!!   $m"; done
  echo "!!! Nothing above is a result about those models. Check the balance and the"
  echo "!!! entitlements before re-running them - and note that one refusal can be"
  echo "!!! model-specific, so this list is evidence, not a diagnosis."
fi
