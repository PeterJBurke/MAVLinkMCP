#!/bin/bash
# Generates /etc/droneserver/laneN.env for N=0..7 from the staging.env reference,
# changing only ports/paths per lane. Never prints key values.
set -euo pipefail
REF=/etc/droneserver/staging.env.reference
mkdir -p /var/lib/droneserver
for N in 0 1 2 3 4 5 6 7; do
  SITL_PORT=$((5670 + N))          # SITL TCP output port for this lane (127.0.0.1 only)
  SERVER_PORT=$((8091 + N))        # droneserver MCP port 8091..8098
  RELAY_LISTEN_PORT=$((15670 + N)) # relay listen port (server -> relay)
  RECORDER_MIRROR_PORT=$((14541 + 10*N))
  TAP_MIRROR_PORT=$((14650 + 10*N))
  MAVSDK_PORT=$((50061 + N))
  LANE_DIR=/var/lib/droneserver/lane${N}
  mkdir -p "$LANE_DIR/flight_logs"
  OUT=/etc/droneserver/lane${N}.env
  sed -e "s#^MAVLINK_PORT=.*#MAVLINK_PORT=${RELAY_LISTEN_PORT}#" \
      -e "s#^MCP_PORT=.*#MCP_PORT=${SERVER_PORT}#" \
      -e "s#^FLIGHT_LOG_DIR=.*#FLIGHT_LOG_DIR=${LANE_DIR}/flight_logs#" \
      -e "s#^SAFETY_AUDIT_LOG_PATH=.*#SAFETY_AUDIT_LOG_PATH=${LANE_DIR}/audit.jsonl#" \
      -e "s#^MISSION_STATE_PATH=.*#MISSION_STATE_PATH=${LANE_DIR}/mission_state.json#" \
      -e "s#^MAVSDK_SERVER_PORT=.*#MAVSDK_SERVER_PORT=${MAVSDK_PORT}#" \
      -e "s#^SAFETY_GEOFENCE_MAX_RADIUS_M=.*#SAFETY_GEOFENCE_MAX_RADIUS_M=1000#" \
      "$REF" > "$OUT"
  # Drop the staging-specific top-of-file comment block (PX4 relay history notes,
  # not applicable to a farm lane) and add a lane header instead.
  TMP=$(mktemp)
  { echo "# droneserver lane ${N} on llmuavfarm — ArduCopter SITL (Copter-4.5.7), generated $(date -u +%FT%TZ)"; \
    echo "# SITL TCP: 127.0.0.1:${SITL_PORT} | relay listen: 127.0.0.1:${RELAY_LISTEN_PORT} | server: 127.0.0.1:${SERVER_PORT}"; \
    echo "# recorder mirror: 127.0.0.1:${RECORDER_MIRROR_PORT} | tap mirror: 127.0.0.1:${TAP_MIRROR_PORT}"; \
    grep -v "^#" "$OUT"; } > "$TMP"
  mv "$TMP" "$OUT"
  chmod 600 "$OUT"
  echo "wrote $OUT (SITL=${SITL_PORT} relay_listen=${RELAY_LISTEN_PORT} server=${SERVER_PORT} recorder_mirror=${RECORDER_MIRROR_PORT} tap_mirror=${TAP_MIRROR_PORT} mavsdk=${MAVSDK_PORT})"
done
