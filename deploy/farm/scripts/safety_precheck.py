#!/usr/bin/env python3
"""Pre-trial safety precheck: confirm the aircraft is disarmed and on the
ground before a new trial starts. Added after lane7's TG2 crash left an
armed, airborne aircraft behind (2026-08-20 incident) - the EKF/GPS-ready
watchdog alone does not catch "previous trial ended badly / mid-cleanup".

If the aircraft is armed or in the air, this attempts a return_to_launch and
polls (up to ~90s) for it to land and disarm before giving up.

Usage: safety_precheck.py URL API_KEY
Exit 0: safe (disarmed, on ground) - proceed.
Exit 1: could not confirm safe within the timeout - caller should NOT fly.
"""
import sys
import time

sys.path.insert(0, "/root/droneserver/src")
from droneserver.benchmark.client import BenchmarkClient  # noqa: E402

url, api_key = sys.argv[1], sys.argv[2]
c = BenchmarkClient(url=url, api_key=api_key)

armed = c.call("get_armed")
in_air = c.call("get_in_air")
print(f"precheck: armed={armed.get('armed')} in_air={in_air.get('in_air')}")

if not armed.get("armed") and not in_air.get("in_air"):
    print("SAFE: already disarmed and on the ground")
    sys.exit(0)

print("UNSAFE: aircraft is armed/airborne from a prior trial - commanding return_to_launch")
try:
    c.call("return_to_launch", timeout=180)
except Exception as e:  # noqa: BLE001
    print(f"return_to_launch call failed: {e}")

for i in range(18):  # up to ~90s
    time.sleep(5)
    armed = c.call("get_armed")
    in_air = c.call("get_in_air")
    print(f"  poll {i}: armed={armed.get('armed')} in_air={in_air.get('in_air')}")
    if not armed.get("armed") and not in_air.get("in_air"):
        print("SAFE: landed and disarmed")
        sys.exit(0)

print("STILL UNSAFE after 90s of polling - do not start a new trial")
sys.exit(1)
