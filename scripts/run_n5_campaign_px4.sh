#!/bin/bash
# N=5 benchmark campaign — PX4 arm (the "both firmwares" evidence, Plan 08/23).
#
# This is the PX4 twin of run_n5_campaign.sh. Same 11 direct-API models, same
# T1-T9 x 5 trials with full Plan-19 capture, but flown against PX4 SITL on
# llmuavpx4 instead of ArduCopter on llmuavsitl. Differences from the ArduPilot
# script, all forced by the firmware/target:
#
#   --firmware PX4, --sitl-host llmuavpx4, tap on 14656, dataflash under
#   /var/lib/px4-sitl/log (PX4 nests logs by date; capture searches -maxdepth 3),
#   and T7 uses MPC_XY_CRUISE with an in-range --param-write-value (WPNAV_SPEED
#   does not exist on PX4, and MPC_XY_CRUISE maxes at 12 m/s so "original+10"
#   clamps and T7 fails - see commit c979609).
#
#   MODELS="..." bash scripts/run_n5_campaign_px4.sh          # override list
#   MISSIONS=T1,T7 TRIALS=1 bash scripts/run_n5_campaign_px4.sh   # rehearsal
#
# One model at a time, always: they share a single simulated aircraft.
set -u
cd /root/droneserver || exit 2
set -a; . /root/llmuav.env; set +a
export DRONESERVER_API_KEY=$(grep "^SAFETY_API_KEYS=" /etc/droneserver/staging.env | cut -d= -f2- | cut -d, -f1 | cut -d: -f2)
export DRONESERVER_RECORDER_API_KEY=$(cat /root/llmuav-recorder.key)

MISSIONS="${MISSIONS:-T1,T2,T3,T4,T5,T6,T7,T8,T9}"
TRIALS="${TRIALS:-5}"
MODELS="${MODELS:-claude-opus-5 claude-sonnet-5 claude-haiku-4-5-20251001 gpt-5.2 gemini-3.1-pro-preview gemini-3.6-flash gemini-3.5-flash-lite gemini-robotics-er-2-preview grok-4.5 grok-4.20-0309-reasoning grok-4.20-0309-non-reasoning}"
PER_MODEL_TIMEOUT="${PER_MODEL_TIMEOUT:-21600}"

# --- per-key spend ceilings (CUMULATIVE over each key's whole life) ----------
# The ledger sums every past row for the key, so a guard must clear everything
# ALREADY spent plus the PX4 arm's work. State at 2026-08-12, before this arm:
#   anthropic ~$198, google ~$101, openai ~$12, xai ~$36.
# Expected PX4-arm add (from the ArduPilot arm): anthropic ~$65, google ~$30,
# openai ~$20, xai ~$30.
#
# anthropic 300: 198 + 65 = ~263, clears with margin (unchanged from ArduPilot).
# google    140: 101 + 30 = ~131. RAISED from 75 - the ArduPilot arm already put
#                the key at ~$101, so 75 would BUDGET-stop every Gemini model
#                immediately. 140 stays under Peter's $250 Google account cap.
# others    100: openai ~32, xai ~66 after the arm - both clear.
budget_for() {
  case "$1" in
    anthropic) echo 300 ;;
    google)    echo 200 ;;  # 2026-08-13: raised from 156 after +$45 top-up to finish robotics-er T4-T9
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

# run_llm_missions.py exits 3 when the provider refuses the key for a model; it
# abandons only that model's remaining trials. We do NOT skip the rest of the
# provider's models on a refusal (a per-model entitlement gap reads identically
# to a dead key from out here); every refusal is reprinted as a block at the end.
REFUSED=""

echo "=== N=5 PX4 campaign starting $(date -u +%FT%TZ) ==="
echo "missions=$MISSIONS trials=$TRIALS models=$(echo $MODELS | wc -w) target=PX4 SITL (llmuavpx4)"
echo

