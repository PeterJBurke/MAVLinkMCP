"""Benchmark harness: the standardised mission suite T1-T10.

Shared with Plans 04/08 (LLM comparison + trial protocol), so the mission
definitions and metrics live here rather than in the test suite.
"""

from droneserver.benchmark.client import BenchmarkClient
from droneserver.benchmark.missions import SUITE, SUITE_BY_ID, Mission, MissionResult
from droneserver.benchmark.runner import run_suite

__all__ = ["SUITE", "SUITE_BY_ID", "BenchmarkClient", "Mission", "MissionResult", "run_suite"]
