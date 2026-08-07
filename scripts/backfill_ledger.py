#!/usr/bin/env python3
"""Charge already-completed LLM runs to the spend ledger.

The per-key cap is only meaningful if it counts everything the key has spent,
including the runs made before the ledger existed. This reads the token counts
those runs recorded and prices them with the current table, marking each row
as backfilled so it is never mistaken for a live measurement.

    uv run python scripts/backfill_ledger.py --model gpt-5.2 --provider openai
"""

import argparse
import csv
import os
from pathlib import Path

from droneserver.llm.spend import DEFAULT_LEDGER, DEFAULT_PRICE_FILE, SpendLedger, key_id, load_prices, price_for


def main() -> int:
    parser = argparse.ArgumentParser(description="backfill the spend ledger from completed runs")
    parser.add_argument("--runs", default="llm_runs", help="directory of run directories")
    parser.add_argument("--model", required=True, help="model those runs used")
    parser.add_argument("--provider", required=True, help="provider those runs used")
    parser.add_argument("--ledger", default=str(DEFAULT_LEDGER))
    parser.add_argument("--prices", default=str(DEFAULT_PRICE_FILE))
    args = parser.parse_args()

    table, _ = load_prices(Path(args.prices))
    price = price_for(table, args.model)
    ledger = SpendLedger(path=Path(args.ledger))
    key = key_id(args.provider, os.environ.get(f"{args.provider.upper()}_API_KEY", ""))

    already = set()
    with Path(args.ledger).open(newline="", encoding="utf-8") as fh:
        for row in csv.DictReader(fh):
            already.add((row["run_dir"], row["mission_id"], row["trial"]))

    charged = 0.0
    for run_dir in sorted(Path(args.runs).iterdir()):
        missions = run_dir / "missions.csv"
        if not missions.exists():
            continue
        for row in csv.DictReader(missions.open(newline="", encoding="utf-8")):
            if (str(run_dir), row["mission_id"], row["trial"]) in already:
                continue
            try:
                cost = price.cost_usd(
                    int(row["input_tokens"] or 0),
                    int(row["cached_input_tokens"] or 0),
                    int(row["output_tokens"] or 0),
                )
            except ValueError:
                continue
            if cost <= 0:
                continue
            ledger.record(
                key=key,
                provider=args.provider,
                model=args.model,
                resolved_model="",
                mission_id=row["mission_id"],
                trial=int(row["trial"]),
                input_tokens=int(row["input_tokens"] or 0),
                cached_input_tokens=int(row["cached_input_tokens"] or 0),
                output_tokens=int(row["output_tokens"] or 0),
                reasoning_tokens=int(row["reasoning_tokens"] or 0),
                cost_usd=cost,
                run_dir=str(run_dir),
                note=f"backfilled ({row['verdict']})",
            )
            charged += cost
    print(f"backfilled ${charged:.4f}; key {key} has now spent ${ledger.spent_by(key):.2f}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
