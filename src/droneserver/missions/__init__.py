"""Managed missions: server-side state machine, checkpointing, auto-actions.

The answer to R3's 5-10 minute session ceiling - see runner.py.
"""

from droneserver.missions.runner import RUNNER, MissionRunner
from droneserver.missions.state import MissionRecord, Phase

__all__ = ["RUNNER", "MissionRecord", "MissionRunner", "Phase"]