for model in $MODELS; do
  label="px4-n5-$(echo "$model" | tr '/.' '__')"
  provider=$(provider_of "$model")
  budget=$(budget_for "$provider")
  echo "############ $model  ($(date -u +%FT%TZ))  [$provider, cap \$$budget] ############"
  # TELEMETRY-ADDRESS TOPOLOGY -- RESOLVED option (a), 2026-08-19.
  # The PX4 recorder now has its OWN relay mirror port, the exact twin of what
  # the ArduPilot campaign got on :14541: mavlink-relay-px4.service runs a
  # second '--mirror 127.0.0.1:14657' next to the tap's :14656, so the address
  # below carries ONLY what that relay carries (llmuavpx4's PX4 SITL). No
  # bind-to-any, no second-aircraft contamination path, and no need for
  # --allow-shared-telemetry-bind. Port map: AP tap :14655 / AP recorder :14541,
  # PX4 tap :14656 / PX4 recorder :14657.
  # This replaces the "udp://:14540" bind-to-any address, which the harness now
  # refuses at startup and which is what put two aircraft into single
  # telemetry.csv rows across 472 trials -- history in
  # /root/LLMUAV/Research/PX4-TELEMETRY-CONTAMINATION-VERIFICATION_2026-08-18.md
  # and llm_runs/CHANGELOG-TELEMETRY-CLEAN.md.
  # PREREQUISITE, and it is not optional: mavlink-relay-px4.service must be the
  # RUNNING relay before this campaign starts. It Conflicts= mavlink-relay.service
  # (both listen on 127.0.0.1:5679), so starting it STOPS the ArduPilot relay and
  # takes any in-flight ArduPilot campaign down with it:
  #   systemctl stop mavlink-relay && systemctl start mavlink-relay-px4
  #   systemctl restart droneserver-staging      # re-dial through the new relay
  # and to hand the link back to ArduPilot afterwards, the reverse.
  # KNOWN LEFTOVER (not on this path, but tidy it before publishing): llmuavpx4
  # still carries the drop-in /etc/systemd/system/px4-mavbridge.service.d/
  # 10-telemetry-forward.conf, which UDP-forwards PX4 telemetry to
  # llmuavdev:14540 unconditionally. Nothing binds :14540 any more, so it goes
  # nowhere, but it is the old contaminating topology and removing it (plus a
  # px4-mavbridge restart, which drops the link, so not mid-campaign) leaves
  # exactly one PX4 telemetry path.
  timeout "$PER_MODEL_TIMEOUT" /root/.local/bin/uv run python scripts/run_llm_missions.py \
      --missions "$MISSIONS" --trials "$TRIALS" --model "$model" \
      --budget-usd "$budget" \
      --url http://127.0.0.1:8090/sse \
      --audit-log /var/lib/droneserver/audit.jsonl \
      --target-label "PX4 SITL (llmuavpx4)" \
      --label "$label" \
      --link-recovery-command "systemctl restart droneserver-staging" \
      --capture --mavlink-endpoint udpin:127.0.0.1:14656 \
      --telemetry-address "udpin://127.0.0.1:14657" \
      --firmware PX4 --firmware-version "PX4 v1.16.2" \
      --sitl-host llmuavpx4 \
      --dataflash-remote llmuavpx4:/var/lib/px4-sitl/log \
      --param-name MPC_XY_CRUISE --param-write-value 8.0 \
      --require-complete-capture \
      2>&1 | grep -E -A2 "^model:|^price:|^budget:|PASS |FAIL |VOID|LINK |BUDGET|PROVIDER|spend:|spend on|capture:|degraded|ERROR|Error|Traceback|passed on"
  rc=${PIPESTATUS[0]}
  case "$rc" in
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

echo "=== N=5 PX4 campaign finished $(date -u +%FT%TZ) ==="
if [ -n "$REFUSED" ]; then
  echo
  echo "!!! the provider would not serve these models on the configured key:"
  for m in $REFUSED; do echo "!!!   $m"; done
  echo "!!! Nothing above is a result about those models. Check the balance and the"
  echo "!!! entitlements before re-running them - and note that one refusal can be"
  echo "!!! model-specific, so this list is evidence, not a diagnosis."
fi
