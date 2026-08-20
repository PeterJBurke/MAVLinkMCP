#!/bin/bash
# T4 RE-FLY, PX4 arm — the post-fix round that supersedes the C3-contaminated cells.
#
# WHY THIS ROUND EXISTS
# ---------------------
# The PX4 T4 column of the 2026-08-12/13 N=5 campaign does not measure the
# models. It measures a defect in this server's managed-mission executor.
#
# In 33 of PX4's 44 T4 failures (9 of 11 models; category C3 of
# /root/LLMUAV/Research/FAILURE-TAXONOMY_2026-08-19.md) the sequence was:
#
#   1. the model built a correct mission and called start_managed_mission;
#   2. the server uploaded it, armed, took off in GUIDED to 20 m, and asked
#      PX4 for AUTO.MISSION;
#   3. PX4 ACKed the command as ACCEPTED and then REFUSED the transition -
#      "Switching to Mission is currently not available" (STATUSTEXT severity
#      CRITICAL) - because the uploaded mission carried the ArduPilot seq-0
#      HOME placeholder, which PX4 reads as a real waypoint and rejects while
#      the vehicle is in the air. MavSDK read the first ACK and reported
#      success, so the server believed the mission was running;
#   4. the aircraft sat in HOLD over its launch point. The runner's completion
#      test read HOLD as "PX4 finished its mission", announced "mission items
#      complete" about SEVEN SECONDS after the start at 0% progress /
#      current_item 0 of 6, and commanded RTL;
#   5. the model, reading a server that said the mission had finished, believed
#      it. 28 of the 38 C3 trials closed with "MISSION COMPLETE".
#
# Telemetry from the canonical trial: max_distance_from_home_m 0.6.
#
# Both halves are fixed (commit on 2026-08-19):
#   * the mission is not RUNNING until the autopilot is SEEN executing it, and
#     if the ArduPilot-shaped layout is refused the server re-uploads a
#     PX4-compatible one; a start that cannot be confirmed FAILS, loudly, and
#     brings the aircraft down;
#   * completion is gated on evidence the mission actually progressed - an item
#     reached past the one it started on, or the aircraft off its start point -
#     so no signal that is already true at item 0 can produce "complete".
# Covered by tests/test_mission_completion_px4.py and, against the live PX4
# SITL, tests/integration/test_managed_mission_px4_sitl.py.
#
# WHAT THIS ROUND SUPERSEDES (Peter's decision, 2026-08-19)
# --------------------------------------------------------
# The T4 cells of the eleven PX4 N=5 model rows. The 2026-08-12/13 T4 bundles
# are NOT deleted and NOT rewritten: they stay in llm_runs/ as the record of the
# defect, and the taxonomy's C3 section is the analysis of them. This round's
# bundles are labelled px4-t4fix-<model> so the two are never confused, and the
# supersession is a documented substitution in the manuscript, not a silent one.
# Nothing outside T4 and nothing on the ArduPilot arm is affected: the AP T4
# column was healthy (five models 5/5) and is untouched by the fix.
#
# BEFORE RUNNING - three things, none of them optional
# ----------------------------------------------------
# 1. THE RELAY. This campaign needs mavlink-relay-px4.service to be the running
#    relay. It Conflicts= mavlink-relay.service (both listen on 127.0.0.1:5679),
#    so starting it STOPS the ArduPilot relay and takes any in-flight ArduPilot
#    campaign down with it. Confirm nothing is flying on llmuavsitl first, then:
#       systemctl stop mavlink-relay && systemctl start mavlink-relay-px4
#       systemctl restart droneserver-staging     # re-dial through the new relay
#    and hand the link back afterwards with the reverse.
#
# 2. THE BUDGET. Guards below are CUMULATIVE over each key's whole life, so
#    they must clear everything already spent PLUS this round. Measured cost of
#    the T4 cells in the 2026-08-12/13 PX4 round, from docs/benchmark_runs/
#    spend_ledger.csv: anthropic $38.47, google $15.60, xai $13.84, openai
#    $0.65 - TOTAL $68.56 (opus alone is $4.47/trial). Budget ~$85 with headroom;
#    the fix should if anything make it cheaper, because a large part of that
#    bill was models polling get_mission_status in a tight loop against a
#    mission that had "finished" in seven seconds.
#    Ledger position at 2026-08-19 05:10Z, WITH THE T6 CAMPAIGN STILL FLYING:
#       anthropic $331.37 / guard 450  -> +38 = ~370, clears
#       google    $167.28 / guard 200  -> +16 = ~183, clears by $17 ONLY, and
#                                         the live T6 campaign is still adding
#                                         google spend. RE-CHECK THE LEDGER
#                                         IMMEDIATELY BEFORE STARTING; if google
#                                         no longer clears, that is a spend
#                                         decision for Peter, not an edit to
#                                         make here.
#       xai        $88.94 / guard 130  -> +14 = ~103, clears
#       openai     $17.31 / guard 100  -> +1  = ~18,  clears
#
# 3. THE AIRCRAFT. One model at a time, always: they share a single simulated
#    aircraft. Do not run this beside anything else on llmuavpx4.
#
#   MODELS="..." bash scripts/px4_t4_refly.sh          # override the list
#   TRIALS=1 bash scripts/px4_t4_refly.sh              # rehearsal
set -u
cd /root/droneserver || exit 2
set -a; . /root/llmuav.env; set +a
export DRONESERVER_API_KEY=$(grep "^SAFETY_API_KEYS=" /etc/droneserver/staging.env | cut -d= -f2- | cut -d, -f1 | cut -d: -f2)
export DRONESERVER_RECORDER_API_KEY=$(cat /root/llmuav-recorder.key)

