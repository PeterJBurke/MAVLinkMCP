"""LLM-in-the-loop mission harness.

A language model is handed the drone server's real tool schemas and a mission
described in plain English, and chooses its own commands. Everything the paper
needs from a trial - the conversation, both latencies, token counts, and a
verdict judged from the flight recorder rather than the model's claims - is
produced here.

Start at :mod:`droneserver.llm.runner`; ``scripts/run_llm_missions.py`` is the
command-line front door.
"""

from droneserver.llm.agent import AgentRun, Limits, run_agent
from droneserver.llm.mcp_session import LiveMCPSession, TelemetryRecorder
from droneserver.llm.prompts import SYSTEM_PROMPT, mission_prompts
from droneserver.llm.providers import ModelSession, ToolSpec, open_session, resolve_model
from droneserver.llm.runner import LLM_SUITE, SuiteConfig, TrialResult, run_llm_suite

__all__ = [
    "AgentRun",
    "LLM_SUITE",
    "Limits",
    "LiveMCPSession",
    "ModelSession",
    "SYSTEM_PROMPT",
    "SuiteConfig",
    "TelemetryRecorder",
    "ToolSpec",
    "TrialResult",
    "mission_prompts",
    "open_session",
    "resolve_model",
    "run_agent",
    "run_llm_suite",
]
