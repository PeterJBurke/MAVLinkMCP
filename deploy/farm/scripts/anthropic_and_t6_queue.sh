#!/bin/bash
# Queue: haiku T4 (PX4 lane8) -> TG3 (sonnet T6 lane7) -> T10 opus/sonnet/haiku
# (lane2) -> 5x gpt-5.2 T6 lane7. ONE anthropic row in flight at a time
# (strictly sequential). Continues past a non-budget failure of one step;
# stops the anthropic portion cleanly if the anthropic budget guard trips
# (exit code 2 from run_llm_missions.py === BUDGET/not-startable; we check the
# ledger before each step instead of relying on exit code alone).
set -u
cd /root/droneserver

ANTHROPIC_BUDGET=407

check_anthropic_headroom() {
  python3 -c "
import csv
rows = list(csv.DictReader(open('docs/benchmark_runs/spend_ledger.csv')))
latest = None
for r in rows:
    if r['provider'] != 'anthropic':
        continue
    if latest is None or r['ts_utc'] > latest['ts_utc']:
        latest = r
if latest is None:
    print('999')
else:
    print(float(latest['budget_usd']) - float(latest['cumulative_usd_for_key']))
"
}

echo "=== ANTHROPIC+T6 QUEUE START $(date -u +%FT%TZ) ==="

echo "--- headroom before haiku T4: \$$(check_anthropic_headroom) ---"
LANE=8 MODELS="claude-haiku-4-5-20251001" MISSIONS="T4" TRIALS=4 BUDGET=407 LABEL_PREFIX=px4refly \
  bash scripts/px4refly_farm.sh
echo "--- headroom after haiku T4: \$$(check_anthropic_headroom) ---"

echo "=== TG3: claude-sonnet-5 T6 x1 on lane7 $(date -u +%FT%TZ) ==="
BUDGET_USD_OVERRIDE=407 bash scripts/farm_t6_lane7_row.sh 1 claude-sonnet-5 TG3
echo "--- headroom after TG3: \$$(check_anthropic_headroom) ---"

echo "=== T10 opus x1 on lane2 $(date -u +%FT%TZ) ==="
bash scripts/farm_refly_row.sh 2 T10 1 claude-opus-5 150 12 407 t10refly 1
echo "--- headroom after T10 opus: \$$(check_anthropic_headroom) ---"

echo "=== T10 sonnet x1 on lane2 $(date -u +%FT%TZ) ==="
bash scripts/farm_refly_row.sh 2 T10 1 claude-sonnet-5 150 12 407 t10refly 1
echo "--- headroom after T10 sonnet: \$$(check_anthropic_headroom) ---"

echo "=== T10 haiku x1 on lane2 $(date -u +%FT%TZ) ==="
bash scripts/farm_refly_row.sh 2 T10 1 claude-haiku-4-5-20251001 150 12 407 t10refly 1
echo "--- headroom after T10 haiku: \$$(check_anthropic_headroom) ---"

echo "=== ANTHROPIC PORTION DONE $(date -u +%FT%TZ); starting gpt-5.2 T6 reflies (openai, budget 260) ==="

for n in 1 2 3 4 5; do
  echo "=== t6refly-lane7-gpt-5_2-t${n} $(date -u +%FT%TZ) ==="
  BUDGET_USD_OVERRIDE=260 bash scripts/farm_t6_lane7_row.sh 1 gpt-5.2 "t${n}"
done

echo "=== TRUE LAST ROWS: claude-opus-5 T6 x2 makeups on lane7 (canonical origin) $(date -u +%FT%TZ) ==="
echo "--- headroom before opus T6 makeups: \$$(check_anthropic_headroom) ---"
BUDGET_USD_OVERRIDE=407 bash scripts/farm_t6_lane7_row.sh 1 claude-opus-5 t4
echo "--- headroom after opus t4: \$$(check_anthropic_headroom) ---"
BUDGET_USD_OVERRIDE=407 bash scripts/farm_t6_lane7_row.sh 1 claude-opus-5 t5
echo "--- headroom after opus t5: \$$(check_anthropic_headroom) ---"

echo "=== FULL QUEUE DONE $(date -u +%FT%TZ) ==="
