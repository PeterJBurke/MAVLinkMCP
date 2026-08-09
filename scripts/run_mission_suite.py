#!/usr/bin/env python3
"""Run the standardised mission suite (T1-T10) against a droneserver.

Examples::

    # local docker SITL, via a server already running on :8080
    uv run python scripts/run_mission_suite.py --url http://127.0.0.1:8080/sse

    # the staging server on llmuavdev (which talks to llmuavsitl)
    uv run python scripts/run_mission_suite.py \\
        --url http://127.0.0.1:8080/sse --api-key "$DRONESERVER_API_KEY" \\
        --audit-log /var/lib/droneserver/audit.jsonl --missions T1,T2,T8,T9

The suite never contacts a real aircraft: point it at a simulator.
"""

import argparse
import os
import sys
from pathlib import Path

from droneserver.benchmark.capture_cli import (
    add_capture_arguments,
    build_capture_config,
    report_capture,
)
from droneserver.benchmark.client import BenchmarkClient
from droneserver.benchmark.missions import SUITE, SUITE_BY_ID
from droneserver.benchmark.runner import default_mission_ids, run_suite


def main() -> int:
    parser = argparse.ArgumentParser(description="droneserver mission suite T1-T10")
    parser.add_argument("--url", default="http://127.0.0.1:8080/sse", help="MCP SSE endpoint")
    parser.add_argument("--api-key", default=os.environ.get("DRONESERVER_API_KEY", ""),
                        help="API key (defaults to $DRONESERVER_API_KEY)")
    parser.add_argument("--missions", default="", help="comma-separated ids, e.g. T1,T2,T8 (default: all)")
    parser.add_argument("--trials", type=int, default=1, help="trials per mission")
    parser.add_argument("--out", default="benchmark_runs", help="output directory root")
    parser.add_argument("--label", default="", help="label for this run's directory")
    parser.add_argument("--audit-log", default="", help="server audit.jsonl to slice metrics from")
    parser.add_argument("--include-slow", action="store_true", help="include T10 (>10 min)")
    parser.add_argument("--target-label", default="", help="what the server is flying (for the report)")
    parser.add_argument("--list", action="store_true", help="list the suite and exit")

    # -- Plan 19 capture layer (all optional; capture is OFF unless --capture) --
    # Shared with scripts/run_llm_missions.py: one definition, so the two
    # harnesses cannot drift apart again (see benchmark/capture_cli.py).
    add_capture_arguments(parser)

    args = parser.parse_args()

    if args.list:
        for mission in SUITE:
            print(f"{mission.mission_id:4} {'(slow) ' if mission.slow else '       '}{mission.name}")
        return 0

    ids = [m.strip().upper() for m in args.missions.split(",") if m.strip()] or default_mission_ids()
    unknown = [m for m in ids if m not in SUITE_BY_ID]
    if unknown:
        parser.error(f"unknown mission ids: {unknown}; known: {sorted(SUITE_BY_ID)}")

    from datetime import datetime, timezone

    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    out_dir = Path(args.out) / (f"{stamp}_{args.label}" if args.label else stamp)

    # Build the capture config only when --capture is set, so the no-capture
    # path never imports the capture layer (pymavlink/mavsdk).
    capture_cfg = build_capture_config(args, error=parser.error)

    client = BenchmarkClient(url=args.url, api_key=args.api_key)
    print(f"connecting to {args.url} ...", flush=True)
    if not client.wait_ready():
        print("ERROR: the server never reported a live drone link", file=sys.stderr)
        return 2

    context = {
        "target_label": args.target_label or args.url,
        "client_label": "authenticated" if args.api_key else "unauthenticated",
    }
    results = run_suite(
        client=client,
        mission_ids=ids,
        trials=args.trials,
        out_dir=out_dir,
        context=context,
        audit_log=Path(args.audit_log) if args.audit_log else None,
        include_slow=args.include_slow,
        capture=capture_cfg,
    )
    ran = [r for r in results if not r.skipped]
    failed = [r for r in ran if not r.passed]
    print(f"\nwrote {out_dir}/summary.md")
    print(f"{len(ran) - len(failed)}/{len(ran)} missions passed")
    capture_failed = report_capture([r.capture_status for r in results],
                                    require_complete=args.require_complete_capture)
    if failed:
        return 1
    return 4 if capture_failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
