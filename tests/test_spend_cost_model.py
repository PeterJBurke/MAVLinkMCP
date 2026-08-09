"""The cost meter must agree with the provider's bill.

These are regression tests for a defect that destroyed real work: cache-**write**
tokens were billed at the base input rate instead of the provider's write rate,
the meter read ~15% low on Anthropic, and a key ran out of credit mid-campaign
with the ledger still showing $56 of headroom. Eighty trials were lost.

Everything here is offline: no provider is called and no money is spent.
"""

from __future__ import annotations

import csv
from pathlib import Path

import pytest

from droneserver.llm.providers import (
    PROVIDERS,
    REASONING_REPORTED_SEPARATELY,
    AnthropicSession,
    ModelTurn,
    OpenAICompatibleSession,
    Route,
    uncounted_reasoning,
)
from droneserver.llm.spend import (
    CACHE_WRITE_MULTIPLIER,
    LEDGER_HEADER,
    LEGACY_LEDGER_HEADERS,
    Price,
    SpendLedger,
    load_prices,
    price_for,
    project_trial_cost_usd,
    with_cache_write_rate,
)

# --------------------------------------------------------------- Price.cost_usd


def test_cache_write_tokens_are_billed_at_the_write_rate_not_the_base_rate():
    """The defect itself: 1,000 written tokens must cost 1.25x, not 1.0x."""
    price = Price(input=5.0, output=25.0, cached_input=0.5, cache_write=6.25)
    # 1,000 tokens went in, all of them written into the cache.
    with_writes = price.cost_usd(1_000, 0, 0, cache_write_tokens=1_000)
    assert with_writes == pytest.approx(6.25 / 1_000)
    # What the old code charged for exactly the same request.
    assert price.cost_usd(1_000, 0, 0) == pytest.approx(5.0 / 1_000)
    assert with_writes > price.cost_usd(1_000, 0, 0)


def test_the_three_input_rates_are_applied_to_three_disjoint_parts():
    price = Price(input=5.0, output=25.0, cached_input=0.5, cache_write=6.25)
    # input_tokens is the TOTAL, as providers.ModelTurn defines it.
    cost = price.cost_usd(10_000, cached_input_tokens=6_000, output_tokens=100, cache_write_tokens=3_000)
    expected = (1_000 * 5.0 + 6_000 * 0.5 + 3_000 * 6.25 + 100 * 25.0) / 1_000_000
    assert cost == pytest.approx(expected)


def test_an_unpublished_cache_write_rate_falls_back_to_the_base_rate_not_to_zero():
    """No premium published is not the same as free."""
    price = Price(input=5.0, output=25.0, cached_input=0.5)  # cache_write unset
    assert price.cost_usd(1_000, 0, 0, cache_write_tokens=1_000) == pytest.approx(5.0 / 1_000)


def test_cached_and_written_tokens_are_never_double_counted_as_fresh():
    price = Price(input=5.0, output=25.0, cached_input=0.5, cache_write=6.25)
    # Everything that went in was either read from or written to the cache.
    cost = price.cost_usd(1_000, cached_input_tokens=600, output_tokens=0, cache_write_tokens=400)
    assert cost == pytest.approx((600 * 0.5 + 400 * 6.25) / 1_000_000)


def test_reasoning_tokens_reported_outside_output_are_billed_as_output():
    price = Price(input=1.0, output=10.0)
    assert price.cost_usd(0, 0, 100, uncounted_reasoning_tokens=900) == pytest.approx(1_000 * 10.0 / 1_000_000)


# ------------------------------------------------------- the price table lookup


#: The shipped price table, located from this file rather than from the working
#: directory, so the test means the same thing wherever pytest is invoked.
PRICE_FILE = Path(__file__).resolve().parent.parent / "docs" / "model_prices.json"


def test_anthropic_models_get_the_published_cache_write_premium_from_the_table():
    prices, _ = load_prices(PRICE_FILE)
    price = price_for(prices, "claude-opus-5")
    assert price.cache_write == pytest.approx(price.input * 1.25), (
        "Anthropic charges 1.25x base input to write a 5-minute ephemeral cache entry"
    )


