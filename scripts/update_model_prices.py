#!/usr/bin/env python3
"""Refresh `docs/model_prices.json` from OpenRouter's public model catalogue.

The LLM harness enforces a hard per-key spending cap, and it cannot do that
without knowing what tokens cost. Prices are **not** hard-coded anywhere in
this project: they change, and a cost column computed from a stale table is
fiction dressed as data.

OpenRouter publishes per-million-token prices for every model it lists -
including models we call directly at their vendor - and the endpoint needs no
API key. This script reads it and writes a small table with the fetch date
stamped on it, so anyone reading a cost figure can see how old the prices
behind it are.

    uv run python scripts/update_model_prices.py
    uv run python scripts/update_model_prices.py --show gpt-5.2

Prices for a model that OpenRouter does not list must be added to the file by
hand, with a note saying where they came from.
"""

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path

import httpx

CATALOGUE = "https://openrouter.ai/api/v1/models"
DEFAULT_OUT = Path("docs/model_prices.json")


def main() -> int:
    parser = argparse.ArgumentParser(description="refresh the model price table")
    parser.add_argument("--out", default=str(DEFAULT_OUT), help="where to write the table")
    parser.add_argument("--show", default="", help="print the price for one model and exit")
    parser.add_argument("--timeout-s", type=float, default=30.0)
    args = parser.parse_args()

    response = httpx.get(CATALOGUE, timeout=args.timeout_s)
    response.raise_for_status()
    catalogue = response.json().get("data") or []

    prices: dict[str, dict] = {}
    for entry in catalogue:
        pricing = entry.get("pricing") or {}
        try:
            row = {
                # OpenRouter quotes dollars per token; the rest of this project
                # works in dollars per million, which is how vendors advertise.
                "input": float(pricing["prompt"]) * 1_000_000,
                "output": float(pricing["completion"]) * 1_000_000,
                "cached_input": float(pricing.get("input_cache_read") or 0.0) * 1_000_000,
                "supports_tools": "tools" in (entry.get("supported_parameters") or []),
            }
        except (KeyError, TypeError, ValueError):
            continue
        if row["input"] <= 0 and row["output"] <= 0:
            continue  # free or unpriced; nothing to enforce a budget against
        prices[entry["id"]] = row
        # Also file it under the bare model name, so `--model gpt-5.2` resolves
        # without anyone having to know OpenRouter's vendor prefixes.
        bare = entry["id"].split("/")[-1]
        prices.setdefault(bare, row)

    blob = {
        "fetched_utc": datetime.now(timezone.utc).isoformat(),
        "source": CATALOGUE,
        "units": "USD per 1,000,000 tokens",
        "note": (
            "Prices go stale. Providers change them without notice, and OpenRouter's listing for a "
            "model called directly at its vendor is the vendor's list price, not necessarily the rate "
            "on a particular account. Re-run scripts/update_model_prices.py before quoting a cost."
        ),
        "prices": prices,
    }

    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(blob, indent=1, sort_keys=True) + "\n", encoding="utf-8")
    print(f"wrote {out} with {len(prices)} entries (fetched {blob['fetched_utc']})")

    if args.show:
        row = prices.get(args.show) or prices.get(args.show.split("/")[-1])
        print(f"{args.show}: {json.dumps(row) if row else 'not listed'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
