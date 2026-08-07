#!/usr/bin/env python3
"""Fly the mission suite with a language model at the controls.

The model is given the drone server's real tools and a mission described in
plain English. It decides what to call. This script sets the run up, watches
it, and writes the evidence out.

Examples::

    # prove the loop: one mission, one trial
    uv run python scripts/run_llm_missions.py --missions T1 --model gpt-5.2 \
        --url http://127.0.0.1:8090/sse --api-key "$DRONESERVER_API_KEY" \
        --audit-log /var/lib/droneserver/audit.jsonl

    # the flight portion of the suite
    uv run python scripts/run_llm_missions.py --missions T1,T2,T3,T4,T5 --model gpt-5.2 ...

    # a model with no direct key, routed through OpenRouter automatically
    uv run python scripts/run_llm_missions.py --model claude-opus-4 ...

Point it at a simulator. It never contacts a real aircraft, and the safety
layer must stay switched on: a run with guardrails disabled is a different
experiment and the server labels it as one.
"""

import argparse
import asyncio
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

from droneserver.llm.agent import Limits
from droneserver.llm.prompts import mission_prompts
from droneserver.llm.providers import ProviderError, resolve_model
from droneserver.llm.runner import LLM_SUITE, SKIPPED, SuiteConfig, run_llm_suite


def main() -> int:
    parser = argparse.ArgumentParser(description="LLM-in-the-loop drone mission suite")
    parser.add_argument("--url", default="http://127.0.0.1:8090/sse", help="MCP SSE endpoint of the drone server")
    parser.add_argument(
        "--api-key",
        default=os.environ.get("DRONESERVER_API_KEY", ""),
        help="drone-server API key ($DRONESERVER_API_KEY)",
    )
    parser.add_argument("--model", default="gpt-5.2", help="model, or provider:model to force a provider")
    parser.add_argument("--missions", default="T1", help="comma-separated ids, e.g. T1,T2,T8 (default T1)")
    parser.add_argument("--trials", type=int, default=1, help="trials per mission")
    parser.add_argument("--out", default="llm_runs", help="output directory root")
    parser.add_argument("--label", default="", help="label for this run's directory")
    parser.add_argument("--audit-log", default="", help="server audit.jsonl, to join server-side latency")
    parser.add_argument("--target-label", default="", help="what the server is flying (for the report)")
    parser.add_argument("--include-slow", action="store_true", help="include T10 (>10 minutes)")
    parser.add_argument("--max-turns", type=int, default=40, help="model turns before the trial is cut off")
    parser.add_argument("--max-tool-calls", type=int, default=120, help="tool calls before the trial is cut off")
    parser.add_argument("--trial-timeout-s", type=float, default=1800.0, help="wall-clock limit per trial")
    parser.add_argument("--temperature", type=float, default=None, help="sampling temperature, if the model takes one")
    parser.add_argument("--reasoning-effort", default=None, help="reasoning effort, for models that expose it")
    parser.add_argument("--telemetry-interval-s", type=float, default=1.5, help="flight-recorder sampling period")
    parser.add_argument(
        "--prices",
        default="",
        help="JSON file of {model: {input, cached_input, output}} USD per million tokens; "
        "without it, tokens are reported and cost is left blank",
    )
    parser.add_argument("--list", action="store_true", help="show the suite and the prompts, then exit")
    parser.add_argument("--dry-run", action="store_true", help="resolve the model and print the plan; fly nothing")
    args = parser.parse_args()

    from droneserver.benchmark.missions import DEFAULT_CONTEXT

    if args.list:
        prompts = mission_prompts({**DEFAULT_CONTEXT, "home_amsl_m": 0.0})
        for mission_id in [*LLM_SUITE, *SKIPPED]:
            note = f"  (skipped: {SKIPPED[mission_id]})" if mission_id in SKIPPED else ""
            print(f"{mission_id}{note}\n    {prompts[mission_id]}\n")
        return 0

    missions = [m.strip().upper() for m in args.missions.split(",") if m.strip()] or ["T1"]
    unknown = [m for m in missions if m not in LLM_SUITE and m not in SKIPPED]
    if unknown:
        parser.error(f"unknown mission ids: {unknown}; known: {LLM_SUITE + list(SKIPPED)}")

    try:
        route = resolve_model(args.model)
    except ProviderError as e:
        print(f"ERROR: {e}", file=sys.stderr)
        return 2

    prices = json.loads(Path(args.prices).read_text()) if args.prices else {}

    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    label = args.label or f"{route.provider.name}-{route.requested_model}".replace("/", "_")
    out_dir = Path(args.out) / f"{stamp}_{label}"

    model_options = {}
    if args.temperature is not None:
        model_options["temperature"] = args.temperature
    if args.reasoning_effort:
        model_options["reasoning_effort"] = args.reasoning_effort

    config = SuiteConfig(
        url=args.url,
        api_key=args.api_key,
        model_spec=args.model,
        missions=missions,
        trials=args.trials,
        out_dir=out_dir,
        audit_log=Path(args.audit_log) if args.audit_log else None,
        target_label=args.target_label,
        include_slow=args.include_slow,
        telemetry_interval_s=args.telemetry_interval_s,
        limits=Limits(
            max_turns=args.max_turns,
            max_tool_calls=args.max_tool_calls,
            wall_clock_s=args.trial_timeout_s,
        ),
        model_options=model_options,
        prices=prices,
    )

    print(f"model:    {route.requested_model} via {route.provider.name} ({route.routing})")
    print(f"server:   {args.url} ({'authenticated' if args.api_key else 'ANONYMOUS - telemetry scope only'})")
    print(f"missions: {', '.join(missions)} x{args.trials}")
    print(f"output:   {out_dir}")
    if args.dry_run:
        print("dry run: nothing was flown")
        return 0

    results = asyncio.run(run_llm_suite(config))
    flown = [r for r in results if not r.skipped]
    failed = [r for r in flown if not r.passed]
    print(f"\nwrote {out_dir}/summary.md")
    print(f"{len(flown) - len(failed)}/{len(flown)} missions passed on telemetry evidence")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