def test_a_stale_price_table_cannot_reintroduce_the_under_billing():
    """A table with no cache_write column still bills Anthropic writes at 1.25x."""
    stale = {"claude-opus-5": Price(input=5.0, output=25.0, cached_input=0.5)}
    price = price_for(stale, "claude-opus-5")
    assert price.cache_write == pytest.approx(6.25)


def test_no_premium_is_invented_for_a_provider_whose_behaviour_is_unknown():
    stale = {"some-new-model": Price(input=5.0, output=25.0)}
    assert with_cache_write_rate(stale["some-new-model"], "some-new-model").cache_write == 0.0
    assert set(CACHE_WRITE_MULTIPLIER) == {"claude"}, (
        "adding a family here is a claim about that vendor's published pricing"
    )


def test_the_budget_projection_uses_the_dearest_input_rate():
    """A worst case that is cheaper than a real turn is not a worst case."""
    price = Price(input=5.0, output=25.0, cached_input=0.5, cache_write=6.25)
    projected = project_trial_cost_usd(price, max_turns=2, prompt_tokens_per_turn=1_000, output_tokens_per_turn=100)
    per_turn_all_written = (1_000 * 6.25 + 100 * 25.0) / 1_000_000
    assert projected == pytest.approx(2 * per_turn_all_written)
    assert projected > 2 * price.cost_usd(1_000, 0, 100)


# ------------------------------------------------ what the adapters report back


def _anthropic_session() -> AnthropicSession:
    route = Route(
        provider=PROVIDERS["anthropic"],
        wire_model="claude-opus-5",
        requested_model="claude-opus-5",
        routing="direct",
    )
    return AnthropicSession(route, "not-a-real-key")


@pytest.mark.asyncio
async def test_anthropic_turn_reports_cache_writes_separately():
    session = _anthropic_session()
    try:
        turn = session._parse(
            {
                "content": [{"type": "text", "text": "ok"}],
                "stop_reason": "end_turn",
                "model": "claude-opus-5",
                "usage": {
                    "input_tokens": 100,
                    "cache_read_input_tokens": 20_000,
                    "cache_creation_input_tokens": 22_000,
                    "output_tokens": 50,
                },
            },
            latency_ms=1.0,
            wait_ms=0.0,
            attempts=1,
        )
    finally:
        await session.aclose()

    assert turn.input_tokens == 42_100, "input_tokens stays the total that went in"
    assert turn.cached_input_tokens == 20_000
    assert turn.cache_write_tokens == 22_000

    price = Price(input=5.0, output=25.0, cached_input=0.5, cache_write=6.25)
    fixed = price.cost_usd(
        turn.input_tokens, turn.cached_input_tokens, turn.output_tokens, cache_write_tokens=turn.cache_write_tokens
    )
    old = price.cost_usd(turn.input_tokens, turn.cached_input_tokens, turn.output_tokens)
    assert fixed > old
    assert fixed - old == pytest.approx(22_000 * (6.25 - 5.0) / 1_000_000)


def test_openai_shaped_turns_report_no_cache_write_quantity():
    """The OpenAI wire format has no cache-write count; 0 is correct, not a stub."""
    assert ModelTurn(text="", tool_calls=[], finish_reason="", decision_latency_ms=0.0).cache_write_tokens == 0


def _openai_session(provider: str = "openrouter") -> OpenAICompatibleSession:
    route = Route(
        provider=PROVIDERS[provider],
        wire_model="anthropic/claude-opus-5",
        requested_model="claude-opus-5",
        routing="aggregator (no $ANTHROPIC_API_KEY)",
    )
    return OpenAICompatibleSession(route, "not-a-real-key")


