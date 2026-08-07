#!/usr/bin/env python3
"""Fly the mission suite with a language model at the controls.

The model is given the drone server's real tools and a mission described in
plain English. It decides what to call. This script sets the run up, watches
it, and writes the evidence out.

Examples::

    # prove the loop: one mission, one trial
    uv run python scripts/run_llm_missions.py --missions T1 --model gpt-5.2 \
        --url http://127.0.0.1:8090/sse --api-key "$DRONESERVER_API_KEY" \
        --recorder-api-key "$DRONESERVER_RECORDER_API_KEY" \
        --audit-log /var/lib/droneserver/audit.jsonl

    # the flight portion of the suite
    uv run python scripts/run_llm_missions.py --missions T1,T2,T3,T4,T5 --model gpt-5.2 ...

    # a model with no direct key, routed through the aggregator - PIN the
    # endpoint, because tool support varies by host, not just by model
    uv run python scripts/run_llm_missions.py --model claude-opus-4.5 \
        --endpoint-only anthropic

    # what hosts serve a model on OpenRouter, and which of them support tools
    uv run python scripts/run_llm_missions.py --list-endpoints qwen3-max

Point it at a simulator. It never contacts a real aircraft, and the safety
layer must stay switched on: a run with guardrails disabled is a different
experiment and the server labels it as one.

**Spending.** No API key may exceed $100 cumulatively on this project. That cap
is enforced here, not by the provider: every trial is priced and written to
``docs/benchmark_runs/spend_ledger.csv``, and a trial that could cross the cap
is refused before it starts. The harness will not fly a model it has no price
for, because a budget it cannot compute is a budget it cannot honour.
"""

import argparse
import asyncio
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

from droneserver.llm.agent import Limits
from droneserver.llm.prompts import mission_prompts
from droneserver.llm.providers import ProviderError, list_openrouter_endpoints, resolve_model
from droneserver.llm.runner import LLM_SUITE, SKIPPED, SuiteConfig, run_llm_suite
from droneserver.llm.spend import (
    DEFAULT_BUDGET_USD,
    DEFAULT_LEDGER,
    DEFAULT_PRICE_FILE,
    ModelRetired,
    Price,
    PriceUnknown,
    SpendLedger,
    check_not_retired,
    key_id,
    load_prices,
    price_for,
)


