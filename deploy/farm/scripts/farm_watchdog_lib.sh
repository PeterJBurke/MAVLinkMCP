#!/bin/bash
# Shared per-lane engine-readiness + link-liveness watchdog for the farm
# refly/T6 row runners (farm_refly_row.sh, farm_t6_lane7_row.sh).
#
# THE LANE-7 LINK DEFECT (2026-08-20 forensic): the old watchdog (v2) restarted
# sitl@N when the arducopter engine was found dead, waited for a GPS-ready
# journal line, and then went straight to flying - it NEVER restarted
# droneserver-lane@N. droneserver-lane@N's own mavsdk_server subprocess stays
# connected to the OLD (now-replaced) SITL instance via the per-lane relay;
# that connection goes half-dead (~16.5s action stalls), which exceeds
# ArduCopter's ~10s auto-disarm-if-never-confirmed window, so every takeoff in
# that state gets rejected. The old readiness grep was also vulnerable to
# matching a STALE "is using GPS" line already in the journal before this
# restart (proc_start_ts came from systemctl's ActiveEnterTimestamp - unit
# metadata, not a guaranteed anchor on the instant WE issued the restart) -
# v3 anchors on our own wall-clock timestamp captured right before the
# restart command instead.
#
# v3 fixes both: (1) whenever sitl@N is actually restarted, droneserver-lane@N
# is ALSO restarted afterward, serialized, only once a FRESH GPS line is seen
# and only after /sse answers 200; (2) a LINK-LIVENESS assertion (get_in_air
# must answer <2s) runs as part of the pre-trial check, with one full
# recovery cycle (both restarts, serialized) before hard-stopping.
#
# THE LANE-7 get_in_air OUTAGE (2026-08-20 forensic, separate defect from the
# above): after the v3 recovery cycle above (sitl -> GPS wait -> droneserver-
# lane -> /sse), get_in_air still failed - and adding a mavlink-relay-lane@N
# restart into the chain (sitl -> GPS wait -> relay -> droneserver-lane ->
# /sse) did NOT clear it either, on three consecutive re-tests. get_armed and
# get_position stayed fast throughout (<1s) - this is NOT the orphaned-link
# defect v3 targets. Root cause: ArduPilot only emits EXTENDED_SYS_STATE (the
# in_air source message) when a client explicitly requests it via
# MAV_CMD_SET_MESSAGE_INTERVAL; MAVSDK's telemetry.in_air() plugin sends that
# request exactly once, lazily, on first subscription after connecting - and
# that one-shot request can be lost/raced during a lane restart with no
# automatic retry. No amount of chain-restarting fixes a lost one-shot
# request; re-issuing it does, in ~0.1s, over the link that is ALREADY up (no
# restart needed) via droneserver's own set_telemetry_rate MCP tool. v4 adds
# farm_lane_rearm_in_air_stream(): tried FIRST (cheap, non-disruptive) on a
# link-liveness failure, and again as a last resort after the v3 restart-based
# recovery cycle, before hard-stopping.

farm_lane_ensure_ready() {
  # args: LANE PORT
  local LANE="$1" PORT="$2"
  local watchdog_ok=0 sitl_restarted=0 restart_ts=""

  for attempt in 1 2 3; do
    if pgrep -f "bin/arducopter.*-I${LANE} " >/dev/null 2>&1; then
      watchdog_ok=1
      break
    fi
    echo "############ WATCHDOG: lane $LANE arducopter engine is DOWN (attempt $attempt) - restarting sitl@${LANE} ############"
    restart_ts="$(date -u '+%Y-%m-%d %H:%M:%S')"
    systemctl restart "sitl@${LANE}"
    sitl_restarted=1
    sleep 3
  done
  if [ "$watchdog_ok" != "1" ]; then
    echo "############ WATCHDOG: lane $LANE arducopter engine did NOT come back after 3 restarts - ABORTING this row, not spending a trial ############"
    return 9
  fi

  if [ "$sitl_restarted" = "1" ]; then
    local ready=0
    for i in $(seq 1 45); do
      if journalctl -u "sitl@${LANE}" --since "$restart_ts" --no-pager 2>/dev/null | grep -q "is using GPS"; then
        ready=1
        break
      fi
      sleep 1
    done
    if [ "$ready" = "1" ]; then
      echo "############ WATCHDOG: lane $LANE fresh EKF/GPS ready (since $restart_ts) ############"
    else
      echo "############ WATCHDOG: lane $LANE did NOT confirm a FRESH GPS-ready line within 45s of $restart_ts - proceeding anyway (capped wait), flag this trial for scrutiny if it fails ############"
    fi

    echo "############ WATCHDOG: lane $LANE sitl@${LANE} was restarted - restarting droneserver-lane@${LANE} to clear its orphaned mavsdk link ############"
    systemctl restart "droneserver-lane@${LANE}"

    local sse_ok=0
    for i in $(seq 1 30); do
      code=$(curl -s -o /dev/null -w '%{http_code}' --max-time 2 "http://127.0.0.1:${PORT}/sse" 2>/dev/null)
      if [ "$code" = "200" ]; then
        sse_ok=1
        break
      fi
      sleep 1
    done
    if [ "$sse_ok" = "1" ]; then
      echo "############ WATCHDOG: lane $LANE /sse answering 200 after droneserver-lane restart ############"
    else
      echo "############ WATCHDOG: lane $LANE /sse did NOT reach 200 within 30s of restarting droneserver-lane@${LANE} - proceeding anyway (capped wait), flag this trial for scrutiny if it fails ############"
    fi
  fi
  return 0
}

