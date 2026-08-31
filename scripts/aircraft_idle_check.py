#!/usr/bin/env python3
"""Pre-launch aircraft check: is the SITL aircraft disarmed and on the ground?

Reads the staging API key from /etc/droneserver/staging.env itself (first key
of SAFETY_API_KEYS); the key value is never printed. Exit 0 = idle (disarmed,
on ground), 3 = NOT idle, 2 = could not read state.
"""

from droneserver.benchmark.client import BenchmarkClient


def staging_key(path: str = "/etc/droneserver/staging.env") -> str:
    with open(path) as f:
        for line in f:
            if line.startswith("SAFETY_API_KEYS="):
                first = line.strip().split("=", 1)[1].split(",")[0]
                return first.split(":", 1)[1] if ":" in first else first
    return ""


def main() -> int:
    c = BenchmarkClient(url="http://127.0.0.1:8090/sse", api_key=staging_key())
    armed = c.call("get_armed", timeout=60)
    in_air = c.call("get_in_air", timeout=60)
    pos = c.call("get_position", timeout=60)
    if armed.get("status") != "success" or pos.get("status") != "success":
        print(f"could not read state: armed={armed.get('status')} pos={pos.get('status')}")
        return 2
    p = pos.get("position", {})
    print(
        f"armed: {armed.get('armed')} | in_air: {in_air.get('in_air')} | "
        f"pos {p.get('latitude_deg'):.6f},{p.get('longitude_deg'):.6f} "
        f"rel_alt {p.get('relative_altitude_m'):.2f} m"
    )
    idle = armed.get("armed") is False and in_air.get("in_air") is not True
    print("AIRCRAFT IDLE" if idle else "AIRCRAFT NOT IDLE")
    return 0 if idle else 3


if __name__ == "__main__":
    raise SystemExit(main())
