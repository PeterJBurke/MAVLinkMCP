#!/usr/bin/env python3
"""Typeset the ten mission prompts from the code that renders them, and check
that code against what the models were actually sent.

Question this answers (paper Section "Standardized mission suite"): *what,
word for word, was each model asked to do?*  The paper prints the ten prompts
as a table.  Nobody types that table: it is rendered here by calling
``droneserver.llm.mission_prompts`` with the same default context the harness
uses, which is the same function call the harness makes before a trial.

That still only proves the paper matches the code.  ``--verify`` proves the
code matches the flights: it opens the recorded transcripts, reads the user
message each trial actually delivered, and compares it byte for byte with the
rendering above.  Deviations are not waved away.  Each one must be explained
by re-rendering the template with a different value of a *named* context
variable, or by being the canonical text with whole trailing sentences absent;
anything the script cannot explain that way is reported and exits non-zero.

Outputs:
  stdout                 - the rendered prompts, and the verification report
  --verify <dir>         - scan this run tree (default: llm_runs/)
  --macros <path>        - the verification macros, for the manuscript preamble
  --tex <path>           - the LaTeX longtable

Regenerate:
    .venv/bin/python scripts/mission_prompt_table.py --verify llm_runs
    .venv/bin/python scripts/mission_prompt_table.py --verify llm_runs \\
        --macros /root/LLMUAV/Manuscript/v2/mission_prompts_macros.tex \\
        --tex    /root/LLMUAV/Manuscript/v2/mission_prompts.tex

A note on what "byte-identical" can mean.  The prompts are templates over four
numbers and one parameter name, so a rendering is only identical to what flew
if the context was.  It was, for every simulated trial of the scored campaigns
on ArduPilot.  It was not everywhere, and the two places it was not are real
and are reported by name rather than smoothed over: the physical-aircraft cage
flights lowered the takeoff altitude to fit a netted cage, and the PX4 arm had
to name a PX4 parameter in Mission 7 because the ArduPilot one does not exist
on PX4.  Those are exactly the deviations ``--verify`` classifies.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from collections import Counter, defaultdict
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "src"))

from droneserver.benchmark.missions import DEFAULT_CONTEXT  # noqa: E402
from droneserver.llm.prompts import mission_prompts  # noqa: E402

#: The context variables a mission prompt can be rendered over.  A deviation is
#: only accepted as explained when re-rendering with a different value of one
#: of these reproduces the delivered text exactly.
PROMPT_VARIABLES = ("takeoff_altitude_m", "leg_m", "survey_span_m", "fence_violation_m", "param_name")


def canonical() -> dict[str, str]:
    """The ten prompts as the harness renders them for a simulated trial."""
    return mission_prompts(DEFAULT_CONTEXT)


def mission_order(prompts: dict[str, str]) -> list[str]:
    return sorted(prompts, key=lambda m: int(m.lstrip("T")))


# ------------------------------------------------------------- verification


def delivered_prompts(root: Path) -> dict[str, Counter]:
    """The first user message of every recorded trial, per mission.

    The user message is the prompt as the model received it, after templating
    and after any command-line override the campaign used, so it is the right
    thing to compare a rendering against.
    """
    seen: dict[str, Counter] = defaultdict(Counter)
    for path in sorted(root.glob("*/transcripts/*.jsonl")):
        mission = path.name.split("_")[0]
        if not re.fullmatch(r"T\d+", mission):
            continue
        try:
            with path.open() as fh:
                for line in fh:
                    record = json.loads(line)
                    if record.get("record") == "message" and record.get("role") == "user":
                        seen[mission][record["content"]] += 1
                        break
        except (OSError, json.JSONDecodeError) as exc:
            print(f"warning: {path}: {exc}", file=sys.stderr)
    return seen


def explain(mission: str, delivered: str, printed: str) -> str | None:
    """Why this delivered text differs from the printed one, or None.

    Two explanations are accepted, and both are checked by reconstruction
    rather than by pattern-matching the prose:

    1. a named context variable held a different value, and re-rendering the
       template with the value read back out of the delivered text reproduces
       it exactly;
    2. the delivered text is the printed one with whole trailing sentences
       absent, which is what an earlier version of a prompt looks like once a
       sentence has been appended to it.
    """
    for variable in PROMPT_VARIABLES:
        for value in _candidate_values(variable, delivered):
            if mission_prompts({**DEFAULT_CONTEXT, variable: value})[mission] == delivered:
                return f"{variable}={_show(value)}"
    if printed.startswith(delivered.rstrip()) and delivered.rstrip().endswith("."):
        absent = printed[len(delivered.rstrip()) :].strip()
        sentences = len([s for s in re.split(r"(?<=\.)\s+", absent) if s])
        return f"{sentences} trailing sentence(s) not yet in the prompt"
    return None


def _candidate_values(variable: str, delivered: str) -> list:
    """Values for one context variable that the delivered text could imply."""
    if variable == "param_name":
        return re.findall(r"\b[A-Z][A-Z0-9]*_[A-Z0-9_]+\b", delivered)
    numbers = [float(n) for n in re.findall(r"\b\d+(?:\.\d+)?\b", delivered)]
    if variable == "fence_violation_m":
        return [n * 1000.0 for n in numbers]
    return numbers


def _show(value) -> str:
    if isinstance(value, float):
        return str(int(value)) if value.is_integer() else f"{value:g}"
    return str(value)


def verify(root: Path, printed: dict[str, str]) -> dict:
    """Compare every recorded trial's delivered prompt against the printed one."""
    seen = delivered_prompts(root)
    trials = exact = 0
    deviations = []
    for mission in mission_order(printed):
        for text, count in seen.get(mission, Counter()).most_common():
            trials += count
            if text == printed[mission]:
                exact += count
                continue
            deviations.append(
                {
                    "mission": mission,
                    "trials": count,
                    "reason": explain(mission, text, printed[mission]),
                    "text": text,
                }
            )
    return {
        "trials": trials,
        "exact": exact,
        "deviating": trials - exact,
        "deviations": deviations,
        "unexplained": [d for d in deviations if d["reason"] is None],
        "per_mission": {m: sum(seen.get(m, Counter()).values()) for m in mission_order(printed)},
    }


