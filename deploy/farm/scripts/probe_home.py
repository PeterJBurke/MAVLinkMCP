#!/usr/bin/env python3
"""One-off probe: call get_home_position through the MCP client, print it."""

import sys

sys.path.insert(0, "/root/droneserver/src")
from droneserver.benchmark.client import BenchmarkClient

url = sys.argv[1]
api_key = sys.argv[2]
c = BenchmarkClient(url=url, api_key=api_key)
result = c.call("get_home_position")
print(result)