def main() -> int:
    parser = argparse.ArgumentParser(description="LLM-in-the-loop drone mission suite")
    parser.add_argument("--url", default="http://127.0.0.1:8090/sse", help="MCP SSE endpoint of the drone server")
    parser.add_argument(
        "--api-key",
        default=os.environ.get("DRONESERVER_API_KEY", ""),
        help="drone-server API key ($DRONESERVER_API_KEY)",
    )
    parser.add_argument(
        "--recorder-api-key",
        default=os.environ.get("DRONESERVER_RECORDER_API_KEY", ""),
        help="a SECOND, telemetry-scope drone-server key for the flight recorder "
        "($DRONESERVER_RECORDER_API_KEY). Strongly recommended: the server rate-limits per client, "
        "so a recorder sharing the model's key spends the model's allowance",
    )
    parser.add_argument("--model", default="gpt-5.2", help="model, or provider:model to force a provider")
    parser.add_argument("--missions", default="T1", help="comma-separated ids, e.g. T1,T2,T8 (default T1)")
    parser.add_argument("--trials", type=int, default=1, help="trials per mission")
    parser.add_argument("--out", default="llm_runs", help="output directory root")
    parser.add_argument("--label", default="", help="label for this run's directory")
    parser.add_argument("--audit-log", default="", help="server audit.jsonl, to join server-side latency")
    parser.add_argument("--target-label", default="", help="what the server is flying (for the report)")
    parser.add_argument("--include-slow", action="store_true", help="include T10 (>10 minutes)")
    parser.add_argument(
        "--max-turns",
        type=int,
        default=90,
        help="model turns before the trial is cut off. Generous on purpose: a model that verifies "
        "each waypoint and watches a landing spends turns doing the right thing, and cutting it off "
        "mid-landing produces a failure that is ours, not its. Cost is bounded by the budget guard, "
        "not by this",
    )
    parser.add_argument("--max-tool-calls", type=int, default=250, help="tool calls before the trial is cut off")
    parser.add_argument("--trial-timeout-s", type=float, default=1800.0, help="wall-clock limit per trial")
    parser.add_argument("--temperature", type=float, default=None, help="sampling temperature, if the model takes one")
    parser.add_argument("--reasoning-effort", default=None, help="reasoning effort, for models that expose it")
    parser.add_argument("--telemetry-interval-s", type=float, default=3.0, help="flight-recorder sampling period")

    protocol = parser.add_argument_group(
        "model protocol (set explicitly, never inherited - provider defaults differ and an "
        "unstated default is a confound)"
    )
    protocol.add_argument(
        "--parallel-tool-calls",
        choices=["on", "off"],
        default="on",
        help="may the model request several tools in one turn? Defaults differ by vendor "
        "(on for Grok, off for Qwen), so this is always sent",
    )
    protocol.add_argument(
        "--tool-choice",
        default="auto",
        help="'auto' is the only value every provider in the matrix honours - GLM cannot be forced "
        "to call a tool at all",
    )
    protocol.add_argument(
        "--endpoint-only",
        default="",
        help="comma-separated OpenRouter hosts to pin (fallbacks disabled). REQUIRED for aggregator "
        "runs: tool support varies by serving endpoint, so an unpinned run can score a tool-capable "
        "model as tool-blind",
    )
    protocol.add_argument("--quantization", default="", help="weight precision of the pinned endpoint, for the record")

    money = parser.add_argument_group("spending (hard cap: no key may exceed $100 on this project)")
    money.add_argument("--budget-usd", type=float, default=DEFAULT_BUDGET_USD, help="cumulative cap per API key")
    money.add_argument("--ledger", default=str(DEFAULT_LEDGER), help="the spend ledger to read and append to")
    money.add_argument("--prices", default=str(DEFAULT_PRICE_FILE), help="price table (see update_model_prices.py)")
    money.add_argument("--price-input", type=float, default=None, help="override: USD per million input tokens")
    money.add_argument("--price-output", type=float, default=None, help="override: USD per million output tokens")
    money.add_argument("--price-cached-input", type=float, default=None, help="override: USD per million cached tokens")
    money.add_argument("--max-trial-cost-usd", type=float, default=5.0, help="stop a single trial at this cost")

    recovery = parser.add_argument_group("recovering from a dead drone link")
    recovery.add_argument(
        "--link-recovery-command",
        default="",
        help="shell command that restarts the drone server when its MAVLink helper dies "
        "(e.g. 'systemctl restart droneserver-staging'). Every use is stamped on the trial",
    )
    recovery.add_argument("--link-retries", type=int, default=1, help="retries per trial after a link recovery")

    parser.add_argument("--list", action="store_true", help="show the suite and the prompts, then exit")
    parser.add_argument("--list-endpoints", default="", help="show OpenRouter hosts for a model, then exit")
    parser.add_argument("--dry-run", action="store_true", help="resolve everything and print the plan; fly nothing")
    args = parser.parse_args()

    from droneserver.benchmark.missions import DEFAULT_CONTEXT

    if args.list:
        prompts = mission_prompts({**DEFAULT_CONTEXT, "home_amsl_m": 0.0})
        for mission_id in [*LLM_SUITE, *SKIPPED]:
            note = f"  (skipped: {SKIPPED[mission_id]})" if mission_id in SKIPPED else ""
            print(f"{mission_id}{note}\n    {prompts[mission_id]}\n")
        return 0

    if args.list_endpoints:
        endpoints = asyncio.run(
            list_openrouter_endpoints(args.list_endpoints, os.environ.get("OPENROUTER_API_KEY", ""))
        )
        if not endpoints:
            print("no endpoints listed (is the model id right? try the vendor/model form)")
            return 1
        print(f"{'host':<24} {'tools':<6} {'quantization':<14} context")
        for e in endpoints:
            print(
                f"{str(e['provider_name']):<24} {('yes' if e['supports_tools'] else 'NO'):<6} "
                f"{str(e['quantization']):<14} {e['context_length']}"
            )
        print("\nPin one with --endpoint-only <host>. Hosts marked NO cannot call tools at all;")
        print("running against one would score the model as tool-blind, which would be a false result.")
        return 0

    missions = [m.strip().upper() for m in args.missions.split(",") if m.strip()] or ["T1"]
    unknown = [m for m in missions if m not in LLM_SUITE and m not in SKIPPED]
    if unknown:
        parser.error(f"unknown mission ids: {unknown}; known: {LLM_SUITE + list(SKIPPED)}")

    try:
        check_not_retired(args.model)
        route = resolve_model(args.model)
    except (ProviderError, ModelRetired) as e:
        print(f"ERROR: {e}", file=sys.stderr)
        return 2

    if route.provider.name == "openrouter" and not args.endpoint_only:
        print(
            "ERROR: aggregator runs must pin a serving endpoint (--endpoint-only). Tool support "
            "varies by host for the same model name, so an unpinned run can silently record a "
            f"tool-capable model as tool-blind. See: --list-endpoints {route.wire_model}",
            file=sys.stderr,
        )
        return 2

    # ---- price, without which the cap cannot be enforced --------------------
    if args.price_input is not None and args.price_output is not None:
        price = Price(args.price_input, args.price_output, args.price_cached_input or 0.0)
        price_age = "supplied on the command line"
    else:
        table, fetched = load_prices(Path(args.prices))
        try:
            price = price_for(table, route.requested_model)
        except PriceUnknown as e:
            print(f"ERROR: {e}", file=sys.stderr)
            return 2
        price_age = f"from {args.prices}, fetched {fetched or 'unknown'}"

    api_key = os.environ.get(route.provider.api_key_env, "")
    ledger = SpendLedger(path=Path(args.ledger), budget_usd=args.budget_usd)
    key = key_id(route.provider.name, api_key)
    already = ledger.spent_by(key)

    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    label = args.label or f"{route.provider.name}-{route.requested_model}".replace("/", "_")
    out_dir = Path(args.out) / f"{stamp}_{label}"

    model_options = {
        "parallel_tool_calls": args.parallel_tool_calls == "on",
        "tool_choice": args.tool_choice,
        "endpoint_only": [e.strip() for e in args.endpoint_only.split(",") if e.strip()],
        "pinned_quantization": args.quantization,
    }
    if args.temperature is not None:
        model_options["temperature"] = args.temperature
    if args.reasoning_effort:
        model_options["reasoning_effort"] = args.reasoning_effort

    config = SuiteConfig(
        url=args.url,
        api_key=args.api_key,
        recorder_api_key=args.recorder_api_key,
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
            max_cost_usd=args.max_trial_cost_usd,
        ),
        model_options=model_options,
        price=price,
        ledger=ledger,
        key_id=key,
        provider_name=route.provider.name,
        link_recovery_command=args.link_recovery_command,
        link_retries=args.link_retries,
    )

    print(f"model:    {route.requested_model} via {route.provider.name} ({route.routing})")
    print(f"protocol: parallel_tool_calls={args.parallel_tool_calls}, tool_choice={args.tool_choice}", end="")
    print(f", endpoint pinned to {args.endpoint_only}" if args.endpoint_only else "")
    print(f"price:    ${price.input:.2f}/M in, ${price.output:.2f}/M out, ${price.cached_input:.3f}/M cached")
    print(f"          ({price_age})")
    print(f"budget:   ${already:.2f} of ${args.budget_usd:.2f} already spent on key {key}")
    print(f"server:   {args.url} ({'authenticated' if args.api_key else 'ANONYMOUS - telemetry scope only'})")
    print(
        f"recorder: {'own telemetry-scope key' if args.recorder_api_key else 'SHARING the model key - see --recorder-api-key'}"
    )
    print(f"missions: {', '.join(missions)} x{args.trials}")
    print(f"output:   {out_dir}")
    if args.dry_run:
        print("dry run: nothing was flown")
        return 0

    results = asyncio.run(run_llm_suite(config))
    flown = [r for r in results if not r.skipped and not r.link_failure and not r.budget_stop]
    failed = [r for r in flown if not r.passed]
    lost = [r for r in results if r.link_failure]
    stopped = [r for r in results if r.budget_stop]
    print(f"\nwrote {out_dir}/summary.md")
    print(f"{len(flown) - len(failed)}/{len(flown)} missions passed on telemetry evidence")
    if lost:
        print(f"{len(lost)} trial(s) lost to a broken drone link (not counted as model results)")
    if stopped:
        print(f"{len(stopped)} trial(s) not run because of the spending cap; rerun to resume")
    print(f"spend on {key}: ${ledger.spent_by(key):.2f} of ${args.budget_usd:.2f}")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
