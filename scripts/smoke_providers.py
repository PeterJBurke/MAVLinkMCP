#!/usr/bin/env python3
"""Check every model in the comparison matrix can actually call a tool.

Cheap, fast, and run before any flight: a provider whose tool-calling path is
broken should be found out for a fraction of a cent, not halfway through a
mission with an aircraft in the air. It sends one tiny request with two toy
tool schemas and reports what came back, including the resolved model version
and whether the provider reported any prompt caching.
"""

import asyncio
import sys

from droneserver.llm.providers import ToolSpec, open_session, resolve_model

TOOLS = [
    ToolSpec("get_position", "Get the drone's current GPS position.", {"type": "object", "properties": {}}),
    ToolSpec(
        "takeoff",
        "Take off to an altitude in metres.",
        {
            "type": "object",
            "properties": {"takeoff_altitude": {"type": "number", "description": "metres above the launch point"}},
            "required": ["takeoff_altitude"],
        },
    ),
]

MATRIX = [
    "claude-opus-5",
    "claude-sonnet-5",
    "claude-haiku-4-5-20251001",
    "gemini-3.1-pro-preview",
    "gemini-3.6-flash",
    "gemini-3.5-flash-lite",
    "gemini-robotics-er-2-preview",
    "grok-4.5",
    "grok-4.20-0309-reasoning",
    "grok-4.20-0309-non-reasoning",
]


async def probe(spec: str) -> str:
    try:
        route = resolve_model(spec)
    except Exception as e:
        return f"{spec:32} ROUTE-FAIL   {type(e).__name__}: {str(e)[:70]}"
    session = None
    try:
        session = open_session(spec, parallel_tool_calls=True, tool_choice="auto", max_output_tokens=1024)
        session.start(
            "You control a drone. Use the tools you are given.",
            "Where is the drone right now, and then take it up to 12 metres.",
        )
        turn = await session.next_turn(TOOLS)
        names = ",".join(c.name for c in turn.tool_calls) or "NONE"
        return (
            f"{spec:32} OK  {route.provider.name:10} tools={names:26} "
            f"in={turn.input_tokens:<6} cached={turn.cached_input_tokens:<6} out={turn.output_tokens:<5} "
            f"{turn.decision_latency_ms:>6.0f}ms  resolved={turn.resolved_model}"
        )
    except Exception as e:
        return f"{spec:32} FAIL {route.provider.name:10} {type(e).__name__}: {str(e)[:120]}"
    finally:
        if session is not None:
            try:
                await session.aclose()
            except Exception:
                pass


async def main() -> int:
    models = sys.argv[1:] or MATRIX
    for spec in models:
        print(await probe(spec), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
