"""The money path: what the ledger records, and what the guard believes.

A companion to :mod:`tests.test_spend_cost_model`, which covers the *rates*.
This file covers the paths money takes *around* those rates - what gets written
to the ledger, what the correction file can and cannot see, and what the budget
guard projects - because every defect this project has actually paid for lived
there rather than in the arithmetic:

* a meter that under-read by 15% emptied a key mid-campaign and voided 80
  trials (fixed 2026-08-09);
* a guard reading the uncorrected total believed $56 of headroom it did not
  have (fixed the same day);
* and the two below, found by audit on 2026-08-10.

Everything here is offline: no provider is called and no money is spent. The
real ledger, ``docs/benchmark_runs/spend_ledger.csv``, is never opened for
writing by any test - each one builds its own under ``tmp_path``.
"""

from __future__ import annotations

import csv
import importlib.util
import sys
import threading
from pathlib import Path

import pytest

from droneserver.llm import runner
from droneserver.llm.agent import Limits
from droneserver.llm.spend import Price, SpendLedger, project_trial_cost_usd
from tests.test_llm_capture_integration import (  # noqa: F401  (fixtures reused deliberately)
    AUDIT_ROWS,
    FakeModel,
    FakePoller,
    FakeSession,
    _fake_agent_run,
)

REPO = Path(__file__).resolve().parent.parent


