#!/bin/bash
# LOCAL open-weight arm (Plan 04's local-LLM row) — N=1 trials, T1-T9, full
# Plan-19 capture, ArduPilot SITL, against LM Studio on Peter's MacBook over
# the tailnet. This is a COPY of run_n5_campaign.sh's structure, not the same
# script, because the local arm differs in ways worth keeping visually
# separate from the direct-API N=5 campaign:
#
#   * N=1, not N=5 - local inference is slow and this arm is a first-class
#     narrative datapoint (control/privacy/reproducibility), not the paper's
#     statistical core.
#   * ONE model at a time is not just cheapest here, it is REQUIRED: all three
#     models share one simulated aircraft AND the Mac's 16 GB of RAM. LM
#     Studio JIT-loads on request but does NOT auto-evict a resident model to
#     make room for another - loading a second model while a first is loaded
#     fails with a "insufficient system resources" guardrail error, and there
#     is no HTTP or SSH path from this box to unload one remotely. Swapping
#     between these three models requires Peter to unload the previous one by
#     hand in the LM Studio app before the next model in MODELS can load.
#   * telemetry address is udp://:14650, NOT :14540. T10's 2026-08-16 refly
#     proved 14540 receives both simulators' streams under the same sysid
#     (interleaved telemetry.csv = a degraded capture bundle); 14650 is fed
#     only by llmuavsitl. --mavlink-endpoint stays udpin:127.0.0.1:14655.
#   * gpt-oss-20b and any Llama model are deliberately NOT in MODELS - pending
#     Peter's word (Llama isn't even downloaded on the Mac yet).
#
#   MODELS="..." bash scripts/run_local_arm.sh     # override the list
#   MISSIONS=T1,T2 TRIALS=1 bash scripts/run_local_arm.sh   # short rehearsal
set -u
cd /root/droneserver || exit 2
set -a; . /root/llmuav.env; set +a
export DRONESERVER_API_KEY=$(grep "^SAFETY_API_KEYS=" /etc/droneserver/staging.env | cut -d= -f2- | cut -d, -f1 | cut -d: -f2)
export DRONESERVER_RECORDER_API_KEY=$(cat /root/llmuav-recorder.key)

MISSIONS="${MISSIONS:-T1,T2,T3,T4,T5,T6,T7,T8,T9}"
TRIALS="${TRIALS:-1}"
# Order matters here more than in the N=5 script: qwen3-4b-thinking-2507 is
# whichever model is CURRENTLY LOADED in LM Studio should run first, since no
# load is needed for it and every swap to a different model is the operation
# that can fail on the 16 GB guardrail. Callers who already unloaded
# everything can pass any order via MODELS=.
MODELS="${MODELS:-lmstudio:qwen/qwen3-4b-thinking-2507 lmstudio:qwen2.5-7b-instruct lmstudio:google/gemma-4-e4b}"
# Local inference is slow; a full T1-T9 x 1-trial pass can take hours per
# model. Generous so a slow model is not truncated overnight.
PER_MODEL_TIMEOUT="${PER_MODEL_TIMEOUT:-28800}"
# ModelSession's per-turn HTTP deadline defaults to 240s (providers.py), sized
# for hosted APIs. Local inference on the Mac is far slower - a "thinking"
# model's first turn has to prefill the ~10k-token MCP tool schema PLUS
# whatever the mission's system/user prompt adds, then spend possibly
# hundreds of tokens on reasoning before it ever calls a tool - and measured
# during preflight at a composite ~3.6 tokens/sec (prefill-dominated) on
# qwen3-4b-thinking-2507. 240s is not enough: it produced 3 straight
# "no reply within 240s" VOIDs and a PROVIDER STOP on the first attempt
# tonight (2026-08-16 03:21Z), abandoning a model that never actually failed.
# --model-timeout-s (added to run_llm_missions.py for this arm) raises that
# per-turn deadline; --trial-timeout-s raises the whole-trial wall clock to
# match, since a slow first turn should not by itself exhaust the trial.
MODEL_TIMEOUT_S="${MODEL_TIMEOUT_S:-600}"
TRIAL_TIMEOUT_S="${TRIAL_TIMEOUT_S:-2400}"

# lmstudio prices are all $0.00 (docs/model_prices.json), so this budget never
# binds - it exists only because run_llm_missions.py refuses to run without
# one to enforce. Kept at the standing default for uniformity with the other
# wrappers, not because spend is possible here.
budget_for() { echo 100; }
provider_of() { echo lmstudio; }

REFUSED=""

echo "=== LOCAL arm starting $(date -u +%FT%TZ) ==="
echo "missions=$MISSIONS trials=$TRIALS models=$(echo $MODELS | wc -w)"
echo

for model in $MODELS; do
  # local-<model with / and . replaced by _>
  bare="${model#lmstudio:}"
  label="local-$(echo "$bare" | tr '/.' '__')"
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
      --telemetry-address "udp://:14650" \
      --model-timeout-s "$MODEL_TIMEOUT_S" \
      --trial-timeout-s "$TRIAL_TIMEOUT_S" \
      --firmware ArduCopter --firmware-version "ArduCopter V4.7.0-dev (c683d8c1) (SITL)" \
      --sitl-host llmuavsitl \
      --dataflash-remote llmuavsitl:/home/dronepilot/ardupilot/ArduCopter/logs \
      --require-complete-capture \
      2>&1 | grep -E -A2 "^model:|^protocol:|^price:|^budget:|PASS |FAIL |VOID|LINK |BUDGET|PROVIDER|ACCOMMODATION|spend:|spend on|capture:|degraded|ERROR|Error|Traceback|HARNESS CRASH|passed on"
  rc=${PIPESTATUS[0]}
  case "$rc" in
    0) verdict="all judged missions passed" ;;
    1) verdict="at least one mission FAILED on telemetry evidence" ;;
    2) verdict="NOT STARTABLE (bad model, unpinned aggregator, or no price)" ;;
    3) verdict="PROVIDER REFUSED THE KEY - remaining trials abandoned"
       REFUSED="$REFUSED $model" ;;
    4) verdict="missions ran but a Plan 19 capture bundle came out DEGRADED" ;;
    # 5 = OUR harness crashed mid-trial. A VOID row naming the exception is in
    #     missions.csv and the harness tried to land the aircraft on its way
    #     out - but nothing here is a result about the model, and the vehicle
    #     should be looked at before anything else flies.
    5) verdict="THE HARNESS CRASHED mid-trial - trial recorded VOID; CHECK THE AIRCRAFT" ;;
    124) verdict="TIMED OUT after ${PER_MODEL_TIMEOUT}s - trials were cut off" ;;
    *) verdict="unexpected exit" ;;
  esac
  echo "############ end $model (exit $rc: $verdict) $(date -u +%FT%TZ) ############"
  echo
done

echo "=== LOCAL arm finished $(date -u +%FT%TZ) ==="
if [ -n "$REFUSED" ]; then
  echo
  echo "!!! the endpoint would not serve these models on the configured connection:"
  for m in $REFUSED; do echo "!!!   $m"; done
fi
