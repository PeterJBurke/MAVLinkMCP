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

**Cached tokens are three prices, not two.** A provider that caches prompts
bills three different rates for what the harness calls "input": tokens read
back from the cache (cheap), tokens *written* into it (a **premium** on some
providers), and everything else (the base rate). Collapsing the middle one into
the base rate under-reads the meter, which is not a rounding error: it is the
difference between a ledger that tracks the balance and one that drifts. This
module therefore prices cache writes as their own quantity - see
:attr:`Price.cache_write` and :data:`CACHE_WRITE_MULTIPLIER`.
"""

from __future__ import annotations

import csv
import hashlib
import json
import re
import time
from dataclasses import dataclass, replace
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
    # --- appended 2026-08-09 with the cache-write pricing fix ---------------
    # APPENDED, never inserted. A new column in the middle would re-map every
    # historical row's fields under csv.DictReader (row[9] would arrive as
    # cache_write_tokens instead of output_tokens), silently corrupting the
    # record. Appending leaves every old row reading exactly as it always did,
    # with the new fields empty - which is the truth: they were not measured.
    # See docs/benchmark_runs/spend_ledger_corrections.md.
    #
    # Tokens WRITTEN into the provider's prompt cache, billed at the provider's
    # cache-write rate (1.25x base input on Anthropic).
    "cache_write_tokens",
    # Reasoning tokens the provider left OUT of output_tokens (see
    # providers.ModelTurn). Priced as output; zero where they are counted
    # normally.
    "uncounted_reasoning_tokens",
]

#: The header this file had before the cache-write columns were appended. Kept
#: so an existing ledger can be widened without touching a single data row.
LEGACY_LEDGER_HEADERS: tuple[list[str], ...] = (
    [c for c in LEDGER_HEADER if c not in ("cache_write_tokens", "uncounted_reasoning_tokens")],
)

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


#: Cache-**write** premium, as a multiple of the base input rate, for model
#: families whose published pricing has been checked by hand. Used only as a
#: fallback when the price table carries no explicit ``cache_write`` for the
#: model (an old table, or a hand-written ``--price-input/--price-output``).
#:
#: ``claude``: Anthropic charges **1.25x** the base input rate to write a
#: 5-minute ``ephemeral`` cache entry, which is exactly what this harness
#: requests (``providers.AnthropicSession._body``). The 1-hour TTL is 2x and is
#: deliberately NOT requested, so 1.25 is the only multiplier that can apply.
#:
#: **Nothing else is listed on purpose.** OpenAI, Google, xAI, Mistral and
#: DeepSeek do not report a cache-write token count at all on their
#: OpenAI-shaped ``usage`` object (only ``prompt_tokens_details.cached_tokens``,
#: a *discounted read*), so no cache-write quantity ever reaches this table for
#: them and the multiplier would be dead code. OpenRouter passes the upstream's
#: numbers through and its behaviour varies by upstream host, so a blanket
#: multiplier there would be a guess. Where a provider's behaviour is unknown,
#: :meth:`Price.cost_usd` falls back to the **base input rate** - i.e. it
#: assumes no premium - rather than inventing one.
CACHE_WRITE_MULTIPLIER: dict[str, float] = {"claude": 1.25}


@dataclass(frozen=True)
class Price:
    """USD per million tokens."""

    input: float
    output: float
    cached_input: float = 0.0
    #: USD per million tokens **written into** the provider's prompt cache.
    #: ``0.0`` means "not published for this model", and is not the same as
    #: "free": :meth:`cost_usd` then charges them at the base input rate, which
    #: is what every provider that publishes no premium does.
    cache_write: float = 0.0

    def cost_usd(
        self,
        input_tokens: int,
        cached_input_tokens: int,
        output_tokens: int,
        cache_write_tokens: int = 0,
        uncounted_reasoning_tokens: int = 0,
    ) -> float:
        """What these tokens cost, in dollars.

        ``input_tokens`` is *everything* that went in, cache reads and cache
        writes included, which is how the rest of the harness defines it. The
        three input rates are then applied to the three disjoint parts:

        ==================  ==========================================
        cache **reads**     ``cached_input`` (a discount, ~0.1x)
        cache **writes**    ``cache_write``, or the base rate if the
                            provider publishes none (see
                            :data:`CACHE_WRITE_MULTIPLIER`)
        everything else     ``input``
        ==================  ==========================================

        ``uncounted_reasoning_tokens`` are reasoning tokens the provider left
        out of ``output_tokens``; they are billed at the output rate, because
        pricing them at zero is how they were lost in the first place.
        """
        fresh = max(input_tokens - cached_input_tokens - cache_write_tokens, 0)
        cached_rate = self.cached_input if self.cached_input else self.input
        write_rate = self.cache_write if self.cache_write else self.input
        billable_output = output_tokens + max(uncounted_reasoning_tokens, 0)
        return (
            fresh * self.input
            + cached_input_tokens * cached_rate
            + cache_write_tokens * write_rate
            + billable_output * self.output
        ) / 1_000_000


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
            cache_write=float(row.get("cache_write") or 0.0),
        )
        for name, row in (blob.get("prices") or {}).items()
    }
    return prices, blob.get("fetched_utc", "")


def with_cache_write_rate(price: Price, model: str) -> Price:
    """Fill in a missing cache-write rate from :data:`CACHE_WRITE_MULTIPLIER`.

    A price table refreshed before cache-write rates were recorded - or a rate
    typed on the command line - carries no ``cache_write``, and a cache write
    would then be billed at the base rate. For Anthropic that silently
    under-reads the meter by 25% of every written token, which is the defect
    this function exists to close. A model whose family is not listed is
    returned untouched: no premium is invented for a provider we have not
    checked.
    """
    if price.cache_write:
        return price
    bare = model.split("/")[-1].lower()
    for family, multiplier in CACHE_WRITE_MULTIPLIER.items():
        if bare.startswith(family):
            return replace(price, cache_write=price.input * multiplier)
    return price


def _name_variants(model: str) -> list[str]:
    """Other names the same model is listed under.

    Only *identity* mappings, never a substitution for a similar model. A
    vendor's dated snapshot is the same product as its undated name and is
    billed at the same rate, and vendors write the version differently in the
    two places (``claude-haiku-4-5-20251001`` against ``claude-haiku-4.5``).
    Resolving that is bookkeeping. Falling back to a *different* model's price
    would be inventing a number, which this module refuses to do.
    """
    bare = model.split("/")[-1]
    variants = [model, bare, model.lower(), bare.lower()]
    undated = re.sub(r"-20\d{6}$", "", bare)
    if undated != bare:
        variants += [undated, undated.lower()]
    for name in list(variants):
        dotted = re.sub(r"(\d)-(\d)", r"\1.\2", name)
        if dotted != name:
            variants.append(dotted)
    return variants


def price_for(prices: dict[str, Price], model: str) -> Price:
    """The price for a model, trying the names vendors list it under.

    Any missing cache-write rate is filled in from
    :data:`CACHE_WRITE_MULTIPLIER` before the price is handed out, so a stale
    price table cannot quietly reintroduce the under-billing.
    """
    bare = model.split("/")[-1]
    for candidate in _name_variants(model):
        if candidate in prices:
            return with_cache_write_rate(prices[candidate], model)
    for name, price in prices.items():
        if name.split("/")[-1].lower() == bare.lower():
            return with_cache_write_rate(price, model)
    raise PriceUnknown(
        f"no price on file for '{model}'. Refusing to run: the ${DEFAULT_BUDGET_USD:.0f} per-key cap "
        f"cannot be enforced without one. Refresh the table with "
        f"`uv run python scripts/update_model_prices.py`, or pass --price-input/--price-output."
    )


def project_trial_cost_usd(
    price: Price, max_turns: int, prompt_tokens_per_turn: int, output_tokens_per_turn: int
) -> float:
    """A deliberate over-estimate of what one trial could cost.

    The **dearest** input rate the model has, every turn used, every turn
    producing the full output allowance. Real trials cost far less - the tool
    schemas cache well - but a budget guard that assumes the good case is not a
    guard. On a provider that charges a cache-write premium the dearest rate is
    the write rate, not the base rate, so the projection is made against that;
    before this was fixed the guard's "worst case" was cheaper than a real
    Anthropic turn.
    """
    if price.cache_write > price.input:
        return max_turns * price.cost_usd(
            prompt_tokens_per_turn, 0, output_tokens_per_turn, cache_write_tokens=prompt_tokens_per_turn
        )
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
            return
        self._widen_header()

    def _widen_header(self) -> None:
        """Name the columns added since this file was created. Data untouched.

        The ledger is a research record and its rows are never rewritten. When
        columns are added, the *header line alone* is replaced so the new names
        exist; every historical row keeps its exact bytes and simply has no
        value in the new columns, which is the truth - the quantity was not
        measured when they were written. ``csv.DictReader`` reads such a row as
        ``None`` for the missing fields, and :meth:`spent_by` ignores them.
        """
        with self.path.open(newline="", encoding="utf-8") as fh:
            lines = fh.read().splitlines(keepends=True)
        if not lines:
            return
        header = next(csv.reader([lines[0]]), [])
        if header == LEDGER_HEADER or header not in [list(h) for h in LEGACY_LEDGER_HEADERS]:
            return
        with self.path.open("w", newline="", encoding="utf-8") as fh:
            csv.writer(fh).writerow(LEDGER_HEADER)
            fh.writelines(lines[1:])

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
        cache_write_tokens: int = 0,
        uncounted_reasoning_tokens: int = 0,
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
                    cache_write_tokens,
                    uncounted_reasoning_tokens,
                ]
            )
        return cumulative
