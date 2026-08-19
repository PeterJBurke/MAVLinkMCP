#!/bin/bash
# One-shot scheduler: re-run the 4 PX4 Gemini models after Google's daily quota
# resets (~07:00 UTC = midnight Pacific), once the main campaign is done.
# Detached (setsid) so it survives the interactive session ending.
#
# Sequence: wait past the reset window -> wait for the main campaign to finish
# (shared aircraft) -> probe the Google quota with a cheap flash-lite T1 (retry
# every 30 min if still 429) -> launch the Gemini-only re-run via the PX4
# campaign wrapper. 3.1-pro-preview runs LAST (tightest preview quota, most
# likely to re-429; the other three finish first regardless).
set -u
LOG=/root/px4_gemini_rerun.log
exec >> "$LOG" 2>&1
echo "=== gemini re-run scheduler armed $(date -u '+%F %T UTC') ==="

# 1) Wait until past the Google reset window (07:15 UTC, 15 min after midnight PT).
TARGET=$(date -u -d '2026-08-13 07:15:00 UTC' +%s)
while [ "$(date -u +%s)" -lt "$TARGET" ]; do sleep 300; done
echo "=== past reset window $(date -u '+%F %T UTC') ==="

# 2) Wait for the main PX4 campaign to finish (one model flies at a time).
while pgrep -f run_n5_campaign_px4.sh >/dev/null 2>&1; do
  echo "waiting for main campaign to finish... $(date -u '+%T UTC')"; sleep 300
done
echo "=== main campaign done; preparing re-run $(date -u '+%F %T UTC') ==="

cd /root/droneserver || exit 2
set -a; . /root/llmuav.env; set +a
export DRONESERVER_API_KEY=$(grep "^SAFETY_API_KEYS=" /etc/droneserver/staging.env | cut -d= -f2- | cut -d, -f1 | cut -d: -f2)
export DRONESERVER_RECORDER_API_KEY=$(cat /root/llmuav-recorder.key)

# Make sure the PX4 staging server is up and the drone link is live.
systemctl is-active droneserver-staging >/dev/null 2>&1 || systemctl restart droneserver-staging
sleep 8

# 3) Probe the Google quota with a cheap flash-lite T1 (no capture). Retry every
#    30 min, up to ~4 h, in case the reset is slow or the quota clears late.
probe_ok=0
for attempt in $(seq 1 9); do
  echo "=== google quota probe $attempt $(date -u '+%T UTC') ==="
  out=$(/root/.local/bin/uv run python scripts/run_llm_missions.py \
        --model gemini-3.5-flash-lite --missions T1 --trials 1 --budget-usd 140 \
        --url http://127.0.0.1:8090/sse \
        --api-key "$DRONESERVER_API_KEY" --recorder-api-key "$DRONESERVER_RECORDER_API_KEY" \
        --target-label "PX4 SITL (llmuavpx4)" --label px4-gemini-probe \
        --firmware PX4 --firmware-version "PX4 v1.16.2" --sitl-host llmuavpx4 2>&1)
  if echo "$out" | grep -q "HTTP 429"; then
    echo "  still rate-limited (429); waiting 30 min"; sleep 1800; continue
  fi
  echo "  quota recovered."; probe_ok=1; break
done

if [ "$probe_ok" -ne 1 ]; then
  echo "=== ABORT: Google quota never recovered after ~4 h of probing $(date -u) ==="
  echo "=== The 4 Gemini models still need a manual re-run. ==="
  exit 3
fi

# 4) Re-run the 4 Gemini models on PX4. 3.1-pro-preview last.
echo "=== launching Gemini re-run $(date -u '+%F %T UTC') ==="
MODELS="gemini-3.6-flash gemini-3.5-flash-lite gemini-robotics-er-2-preview gemini-3.1-pro-preview" \
  bash scripts/run_n5_campaign_px4.sh
echo "=== gemini re-run finished $(date -u '+%F %T UTC') ==="
