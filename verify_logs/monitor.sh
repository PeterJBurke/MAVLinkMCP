#!/bin/bash
# Sample the harness process's fds, threads and child mavsdk_server count.
LOG=/root/droneserver/verify_logs/resources.log
echo "ts elapsed_s pid fds threads mavsdk_servers rss_kb" > "$LOG"
START=$(date +%s)
while true; do
  PID=$(pgrep -f "scripts/run_mission_suite.py" | head -1)
  [ -z "$PID" ] && { echo "$(date -Is) harness gone" >> "$LOG"; break; }
  FDS=$(ls /proc/$PID/fd 2>/dev/null | wc -l)
  THREADS=$(ls /proc/$PID/task 2>/dev/null | wc -l)
  SRV=$(pgrep -c -f "mavsdk_server" 2>/dev/null || echo 0)
  RSS=$(awk '/VmRSS/{print $2}' /proc/$PID/status 2>/dev/null)
  echo "$(date -Is) $(( $(date +%s) - START )) $PID $FDS $THREADS $SRV $RSS" >> "$LOG"
  sleep 5
done
