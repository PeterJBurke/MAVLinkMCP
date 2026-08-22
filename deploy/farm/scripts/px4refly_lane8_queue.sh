#!/bin/bash
set -u
cd /root/droneserver
echo "=== LANE 8 QUEUE START $(date -u +%FT%TZ) ==="
LANE=8 MODELS="grok-4.20-0309-reasoning" MISSIONS="T4" TRIALS=5 BUDGET=260 LABEL_PREFIX=px4refly bash scripts/px4refly_farm.sh
LANE=8 MODELS="grok-4.5" MISSIONS="T4" TRIALS=3 BUDGET=260 LABEL_PREFIX=px4refly bash scripts/px4refly_farm.sh
echo "=== LANE 8 non-anthropic done $(date -u +%FT%TZ); anthropic gate next ==="