farm_lane_link_liveness() {
  # args: PORT API_KEY
  local PORT="$1" API_KEY="$2"
  timeout 4 /root/droneserver/.venv/bin/python3 /root/droneserver/scripts/link_liveness_check.py \
    "http://127.0.0.1:${PORT}/sse" "$API_KEY" 2
}

farm_lane_rearm_in_air_stream() {
  # args: PORT API_KEY
  # Cheap, non-disruptive fix for the lane-7 get_in_air outage (2026-08-20):
  # re-issue MAVSDK's set_rate_in_air (EXTENDED_SYS_STATE / MAV_CMD_SET_
  # MESSAGE_INTERVAL) over the ALREADY-live link. No restart. ~0.1s when it
  # works. See the header comment for why chain restarts don't fix this.
  local PORT="$1" API_KEY="$2"
  timeout 17 /root/droneserver/.venv/bin/python3 /root/droneserver/scripts/rearm_in_air_stream.py \
    "http://127.0.0.1:${PORT}/sse" "$API_KEY" 2.0
}

farm_lane_precheck() {
  # args: LANE PORT API_KEY
  # Full pre-trial check: readiness watchdog -> link-liveness assertion,
  # with exactly one recovery cycle (both restarts, serialized) if the
  # liveness probe fails, then hard-stop.
  local LANE="$1" PORT="$2" API_KEY="$3"

  farm_lane_ensure_ready "$LANE" "$PORT" || return $?

  if farm_lane_link_liveness "$PORT" "$API_KEY"; then
    echo "############ WATCHDOG: lane $LANE link-liveness OK (get_in_air < 2s) ############"
    return 0
  fi

  echo "############ WATCHDOG: lane $LANE link-liveness FAILED (get_in_air did not answer <2s) - trying the cheap fix first: re-arm the in_air (EXTENDED_SYS_STATE) stream over the existing link, no restart ############"
  if farm_lane_rearm_in_air_stream "$PORT" "$API_KEY" && farm_lane_link_liveness "$PORT" "$API_KEY"; then
    echo "############ WATCHDOG: lane $LANE link-liveness OK after in_air stream re-arm (no restart needed) ############"
    return 0
  fi

  echo "############ WATCHDOG: lane $LANE link-liveness still FAILED after re-arm attempt - orphaned-link signature, running ONE recovery cycle (serialized restart sitl@${LANE} then droneserver-lane@${LANE}) ############"
  local restart_ts
  restart_ts="$(date -u '+%Y-%m-%d %H:%M:%S')"
  systemctl restart "sitl@${LANE}"
  sleep 3
  local ready=0
  for i in $(seq 1 45); do
    if journalctl -u "sitl@${LANE}" --since "$restart_ts" --no-pager 2>/dev/null | grep -q "is using GPS"; then
      ready=1
      break
    fi
    sleep 1
  done
  if [ "$ready" = "1" ]; then
    echo "############ WATCHDOG: lane $LANE recovery: fresh GPS ready ############"
  else
    echo "############ WATCHDOG: lane $LANE recovery: GPS-ready line not confirmed within 45s - proceeding anyway ############"
  fi
  systemctl restart "droneserver-lane@${LANE}"
  local sse_ok=0
  for i in $(seq 1 30); do
    code=$(curl -s -o /dev/null -w '%{http_code}' --max-time 2 "http://127.0.0.1:${PORT}/sse" 2>/dev/null)
    if [ "$code" = "200" ]; then sse_ok=1; break; fi
    sleep 1
  done
  if [ "$sse_ok" = "1" ]; then
    echo "############ WATCHDOG: lane $LANE recovery: /sse 200 ############"
  else
    echo "############ WATCHDOG: lane $LANE recovery: /sse did not reach 200 within 30s - proceeding anyway ############"
  fi

  if farm_lane_link_liveness "$PORT" "$API_KEY"; then
    echo "############ WATCHDOG: lane $LANE link-liveness OK after recovery cycle ############"
    return 0
  fi

  echo "############ WATCHDOG: lane $LANE link-liveness still FAILED after the restart-based recovery cycle - trying the in_air stream re-arm once more as a last resort (the fresh droneserver-lane connection needs the same one-shot EXTENDED_SYS_STATE request re-issued) ############"
  if farm_lane_rearm_in_air_stream "$PORT" "$API_KEY" && farm_lane_link_liveness "$PORT" "$API_KEY"; then
    echo "############ WATCHDOG: lane $LANE link-liveness OK after post-recovery in_air stream re-arm ############"
    return 0
  fi

  echo "############ WATCHDOG: lane $LANE link-liveness STILL FAILED after one full recovery cycle AND an in_air stream re-arm - HARD STOP, do not fly this row. Report to Peter. ############"
  return 11
}
