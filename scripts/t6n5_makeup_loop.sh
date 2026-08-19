#!/bin/bash
# T6-N5 makeup loop (Plan 34 SS8, 2026-08-19).
#
# WHY THIS EXISTS: in the T6-N5 phase-1 campaign, a no-return FAIL leaves the
# aircraft ~1.2 km out; the harness ferries it back but lands ~16 m from the
# run's launch point -- 1 m outside the 15 m start tolerance -- so the run's
# REMAINING trials correctly abort as "NOT FLOWN (rig, not model)". The
# tolerance is deliberately NOT being changed mid-campaign. A fresh run fixes
# its own launch point at start, so re-invoking the campaign per model until
# five T6 trials exist yields the full N=5 with zero protocol drift. Verdicts
# consolidate across run dirs exactly as the demonstration arm's rounds did.
#
# Counts FLOWN trials (verdict PASS/FAIL) in T6 rows of missions.csv across
# this campaign's run dirs (2026-08-18T22:00Z or later). VOID/START rows do
# not count toward the five.
set -u
cd /root/droneserver || exit 2

MODELS="${MODELS:-claude-haiku-4-5-20251001 claude-sonnet-5 gpt-5.2 grok-4.20-0309-reasoning grok-4.20-0309-non-reasoning grok-4.5 claude-opus-5}"
CUTOFF="20260818T2200"

flown_count() {  # $1 = model name
  python3 - "$1" "$CUTOFF" <<'EOF'
import csv, glob, sys
model, cutoff = sys.argv[1], sys.argv[2]
label = "n5-" + model.replace("/", "_").replace(".", "_")
n = 0
for f in glob.glob("/root/droneserver/llm_runs/*_" + label + "/missions.csv"):
    stamp = f.split("/")[-2].split("_")[0]
    if stamp < cutoff:
        continue
    for r in csv.DictReader(open(f)):
        if r["mission_id"] == "T6" and r["verdict"] in ("PASS", "FAIL"):
            n += 1
print(n)
EOF
}

for model in $MODELS; do
  while :; do
    have=$(flown_count "$model")
    need=$((5 - have))
    if [ "$need" -le 0 ]; then
      echo "=== $model: $have/5 flown - complete ==="
      break
    fi
    echo "=== $model: $have/5 flown - makeup run of $need trial(s) $(date -u +%FT%TZ) ==="
    MISSIONS=T6 TRIALS="$need" MODELS="$model" \
    MAPS_URL=https://mapstools.googleapis.com/mcp \
    GEOFENCE_RADIUS_M=15000 MAX_TRIAL_COST_USD=12 MAX_TURNS=200 TRIAL_TIMEOUT_S=3600 \
    bash scripts/run_n5_campaign.sh
    after=$(flown_count "$model")
    if [ "$after" -le "$have" ]; then
      echo "!!! $model: makeup run added no flown trials ($have -> $after) - stopping this model to avoid a loop; investigate" >&2
      break
    fi
  done
done
echo "=== makeup loop finished $(date -u +%FT%TZ) ==="
