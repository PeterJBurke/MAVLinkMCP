#!/usr/bin/env python3
"""Link-liveness probe: get_in_air must answer in under a tight budget.

This is the exact probe that dies on an orphaned mavsdk_server link (the
lane-7 watchdog defect, 2026-08-20 forensic): when sitl@N is restarted but
droneserver-lane@N is not, droneserver-lane@N's mavsdk_server subprocess
stays connected to the dead/replaced SITL instance and goes half-dead
(~16.5s action stalls) - well past ArduCopter's ~10s auto-disarm-if-never-
confirmed window, so every takeoff in that state gets rejected. A healthy
link answers get_in_air in well under 1s; this gives a cheap, fast
pre-trial assertion instead of discovering the defect via a failed takeoff.

Usage: link_liveness_check.py URL API_KEY [TIMEOUT_S]
Exit 0: link live (answered within budget).
Exit 1: link dead/slow (timeout or error) - caller should recover then retry.
"""
import sys
import time

sys.path.insert(0, "/root/droneserver/src")
from droneserver.benchmark.client import BenchmarkClient  # noqa: E402

url, api_key = sys.argv[1], sys.argv[2]
timeout_s = float(sys.argv[3]) if len(sys.argv) > 3 else 2.0

c = BenchmarkClient(url=url, api_key=api_key)
t0 = time.perf_counter()
try:
    result = c.call("get_in_air", timeout=timeout_s)
    elapsed = time.perf_counter() - t0
    if elapsed <= timeout_s and isinstance(result, dict) and result.get("status") == "success":
        print(f"LINK LIVE: get_in_air answered in {elapsed:.2f}s: in_air={result.get('in_air')}")
        sys.exit(0)
    print(f"LINK SUSPECT: get_in_air returned {result!r} in {elapsed:.2f}s (budget {timeout_s}s)")
    sys.exit(1)
except Exception as e:  # noqa: BLE001
    elapsed = time.perf_counter() - t0
    print(f"LINK DEAD: get_in_air failed/timed out after {elapsed:.2f}s: {type(e).__name__}: {e}")
    sys.exit(1)
