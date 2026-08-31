#!/usr/bin/env python3
"""One-shot EXTENDED_SYS_STATE re-arm: ask the autopilot to (re-)publish the
in_air topic via MAVSDK's set_rate_in_air (MAV_CMD_SET_MESSAGE_INTERVAL for
EXTENDED_SYS_STATE), sent over the lane's EXISTING mavsdk_server connection.

Root cause (lane-7 get_in_air outage, 2026-08-20 forensic): ArduPilot does not
stream EXTENDED_SYS_STATE by default; MAVSDK's telemetry.in_air() plugin
requests it once, lazily, the first time something subscribes after the drone
connects. If that one-shot request is lost or races the connection coming up
(observed after a sitl@N + droneserver-lane@N restart pair, even with a full
serialized sitl -> relay -> droneserver-lane -> /sse restart), it is never
retried automatically - get_armed/get_position stay fast (they ride
HEARTBEAT/GLOBAL_POSITION_INT, which ArduPilot streams by default) while
get_in_air times out indefinitely. A full-chain restart does NOT fix this (it
was tried first and did not clear the defect); explicitly re-requesting the
rate over the already-live link does, in about 0.1s, with no restart needed.

Usage: rearm_in_air_stream.py URL API_KEY [RATE_HZ]
Exit 0: set_telemetry_rate(in_air) reported success.
Exit 1: the call failed or errored - caller should fall back to a full
        restart-based recovery cycle.
"""

import sys
import time

sys.path.insert(0, "/root/droneserver/src")
from droneserver.benchmark.client import BenchmarkClient  # noqa: E402

url, api_key = sys.argv[1], sys.argv[2]
rate_hz = float(sys.argv[3]) if len(sys.argv) > 3 else 2.0

c = BenchmarkClient(url=url, api_key=api_key)
t0 = time.perf_counter()
try:
    result = c.call("set_telemetry_rate", timeout=15.0, topic="in_air", rate_hz=rate_hz)
    elapsed = time.perf_counter() - t0
    if isinstance(result, dict) and result.get("status") == "success":
        print(f"REARMED: in_air rate set to {rate_hz}Hz in {elapsed:.2f}s: {result}")
        sys.exit(0)
    print(f"REARM FAILED: set_telemetry_rate returned {result!r} in {elapsed:.2f}s")
    sys.exit(1)
except Exception as e:  # noqa: BLE001
    elapsed = time.perf_counter() - t0
    print(f"REARM FAILED: set_telemetry_rate raised after {elapsed:.2f}s: {type(e).__name__}: {e}")
    sys.exit(1)
