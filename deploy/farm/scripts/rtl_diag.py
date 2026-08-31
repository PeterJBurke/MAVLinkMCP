import sys
import time

sys.path.insert(0, "/root/droneserver/src")
from droneserver.benchmark.client import BenchmarkClient

envfile = "/etc/droneserver/lane7.env"
apikey = None
with open(envfile) as f:
    for line in f:
        if line.startswith("SAFETY_API_KEYS="):
            apikey = line.strip().split("=", 1)[1].split(",")[0].split(":")[1]
            break
assert apikey, "no key found"

c = BenchmarkClient(url="http://127.0.0.1:8098/sse", api_key=apikey)
print("get_flight_mode:", c.call("get_flight_mode"))
print("get_position 1:", c.call("get_position"))
print("--- reissuing return_to_launch (properly scoped key) ---")
try:
    r = c.call("return_to_launch", timeout=60)
    print("rtl result:", r)
except Exception as e:
    print("rtl exception:", type(e).__name__, e)
for i in range(6):
    time.sleep(10)
    p = c.call("get_position")
    a = c.call("get_armed")
    ia = c.call("get_in_air")
    fm = c.call("get_flight_mode")
    print(f"t+{(i+1)*10}s: mode={fm.get('flight_mode')} armed={a.get('armed')} in_air={ia.get('in_air')} pos={p.get('position')}")
