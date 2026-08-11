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
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import httpx

CATALOGUE = "https://openrouter.ai/api/v1/models"
#: xAI publishes its own prices, so we take them from the vendor rather than
#: from a reseller's listing. Its units are ten-millionths of a dollar per
#: token; dividing by 10,000 gives the dollars-per-million everyone quotes.
XAI_CATALOGUE = "https://api.x.ai/v1/language-models"
XAI_UNITS_PER_USD_PER_MILLION = 10_000
DEFAULT_OUT = Path("docs/model_prices.json")

#: Prices for models **no reseller lists**, read by hand from the vendor's own
#: pricing page. These are merged AFTER the catalogue fetch so that re-running
#: this script can never silently drop them - which is exactly what would
#: happen otherwise, because OpenRouter does not carry these models and the
#: whole file is rewritten on every refresh. That failure has already bitten
#: once: gemini-robotics-er-2-preview was in the campaign's accepted matrix but
#: had no price on file, so run_llm_missions.py exited 2 ("no price ... the
#: $100 per-key cap cannot be enforced without one") and skipped it.
#:
#: Each entry names its source and the date it was read. ``setdefault`` is used
#: on merge, so if a reseller ever DOES start listing one of these, the fresh
#: catalogue price wins and the hand-entry becomes a harmless fallback.
#:
#: units: USD per 1,000,000 tokens, matching the rest of the table.
MANUAL_PRICES: dict[str, dict[str, Any]] = {
    "gemini-robotics-er-2-preview": {
        "input": 2.0,
        "output": 10.0,
        "cached_input": 0.2,
        # Google prices context caching as STORAGE ($1.00 / 1M tokens / hour),
        # not a per-token write premium, and reports no cache-write token count
        # on its usage object, so there is nothing to bill at a premium: 0.0
        # reads as "base input rate" and is never actually reached. See
        # droneserver.llm.spend.Price.cache_write.
        "cache_write": 0.0,
        "supports_tools": True,
        "source": "https://ai.google.dev/gemini-api/docs/pricing (read 2026-08-11)",
    },
}
#: Vendor mirror prefixes to also file a manual price under, so `--model
#: google/gemini-...` resolves exactly as the reseller-prefixed rows do.
MANUAL_MIRROR_PREFIXES: dict[str, str] = {"gemini": "google"}


def _merge_manual(prices: dict) -> int:
    """File the hand-entered prices, and their vendor-mirror aliases.

    Uses ``setdefault`` so a catalogue price for the same model always wins;
    the manual entry only fills a gap the catalogue leaves.
    """
    added = 0
    for name, row in MANUAL_PRICES.items():
        if name not in prices:
            prices[name] = dict(row)
            added += 1
        for stem, prefix in MANUAL_MIRROR_PREFIXES.items():
            if name.startswith(stem):
                prices.setdefault(f"{prefix}/{name}", dict(row))
    return added


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
            # Heterogeneous on purpose: prices are floats, supports_tools is a
            # bool, priced_by is a string. Annotated so the mixture is a stated
            # fact rather than something a reader (or a checker) has to infer.
            row: dict[str, Any] = {
                # OpenRouter quotes dollars per token; the rest of this project
                # works in dollars per million, which is how vendors advertise.
                "input": float(pricing["prompt"]) * 1_000_000,
                "output": float(pricing["completion"]) * 1_000_000,
                "cached_input": float(pricing.get("input_cache_read") or 0.0) * 1_000_000,
                # The THIRD input rate, and the one whose absence made this
                # harness's meter read ~15% low on Anthropic: what it costs to
                # WRITE a token into the prompt cache. OpenRouter publishes it
                # as input_cache_write (and input_cache_write_1h for the 1-hour
                # TTL, which this harness never requests - it asks for the
                # 5-minute ephemeral cache). 0.0 means the provider publishes
                # no premium, which droneserver.llm.spend.Price reads as "bill
                # these at the base input rate".
                "cache_write": float(pricing.get("input_cache_write") or 0.0) * 1_000_000,
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

    sources = [CATALOGUE]
    xai_key = os.environ.get("XAI_API_KEY", "").strip()
    if xai_key:
        added = _merge_xai(prices, xai_key, args.timeout_s)
        if added:
            sources.append(XAI_CATALOGUE)
            print(f"merged {added} models priced directly by xAI")

    manual = _merge_manual(prices)
    if manual:
        sources.append("hand-entered (see MANUAL_PRICES)")
        print(f"merged {manual} hand-entered prices no reseller lists")

    blob = {
        "fetched_utc": datetime.now(timezone.utc).isoformat(),
        "source": sources,
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
        shown = prices.get(args.show) or prices.get(args.show.split("/")[-1])
        print(f"{args.show}: {json.dumps(shown) if shown else 'not listed'}")
    return 0


def _merge_xai(prices: dict, api_key: str, timeout_s: float) -> int:
    """Add xAI's own published prices, which cover models no reseller lists.

    The grok-4.20 reasoning/non-reasoning ablation pair, in particular, does
    not appear in OpenRouter's catalogue under those names, and the harness
    refuses to fly a model it cannot price. Taking the numbers from the vendor
    is better than aliasing them onto a similar model and hoping.
    """
    try:
        response = httpx.get(XAI_CATALOGUE, headers={"Authorization": f"Bearer {api_key}"}, timeout=timeout_s)
        response.raise_for_status()
        catalogue = response.json()
    except Exception as e:
        print(f"could not read xAI prices ({type(e).__name__}); leaving them to the reseller listing")
        return 0

    added = 0
    for entry in catalogue.get("models") or catalogue.get("data") or []:
        name = entry.get("id")
        if not name:
            continue
        try:
            row: dict[str, Any] = {
                "input": float(entry["prompt_text_token_price"]) / XAI_UNITS_PER_USD_PER_MILLION,
                "output": float(entry["completion_text_token_price"]) / XAI_UNITS_PER_USD_PER_MILLION,
                "cached_input": float(entry.get("cached_prompt_text_token_price") or 0.0)
                / XAI_UNITS_PER_USD_PER_MILLION,
                # xAI's catalogue publishes no cache-write price, and its API
                # reports no cache-write token count either, so there is
                # nothing to bill at a premium and nothing to guess at. Left at
                # 0.0, which Price reads as "base input rate".
                "cache_write": 0.0,
                "supports_tools": True,
                "priced_by": "xai",
            }
        except (KeyError, TypeError, ValueError):
            continue
        if row["input"] <= 0 and row["output"] <= 0:
            continue
        prices[name] = row  # the vendor wins over a reseller listing
        prices.setdefault(f"xai/{name}", row)
        added += 1
    return added


if __name__ == "__main__":
    raise SystemExit(main())
