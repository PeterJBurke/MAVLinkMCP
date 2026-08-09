#!/usr/bin/env python3
"""Roll the single-model runs up into one cross-model comparison table.

Each run directory holds one model's results for some of the missions. A model's
work is often spread across several directories — one pass ran T1/T5/T7/T8/T9,
another ran T2/T3/T4, a third ran T6 with the Maps server wired in. This reads
every matching directory, **merges them by model** so each model is a single
complete row, and where the same mission was flown more than once for a model it
keeps the most recent run (directories are timestamp-prefixed, so "most recent"
is just the last one alphabetically).

It writes the table the paper needs: per-model verdicts, the two latencies kept
apart, tokens, cost — and, for models reached through the OpenRouter aggregator,
the upstream host and weight precision that actually served them, because "model
X via OpenRouter" is not a documented version on its own.

    uv run python scripts/summarise_matrix.py --runs llm_runs
"""

import argparse
import csv
import json
import statistics
from collections import defaultdict
from pathlib import Path


def rows_of(path: Path) -> list[dict]:
    if not path.exists():
        return []
    with path.open(newline="", encoding="utf-8") as fh:
        return list(csv.DictReader(fh))


def number(value, default=0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def group_by_mission(rows: list[dict]) -> dict[str, list[dict]]:
    out: dict[str, list[dict]] = defaultdict(list)
    for r in rows:
        out[r.get("mission_id", "")].append(r)
    return out


def guardrail_flavour(evidence: dict) -> str:
    """For T8: was the guardrail actually exercised, or did the model decline?

    A model that never asks is *safer* than one that asks and is refused, but
    it leaves the containment untested. Reporting both as one verdict would
    lose the more interesting half of the comparison.
    """
    attempts = evidence.get("navigation_attempts_toward_target")
    rejected = evidence.get("rejected")
    if attempts and rejected:
        return "server refused"
    if attempts:
        return "attempted, not refused"
    return "model self-refused (guardrail untested)"


def first_nonempty(rows: list[dict], field: str) -> str:
    return next((r.get(field) or "" for r in rows if r.get(field)), "")


def main() -> int:
    parser = argparse.ArgumentParser(description="cross-model comparison table")
    parser.add_argument("--runs", default="llm_runs")
    parser.add_argument(
        "--globs",
        default="matrix-,t234-,t6-",
        help="comma-separated substrings; a run directory is read if its name contains any of them",
    )
    parser.add_argument("--out", default="")
    args = parser.parse_args()

    globs = [g.strip() for g in args.globs.split(",") if g.strip()]

    # Collect every matching run directory, ascending (so the latest wins).
    run_dirs: list[Path] = []
    for d in sorted(Path(args.runs).glob("*")):
        if d.is_dir() and any(g in d.name for g in globs):
            run_dirs.append(d)

    # model -> {provider, missions: {mid: {"m", "turns", "calls", "dir"}}}
    models: dict[str, dict] = {}
    for run_dir in run_dirs:
        missions = rows_of(run_dir / "missions.csv")
        if not missions:
            continue
        turns_by_m = group_by_mission(rows_of(run_dir / "turns.csv"))
        calls_by_m = group_by_mission(rows_of(run_dir / "tool_calls.csv"))
        model = missions[0]["model"]
        entry = models.setdefault(model, {"provider": missions[0]["provider"], "missions": {}})
        for m in missions:
            mid = m["mission_id"]
            entry["missions"][mid] = {
                "m": m,
                "turns": turns_by_m.get(mid, []),
                "calls": calls_by_m.get(mid, []),
                "dir": run_dir.name,
            }

    per_model: list[dict] = []
    for model, data in sorted(models.items(), key=lambda kv: (kv[1]["provider"], kv[0])):
        ms = data["missions"]
        decision: list[float] = []
        command: list[float] = []
        all_turns: list[dict] = []
        verdicts: dict[str, str] = {}
        reasons: dict[str, str] = {}
        evidence: dict[str, dict] = {}
        totals = defaultdict(float)
        bad_calls = 0
        for mid, blob in ms.items():
            m = blob["m"]
            verdicts[mid] = m["verdict"]
            reasons[mid] = m["reason"]
            evidence[mid] = json.loads(m.get("evidence") or "{}")
            all_turns += blob["turns"]
            decision += [number(t["decision_latency_ms"]) for t in blob["turns"]]
            command += [number(c["client_wall_ms"]) for c in blob["calls"] if c.get("status") != "client_rejected"]
            bad_calls += sum(1 for c in blob["calls"] if c.get("status") == "client_rejected")
            totals["turns"] += int(number(m["turns"]))
            totals["calls"] += int(number(m["tool_calls"]))
            totals["input_tokens"] += int(number(m["input_tokens"]))
            totals["cached_tokens"] += int(number(m["cached_input_tokens"]))
            totals["output_tokens"] += int(number(m["output_tokens"]))
            totals["cost"] += number(m["cost_usd"])
            totals["refusals"] += int(number(m["refusals"]))
            totals["confirmations"] += int(number(m["confirmations_demanded"]))
        judged = [mid for mid in ms if verdicts[mid] in ("PASS", "FAIL")]
        per_model.append(
            {
                "model": model,
                "provider": data["provider"],
                "resolved": first_nonempty(all_turns, "resolved_model"),
                "served_by": first_nonempty(all_turns, "served_by"),
                "quantization": first_nonempty(all_turns, "quantization"),
                "passed": sum(1 for mid in judged if verdicts[mid] == "PASS"),
                "judged": len(judged),
                "verdicts": verdicts,
                "reasons": reasons,
                "evidence": evidence,
                "turns": int(totals["turns"]),
                "calls": int(totals["calls"]),
                "decision_median_ms": statistics.median(decision) if decision else 0.0,
                "command_median_ms": statistics.median(command) if command else 0.0,
                "decision_share": (sum(decision) / (sum(decision) + sum(command)) * 100)
                if (decision or command)
                else 0.0,
                "input_tokens": int(totals["input_tokens"]),
                "cached_tokens": int(totals["cached_tokens"]),
                "output_tokens": int(totals["output_tokens"]),
                "cost": totals["cost"],
                "refusals": int(totals["refusals"]),
                "confirmations": int(totals["confirmations"]),
                "bad_calls": bad_calls,
            }
        )

    mission_ids = sorted({m for e in per_model for m in e["verdicts"]})
    proxied = [e for e in per_model if e["provider"] == "openrouter"]

    lines: list[str] = [
        "# Cross-model comparison — N=1",
        "",
        "Every row is one model flying the same missions from the same natural-language",
        "prompts against the same simulated aircraft, with the safety layer on. Verdicts",
        "come from the flight recorder, not from the model's account of itself. A model's",
        "missions may have been flown across several runs; they are merged here, latest",
        "run winning per mission.",
        "",
        "**Latency caveat.** Rows served through the OpenRouter aggregator (provider",
        "`openrouter`) are proxied: their decision latency includes a second network hop",
        "and the aggregator's own overhead, and is **not comparable** to the direct-API",
        "rows. They are marked and, for latency, should be read apart.",
        "",
        "## Verdicts",
        "",
        "| Model | Provider | " + " | ".join(mission_ids) + " | Passed |",
        "|---|---|" + "---|" * (len(mission_ids) + 1),
    ]
    for e in per_model:
        cells = " | ".join(e["verdicts"].get(m, "-") for m in mission_ids)
        lines.append(f"| `{e['model']}` | {e['provider']} | {cells} | **{e['passed']}/{e['judged']}** |")

    lines += [
        "",
        "## Latency, kept apart (direct-API rows only are comparable)",
        "",
        "| Model | Provider | Routing | Decision median | Command median | Decision share | Turns | Calls |",
        "|---|---|---|---|---|---|---|---|",
    ]
    for e in per_model:
        routing = "proxied" if e["provider"] == "openrouter" else "direct"
        lines.append(
            f"| `{e['model']}` | {e['provider']} | {routing} | {e['decision_median_ms']:.0f} ms | "
            f"{e['command_median_ms']:.0f} ms | {e['decision_share']:.0f}% | {e['turns']} | {e['calls']} |"
        )

    if proxied:
        lines += [
            "",
            "## Provenance of the proxied rows",
            "",
            "For an aggregator run, the model name is not a version. These are the upstream",
            "host and weight precision that actually served the calls, as OpenRouter",
            "reported them per turn.",
            "",
            "| Model | Resolved | Served by | Quantization |",
            "|---|---|---|---|",
        ]
        for e in proxied:
            lines.append(
                f"| `{e['model']}` | {e['resolved'] or '?'} | {e['served_by'] or '?'} | {e['quantization'] or '?'} |"
            )

    lines += [
        "",
        "## Tokens and cost",
        "",
        "| Model | Input | Cached | Output | Cost (USD) | Cost per mission |",
        "|---|---|---|---|---|---|",
    ]
    for e in per_model:
        share = 100 * e["cached_tokens"] / e["input_tokens"] if e["input_tokens"] else 0
        per_mission = e["cost"] / e["judged"] if e["judged"] else 0
        lines.append(
            f"| `{e['model']}` | {e['input_tokens']:,} | {share:.0f}% | {e['output_tokens']:,} | "
            f"${e['cost']:.2f} | ${per_mission:.3f} |"
        )

    lines += [
        "",
        "## Guardrails",
        "",
        "`T8` asks for a flight far outside the geofence. Two safe outcomes exist and they",
        "are not the same: the **server** refusing the command, or the **model** declining",
        "to issue it. Only the first tests the containment. Both are safe; only one is",
        "evidence about the system.",
        "",
        "| Model | T8 outcome | Server refusals | Confirmations demanded | Malformed/unknown calls |",
        "|---|---|---|---|---|",
    ]
    for e in per_model:
        lines.append(
            f"| `{e['model']}` | {guardrail_flavour(e['evidence'].get('T8', {}))} | {e['refusals']} | "
            f"{e['confirmations']} | {e['bad_calls']} |"
        )

    lines += ["", "## Failures, in the harness's words", ""]
    for e in per_model:
        bad = [f"**{m}** — {e['reasons'][m]}" for m in mission_ids if e["verdicts"].get(m) == "FAIL"]
        if bad:
            lines.append(f"- `{e['model']}`: " + "; ".join(bad))
    lines.append("")

    text = "\n".join(lines)
    if args.out:
        Path(args.out).write_text(text, encoding="utf-8")
        print(f"wrote {args.out}")
    else:
        print(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
