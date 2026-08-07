"""Money: what a run costs, and the hard cap it may not cross.

**Who this is for:** whoever is paying, and anyone reproducing a run who needs
to know what it cost.

**The rule.** No single API key may spend more than **$100 cumulatively on this
project**. That is an operator's rule, not a suggestion, and it is enforced
here rather than left to the provider - most providers will happily bill past
any figure you had in mind, and the ones that do offer caps offer them per
*account*, not per experiment.

**How it is enforced.** Every trial appends a row to a ledger keyed by the API
key's *fingerprint* - a one-way hash, never the key itself. Before a trial
starts, the harness adds up what that key has already spent, projects what this
trial could cost at its configured limits, and **refuses to start if the total
could cross the cap**. Projection deliberately uses uncached input pricing and
the full turn and output allowance, so it over-estimates: a cap that is only
respected on average is not a cap. A trial is also stopped mid-flight if its
own running cost passes a per-trial ceiling.

**Where prices come from.** Not from memory. ``docs/model_prices.json`` is
fetched from OpenRouter's public model catalogue, which publishes per-million
token prices for every model it lists, including the ones we call directly.
The file records when it was fetched, because **prices go stale**: providers
change them, and a cost column computed from a year-old table is fiction. If a
model has no price, the harness refuses to run it rather than flying blind
against a budget it cannot compute.

**Retired models are refused outright.** Vendor pricing pages keep listing
models that no longer exist; running one wastes a slot in the comparison matrix
and produces a row of errors. :data:`RETIRED_MODELS` names them and the date
they went.
"""

from __future__ import annotations

import csv
import hashlib
import json
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

#: The operator's hard cap, per API key, for the whole project.
DEFAULT_BUDGET_USD = 100.0

#: Where the ledger lives. One file for the project, appended to forever.
DEFAULT_LEDGER = Path("docs/benchmark_runs/spend_ledger.csv")
DEFAULT_PRICE_FILE = Path("docs/model_prices.json")

LEDGER_HEADER = [
    "ts_utc",
    "provider",
    "key_id",
    "model",
    "resolved_model",
    "mission_id",
    "trial",
    "input_tokens",
    "cached_input_tokens",
    "output_tokens",
    "reasoning_tokens",
    "cost_usd",
    "cumulative_usd_for_key",
    "budget_usd",
    "remaining_usd",
    "run_dir",
    "note",
]

#: Models that no longer exist. Keeping them out of the matrix is cheaper than
#: explaining a column of errors. Vendor pricing pages lag retirements, so this
#: list is maintained by hand from announcements.
RETIRED_MODELS: dict[str, str] = {
    "magistral": "retired 2026-07-31 (Mistral); its pricing page still lists it",
    "magistral-medium": "retired 2026-07-31 (Mistral)",
    "magistral-small": "retired 2026-07-31 (Mistral)",
    "devstral": "retired 2026-07-31 (Mistral)",
    "devstral-medium": "retired 2026-07-31 (Mistral)",
    "devstral-small": "retired 2026-07-31 (Mistral)",
    "kimi-k2": "discontinued 2026-05-25 (Moonshot)",
    "kimi-k2.5": "discontinued 2026-05-25 (Moonshot)",
}


class BudgetExceeded(RuntimeError):
    """Starting this trial could take a key past its cap. Nothing was spent."""


class PriceUnknown(RuntimeError):
    """No price for this model, so the cap cannot be enforced. Refusing to run."""


class ModelRetired(RuntimeError):
    """The model no longer exists."""


def key_id(provider: str, api_key: str) -> str:
    """A stable, non-reversible name for a key. The key itself never appears."""
    return f"{provider}:{hashlib.sha256(api_key.encode()).hexdigest()[:12]}"


def check_not_retired(model: str) -> None:
    bare = model.split("/")[-1].lower()
    for name, why in RETIRED_MODELS.items():
        if bare == name or bare.startswith(name + "-"):
            raise ModelRetired(f"{model}: {why}. Pick a current model.")


@dataclass(frozen=True)
class Price:
    """USD per million tokens."""

    input: float
    output: float
    cached_input: float = 0.0

    def cost_usd(self, input_tokens: int, cached_input_tokens: int, output_tokens: int) -> float:
        fresh = max(input_tokens - cached_input_tokens, 0)
        cached_rate = self.cached_input if self.cached_input else self.input
        return (fresh * self.input + cached_input_tokens * cached_rate + output_tokens * self.output) / 1_000_000


