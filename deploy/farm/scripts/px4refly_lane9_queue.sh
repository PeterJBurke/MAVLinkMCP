#!/bin/bash
set -u
cd /root/droneserver
echo "=== LANE 9 QUEUE START $(date -u +%FT%TZ) ==="
LANE=9 MODELS="grok-4.20-0309-non-reasoning" MISSIONS="T4,T9" TRIALS=5 BUDGET=260 LABEL_PREFIX=px4refly bash scripts/px4refly_farm.sh
LANE=9 MODELS="gpt-5.2" MISSIONS="T4" TRIALS=5 BUDGET=260 LABEL_PREFIX=px4refly bash scripts/px4refly_farm.sh
echo "=== LANE 9 non-anthropic done $(date -u +%FT%TZ); anthropic gate next ==="