@pytest.mark.asyncio
async def test_an_aggregator_that_does_pass_the_cache_write_count_through_is_believed():
    """Phase B routes Anthropic models through OpenRouter, whose upstream does
    charge the 1.25x premium. When the count comes through, use it."""
    session = _openai_session()
    try:
        turn = session._parse(
            {
                "choices": [{"message": {"content": "ok"}, "finish_reason": "stop"}],
                "usage": {
                    "prompt_tokens": 42_100,
                    "prompt_tokens_details": {"cached_tokens": 20_000},
                    "cache_creation_input_tokens": 22_000,
                    "completion_tokens": 50,
                },
            },
            latency_ms=1.0,
            wait_ms=0.0,
            attempts=1,
        )
    finally:
        await session.aclose()
    assert turn.cache_write_tokens == 22_000


@pytest.mark.asyncio
async def test_a_provider_that_reports_no_cache_write_count_yields_zero():
    session = _openai_session("openai")
    try:
        turn = session._parse(
            {
                "choices": [{"message": {"content": "ok"}, "finish_reason": "stop"}],
                "usage": {
                    "prompt_tokens": 1_000,
                    "prompt_tokens_details": {"cached_tokens": 900},
                    "completion_tokens": 50,
                },
            },
            latency_ms=1.0,
            wait_ms=0.0,
            attempts=1,
        )
    finally:
        await session.aclose()
    assert turn.cache_write_tokens == 0


# ------------------------------------------------- reasoning outside the output


@pytest.mark.parametrize(
    "provider,output,reasoning,expected",
    [
        # Arithmetically disjoint: reasoning cannot exceed output if it is part of it.
        ("openrouter", 100, 900, 900),
        # Known to report them separately, whatever the numbers say.
        ("xai", 900, 100, 100),
        # Included in output on these providers, and the numbers are consistent with that.
        ("openai", 900, 100, 0),
        ("google", 900, 100, 0),
        # Nothing to add.
        ("xai", 900, 0, 0),
    ],
)
def test_uncounted_reasoning(provider, output, reasoning, expected):
    assert uncounted_reasoning(provider, output, reasoning) == expected


def test_only_providers_proven_disjoint_are_listed():
    assert REASONING_REPORTED_SEPARATELY == frozenset({"xai"})


# ------------------------------------------------------------------- the ledger


def test_a_legacy_ledger_gains_the_new_columns_without_a_row_being_touched(tmp_path):
    path = tmp_path / "spend_ledger.csv"
    legacy_header = list(LEGACY_LEDGER_HEADERS[0])
    legacy_row = [
        "2026-08-08T00:00:00+00:00",
        "anthropic",
        "anthropic:deadbeef",
        "claude-opus-5",
        "claude-opus-5",
        "T1",
        "1",
        "42100",
        "20000",
        "50",
        "0",
        "0.100000",
        "0.100000",
        "100.00",
        "99.900000",
        "llm_runs/old",
        "PASS",
    ]
    with path.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.writer(fh)
        writer.writerow(legacy_header)
        writer.writerow(legacy_row)

    ledger = SpendLedger(path=path)

    with path.open(newline="", encoding="utf-8") as fh:
        rows = list(csv.reader(fh))
    assert rows[0] == LEDGER_HEADER, "the header names the new columns"
    assert rows[1] == legacy_row, "the historical row is byte-for-byte what it was"

    # The whole point of APPENDING rather than inserting: every old field must
    # still read back under its own name.
    with path.open(newline="", encoding="utf-8") as fh:
        record = next(iter(csv.DictReader(fh)))
    assert record["input_tokens"] == "42100"
    assert record["output_tokens"] == "50"
    assert record["cost_usd"] == "0.100000"
    assert record["note"] == "PASS"
    assert not record["cache_write_tokens"], "not measured then, and the blank says so"
    assert ledger.spent_by("anthropic:deadbeef") == pytest.approx(0.1)


