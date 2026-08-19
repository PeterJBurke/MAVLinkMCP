#!/bin/bash
# T10 arm - N=1 per model (Peter's decision 2026-08-19, Plan 34 SS8 addendum).
#
# T10 (long serpentine survey, >10 min) was decoupled from the N=5 core by
# Plan 08 (it tests the server's mission-state architecture, not the model).
# Peter now wants a per-model T10 column in the success heatmap at N=1: does
# the model correctly initiate (one start_managed_mission call), supervise,
# and honestly recognize completion of a long mission?
#
# RUN AFTER the T6-N5 campaign + makeups + Gemini phase, and AFTER the staging
# fence is RESTORED to 1000 m - T10 flies at the suite-default protocol (the
# canonical scripted T10 ran under the default fence; no GEOFENCE_RADIUS_M
# override here, no MAPS_URL).
set -u
cd /root/droneserver || exit 2
MODELS="${MODELS:-claude-haiku-4-5-20251001 claude-sonnet-5 claude-opus-5 gpt-5.2 gemini-3.1-pro-preview gemini-3.6-flash gemini-3.5-flash-lite gemini-robotics-er-2-preview grok-4.5 grok-4.20-0309-reasoning grok-4.20-0309-non-reasoning}"
# T10 turn ceiling (Peter's ruling 2026-08-19: 150, extending the standing refly
# ruling). A >10-min mission needs more turns than the 90 default: 3.1-pro paced
# responsibly (~7-12 s/turn) and was cut mid-landing at 90 after a correct 627 s
# flight. At 150, responsible pacing passes; frantic 1.3 s/turn polling still
# fails on the merits. Cost stays bounded by the per-trial money ceiling.
MAX_TURNS="${MAX_TURNS:-150}"
# Cumulative-per-key spend cap. 260 was sized for the google key; the anthropic
# key's LEDGER (cumulative, all campaigns) was already $360.45, so a flat 260
# skipped all three Claude rows on 2026-08-19. Set per relaunch, matched to the
# actual credit on the key being flown (anthropic 2026-08-19: 360.45 ledger +
# $45 top-up = 405).
BUDGET_USD="${BUDGET_USD:-260}"
# Per-trial money ceiling. Default was the harness's $5, which structurally
# binds opus pricing on a >10-min mission (Peter's ruling 2026-08-19: raise to
# $12, the T6-campaign precedent, and refly the four ceiling-cut rows; frantic
# pollers still fail via the 150-turn ceiling, so discrimination is preserved).
MAX_TRIAL_COST_USD="${MAX_TRIAL_COST_USD:-12}"
set -a; . /root/llmuav.env; set +a
export DRONESERVER_API_KEY=$(grep "^SAFETY_API_KEYS=" /etc/droneserver/staging.env | cut -d= -f2- | cut -d, -f1 | cut -d: -f2)
export DRONESERVER_RECORDER_API_KEY=$(cat /root/llmuav-recorder.key)
# NOTE: --budget-usd is cumulative-per-key; raised 100->260 on 2026-08-19 because the google key was already at $181 from the T6 N=5 campaign and $100 hard-blocked every gemini T10 row. 260 clears google/xai/openai cumulative + T10 spread; matched to Peter's topped-up credit.
for model in $MODELS; do
  label="t10n1-$(echo "$model" | tr '/.' '__')"
  echo "############ T10 $model ($(date -u +%FT%TZ)) ############"
  timeout 5400 /root/.local/bin/uv run python scripts/run_llm_missions.py \
      --missions T10 --trials 1 --model "$model" --include-slow \
      --max-turns "$MAX_TURNS" \
      --max-trial-cost-usd "$MAX_TRIAL_COST_USD" \
      --budget-usd "$BUDGET_USD" \
      --url http://127.0.0.1:8090/sse \
      --audit-log /var/lib/droneserver/audit.jsonl \
      --target-label "ArduPilot SITL (llmuavsitl)" \
      --label "$label" \
      --link-recovery-command "systemctl restart droneserver-staging" \
      --capture --mavlink-endpoint udpin:127.0.0.1:14655 \
      --telemetry-address "udpin://127.0.0.1:14541" \
      --firmware ArduCopter --firmware-version "ArduCopter V4.7.0-dev (c683d8c1) (SITL)" \
      --sitl-host llmuavsitl \
      --dataflash-remote llmuavsitl:/home/dronepilot/ardupilot/ArduCopter/logs \
      --require-complete-capture \
      2>&1 | grep -E -A2 "PASS |FAIL |VOID|LINK |BUDGET|PROVIDER|spend:|spend on|capture:|degraded|ERROR|Error|Traceback"
done
echo "=== T10 arm finished $(date -u +%FT%TZ) ==="
