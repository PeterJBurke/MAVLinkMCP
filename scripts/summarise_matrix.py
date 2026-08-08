#!/usr/bin/env python3
"""Roll several single-model runs up into one cross-model comparison table.

Each run directory holds one model's results. This reads them all and writes
the table the paper needs: per-model verdicts, the two latencies kept apart,
tokens, and cost — plus, for the safety missions, the three-way distinction
that a bare pass/fail hides (did the *server* refuse the command, or did the
model decline to issue it at all?).

    uv run python scripts/summarise_matrix.py --runs llm_runs --glob 'matrix-*'
"""

import argparse
import csv
import json
import statistics
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


def main() -> int:
    parser = argparse.ArgumentParser(description="cross-model comparison table")
    parser.add_argument("--runs", default="llm_runs")
    parser.add_argument("--glob", default="*matrix-*")
    parser.add_argument("--out", default="")
    args = parser.parse_args()

    lines: list[str] = []
    per_model: list[dict] = []

    for run_dir in sorted(Path(args.runs).glob(f"*{args.glob.strip('*')}*")):
        missions = rows_of(run_dir / "missions.csv")
        turns = rows_of(run_dir / "turns.csv")
        calls = rows_of(run_dir / "tool_calls.csv")
        if not missions:
            continue
        model = missions[0]["model"]
        provider = missions[0]["provider"]
        decision = [number(t["decision_latency_ms"]) for t in turns]
        command = [number(c["client_wall_ms"]) for c in calls if c["status"] != "client_rejected"]
        judged = [m for m in missions if m["verdict"] in ("PASS", "FAIL")]
        entry = {
            "model": model,
            "provider": provider,
            "resolved": next((t.get("resolved_model") or "" for t in turns if t.get("resolved_model")), ""),
            "passed": sum(1 for m in judged if m["verdict"] == "PASS"),
            "judged": len(judged),
            "verdicts": {m["mission_id"]: m["verdict"] for m in missions},
            "reasons": {m["mission_id"]: m["reason"] for m in missions},
            "evidence": {m["mission_id"]: json.loads(m["evidence"] or "{}") for m in missions},
            "turns": sum(int(number(m["turns"])) for m in missions),
            "calls": sum(int(number(m["tool_calls"])) for m in missions),
            "decision_median_ms": statistics.median(decision) if decision else 0.0,
            "command_median_ms": statistics.median(command) if command else 0.0,
            "decision_share": (sum(decision) / (sum(decision) + sum(command)) * 100) if (decision or command) else 0.0,
            "input_tokens": sum(int(number(m["input_tokens"])) for m in missions),
            "cached_tokens": sum(int(number(m["cached_input_tokens"])) for m in missions),
            "output_tokens": sum(int(number(m["output_tokens"])) for m in missions),
            "cost": sum(number(m["cost_usd"]) for m in missions),
            "refusals": sum(int(number(m["refusals"])) for m in missions),
            "confirmations": sum(int(number(m["confirmations_demanded"])) for m in missions),
            "bad_calls": sum(1 for c in calls if c["status"] == "client_rejected"),
            "run_dir": str(run_dir),
        }
        per_model.append(entry)

    mission_ids = sorted({m for e in per_model for m in e["verdicts"]})

    lines += [
        "# Cross-model comparison — N=1",
        "",
        "Every row is one model flying the same missions from the same natural-language",
        "prompts against the same simulated aircraft, with the safety layer on. Verdicts",
        "come from the flight recorder, not from the model's account of itself.",
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
        "## Latency, kept apart",
        "",
        "| Model | Decision median | Command median | Decision share | Turns | Calls |",
        "|---|---|---|---|---|---|",
    ]
    for e in per_model:
        lines.append(
            f"| `{e['model']}` | {e['decision_median_ms']:.0f} ms | {e['command_median_ms']:.0f} ms | "
            f"{e['decision_share']:.0f}% | {e['turns']} | {e['calls']} |"
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