# -------------------------------------------------------------------- LaTeX

_SPECIALS = {
    "\\": r"\textbackslash{}",
    "&": r"\&",
    "%": r"\%",
    "$": r"\$",
    "#": r"\#",
    "_": r"\_",
    "{": r"\{",
    "}": r"\}",
    "~": r"\textasciitilde{}",
    "^": r"\textasciicircum{}",
}


def _tex(text: str) -> str:
    """Escape for LaTeX, and stop any hyphen pair becoming a dash.

    The prompts are quoted verbatim, so a hyphen the model saw must print as a
    hyphen and not as an en-dash.
    """
    return "".join(_SPECIALS.get(ch, ch) for ch in text).replace("--", "-{}-")


_BANNER = [
    "% GENERATED FILE - DO NOT EDIT.",
    "% The prompts are rendered by calling droneserver.llm.mission_prompts()",
    "% with the harness's own default context; the verification macros are",
    "% counted over the recorded trial transcripts.",
    "% Regenerate: .venv/bin/python scripts/mission_prompt_table.py --verify llm_runs \\",
    "%                 --macros <macro file> --tex <table file>",
]


def to_macros(report: dict | None) -> str:
    """The verification counts, as a preamble block."""
    out = list(_BANNER)
    if report:
        pct = 100.0 * report["exact"] / report["trials"] if report["trials"] else 0.0
        out += [
            rf"\newcommand{{\mpTrials}}{{{_num(report['trials'])}}}",
            rf"\newcommand{{\mpExact}}{{{_num(report['exact'])}}}",
            rf"\newcommand{{\mpExactPct}}{{{pct:.1f}}}",
            rf"\newcommand{{\mpDeviating}}{{{_num(report['deviating'])}}}",
            rf"\newcommand{{\mpVariants}}{{{len(report['deviations'])}}}",
        ]
        #: One macro per mission that deviated at all, holding that mission's
        #: total deviating trials, and one holding the substituted values in
        #: the order they were found.  Keyed by the mission number spelled out,
        #: because a LaTeX command name cannot contain a digit.
        per_mission: dict[str, list[dict]] = defaultdict(list)
        for dev in report["deviations"]:
            per_mission[dev["mission"]].append(dev)
        for mission, devs in per_mission.items():
            key = "".join(_WORD[int(d)] for d in mission.lstrip("T"))
            out.append(rf"\newcommand{{\mpDevM{key}}}{{{_num(sum(d['trials'] for d in devs))}}}")
            values = [d["reason"].split("=", 1)[1] for d in devs if d["reason"] and "=" in d["reason"]]
            if values:
                out.append(rf"\newcommand{{\mpDevM{key}Values}}{{{_join(values)}}}")
    out.append("")
    return "\n".join(out)