# T4 only, N=5, the same eleven core models the arm was flown with.
MISSIONS="${MISSIONS:-T4}"
TRIALS="${TRIALS:-5}"
MODELS="${MODELS:-claude-opus-5 claude-sonnet-5 claude-haiku-4-5-20251001 gpt-5.2 gemini-3.1-pro-preview gemini-3.6-flash gemini-3.5-flash-lite gemini-robotics-er-2-preview grok-4.5 grok-4.20-0309-reasoning grok-4.20-0309-non-reasoning}"
# One mission, five trials: far shorter than the full nine-mission arm.
PER_MODEL_TIMEOUT="${PER_MODEL_TIMEOUT:-5400}"

# Cumulative-per-key guards, carried over unchanged from run_n5_campaign_px4.sh
# and from the T6-N5 authorisations. See note 2 in the header before raising any
# of these - a guard is a spend authorisation, and raising one is Peter's call.
budget_for() {
  case "$1" in
    anthropic) echo 450 ;;
    google)    echo 200 ;;
    xai)       echo 130 ;;
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

REFUSED=""

echo "=== PX4 T4 re-fly (post-C3-fix) starting $(date -u +%FT%TZ) ==="
echo "missions=$MISSIONS trials=$TRIALS models=$(echo $MODELS | wc -w) target=PX4 SITL (llmuavpx4)"
echo "supersedes the T4 cells of the 2026-08-12/13 px4-n5 round (taxonomy C3)"
echo

for model in $MODELS; do
  # px4-t4fix- marks this round unmistakably against the px4-n5- bundles it
  # supersedes; every discovery/enumeration script keys off the label prefix.
  label="px4-t4fix-$(echo "$model" | tr '/.' '__')"
  provider=$(provider_of "$model")
  budget=$(budget_for "$provider")
  echo "############ $model  ($(date -u +%FT%TZ))  [$provider, cap \$$budget] ############"
  # Capture and telemetry exactly as run_n5_campaign_px4.sh has them after the
  # 2026-08-19 topology resolution: the tap on the relay's :14656 mirror and the
  # recorder on its OWN :14657 mirror, both fed only by llmuavpx4's PX4 SITL.
  # T7 is not in this round, but --param-name stays PX4-correct so a
  # MISSIONS=T4,T7 rehearsal does not fail on WPNAV_SPEED.
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
      2>&1 | grep -E -A2 "^model:|^price:|^budget:|PASS |FAIL |VOID|LINK |BUDGET|PROVIDER|spend:|spend on|capture:|degraded|ERROR|Error|Traceback|HARNESS CRASH|passed on"
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

echo "=== PX4 T4 re-fly finished $(date -u +%FT%TZ) ==="
if [ -n "$REFUSED" ]; then
  echo
  echo "!!! the provider would not serve these models on the configured key:"
  for m in $REFUSED; do echo "!!!   $m"; done
  echo "!!! Nothing above is a result about those models. Check the balance and the"
  echo "!!! entitlements before re-running them - and note that one refusal can be"
  echo "!!! model-specific, so this list is evidence, not a diagnosis."
fi
echo
echo "AFTERWARDS - what to check before this round is allowed to supersede anything:"
echo "  * every trial's events.jsonl carries a 'mission execution confirmed' event"
echo "    and no 'mission items complete' before real progress;"
echo "  * any trial that still ends 'start_unconfirmed' is a REAL platform"
echo "    failure, not the old defect - read it, do not re-run it away;"
echo "  * telemetry.csv is single-vehicle (verify.py checks it) - the recorder"
echo "    is on :14657 now, and a bundle that says otherwise means the relay was"
echo "    not the PX4 one;"
echo "  * hand the MAVLink link back to ArduPilot when done:"
echo "      systemctl stop mavlink-relay-px4 && systemctl start mavlink-relay"
echo "      systemctl restart droneserver-staging"
