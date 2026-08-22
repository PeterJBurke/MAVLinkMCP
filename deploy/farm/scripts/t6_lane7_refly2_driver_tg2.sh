#!/bin/bash
# Part 5 of the lane-7 T6 refly2 driver (2026-08-20): TG2 = grok-4.20-0309-
# reasoning T6 x1, newly unblocked after DEFECT C's fix (commit 1720878).
set -u
cd /root/droneserver || exit 2
export LABEL_PREFIX=t6refly2

ROWS=(
  "grok-4.20-0309-reasoning tg2"
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

  if [ "$rc" = "5" ]; then
    echo "############ NOTE: row model=$MODEL suffix=$SUFFIX exited 5 = DEFECT-C-fixed harness crash -> VOID-not-scored, safe-landing already attempted by the harness. Doing an aircraft-check before continuing. ############"
  fi

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
echo "TG2 ROW COMPLETE $(date -u +%FT%TZ)"
echo "=================================================================="
exit 0
