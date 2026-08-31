#!/usr/bin/env python3
"""Typeset the adversarial suite's 29 cases from the test file that defines them.

Question this answers (paper appendix "The adversarial suite, case by case"):
*what, exactly, was tested?*  The paper used to answer that with a prose list
and a pointer to ``docs/adversarial_results.md``.  This script answers it with
a table nobody typed: every row is read out of the suite's own source, so the
paper cannot drift from the code it describes.

Where each column comes from
----------------------------
``case``      the first argument of a ``record(...)`` call in
              ``tests/integration/test_adversarial_sitl.py``.  Cases carried by
              a ``@pytest.mark.parametrize`` decorator are expanded here the
              same way pytest expands them, one row per parameter set, so the
              three injection payloads are three rows and not one.
``category``  the second argument.  Rows are grouped under it, in the same
              classes the paper's adversarial figure draws as bars.
``attack``    the third argument: what a confused, hallucinating or injected
              client did.
``expected``  the fourth argument: the outcome the case asserts *before* it is
              run.  This is the specification, not the observation.
``observed``  from ``docs/adversarial_results.md``, the artifact the suite
              itself writes on its last test while running against a live
              ArduCopter SITL.  The status and rule id the server actually
              returned.
``result``    from the same artifact: pass when the server produced the
              specified outcome.

Two sources, deliberately.  The test file states what is asserted; the results
file states what happened when it ran.  Joining them is what makes the table
evidence rather than a restatement of intent, and the join is checked: the
script exits non-zero if the two disagree about which cases exist, if the
results file's own headline count disagrees with its rows, or if ``--expect``
is given and the case count is not it.

Outputs:
  stdout               - the joined table, one line per case
  --macros <path>      - the count macros, for the manuscript preamble
  --tex <path>         - the LaTeX longtable

Regenerate:
    .venv/bin/python scripts/adversarial_case_table.py --expect 29
    .venv/bin/python scripts/adversarial_case_table.py --expect 29 \\
        --macros /root/LLMUAV/Manuscript/v2/adversarial_cases_macros.tex \\
        --tex    /root/LLMUAV/Manuscript/v2/adversarial_cases.tex

The suite itself is not run here: running it needs a live simulator and takes
minutes, and its result is already checked in as the artifact this script
reads.  Re-running it (``pytest -m sitl tests/integration/
test_adversarial_sitl.py``) rewrites that artifact, and this script then
regenerates the table from the new one.
"""

from __future__ import annotations

import argparse
import ast
import re
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
SUITE = REPO / "tests" / "integration" / "test_adversarial_sitl.py"
RESULTS = REPO / "docs" / "adversarial_results.md"

#: Order the categories are printed in.  Not a hand-kept list of the categories
#: themselves - every category found in the source is printed, and any that is
#: not named here sorts after the ones that are, alphabetically.  The order
#: below is the reading order of the paper's paragraph: the gate first, then
#: who may call, then what the arguments may say, then where the aircraft may
#: go, then when, then the payload-shaped attacks, then the rate limit, then
#: the record, then the review regressions.
CATEGORY_ORDER = [
    "criticality tier",
    "authorization",
    "parameter bounds",
    "geofence",
    "state precondition",
    "prompt injection",
    "rate limit",
    "audit",
    "review regression",
]


# --------------------------------------------------------------- the test file


def _parametrize_bindings(func: ast.FunctionDef) -> list[dict[str, object]]:
    """Expand ``@pytest.mark.parametrize`` into one binding dict per row.

    Only literal argument lists are expanded, which is all this suite uses.  A
    decorator whose values are not literals raises rather than being silently
    skipped: a case that vanished from the table would be the one failure mode
    this whole script exists to prevent.
    """
    bindings: list[dict[str, object]] = [{}]
    for dec in func.decorator_list:
        if not isinstance(dec, ast.Call):
            continue
        name = ast.unparse(dec.func)
        if not name.endswith("parametrize"):
            continue
        names = [n.strip() for n in ast.literal_eval(dec.args[0]).split(",")]
        rows = ast.literal_eval(dec.args[1])
        expanded = []
        for base in bindings:
            for row in rows:
                values = row if isinstance(row, tuple) else (row,)
                expanded.append({**base, **dict(zip(names, values, strict=True))})
        bindings = expanded
    return bindings


def _literal(node: ast.AST, bindings: dict[str, object]) -> str:
    """The value of a ``record()`` argument, with parametrize names bound.

    Handles the two forms the suite uses: a plain string constant, and an
    f-string over the parametrized values.  Anything else raises.
    """
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    if isinstance(node, ast.Name) and node.id in bindings:
        return str(bindings[node.id])
    if isinstance(node, ast.JoinedStr):
        return eval(  # noqa: S307 - a literal f-string from our own test file
            compile(ast.Expression(node), "<record>", "eval"),
            {"__builtins__": {}},
            dict(bindings),
        )
    raise ValueError(f"cannot resolve a record() argument: {ast.dump(node)[:120]}")


