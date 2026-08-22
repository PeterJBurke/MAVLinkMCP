#!/bin/bash
set -u
cd /root/droneserver
echo "=== LANE 8 GOOGLE QUEUE START $(date -u +%FT%TZ) ==="
LANE=8 MODELS="gemini-3.5-flash-lite" MISSIONS="T2,T3,T4,T5" TRIALS=5 BUDGET=260 LABEL_PREFIX=px4refly bash scripts/px4refly_farm.sh
LANE=8 MODELS="gemini-3.6-flash" MISSIONS="T4" TRIALS=5 BUDGET=260 LABEL_PREFIX=px4refly bash scripts/px4refly_farm.sh
echo "=== LANE 8 GOOGLE QUEUE DONE $(date -u +%FT%TZ); turnlimit150 gate next ==="
LANE=8 MODELS="grok-4.20-0309-reasoning" MISSIONS="T4" TRIALS=5 BUDGET=260 MAX_TURNS=150 LABEL_PREFIX=px4refly150 PER_MODEL_TIMEOUT=9000 bash scripts/px4refly_farm.sh
echo "=== LANE 8 ALL QUEUES DONE $(date -u +%FT%TZ) ==="
