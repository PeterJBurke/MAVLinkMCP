#!/bin/bash
# Runs t6_shape_validation.py against a list of lanes concurrently, reading
# each lane's API key from its own env file (never printed). Usage:
#   ./run_r0_gate.sh 0 1 2
set -u
cd /root/droneserver
mkdir -p /root/r0_gate_logs
PIDS=()
for N in "$@"; do
  KEY=$(grep '^SAFETY_API_KEYS=' /etc/droneserver/lane${N}.env | cut -d= -f2- | cut -d, -f1 | cut -d: -f2)
  URL="http://127.0.0.1:809$((1+N))/sse"
  (
    echo "=== lane $N : $URL ==="
    .venv/bin/python scripts/t6_shape_validation.py --url "$URL" --api-key "$KEY" \
      > /root/r0_gate_logs/lane${N}.log 2>&1
    echo "lane $N exit=$?" >> /root/r0_gate_logs/lane${N}.log
  ) &
  PIDS+=($!)
done
for pid in "${PIDS[@]}"; do wait "$pid"; done
echo "all done; results in /root/r0_gate_logs/lane*.log"
for N in "$@"; do
  echo "--- lane $N summary ---"
  tail -5 /root/r0_gate_logs/lane${N}.log
done
