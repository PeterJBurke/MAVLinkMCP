#!/usr/bin/env python3
"""Bound the under-billing in ledger rows written before the cache-write fix.

**Why this is a separate file and not an edit to the ledger.**
``docs/benchmark_runs/spend_ledger.csv`` is a research record: it is what the
harness charged, when it charged it, and it is append-only. Rewriting a
``cost_usd`` in it would destroy the evidence of the defect and leave a file
that agrees with itself and with nothing else. So the correction lives here,
beside it, and the ledger keeps its own history.

**Why the correction is a bound and not a number.** Until 2026-08-09 the
harness recorded ``input_tokens`` (everything that went in) and
``cached_input_tokens`` (the part read back from the prompt cache), and nothing
else. The part *written into* the cache - which Anthropic bills at **1.25x**
the base input rate for the 5-minute ``ephemeral`` cache this harness requests -
was never recorded separately, and was therefore charged at 1.0x. From the two
numbers that were kept it is impossible to recover how much of
``input_tokens - cached_input_tokens`` was a cache write rather than a fresh
token, so no exact per-row correction exists. What does exist is a bound:

* **lower bound**: 0 written tokens, i.e. the recorded figure is right;
* **upper bound**: every non-cache-read input token was a cache write, adding
  0.25 x rate x (input - cached) to the row.

The upper bound is the interesting one, because it is the one that matches
reality: it reproduces the observed credit exhaustion to within 0.7%, which is
strong evidence that on these runs nearly all non-cache-read input really was
cache-write traffic.

Only providers that both report cache writes and charge a premium for them can
be affected. In this ledger that is Anthropic alone: every other provider in
the matrix speaks the OpenAI wire format, which reports no cache-write token
count at all and bills writes at the base input rate, so its rows are exact.

    uv run python scripts/ledger_cache_write_correction.py
    uv run python scripts/ledger_cache_write_correction.py --out docs/benchmark_runs/...csv
"""

import argparse
import csv
from collections import defaultdict
from pathlib import Path

from droneserver.llm.spend import (
    CACHE_WRITE_MULTIPLIER,
    DEFAULT_LEDGER,
    DEFAULT_PRICE_FILE,
    load_prices,
    price_for,
)

DEFAULT_OUT = Path("docs/benchmark_runs/spend_ledger_corrections.csv")

HEADER = [
    "key_id",
    "provider",
    "model",
    "rows",
    "input_tokens",
    "cached_input_tokens",
    "unattributed_input_tokens",
    "recorded_cost_usd",
    "correction_upper_usd",
    "corrected_upper_usd",
]


def main() -> int:
    parser = argparse.ArgumentParser(description="bound the pre-fix cache-write under-billing")
    parser.add_argument("--ledger", default=str(DEFAULT_LEDGER))
    parser.add_argument("--prices", default=str(DEFAULT_PRICE_FILE))
    parser.add_argument("--out", default=str(DEFAULT_OUT))
    args = parser.parse_args()

    table, _ = load_prices(Path(args.prices))
    groups: dict[tuple[str, str, str], dict[str, float]] = defaultdict(
        lambda: {"rows": 0.0, "input": 0.0, "cached": 0.0, "recorded": 0.0, "extra": 0.0}
    )

    with Path(args.ledger).open(newline="", encoding="utf-8") as fh:
        for row in csv.DictReader(fh):
            # A row that already records the split is exact; nothing to bound.
            if (row.get("cache_write_tokens") or "").strip():
                continue
            provider, model = row.get("provider", ""), row.get("model", "")
            bare = model.split(":")[-1].split("/")[-1].lower()
            multiplier = next((m for f, m in CACHE_WRITE_MULTIPLIER.items() if bare.startswith(f)), None)
            if multiplier is None:
                continue  # no cache-write premium published: the row is exact
            try:
                price = price_for(table, model.split(":")[-1])
                total_in = int(row["input_tokens"] or 0)
                cached = int(row["cached_input_tokens"] or 0)
                recorded = float(row["cost_usd"] or 0.0)
            except Exception:  # no price on file, or an unreadable row: skip it
                continue
            unattributed = max(total_in - cached, 0)
            bucket = groups[(row.get("key_id", ""), provider, model)]
            bucket["rows"] += 1
            bucket["input"] += total_in
            bucket["cached"] += cached
            bucket["recorded"] += recorded
            bucket["extra"] += unattributed * price.input * (multiplier - 1.0) / 1_000_000

    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    with out.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.writer(fh)
        writer.writerow(HEADER)
        for (key, provider, model), b in sorted(groups.items()):
            writer.writerow(
                [
                    key,
                    provider,
                    model,
                    int(b["rows"]),
                    int(b["input"]),
                    int(b["cached"]),
                    int(b["input"] - b["cached"]),
                    f"{b['recorded']:.6f}",
                    f"{b['extra']:.6f}",
                    f"{b['recorded'] + b['extra']:.6f}",
                ]
            )

    recorded = sum(b["recorded"] for b in groups.values())
    extra = sum(b["extra"] for b in groups.values())
    print(f"wrote {out}")
    print(f"rows needing a bound: {int(sum(b['rows'] for b in groups.values()))}")
    print(f"recorded ${recorded:.4f}; upper-bound correction ${extra:.4f}; upper-bound total ${recorded + extra:.4f}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