def load_prices(path: Path = DEFAULT_PRICE_FILE) -> tuple[dict[str, Price], str]:
    """Read the price table. Returns ``({model: Price}, when-it-was-fetched)``."""
    if not path.exists():
        return {}, ""
    blob = json.loads(path.read_text(encoding="utf-8"))
    prices = {
        name: Price(
            input=float(row["input"]),
            output=float(row["output"]),
            cached_input=float(row.get("cached_input") or 0.0),
        )
        for name, row in (blob.get("prices") or {}).items()
    }
    return prices, blob.get("fetched_utc", "")


def price_for(prices: dict[str, Price], model: str) -> Price:
    """The price for a model, trying the plain name and the vendor-slug form."""
    bare = model.split("/")[-1]
    for candidate in (model, bare, model.lower(), bare.lower()):
        if candidate in prices:
            return prices[candidate]
    for name, price in prices.items():
        if name.split("/")[-1].lower() == bare.lower():
            return price
    raise PriceUnknown(
        f"no price on file for '{model}'. Refusing to run: the ${DEFAULT_BUDGET_USD:.0f} per-key cap "
        f"cannot be enforced without one. Refresh the table with "
        f"`uv run python scripts/update_model_prices.py`, or pass --price-input/--price-output."
    )


def project_trial_cost_usd(
    price: Price, max_turns: int, prompt_tokens_per_turn: int, output_tokens_per_turn: int
) -> float:
    """A deliberate over-estimate of what one trial could cost.

    Uncached input pricing, every turn used, every turn producing the full
    output allowance. Real trials cost far less - the tool schemas cache well -
    but a budget guard that assumes the good case is not a guard.
    """
    return max_turns * price.cost_usd(prompt_tokens_per_turn, 0, output_tokens_per_turn)


@dataclass
class SpendLedger:
    """The append-only record of what has been spent, and the cap on it."""

    path: Path = DEFAULT_LEDGER
    budget_usd: float = DEFAULT_BUDGET_USD

    def __post_init__(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        if not self.path.exists():
            with self.path.open("w", newline="", encoding="utf-8") as fh:
                csv.writer(fh).writerow(LEDGER_HEADER)

    def spent_by(self, key: str) -> float:
        """Everything this key has ever been charged, per this ledger."""
        if not self.path.exists():
            return 0.0
        total = 0.0
        with self.path.open(newline="", encoding="utf-8") as fh:
            for row in csv.DictReader(fh):
                if row.get("key_id") == key:
                    try:
                        total += float(row.get("cost_usd") or 0.0)
                    except ValueError:
                        continue
        return total

    def remaining(self, key: str) -> float:
        return self.budget_usd - self.spent_by(key)

    def check_before_trial(self, key: str, projected_usd: float) -> float:
        """Refuse the trial if it could cross the cap. Returns what is left."""
        left = self.remaining(key)
        if projected_usd > left:
            raise BudgetExceeded(
                f"key {key} has ${left:.2f} of its ${self.budget_usd:.2f} budget left, and this trial "
                f"could cost up to ${projected_usd:.2f}. Not starting it. Nothing has been spent."
            )
        return left

    def record(
        self,
        *,
        key: str,
        provider: str,
        model: str,
        resolved_model: str,
        mission_id: str,
        trial: int,
        input_tokens: int,
        cached_input_tokens: int,
        output_tokens: int,
        reasoning_tokens: int,
        cost_usd: float,
        run_dir: str,
        note: str = "",
    ) -> float:
        """Append one trial's spend. Returns the key's new cumulative total."""
        cumulative = self.spent_by(key) + cost_usd
        with self.path.open("a", newline="", encoding="utf-8") as fh:
            csv.writer(fh).writerow(
                [
                    datetime.fromtimestamp(time.time(), timezone.utc).isoformat(),
                    provider,
                    key,
                    model,
                    resolved_model,
                    mission_id,
                    trial,
                    input_tokens,
                    cached_input_tokens,
                    output_tokens,
                    reasoning_tokens,
                    f"{cost_usd:.6f}",
                    f"{cumulative:.6f}",
                    f"{self.budget_usd:.2f}",
                    f"{self.budget_usd - cumulative:.6f}",
                    run_dir,
                    note,
                ]
            )
        return cumulative