def test_the_guard_counts_what_the_rows_cannot_show(tmp_path):
    """A cap enforced against a figure known to be low is not a cap.

    The pre-fix rows under-record Anthropic spend and are deliberately never
    rewritten, so the correction lives in a sibling file - and the guard has to
    read it, or it believes a number this project has itself documented as ~13%
    low on precisely the key that already overran its balance.
    """
    ledger = SpendLedger(path=tmp_path / "spend_ledger.csv")
    ledger.record(
        key="anthropic:deadbeef",
        provider="anthropic",
        model="claude-opus-5",
        resolved_model="claude-opus-5",
        mission_id="T1",
        trial=1,
        input_tokens=1_000,
        cached_input_tokens=0,
        output_tokens=0,
        reasoning_tokens=0,
        cost_usd=40.0,
        run_dir="llm_runs/old",
    )
    assert ledger.spent_by("anthropic:deadbeef") == pytest.approx(40.0)

    with (tmp_path / "spend_ledger_corrections.csv").open("w", newline="", encoding="utf-8") as fh:
        writer = csv.writer(fh)
        writer.writerow(["key_id", "provider", "model", "correction_upper_usd"])
        writer.writerow(["anthropic:deadbeef", "anthropic", "claude-opus-5", "6.700510"])
        writer.writerow(["openai:cafe", "openai", "gpt-5.2", "1.000000"])

    assert ledger.recorded_by("anthropic:deadbeef") == pytest.approx(40.0), "the file still says what it said"
    assert ledger.corrections_for("anthropic:deadbeef") == pytest.approx(6.70051)
    assert ledger.spent_by("anthropic:deadbeef") == pytest.approx(46.70051), "the guard sees the corrected total"
    assert ledger.remaining("anthropic:deadbeef") == pytest.approx(100.0 - 46.70051)
    # A key with no correction is untouched, and another key's correction is
    # never borrowed.
    assert ledger.spent_by("google:abc") == 0.0


def test_a_missing_or_unreadable_corrections_file_never_stops_a_run(tmp_path):
    ledger = SpendLedger(path=tmp_path / "spend_ledger.csv")
    assert ledger.corrections_for("anything") == 0.0
    (tmp_path / "spend_ledger_corrections.csv").write_text("this is not a csv we understand\n", encoding="utf-8")
    assert ledger.corrections_for("anything") == 0.0


def test_the_cumulative_column_stays_a_running_total_of_the_file_itself(tmp_path):
    """Otherwise the ledger stops being checkable against itself."""
    ledger = SpendLedger(path=tmp_path / "spend_ledger.csv")
    with (tmp_path / "spend_ledger_corrections.csv").open("w", newline="", encoding="utf-8") as fh:
        writer = csv.writer(fh)
        writer.writerow(["key_id", "correction_upper_usd"])
        writer.writerow(["anthropic:deadbeef", "6.700510"])
    cumulative = ledger.record(
        key="anthropic:deadbeef",
        provider="anthropic",
        model="claude-opus-5",
        resolved_model="claude-opus-5",
        mission_id="T1",
        trial=1,
        input_tokens=1_000,
        cached_input_tokens=0,
        output_tokens=0,
        reasoning_tokens=0,
        cost_usd=2.0,
        run_dir="llm_runs/new",
    )
    assert cumulative == pytest.approx(2.0), "the column sums this file's rows, not the correction"
    with (tmp_path / "spend_ledger.csv").open(newline="", encoding="utf-8") as fh:
        record = next(iter(csv.DictReader(fh)))
    assert record["cumulative_usd_for_key"] == "2.000000"
    assert ledger.spent_by("anthropic:deadbeef") == pytest.approx(8.70051), "but the guard still sees both"


def test_new_rows_record_the_token_split(tmp_path):
    ledger = SpendLedger(path=tmp_path / "ledger.csv")
    ledger.record(
        key="anthropic:deadbeef",
        provider="anthropic",
        model="claude-opus-5",
        resolved_model="claude-opus-5",
        mission_id="T1",
        trial=1,
        input_tokens=42_100,
        cached_input_tokens=20_000,
        cache_write_tokens=22_000,
        output_tokens=50,
        reasoning_tokens=0,
        uncounted_reasoning_tokens=0,
        cost_usd=0.2,
        run_dir="llm_runs/new",
    )
    with (tmp_path / "ledger.csv").open(newline="", encoding="utf-8") as fh:
        record = next(iter(csv.DictReader(fh)))
    assert record["cache_write_tokens"] == "22000"
    assert record["uncounted_reasoning_tokens"] == "0"
    assert record["input_tokens"] == "42100"