def cases_from_suite(path: Path) -> list[dict[str, str]]:
    """Every case the suite declares, in source order."""
    tree = ast.parse(path.read_text())
    cases: list[dict[str, str]] = []
    for func in [n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef)]:
        bindings_list = _parametrize_bindings(func)
        for call in [n for n in ast.walk(func) if isinstance(n, ast.Call)]:
            if not (isinstance(call.func, ast.Name) and call.func.id == "record"):
                continue
            if len(call.args) < 4:
                raise ValueError(f"record() with {len(call.args)} arguments in {func.name}")
            for bindings in bindings_list:
                cases.append(
                    {
                        "case": _literal(call.args[0], bindings),
                        "category": _literal(call.args[1], bindings),
                        "attack": _literal(call.args[2], bindings),
                        "expected": _literal(call.args[3], bindings),
                        "test": func.name,
                    }
                )
    return cases


# ------------------------------------------------------------ the results file


def results_from_artifact(path: Path) -> tuple[dict[str, dict[str, str]], int, int]:
    """The observed outcome per case, plus the file's own headline counts."""
    text = path.read_text()
    headline = re.search(r"\*\*(\d+) of (\d+) cases behaved as specified\.\*\*", text)
    if not headline:
        raise ValueError(f"{path} has no headline count line")
    rows: dict[str, dict[str, str]] = {}
    for line in text.splitlines():
        cells = [c.strip() for c in line.strip().strip("|").split("|")]
        if len(cells) != 7 or not re.fullmatch(r"[A-Z]\d+", cells[0]):
            continue
        rows[cells[0]] = {
            "observed": cells[4].strip("`"),
            "rule": cells[5].strip("`"),
            "result": cells[6].lower(),
        }
    return rows, int(headline.group(1)), int(headline.group(2))


# ------------------------------------------------------------------- the LaTeX

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

#: Tokens that are code, not prose: a dotted rule id, or a snake_case
#: identifier.  They are set in typewriter so a reader can tell a rule the
#: server fired from a description of it.
_CODEISH = re.compile(r"\b[a-z][a-z0-9]*(?:[._][a-z0-9]+)+\b")


def _tex_escape(text: str) -> str:
    return "".join(_SPECIALS.get(ch, ch) for ch in text)


def _tex_code(token: str) -> str:
    """Typewriter, breakable, and safe from the hyphen ligature.

    Rule ids such as ``precondition.navigation_requires_airborne`` are longer
    than the narrow columns of this table, and a typewriter word carries no
    hyphenation points, so each separator is given an explicit break
    opportunity that prints nothing.  Hyphen pairs are split for the same
    reason the prompts are: a hyphen a reader might copy must stay a hyphen.
    """
    escaped = _tex_escape(token).replace("--", "-{}-")
    escaped = re.sub(r"(\\_|\.)", r"\1\\allowbreak{}", escaped)
    return r"\texttt{" + escaped + "}"


def _tex_prose(text: str) -> str:
    out, last = [], 0
    for m in _CODEISH.finditer(text):
        out.append(_tex_escape(text[last : m.start()]))
        out.append(_tex_code(m.group(0)))
        last = m.end()
    out.append(_tex_escape(text[last:]))
    return "".join(out).replace("--", "-{}-")


def _category_key(category: str) -> tuple[int, str]:
    if category in CATEGORY_ORDER:
        return (CATEGORY_ORDER.index(category), "")
    return (len(CATEGORY_ORDER), category)


_BANNER = [
    "% GENERATED FILE - DO NOT EDIT.",
    "% Every value is read out of tests/integration/test_adversarial_sitl.py and",
    "% docs/adversarial_results.md in the droneserver repository.",
    "% Regenerate: .venv/bin/python scripts/adversarial_case_table.py --expect 29 \\",
    "%                 --macros <macro file> --tex <table file>",
]


def to_macros(rows: list[dict[str, str]], passed: int) -> str:
    """The counts, as a preamble block: they are quoted before the table."""
    categories = {r["category"] for r in rows}
    return "\n".join(
        _BANNER
        + [
            rf"\newcommand{{\advCases}}{{{len(rows)}}}",
            rf"\newcommand{{\advPassed}}{{{passed}}}",
            rf"\newcommand{{\advCategories}}{{{len(categories)}}}",
            "",
        ]
    )


