#!/bin/bash
set -u
cd /root/droneserver
echo "=== LANE 9 GOOGLE QUEUE START $(date -u +%FT%TZ) ==="
LANE=9 MODELS="gemini-3.1-pro-preview" MISSIONS="T4" TRIALS=5 BUDGET=260 LABEL_PREFIX=px4refly bash scripts/px4refly_farm.sh
LANE=9 MODELS="gemini-robotics-er-2-preview" MISSIONS="T4,T5" TRIALS=5 BUDGET=260 LABEL_PREFIX=px4refly bash scripts/px4refly_farm.sh
echo "=== LANE 9 GOOGLE QUEUE DONE $(date -u +%FT%TZ); turnlimit150 gate next ==="
LANE=9 MODELS="grok-4.20-0309-non-reasoning" MISSIONS="T4" TRIALS=5 BUDGET=260 MAX_TURNS=150 LABEL_PREFIX=px4refly150 PER_MODEL_TIMEOUT=9000 bash scripts/px4refly_farm.sh
echo "=== LANE 9 ALL QUEUES DONE $(date -u +%FT%TZ) ==="
