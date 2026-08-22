#!/bin/bash
# Part 3 of the lane-7 T6 refly2 driver (2026-08-20): final 5 rows (gpt-5.2
# t1-t5) after the coordinator's v4 precheck (in_air stream re-arm) landed in
# farm_watchdog_lib.sh and the lane-1/lane-6 side repairs were done.
set -u
cd /root/droneserver || exit 2
export LABEL_PREFIX=t6refly2

ROWS=(
  "gpt-5.2 t1"
  "gpt-5.2 t2"
  "gpt-5.2 t3"
  "gpt-5.2 t4"
  "gpt-5.2 t5"
)

LOGDIR="/root/t6_lane7_refly2_logs"
mkdir -p "$LOGDIR"

for row in "${ROWS[@]}"; do
  read -r MODEL SUFFIX <<< "$row"
  ROWLOG="${LOGDIR}/$(echo "$MODEL" | tr '/.' '__')-${SUFFIX}.log"
  echo "=================================================================="
  echo "STARTING ROW: model=$MODEL suffix=$SUFFIX log=$ROWLOG $(date -u +%FT%TZ)"
  echo "=================================================================="
  bash scripts/farm_t6_lane7_row.sh 1 "$MODEL" "$SUFFIX" > "$ROWLOG" 2>&1
  rc=$?
  echo "ROW DONE: model=$MODEL suffix=$SUFFIX rc=$rc $(date -u +%FT%TZ)"
  tail -n 30 "$ROWLOG"

  if [ "$rc" = "9" ] || [ "$rc" = "10" ] || [ "$rc" = "11" ]; then
    echo "############ DRIVER HALT: row model=$MODEL suffix=$SUFFIX returned watchdog/precheck failure rc=$rc - LINK FIX DID NOT HOLD (or a genuine safety issue). STOPPING. ############"
    exit "$rc"
  fi

  ENVFILE="/etc/droneserver/lane7.env"
  APIKEY=$(grep "^SAFETY_API_KEYS=" "$ENVFILE" | cut -d= -f2- | cut -d, -f1 | cut -d: -f2)
  echo "--- post-row idle check ---"
  .venv/bin/python3 scripts/safety_precheck.py "http://127.0.0.1:8098/sse" "$APIKEY"
  idle_rc=$?
  if [ "$idle_rc" != "0" ]; then
    echo "############ DRIVER HALT: aircraft NOT confirmed idle after row model=$MODEL suffix=$SUFFIX (safety_precheck rc=$idle_rc). STOPPING. ############"
    exit 20
  fi

  if grep -qi "BUDGET" "$ROWLOG"; then
    echo "############ BUDGET GUARD MENTIONED in row model=$MODEL suffix=$SUFFIX - see $ROWLOG ############"
  fi
done

echo "=================================================================="
echo "ALL GPT-5.2 ROWS COMPLETE $(date -u +%FT%TZ)"
echo "=================================================================="
exit 0