def _load_script(name: str):
    """Import a ``scripts/*.py`` CLI as a module (they are not a package)."""
    path = REPO / "scripts" / name
    spec = importlib.util.spec_from_file_location(name.removesuffix(".py"), path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


# --------------------------------------------------------------------------
# 1. "not measured" and "measured as zero" are different facts
# --------------------------------------------------------------------------


def _correction_rows(tmp_path: Path, ledger_path: Path) -> list[dict]:
    correction = _load_script("ledger_cache_write_correction.py")
    out = tmp_path / "corrections.csv"
    argv = [
        "ledger_cache_write_correction.py",
        "--ledger",
        str(ledger_path),
        "--prices",
        str(REPO / "docs" / "model_prices.json"),
        "--out",
        str(out),
    ]
    old, sys.argv = sys.argv, argv
    try:
        correction.main()
    finally:
        sys.argv = old
    with out.open(newline="", encoding="utf-8") as fh:
        return list(csv.DictReader(fh))


def test_an_unmeasured_cache_write_split_is_left_blank_not_written_as_zero(tmp_path):
    """A literal ``0`` claims the split was measured and was nil.

    ``scripts/ledger_cache_write_correction.py`` skips every row whose
    ``cache_write_tokens`` is non-empty, on the reasoning that such a row
    records the split and is therefore exact. So writing ``0`` for a row whose
    split was never measured - which is what backfilling a pre-fix run does -
    silently exempts that row from the correction, and the budget guard goes
    back to believing an Anthropic total it has itself documented as ~13% low.
    That is defect #1 and defect #2 of 2026-08-09, reintroduced through the
    back door.
    """
    ledger = SpendLedger(path=tmp_path / "spend_ledger.csv")
    ledger.record(
        key="anthropic:deadbeef",
        provider="anthropic",
        model="claude-opus-5",
        resolved_model="claude-opus-5",
        mission_id="T1",
        trial=1,
        input_tokens=1_000_000,
        cached_input_tokens=0,
        output_tokens=0,
        reasoning_tokens=0,
        cost_usd=5.0,
        run_dir="llm_runs/old",
        note="backfilled (PASS)",
        cache_write_tokens=None,  # the split was never measured
        uncounted_reasoning_tokens=None,
    )
    with (tmp_path / "spend_ledger.csv").open(newline="", encoding="utf-8") as fh:
        row = next(iter(csv.DictReader(fh)))
    assert row["cache_write_tokens"] == "", "a blank says 'not measured'; 0 would say 'measured, none'"

    rows = _correction_rows(tmp_path, tmp_path / "spend_ledger.csv")
    assert rows, "the row must still be visible to the correction"
    # 1,000,000 unattributed input tokens x (6.25 - 5.00)/M = $1.25.
    assert float(rows[0]["correction_upper_usd"]) == pytest.approx(1.25)


def test_backfilling_a_pre_fix_run_does_not_exempt_it_from_the_correction(tmp_path):
    """End to end through the real script, because that is where the 0 came from."""
    runs = tmp_path / "llm_runs"
    (runs / "20260808T000000Z_opus").mkdir(parents=True)
    with (runs / "20260808T000000Z_opus" / "missions.csv").open("w", newline="", encoding="utf-8") as fh:
        writer = csv.writer(fh)
        # A pre-fix missions.csv: no cache_write_tokens column at all.
        writer.writerow(
            [
                "mission_id",
                "trial",
                "verdict",
                "input_tokens",
                "cached_input_tokens",
                "output_tokens",
                "reasoning_tokens",
            ]
        )
        writer.writerow(["T1", "1", "PASS", "1000000", "0", "0", "0"])

    ledger_path = tmp_path / "spend_ledger.csv"
    backfill = _load_script("backfill_ledger.py")
    argv = [
        "backfill_ledger.py",
        "--runs",
        str(runs),
        "--model",
        "claude-opus-5",
        "--provider",
        "anthropic",
        "--ledger",
        str(ledger_path),
        "--prices",
        str(REPO / "docs" / "model_prices.json"),
    ]
    old, sys.argv = sys.argv, argv
    try:
        SpendLedger(path=ledger_path)  # create the header the script appends to
        backfill.main()
    finally:
        sys.argv = old

    with ledger_path.open(newline="", encoding="utf-8") as fh:
        row = next(iter(csv.DictReader(fh)))
    assert row["cache_write_tokens"] == "", "backfilled rows never measured the split"

    rows = _correction_rows(tmp_path, ledger_path)
    assert rows and float(rows[0]["correction_upper_usd"]) > 0, (
        "a backfilled Anthropic row is under-billed by exactly the amount the correction exists to bound"
    )


def test_a_row_that_really_did_measure_the_split_is_still_exempt(tmp_path):
    """The other direction: a measured 0 must NOT be corrected, or we double-charge."""
    ledger = SpendLedger(path=tmp_path / "spend_ledger.csv")
    ledger.record(
        key="anthropic:deadbeef",
        provider="anthropic",
        model="claude-opus-5",
        resolved_model="claude-opus-5",
        mission_id="T1",
        trial=1,
        input_tokens=1_000_000,
        cached_input_tokens=0,
        output_tokens=0,
        reasoning_tokens=0,
        cost_usd=5.0,
        run_dir="llm_runs/new",
        note="PASS",
        cache_write_tokens=0,  # measured, and there were none
    )
    assert _correction_rows(tmp_path, tmp_path / "spend_ledger.csv") == []


# --------------------------------------------------------------------------
# 2. the projection against the ceiling the harness actually enforces
# --------------------------------------------------------------------------


def test_the_projection_respects_the_per_trial_ceiling_the_harness_enforces():
    """$31.50 of headroom demanded for a trial that is stopped at $5.

    ``project_trial_cost_usd`` assumed every one of ``max_turns`` turns pays the
    dearest input rate on the full prompt, which for claude-opus-5 at the
    shipped defaults (90 turns, 40 000 prompt tokens, 4 000 output tokens) is

        90 x (40 000 x $6.25/M + 4 000 x $25/M) = 90 x $0.35 = $31.50

    against a real trial cost near $0.78. But the harness does not permit a
    $31.50 trial: ``Limits.max_cost_usd`` (``--max-trial-cost-usd``, $5 by
    default) is checked before every turn and stops the trial. So the guard was
    demanding 6x the largest trial the harness can produce, which strands the
    last $31.50 of every key and BUDGET-stops a campaign arm with money left.
    """
    price = Price(input=5.0, output=25.0, cached_input=0.5, cache_write=6.25)
    uncapped = project_trial_cost_usd(price, 90, prompt_tokens_per_turn=40_000, output_tokens_per_turn=4_000)
    assert uncapped == pytest.approx(31.50), "the historical figure, reproduced"

    capped = project_trial_cost_usd(
        price, 90, prompt_tokens_per_turn=40_000, output_tokens_per_turn=4_000, ceiling_usd=5.0
    )
    # The ceiling is checked BETWEEN turns, so one more worst-case turn can
    # still be started after it is crossed. That turn, and no more.
    assert capped == pytest.approx(5.0 + 0.35)
    assert capped < uncapped


def test_the_ceiling_never_makes_the_projection_larger():
    """A ceiling above the turn-by-turn worst case must not inflate it."""
    price = Price(input=1.0, output=5.0, cached_input=0.1, cache_write=1.25)
    uncapped = project_trial_cost_usd(price, 4, prompt_tokens_per_turn=1_000, output_tokens_per_turn=100)
    capped = project_trial_cost_usd(price, 4, prompt_tokens_per_turn=1_000, output_tokens_per_turn=100, ceiling_usd=999)
    assert capped == pytest.approx(uncapped)


def test_no_ceiling_leaves_the_projection_exactly_as_it_was():
    price = Price(input=5.0, output=25.0, cached_input=0.5, cache_write=6.25)
    assert project_trial_cost_usd(price, 90, 40_000, 4_000, ceiling_usd=None) == pytest.approx(31.50)


def test_the_prompt_estimate_is_above_every_turn_ever_recorded():
    """It is called an over-estimate, so it has to actually be one.

    At 40,000 it was not: 890 of the project's 4,285 recorded turns were larger,
    the biggest by 2.26x. That matters now that the projection's tail allowance
    is one turn at this size - the allowance has to cover the turn that was in
    flight when the cost ceiling was crossed.
    """
    assert runner._prompt_token_estimate({}) > runner.LARGEST_RECORDED_PROMPT_TOKENS
    assert runner._prompt_token_estimate({"prompt_token_estimate": 12_345}) == 12_345


# --------------------------------------------------------------------------
# 3. the corrections file must never be able to stop a run
# --------------------------------------------------------------------------


def test_a_corrections_file_that_is_not_even_text_contributes_zero(tmp_path):
    """Documented contract: 'a correction that cannot be read must not stop a run'.

    Only :class:`OSError` was caught, so a file that is not valid UTF-8 - a
    truncated write, a file from another tool, anything - raised
    ``UnicodeDecodeError`` out of the budget guard and killed the campaign at
    the next trial.
    """
    ledger = SpendLedger(path=tmp_path / "spend_ledger.csv")
    (tmp_path / "spend_ledger_corrections.csv").write_bytes(b"key_id,correction_upper_usd\n\xff\xfe\x00garbage\n")
    assert ledger.corrections_for("anthropic:deadbeef") == 0.0
    assert ledger.spent_by("anthropic:deadbeef") == 0.0


def test_a_corrections_file_the_csv_module_itself_refuses_contributes_zero(tmp_path, monkeypatch):
    """``csv.Error`` is not an ``OSError`` either, and was equally fatal."""
    ledger = SpendLedger(path=tmp_path / "spend_ledger.csv")
    (tmp_path / "spend_ledger_corrections.csv").write_text(
        "key_id,correction_upper_usd\nanthropic:deadbeef,6.70\n", encoding="utf-8"
    )
    limit = csv.field_size_limit()
    csv.field_size_limit(4)  # any field longer than this now raises csv.Error
    try:
        assert ledger.corrections_for("anthropic:deadbeef") == 0.0
    finally:
        csv.field_size_limit(limit)


# --------------------------------------------------------------------------
# 4. a retried trial still charges the attempt it threw away
# --------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _fake_flight(monkeypatch):
    """The fakes from the capture test, so a 'flight' costs nothing real."""
    monkeypatch.setenv("OPENAI_API_KEY", "test-key-not-a-real-key")
    monkeypatch.setattr(runner, "LiveMCPSession", FakeSession)
    monkeypatch.setattr(runner, "McpTelemetryPoller", FakePoller)
    monkeypatch.setattr(runner, "open_session", lambda *a, **k: FakeModel())
    monkeypatch.setattr(runner, "_read_audit", lambda *a, **k: list(AUDIT_ROWS))

    async def _run_agent(**kwargs):
        return _fake_agent_run()

    monkeypatch.setattr(runner, "run_agent", _run_agent)


async def test_a_link_retry_still_charges_the_attempt_it_threw_away(tmp_path, monkeypatch):
    """The first attempt called the model. The provider billed for it.

    On a link failure the harness restarts the drone server and re-flies the
    trial, then records only the *second* attempt's spend. The first attempt's
    tokens were bought and paid for - a full mission's worth - and vanished
    from the ledger, making the guard optimistic by exactly the amount that
    emptied a key in the first place. The N=5 campaign runs with
    ``--link-recovery-command``, so this path is live.
    """
    seen = {"n": 0}

    def _link_errors(_calls):
        seen["n"] += 1
        return 99 if seen["n"] == 1 else 0

    async def _recover(config, harness, log):
        return True

    monkeypatch.setattr(runner, "_link_errors", _link_errors)
    monkeypatch.setattr(runner, "_recover_link", _recover)

    ledger = SpendLedger(path=tmp_path / "spend_ledger.csv")
    config = runner.SuiteConfig(
        url="http://127.0.0.1:8090/sse",
        api_key="k",
        model_spec="gpt-5.2",
        missions=["T1"],
        trials=1,
        out_dir=tmp_path / "run1",
        limits=Limits(max_cost_usd=5.0),
        price=Price(input=5.0, output=25.0),
        ledger=ledger,
        key_id="openai:deadbeef",
        provider_name="openai",
        link_recovery_command="true",
        link_retries=1,
    )
    results = await runner.run_llm_suite(config, log=lambda *a: None)

    assert len(results) == 1, "the run still reports one trial"
    with (tmp_path / "spend_ledger.csv").open(newline="", encoding="utf-8") as fh:
        rows = list(csv.DictReader(fh))
    assert len(rows) == 2, "both attempts were paid for, so both must be charged"
    assert rows[0]["note"] == "LINK", "the abandoned attempt is marked as what it was"
    per_attempt = (100 * 5.0 + 20 * 25.0) / 1_000_000
    assert sum(float(r["cost_usd"]) for r in rows) == pytest.approx(2 * per_attempt)
    assert ledger.spent_by("openai:deadbeef") == pytest.approx(2 * per_attempt)


# --------------------------------------------------------------------------
# 5. the ledger writer under two writers
# --------------------------------------------------------------------------


def test_the_cumulative_column_survives_two_writers(tmp_path):
    """``cumulative_usd_for_key`` is documented as the running total of this
    file's own rows - the property that lets the ledger be checked against
    itself. Computing it outside the append made it a read-modify-write: with
    four concurrent writers, 24 of 120 rows came out sharing a cumulative with
    another row and the column stopped being a running total at all. The rows
    never tore (a short O_APPEND write is atomic); only the arithmetic did,
    which is the kind of wrong that is noticed after it is published.
    """
    path = tmp_path / "spend_ledger.csv"
    SpendLedger(path=path)

    def writer(n: int) -> None:
        ledger = SpendLedger(path=path)
        for i in range(25):
            ledger.record(
                key="openai:deadbeef",
                provider="openai",
                model="gpt-5.2",
                resolved_model="gpt-5.2",
                mission_id=f"T{n}",
                trial=i,
                input_tokens=1,
                cached_input_tokens=0,
                output_tokens=1,
                reasoning_tokens=0,
                cost_usd=1.0,
                run_dir="llm_runs/x",
            )

    threads = [threading.Thread(target=writer, args=(n,)) for n in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    with path.open(newline="", encoding="utf-8") as fh:
        rows = list(csv.DictReader(fh))
    assert len(rows) == 100, "no row may be lost or torn"
    cumulative = [float(r["cumulative_usd_for_key"]) for r in rows]
    assert cumulative == sorted(cumulative), "the column must be a running total"
    assert len(set(cumulative)) == len(cumulative), "no two rows may claim the same running total"
    assert cumulative[-1] == pytest.approx(sum(float(r["cost_usd"]) for r in rows))
