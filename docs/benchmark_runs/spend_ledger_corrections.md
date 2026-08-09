# Correction to the spend ledger: cache-write tokens billed at the wrong rate

**Status:** the defect is fixed in the harness as of 2026-08-09. This note
bounds what it cost while it was live. It corrects nothing in
`spend_ledger.csv` itself — see "Why the ledger was not edited" below.

## What was wrong

A provider that caches prompts bills three different rates for what the harness
calls "input": tokens read back from the cache (cheap), tokens **written into**
it, and everything else (the base rate). The harness only ever modelled two.

Anthropic's Messages API reports the three separately
(`input_tokens`, `cache_read_input_tokens`, `cache_creation_input_tokens`), and
`llm/providers.py` correctly added all three together into the harness's
`input_tokens`. But `llm/spend.py` then computed

```
fresh = input_tokens - cached_input_tokens
```

and priced `fresh` at the **base input rate**. `fresh` therefore silently
included every cache-**write** token — and Anthropic charges **1.25×** the base
input rate to write a 5-minute `ephemeral` cache entry, which is exactly what
this harness requests (`AnthropicSession._body`, cache breakpoints on the tool
list and the system prompt).

So every written token was billed at 0.8 of what it actually cost. The drone
server publishes 98 tool schemas — about 22,000 tokens of cacheable prefix on
every trial — so writes are not a rounding error on this workload.

## What it cost

Recomputed by `scripts/ledger_cache_write_correction.py`, which writes
`spend_ledger_corrections.csv`:

| Key | Model | Rows | Recorded | Correction (upper bound) | Corrected (upper bound) |
|---|---|---:|---:|---:|---:|
| `anthropic:467e65585f45` | `claude-opus-5` | 49 | $38.4376 | $5.8330 | $44.2706 |
| `anthropic:467e65585f45` | `claude-sonnet-5` | 48 | $3.0051 | $0.4944 | $3.4994 |
| `anthropic:467e65585f45` | `claude-haiku-4-5-20251001` | 49 | $2.1954 | $0.3731 | $2.5685 |
| **Total** | | **146** | **$43.6381** | **$6.7005** | **$50.3386** |

**No other provider is affected.** Every other provider in the matrix speaks
the OpenAI wire format, whose `usage` object reports only
`prompt_tokens_details.cached_tokens` — a discounted *read*. No cache-write
token count is reported and no cache-write premium is published, so writes are
billed at the base input rate, which is what the harness charged. Those rows
are exact.

## Why this is a bound, not a number

The rows written before the fix record `input_tokens` and
`cached_input_tokens` and nothing else. The split of
`input_tokens − cached_input_tokens` between *fresh* tokens and *cache-write*
tokens was never measured, and cannot be recovered from what was kept. So:

- **lower bound** — none of it was cache-write, and the recorded figure is right;
- **upper bound** — all of it was cache-write: add `0.25 × rate × (input − cached)`.

The upper bound is the one to believe. $43.638 recorded + $6.70 = **$50.34**
against a **$50.00** balance loaded on that key — and that key did in fact run
out of credit mid-campaign, at a point where the ledger still read $43.64 and
the budget guard therefore saw $56 of headroom it did not have. Agreement to
0.7% between an independent bound and an observed event is about as good as
post-hoc accounting gets, and it says that on this workload essentially all
non-cache-read input was cache-write traffic.

**Downstream effect:** the balance was exhausted with no warning, and the 80
trials that followed were recorded as VOID rather than as flights. Those trials
measured nothing and must not appear in any results table. (They are correctly
VOID rather than false passes — that part of the harness held.)

## Why the ledger was not edited

Three options were available: rewrite `cost_usd` in place, add a correction
column to the ledger, or write a separate corrections file. **The separate file
was chosen.**

- *Rewriting in place* was rejected outright. The ledger is the record of what
  the harness charged and when — the evidence that the defect existed and the
  audit trail for the budget guard's decisions. A rewritten `cost_usd` would
  destroy that and leave a file that agrees with itself and with nothing else.
- *A correction column in the ledger* was rejected because the correction is
  not a per-row number. It is a bound, it only exists for one provider, and
  filling a column with an upper bound would read like a measurement.
- *A separate file* keeps the two things apart: `spend_ledger.csv` says what
  was charged, `spend_ledger_corrections.csv` says what the charge should have
  been bounded by, and this note says why they differ.

Two columns *were* appended to the ledger header — `cache_write_tokens` and
`uncounted_reasoning_tokens` — so rows written from now on record the split.
They were **appended, never inserted**: a new column in the middle would
re-map every historical row's fields under `csv.DictReader`. Historical rows
have no value in them, and that blank is meaningful: the quantity was not
measured when they were written.

## A second, smaller gap in the same accounting (partly fixed)

For xAI and some OpenRouter rows the provider reports reasoning tokens
*outside* `completion_tokens`, and the harness priced them at zero. Proof they
are disjoint: 12 ledger rows carry `reasoning_tokens > output_tokens`, which is
impossible if reasoning is a subset of output.

Now handled by `providers.uncounted_reasoning`, on two conservative tests: the
provider is known to report them separately (xAI — proven by those rows, and a
provider does not vary this per request), or `reasoning > output` on that turn
(arithmetically disjoint). **Residual, disclosed:** an aggregator turn whose
upstream excludes reasoning but happens to report fewer reasoning than output
tokens is still priced low. Total exposure across the whole ledger to date is
bounded at about **$1.42** (Plan 12) — xAI ≈ $0.10, OpenRouter ≈ $1.32.
Historical rows are not corrected for it, for the same reason as above: the
inclusion flag was never recorded, so any per-row figure would be invented.