def to_latex(rows: list[dict[str, str]], passed: int) -> str:
    categories = sorted({r["category"] for r in rows}, key=_category_key)
    out = _BANNER + [
        r"\begingroup",
        r"\footnotesize",
        r"\setlength{\tabcolsep}{4pt}",
        r"\renewcommand{\arraystretch}{1.15}",
        r"\begin{longtable}{@{}"
        r">{\raggedright\arraybackslash}p{0.045\textwidth}"
        r">{\raggedright\arraybackslash}p{0.270\textwidth}"
        r">{\raggedright\arraybackslash}p{0.270\textwidth}"
        r">{\raggedright\arraybackslash}p{0.230\textwidth}"
        r">{\raggedright\arraybackslash}p{0.060\textwidth}@{}}",
    ]
    caption = (
        r"Every one of the \advCases\ adversarial cases, one row each: what the "
        r"client did, the outcome the case asserts before it runs, what the server "
        r"actually returned, and whether those agree. Grouped into the \advCategories\ "
        r"classes the bars of Figure~\ref{fig:adversarial} count. A case passes when "
        r"the server produced the specified outcome, which is a refusal in most rows "
        r"and a correct allow in the four where allowing is the right answer (B3, A6, "
        r"I3, I5): a guard that blocked everything would be useless. "
        r"``Observed'' prints the status the server returned and the rule id it fired, "
        r"and \texttt{-} means no rule fired, because the call was allowed or was "
        r"answered with a confirmation demand rather than a rejection. "
        r"\emph{Data provenance:} measured, and no row is transcribed by hand. The "
        r"case, category, attack and expectation columns are read out of the suite's "
        r"own source (\texttt{tests/integration/test\_adversarial\_sitl.py}) and the "
        r"observed and result columns out of the artifact that suite writes while "
        r"running against a live ArduCopter~4.5.7 software-in-the-loop instance "
        r"(\texttt{docs/adversarial\_results.md}); "
        r"\texttt{scripts/adversarial\_case\_table.py} joins them and fails if the "
        r"two disagree about which cases exist."
    )
    out += [
        rf"\caption{{{caption}}}\label{{tab:advcases}}\\",
        r"\toprule",
        r"Case & Attack or mistake & Expected outcome & Observed & Result \\",
        r"\midrule",
        r"\endfirsthead",
        r"\multicolumn{5}{@{}l}{\emph{Table~\ref{tab:advcases} continued: the "
        r"adversarial suite, case by case.}}\\[2pt]",
        r"\toprule",
        r"Case & Attack or mistake & Expected outcome & Observed & Result \\",
        r"\midrule",
        r"\endhead",
        r"\bottomrule",
        r"\endfoot",
        r"\bottomrule",
        r"\endlastfoot",
    ]
    first = True
    for category in categories:
        members = [r for r in rows if r["category"] == category]
        out.append("" if first else r"\addlinespace[2pt]")
        first = False
        out.append(rf"\multicolumn{{5}}{{@{{}}l}}{{\itshape {_tex_prose(category)}}}\\[1pt]")
        for row in sorted(members, key=lambda r: r["case"]):
            observed = _tex_code(row["observed"])
            if row["rule"] and row["rule"] != "-":
                observed += r",\ " + _tex_code(row["rule"])
            out.append(
                " & ".join(
                    [
                        row["case"],
                        _tex_prose(row["attack"]),
                        _tex_prose(row["expected"]),
                        observed,
                        row["result"],
                    ]
                )
                + r" \\"
            )
    out += [r"\end{longtable}", r"\endgroup", ""]
    return "\n".join(out)


# ---------------------------------------------------------------------- driver


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--suite", type=Path, default=SUITE)
    ap.add_argument("--results", type=Path, default=RESULTS)
    ap.add_argument("--tex", type=Path, help="write the LaTeX longtable here")
    ap.add_argument("--macros", type=Path, help="write the LaTeX count macros here (preamble block)")
    ap.add_argument("--expect", type=int, help="fail unless exactly this many cases are found")
    args = ap.parse_args()

    cases = cases_from_suite(args.suite)
    observed, passed, declared = results_from_artifact(args.results)

    problems = []
    duplicates = sorted({c["case"] for c in cases if [x["case"] for x in cases].count(c["case"]) > 1})
    if duplicates:
        problems.append(f"duplicate case ids in the suite: {duplicates}")
    only_source = sorted({c["case"] for c in cases} - set(observed))
    only_results = sorted(set(observed) - {c["case"] for c in cases})
    if only_source:
        problems.append(f"declared by the suite but absent from {args.results.name}: {only_source}")
    if only_results:
        problems.append(f"in {args.results.name} but no longer declared by the suite: {only_results}")
    if declared != len(observed):
        problems.append(f"{args.results.name} says {declared} cases and prints {len(observed)} rows")
    if args.expect is not None and len(cases) != args.expect:
        problems.append(f"expected {args.expect} cases, the suite declares {len(cases)}")
    if problems:
        for p in problems:
            print(f"FAIL: {p}", file=sys.stderr)
        return 1

    rows = [{**c, **observed[c["case"]]} for c in cases]
    width = max(len(r["attack"]) for r in rows)
    for row in sorted(rows, key=lambda r: (_category_key(r["category"]), r["case"])):
        print(f"{row['case']:<3} {row['category']:<18} {row['attack']:<{width}}  {row['result']}")
    print(f"\n{passed} of {len(rows)} cases behaved as specified, in {len({r['category'] for r in rows})} categories.")

    if args.macros:
        args.macros.write_text(to_macros(rows, passed))
        print(f"wrote {args.macros}")
    if args.tex:
        args.tex.write_text(to_latex(rows, passed))
        print(f"wrote {args.tex}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