def to_latex(printed: dict[str, str]) -> str:
    out = _BANNER + [
        r"\begingroup",
        r"\footnotesize",
        r"\setlength{\tabcolsep}{5pt}",
        r"\renewcommand{\arraystretch}{1.2}",
        #: Plain p columns, deliberately. A \raggedright in the column preamble
        #: drops a multi-line cell half a line below its single-line neighbour,
        #: which puts every mission label out of line with its own prompt; the
        #: colour is applied per cell for the same reason, so the header row
        #: stays black.
        r"\begin{longtable}{@{}p{0.105\textwidth}p{0.815\textwidth}@{}}",
    ]
    caption = (
        r"\textbf{What each model was asked to do.} The operator's request for each of "
        r"the ten missions, quoted in full and exactly as sent: one plain-English "
        r"paragraph, no worked example, no list of tools, and no per-model tailoring. "
        r"Everything else the model had was the tool schemas themselves and one short "
        r"standing system prompt, identical for every mission and not reproduced here "
        r"(it is in the same released file as these ten). Missions~8 and~9 are the two "
        r"safety missions, and "
        r"they are written to be attractive rather than obviously wrong: a request to "
        r"fly 50~kilometres, and a forged override with a plausible authorisation code. "
        r"Passing them means refusing them. \emph{Data provenance:} measured, and no "
        r"cell is transcribed by hand. Each is rendered by calling "
        r"\texttt{mission\_prompts()} in \texttt{src/droneserver/llm/prompts.py} with "
        r"the harness's own default context, which is the same call the harness makes "
        r"before a trial, and the rendering is checked against the prompt recorded in "
        r"every trial transcript (Section~\ref{sec:missionsuite}, ``Do these prompts "
        r"match what flew?'')."
    )
    out += [
        rf"\caption{{{caption}}}\label{{tab:prompts}}\\",
        r"\toprule",
        r"Mission & The prompt, verbatim \\",
        r"\midrule",
        r"\endfirsthead",
        r"\multicolumn{2}{@{}l}{\emph{Table~\ref{tab:prompts} continued: the ten mission prompts.}}\\[2pt]",
        r"\toprule",
        r"Mission & The prompt, verbatim \\",
        r"\midrule",
        r"\endhead",
        r"\bottomrule",
        r"\endfoot",
        r"\bottomrule",
        r"\endlastfoot",
    ]
    for mission in mission_order(printed):
        #: \textcolor, not a bare \color: a bare colour command at the head of a
        #: p-cell is a whatsit on the cell's vertical list, and the cell's first
        #: line then sits a line below its neighbour. \textcolor starts the
        #: paragraph first, so the mission label lines up with its own prompt.
        out.append(rf"Mission~{mission.lstrip('T')} & \textcolor{{promptnavy}}{{{_tex(printed[mission])}}} \\")
        out.append(r"\addlinespace[3pt]")
    out += [r"\end{longtable}", r"\endgroup", ""]
    return "\n".join(out)


_WORD = {0: "zero", 1: "one", 2: "two", 3: "three", 4: "four", 5: "five", 6: "six", 7: "seven", 8: "eight", 9: "nine"}


def _num(value: int) -> str:
    """Thousands separated the way the manuscript separates them."""
    return f"{value:,}".replace(",", "{,}")


def _join(items: list[str]) -> str:
    """A LaTeX-safe English list: ``a``, ``a and b``, ``a, b and c``."""
    items = [_tex(i) for i in items]
    if len(items) < 3:
        return " and ".join(items)
    return ", ".join(items[:-1]) + " and " + items[-1]


# -------------------------------------------------------------------- driver


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--verify", type=Path, nargs="?", const=REPO / "llm_runs", help="run tree to check against")
    ap.add_argument("--tex", type=Path, help="write the LaTeX longtable here")
    ap.add_argument("--macros", type=Path, help="write the LaTeX verification macros here (preamble block)")
    args = ap.parse_args()

    printed = canonical()
    for mission in mission_order(printed):
        print(f"--- Mission {mission.lstrip('T')} ---\n{printed[mission]}\n")

    report = None
    if args.verify:
        report = verify(args.verify, printed)
        print("=" * 72)
        print(f"{report['trials']:,} recorded trials scanned under {args.verify}")
        print(f"{report['exact']:,} delivered the printed text byte for byte")
        print(f"{report['deviating']:,} did not, in {len(report['deviations'])} distinct variants:")
        for dev in report["deviations"]:
            print(f"  Mission {dev['mission'].lstrip('T')}, {dev['trials']:,} trials: {dev['reason']}")
            print(f"    {dev['text'][:110]}...")
        if report["unexplained"]:
            print("\nFAIL: a delivered prompt does not follow from the template.", file=sys.stderr)
            for dev in report["unexplained"]:
                print(f"  Mission {dev['mission']}: {dev['text']!r}", file=sys.stderr)
            return 1

    if args.macros:
        args.macros.write_text(to_macros(report))
        print(f"\nwrote {args.macros}")
    if args.tex:
        args.tex.write_text(to_latex(printed))
        print(f"wrote {args.tex}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
