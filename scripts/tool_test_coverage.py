#!/usr/bin/env python3
"""Derive, per MCP tool, which layers of evidence actually test it.

Question this answers (paper appendix "Which tools were tested, and how"):
*has every one of the registered tools been exercised in this work, and by
what?*  Nothing here is hand-typed: the tool roster, the three evidence
layers and every count are read off the source tree, the test tree and the
flown campaign records at run time.

Evidence layers
---------------
1. ``unit``       - the unit suite (``tests/*.py``, no autopilot present).
                    Sub-classified, because the mapping from a test file to a
                    tool is genuinely fuzzy and we say so rather than round it
                    up:
                      direct    the test module imports the tool's own
                                function object out of ``droneserver.tools``
                                and calls it (usually through
                                ``.__wrapped__`` to bypass the safety
                                decorator, which is itself under test
                                elsewhere);
                      named     the tool's name appears as a string literal in
                                a unit test module that is not a whole-registry
                                inventory test, i.e. the test asserts something
                                about *that* tool (its tier, its rule table,
                                its confirmation behaviour);
                      registry  the tool is covered only by the invariant tests
                                that enumerate the live registry
                                (``mcp.list_tools()``) and assert a property of
                                every registered tool.  Real coverage, but of
                                the registration/guard contract, not of the
                                tool body.
2. ``sitl``       - the SITL integration suite (``tests/integration/``), which
                    drives a real autopilot through the real MCP wire protocol.
                    A tool counts when a ``<client>.call("<tool>", ...)``
                    invocation is found: either as a literal first argument, or
                    as a string constant that a ``for`` loop feeds into
                    ``.call(<var>)``.
3. ``corpus``     - the scored N=5 campaigns (ArduPilot + PX4).  A tool counts
                    when a model actually chose it: one row of a run's
                    ``tool_calls.csv``.  The wider ``llm_runs`` /
                    ``benchmark_runs`` trees are also scanned, separately, so
                    "never chosen in the scored corpus" can be distinguished
                    from "never chosen by any model anywhere".

Outputs (all regenerable, nothing cached):
  docs/tool_test_coverage.csv       - one row per registered tool
  docs/tool_test_coverage.md        - human-readable summary
  stdout                            - the same summary
  --tex <path>                      - LaTeX macro block for the manuscript

Regenerate:
    .venv/bin/python scripts/tool_test_coverage.py
    .venv/bin/python scripts/tool_test_coverage.py --tex \
        /root/LLMUAV/Manuscript/v2/tool_test_coverage.tex

The campaign directory lists are the same canonical lists the manuscript's own
figure generators use.  They are duplicated here so this script runs inside the
code repository alone, and cross-checked against the paper repository's
``Research/rev4/corpus.py`` whenever that repository is present: a drift
between the two is a hard error, never a silent divergence.
"""

from __future__ import annotations

import argparse
import ast
import csv
import json
import re
import subprocess
import sys
from collections import Counter, defaultdict
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
SRC = REPO / "src" / "droneserver"
TESTS = REPO / "tests"
INTEGRATION = TESTS / "integration"
DOCS = REPO / "docs"
LLM_RUNS = REPO / "llm_runs"
BENCH_RUNS = REPO / "benchmark_runs"
PAPER_REPO = Path("/root/LLMUAV")

# --------------------------------------------------------------------------- #
# Canonical scored-corpus run directories (the two N=5 campaigns).
# Mirrors Research/rev4/corpus.py in the paper repository; cross-checked below.
# --------------------------------------------------------------------------- #
AP_DIRS = [
    "20260811T185349Z_n5-claude-haiku-4-5-20251001",
    "20260811T202654Z_n5-claude-sonnet-5",
    "20260811T013230Z_n5-claude-opus-5",
    "20260811T213539Z_n5-claude-opus-5",
    "20260811T023413Z_n5-gpt-5_2",
    "20260811T042543Z_n5-grok-4_5",
    "20260811T053029Z_n5-grok-4_20-0309-reasoning",
    "20260811T063925Z_n5-grok-4_20-0309-non-reasoning",
    "20260811T220330Z_n5-gemini-3_5-flash-lite",
    "20260811T230527Z_n5-gemini-3_6-flash",
    "20260812T000330Z_n5-gemini-robotics-er-2-preview",
    "20260812T053613Z_n5-gemini-3.1-pro-preview",
]
PX4_DIRS = [
    "20260812T204716Z_px4-n5-claude-opus-5",
    "20260812T223348Z_px4-n5-claude-sonnet-5",
    "20260812T235647Z_px4-n5-claude-haiku-4-5-20251001",
    "20260813T011620Z_px4-n5-gpt-5_2",
    "20260813T031200Z_px4-n5-grok-4_5",
    "20260813T195553Z_px4-n5-gemini-3_1-pro-preview",
    "20260813T172846Z_px4-n5-gemini-3_6-flash",
    "20260813T161730Z_px4-n5-gemini-3_5-flash-lite",
    "20260813T222631Z_px4-n5-gemini-robotics-er-2-preview",
    "20260813T231002Z_px4-n5-gemini-robotics-er-2-preview",
    "20260813T142936Z_px4-n5-grok-4_20-0309-reasoning",
    "20260813T125334Z_px4-n5-grok-4_20-0309-non-reasoning",
]


def cross_check_corpus_dirs() -> str:
    """The paper repo owns the canonical arm membership; fail on drift."""
    corpus_py = PAPER_REPO / "Research" / "rev4" / "corpus.py"
    if not corpus_py.exists():
        return "paper repo not present; using this script's embedded lists"
    tree = ast.parse(corpus_py.read_text())
    got = {}
    for node in tree.body:
        if isinstance(node, ast.Assign) and isinstance(node.targets[0], ast.Name):
            name = node.targets[0].id
            if name in ("AP_DIRS", "PX4_DIRS") and isinstance(node.value, (ast.List, ast.Tuple)):
                got[name] = [e.value for e in node.value.elts if isinstance(e, ast.Constant)]
    if got.get("AP_DIRS") != AP_DIRS or got.get("PX4_DIRS") != PX4_DIRS:
        raise SystemExit(
            f"canonical campaign directory lists have drifted from {corpus_py}; reconcile before publishing any count"
        )
    return f"cross-checked against {corpus_py}"


# --------------------------------------------------------------------------- #
# Layer 0: the tool roster
# --------------------------------------------------------------------------- #
def _is_mcp_tool(fn) -> bool:
    for dec in fn.decorator_list:
        target = dec.func if isinstance(dec, ast.Call) else dec
        if (
            isinstance(target, ast.Attribute)
            and target.attr == "tool"
            and isinstance(target.value, ast.Name)
            and target.value.id == "mcp"
        ):
            return True
    return False


def roster_from_source() -> dict[str, str]:
    """{tool name -> defining module path relative to the repo}."""
    tools: dict[str, str] = {}
    for path in sorted(SRC.rglob("*.py")):
        for node in ast.parse(path.read_text()).body:
            if isinstance(node, (ast.AsyncFunctionDef, ast.FunctionDef)) and _is_mcp_tool(node):
                tools[node.name] = str(path.relative_to(REPO))
    return tools


def roster_from_matrix() -> set[str]:
    """Tool names named by docs/coverage_matrix.csv's implemented_by_tools."""
    path = DOCS / "coverage_matrix.csv"
    names: set[str] = set()
    if not path.exists():
        return names
    with path.open() as fh:
        for row in csv.DictReader(fh):
            raw = (row.get("implemented_by_tools") or "").strip()
            for part in re.split(r"[;,]\s*", raw):
                part = part.strip()
                if not part or part.startswith("["):  # [infra: ...] entries
                    continue
                names.add(re.sub(r"\s*\(.*\)$", "", part))  # drop "(dynamic)"
    return names


def matrix_totals() -> dict[str, int]:
    path = DOCS / "coverage_matrix.csv"
    rows = list(csv.DictReader(path.open()))
    server_side = [r for r in rows if r["plugin"].endswith("_server")]
    client_side = [r for r in rows if not r["plugin"].endswith("_server")]
    return {
        "methods_total": len(rows),
        "methods_client_side": len(client_side),
        "methods_server_side": len(server_side),
        "implemented": sum(1 for r in client_side if r["status"] == "implemented"),
        "documented_na": sum(1 for r in client_side if r["status"] != "implemented"),
    }


# --------------------------------------------------------------------------- #
# Layer 1: the unit suite
# --------------------------------------------------------------------------- #
def _dotted_segments(node) -> list[str]:
    out = []
    while isinstance(node, ast.Attribute):
        out.append(node.attr)
        node = node.value
    if isinstance(node, ast.Name):
        out.append(node.id)
    return out[::-1]


def scan_unit_tests(roster: set[str]):
    """-> (direct, named, registry_modules, per_module_detail)"""
    direct: dict[str, set[str]] = defaultdict(set)
    named: dict[str, set[str]] = defaultdict(set)
    registry_modules: list[str] = []

    for path in sorted(TESTS.glob("*.py")):
        rel = str(path.relative_to(REPO))
        tree = ast.parse(path.read_text())

        # names bound from droneserver.tools (function objects and module aliases)
        bound_tools: set[str] = set()
        module_aliases: set[str] = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and (node.module or "").startswith("droneserver.tools"):
                for alias in node.names:
                    local = alias.asname or alias.name
                    if alias.name in roster:
                        bound_tools.add(local)
                    else:
                        module_aliases.add(local)
            elif isinstance(node, ast.Import):
                for alias in node.names:
                    if alias.name.startswith("droneserver.tools"):
                        module_aliases.add((alias.asname or alias.name).split(".")[0])

        # whole-registry invariant test?  mcp.list_tools() on the imported app
        for node in ast.walk(tree):
            if (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Attribute)
                and node.func.attr == "list_tools"
                and isinstance(node.func.value, ast.Name)
                and node.func.value.id == "mcp"
            ):
                registry_modules.append(rel)
                break

        # The test module binds or calls the tool's own function object out of
        # droneserver.tools: either `from ...tools.action import arm_drone` and
        # then `arm_drone.__wrapped__(...)`, or `from ...tools import action`
        # and then `action.monitor_flight.__wrapped__`.  Binding counts as well
        # as calling: a unit test only reaches into a tool's implementation in
        # order to drive it.
        for node in ast.walk(tree):
            if isinstance(node, ast.Attribute):
                segs = _dotted_segments(node)
                if segs and segs[0] in (module_aliases | bound_tools):
                    for seg in segs:
                        if seg in roster:
                            direct[seg].add(rel)
            elif isinstance(node, ast.Name) and node.id in bound_tools and node.id in roster:
                direct[node.id].add(rel)

        # inventory literals: a collection of >10 tool names is an inventory
        # assertion, not behavioural coverage of each tool in it
        inventory: set[str] = set()
        for node in ast.walk(tree):
            if isinstance(node, (ast.Set, ast.List, ast.Tuple)):
                got = {e.value for e in node.elts if isinstance(e, ast.Constant) and e.value in roster}
                if len(got) > 10:
                    inventory |= got
            elif isinstance(node, ast.Dict):
                got = {k.value for k in node.keys if isinstance(k, ast.Constant) and k.value in roster}
                if len(got) > 10:
                    inventory |= got

        for node in ast.walk(tree):
            if isinstance(node, ast.Constant) and isinstance(node.value, str):
                if node.value in roster and node.value not in inventory:
                    named[node.value].add(rel)

    return direct, named, sorted(set(registry_modules))


# --------------------------------------------------------------------------- #
# Layer 2: the SITL integration suite
# --------------------------------------------------------------------------- #
def scan_sitl_tests(roster: set[str]):
    """-> (literal, loop_dispatched, files_scanned)"""
    literal: dict[str, set[str]] = defaultdict(set)
    loop: dict[str, set[str]] = defaultdict(set)
    files: list[str] = []

    for path in sorted(INTEGRATION.rglob("*.py")):
        rel = str(path.relative_to(REPO))
        tree = ast.parse(path.read_text())
        files.append(rel)

        for node in ast.walk(tree):
            if (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Attribute)
                and node.func.attr == "call"
                and node.args
                and isinstance(node.args[0], ast.Constant)
                and node.args[0].value in roster
            ):
                literal[node.args[0].value].add(rel)

        # for <tgt> in (<literals>): ... .call(<tgt>, ...)
        for node in ast.walk(tree):
            if not isinstance(node, ast.For):
                continue
            targets = set()
            if isinstance(node.target, ast.Name):
                targets.add(node.target.id)
            elif isinstance(node.target, ast.Tuple) and node.target.elts:
                first = node.target.elts[0]
                if isinstance(first, ast.Name):
                    targets.add(first.id)
            if not targets:
                continue
            dispatches = any(
                isinstance(c, ast.Call)
                and isinstance(c.func, ast.Attribute)
                and c.func.attr == "call"
                and c.args
                and isinstance(c.args[0], ast.Name)
                and c.args[0].id in targets
                for stmt in node.body
                for c in ast.walk(stmt)
            )
            if not dispatches:
                continue
            for elt in getattr(node.iter, "elts", []):
                head = elt.elts[0] if isinstance(elt, (ast.Tuple, ast.List)) and elt.elts else elt
                if isinstance(head, ast.Constant) and head.value in roster:
                    loop[head.value].add(rel)

    # honest skips: a SITL test that declines to run rather than pretending
    skips: list[tuple[str, str]] = []
    for path in sorted(INTEGRATION.rglob("*.py")):
        tree = ast.parse(path.read_text())
        for node in ast.walk(tree):
            if (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Attribute)
                and node.func.attr == "skip"
                and isinstance(node.func.value, ast.Name)
                and node.func.value.id == "pytest"
                and node.args
                and isinstance(node.args[0], ast.Constant)
            ):
                skips.append((str(path.relative_to(REPO)), node.args[0].value))

    return literal, loop, files, skips


# --------------------------------------------------------------------------- #
# Layer 3: the flown corpus
# --------------------------------------------------------------------------- #
def _count_csv(path: Path, column: str) -> Counter:
    c: Counter = Counter()
    if not path.exists():
        return c
    with path.open(newline="") as fh:
        for row in csv.DictReader(fh):
            value = row.get(column)
            if value:
                c[value] += 1
    return c


def scan_corpus(roster: set[str]):
    scored: Counter = Counter()
    per_campaign = {"ArduPilot": Counter(), "PX4": Counter()}
    #: how calls naming an unregistered tool were disposed of
    unknown_disposal: Counter = Counter()
    audited = 0
    for label, dirs in (("ArduPilot", AP_DIRS), ("PX4", PX4_DIRS)):
        for d in dirs:
            run = LLM_RUNS / d
            tc = run / "tool_calls.csv"
            if tc.exists():
                with tc.open(newline="") as fh:
                    for row in csv.DictReader(fh):
                        if row["tool"] and row["tool"] not in roster:
                            unknown_disposal[f"{row.get('status', '')}/{row.get('client_side_rejection', '')}"] += 1
            c = _count_csv(tc, "tool")
            per_campaign[label] += c
            scored += c
            slice_path = run / "audit_slice.csv"
            if slice_path.exists():
                with slice_path.open(newline="") as fh:
                    audited += sum(1 for _ in csv.DictReader(fh))
    return scored, per_campaign, audited, unknown_disposal


def verify_corpus_parser():
    """Independent check of layer 3: re-count every scored trial's tool calls
    from the raw ``transcript.jsonl`` (the model's own emitted tool_calls) and
    compare against the run-level ``tool_calls.csv`` this script reads.

    Returns (trials_checked, mismatching_trials, calls_from_transcripts).
    """
    checked = 0
    mismatches: list[str] = []
    total = 0
    retries = 0
    for d in AP_DIRS + PX4_DIRS:
        run = LLM_RUNS / d
        csv_path = run / "tool_calls.csv"
        if not csv_path.exists():
            continue
        by_trial: dict[tuple[str, str], Counter] = defaultdict(Counter)
        with csv_path.open(newline="") as fh:
            for row in csv.DictReader(fh):
                by_trial[(row["mission_id"], row["trial"])][row["tool"]] += 1
        for transcript in sorted(run.glob("T*/trial_*/transcript.jsonl")):
            mission = transcript.parent.parent.name
            trial = transcript.parent.name.split("_")[-1]
            # A transcript can hold more than one attempt: when the harness
            # restarted the drone link and re-flew a trial, the aborted attempt
            # stays on disk ahead of the retained one, marked by the turn
            # counter resetting to 0.  The scored record counts the retained
            # attempt, so the check compares against that same segment.
            segments: list[Counter] = [Counter()]
            with transcript.open() as fh:
                for line in fh:
                    line = line.strip()
                    if not line:
                        continue
                    rec = json.loads(line)
                    if rec.get("turn_idx") == 0 and rec.get("role") == "system" and sum(segments[-1].values()):
                        segments.append(Counter())
                    if rec.get("role") == "assistant" and rec.get("tool_calls"):
                        for call in rec["tool_calls"]:
                            name = call.get("name") or call.get("tool")
                            if name:
                                segments[-1][name] += 1
            got = segments[-1]
            retries += len(segments) - 1
            checked += 1
            total += sum(got.values())
            if got != by_trial.get((mission, trial), Counter()):
                mismatches.append(f"{d}/{mission}/trial_{trial}")
    return checked, mismatches, total, retries


def scan_all_runs():
    """Every recorded harness run anywhere in the repo, scored or not."""
    everywhere: Counter = Counter()
    runs = 0
    for root in (LLM_RUNS, BENCH_RUNS, DOCS / "benchmark_runs"):
        if not root.exists():
            continue
        for path in root.rglob("tool_calls.csv"):
            runs += 1
            everywhere += _count_csv(path, "tool")
    return everywhere, runs


# --------------------------------------------------------------------------- #
# Test-suite sizes
# --------------------------------------------------------------------------- #
def collected(args: list[str]) -> int | None:
    try:
        out = subprocess.run(
            [sys.executable, "-m", "pytest", "--collect-only", "-q", *args],
            cwd=REPO,
            capture_output=True,
            text=True,
            timeout=600,
        ).stdout
    except Exception:
        return None
    m = re.search(r"(\d+) tests? collected", out) or re.search(r"collected (\d+) items?", out)
    return int(m.group(1)) if m else None


# --------------------------------------------------------------------------- #
def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--tex", type=Path, help="also write a LaTeX macro block here")
    ap.add_argument("--no-collect", action="store_true", help="skip pytest collection")
    ap.add_argument("--no-verify", action="store_true", help="skip the transcript re-count")
    args = ap.parse_args()

    corpus_note = cross_check_corpus_dirs()

    roster_map = roster_from_source()
    roster = set(roster_map)
    matrix_names = roster_from_matrix()
    totals = matrix_totals()

    only_matrix = sorted(matrix_names - roster)
    only_source = sorted(roster - matrix_names)

    direct, named, registry_modules = scan_unit_tests(roster)
    sitl_literal, sitl_loop, sitl_files, sitl_skips = scan_sitl_tests(roster)
    scored, per_campaign, audited, unknown_disposal = scan_corpus(roster)
    everywhere, all_runs = scan_all_runs()

    unit_any = set(direct) | set(named)
    sitl_any = set(sitl_literal) | set(sitl_loop)
    corpus_any = {t for t in scored if t in roster}
    anywhere_any = {t for t in everywhere if t in roster}

    # names a model emitted that are not registered tools: hallucinated calls
    hallucinated = Counter({t: n for t, n in scored.items() if t not in roster})

    all_three = unit_any & sitl_any & corpus_any
    no_layer = roster - unit_any - sitl_any - corpus_any
    # of those, the ones no model ever chose in ANY recorded run either: these
    # are the tools whose only evidence is the whole-registry invariants
    no_evidence = no_layer - anywhere_any
    flown_only = no_layer & anywhere_any
    tested_never_chosen = (unit_any | sitl_any) - corpus_any
    no_unit_no_sitl = roster - unit_any - sitl_any
    # tools whose ONLY SITL exercise is the adversarial (attack) suite
    adversarial_file = "tests/integration/test_adversarial_sitl.py"
    adversarial_only = {
        t for t in sitl_any if (sitl_literal.get(t, set()) | sitl_loop.get(t, set())) == {adversarial_file}
    }

    if args.no_verify:
        verify = (0, [], 0, 0)
    else:
        verify = verify_corpus_parser()

    n_unit = None if args.no_collect else collected(["tests/", "--ignore=tests/integration"])
    n_sitl = None if args.no_collect else collected(["tests/integration"])

    # ---------------------------------------------------------------- per-tool CSV
    DOCS.mkdir(exist_ok=True)
    csv_path = DOCS / "tool_test_coverage.csv"
    with csv_path.open("w", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(
            [
                "tool",
                "module",
                "unit_direct",
                "unit_named",
                "unit_registry_invariant",
                "sitl_literal",
                "sitl_loop_dispatched",
                "scored_corpus_calls",
                "ardupilot_calls",
                "px4_calls",
                "any_recorded_run_calls",
                "layers",
                "unit_test_files",
                "sitl_test_files",
            ]
        )
        for tool in sorted(roster):
            layers = sum(
                [
                    tool in unit_any,
                    tool in sitl_any,
                    tool in corpus_any,
                ]
            )
            w.writerow(
                [
                    tool,
                    roster_map[tool],
                    int(tool in direct),
                    int(tool in named),
                    1,  # every registered tool is covered by the registry invariants
                    int(tool in sitl_literal),
                    int(tool in sitl_loop),
                    scored.get(tool, 0),
                    per_campaign["ArduPilot"].get(tool, 0),
                    per_campaign["PX4"].get(tool, 0),
                    everywhere.get(tool, 0),
                    layers,
                    ";".join(sorted(direct.get(tool, set()) | named.get(tool, set()))),
                    ";".join(sorted(sitl_literal.get(tool, set()) | sitl_loop.get(tool, set()))),
                ]
            )

    # ---------------------------------------------------------------- summary
    def block(title, items):
        return f"### {title} ({len(items)})\n\n" + (
            "\n".join(f"- `{t}`" for t in sorted(items)) + "\n" if items else "_none_\n"
        )

    md = [
        "# Which tools were tested, and by what",
        "",
        f"Generated by `scripts/tool_test_coverage.py`. Corpus lists: {corpus_note}.",
        "",
        "## Roster",
        "",
        f"- Registered MCP tools (`@mcp.tool()` in `src/droneserver/`): **{len(roster)}**",
        f"- Tool names named by `docs/coverage_matrix.csv`: **{len(matrix_names)}**",
        f"- In the matrix but not registered: {only_matrix or 'none'}",
        f"- Registered but named by no matrix row: {only_source or 'none'}",
        f"- MavSDK client-side methods: **{totals['methods_client_side']}** "
        f"(implemented **{totals['implemented']}**, documented-N/A **{totals['documented_na']}**); "
        f"drone-side `*_server` methods excluded: {totals['methods_server_side']}",
        "",
        "## Layer sizes",
        "",
        f"- Unit tests collected: **{n_unit if n_unit is not None else 'not collected'}**",
        f"- SITL integration tests collected: **{n_sitl if n_sitl is not None else 'not collected'}**",
        f"- Whole-registry invariant unit modules: {len(registry_modules)} "
        f"({', '.join(f'`{m}`' for m in registry_modules)})",
        f"- SITL test modules scanned: {len(sitl_files)}",
        f"- Explicit `pytest.skip` sites in the SITL suite, each with a written "
        f"reason (a declared gap, never a silent pass): {len(sitl_skips)}",
        f"- Tools whose only SITL exercise is the adversarial suite: {len(adversarial_only)}",
        f"- Scored-corpus runs: {len(AP_DIRS)} ArduPilot + {len(PX4_DIRS)} PX4 directories",
        f"- Audited server-side calls in the scored corpus: **{audited:,}**",
        f"- Model-issued tool calls in the scored corpus: **{sum(scored.values()):,}**",
        f"- `tool_calls.csv` files scanned across every recorded run: {all_runs}",
        f"- Parser self-check: {verify[0]} scored trials re-counted from raw "
        f"`transcript.jsonl` ({verify[2]:,} model-emitted calls); "
        f"mismatching trials: **{len(verify[1])}**; "
        f"transcripts carrying a superseded earlier attempt: {verify[3]}"
        + (f" ({', '.join(verify[1][:5])})" if verify[1] else ""),
        "",
        "## Per-layer tool counts",
        "",
        f"| Layer | Tools covered | of {len(roster)} |",
        "|---|--:|--:|",
        f"| Unit suite, tool-specific (direct call or named assertion) | {len(unit_any)} | "
        f"{100 * len(unit_any) / len(roster):.1f}% |",
        f"| &nbsp;&nbsp;of which direct call of the tool function | {len(direct)} | "
        f"{100 * len(direct) / len(roster):.1f}% |",
        f"| Unit suite, whole-registry invariants | {len(roster)} | 100.0% |",
        f"| SITL integration suite | {len(sitl_any)} | {100 * len(sitl_any) / len(roster):.1f}% |",
        f"| Scored N=5 corpus (a model chose it) | {len(corpus_any)} | {100 * len(corpus_any) / len(roster):.1f}% |",
        f"| Any recorded run (scored or not) | {len(anywhere_any)} | {100 * len(anywhere_any) / len(roster):.1f}% |",
        f"| All three layers | {len(all_three)} | {100 * len(all_three) / len(roster):.1f}% |",
        "",
        "## Gap categories",
        "",
        block(
            "No tool-specific evidence at any of the three layers (covered only by the whole-registry invariants)",
            no_layer,
        ),
        block(
            "...of which no model ever chose them in ANY recorded run either (zero targeted evidence)",
            no_evidence,
        ),
        block("...of which a model did choose them outside the scored corpus", flown_only),
        block("No unit-specific and no SITL coverage (registry invariant only)", no_unit_no_sitl),
        block("Exercised in SITL only by the adversarial suite", adversarial_only),
        block("Tested (unit and/or SITL) but never chosen by any model in the scored corpus", tested_never_chosen),
        "### Tool names models emitted that are not registered tools "
        f"({len(hallucinated)} names, {sum(hallucinated.values())} calls; "
        f"disposal {dict(unknown_disposal)})\n",
    ]
    for t, n in hallucinated.most_common():
        md.append(f"- `{t}`: {n}")
    md.append("")
    md.append("## Most-invoked tools in the scored corpus\n")
    md.append("| tool | calls | ArduPilot | PX4 |")
    md.append("|---|--:|--:|--:|")
    for t, n in scored.most_common(15):
        md.append(f"| `{t}` | {n} | {per_campaign['ArduPilot'][t]} | {per_campaign['PX4'][t]} |")
    md.append("")

    text = "\n".join(md)
    (DOCS / "tool_test_coverage.md").write_text(text)
    print(text)

    # ---------------------------------------------------------------- LaTeX
    if args.tex:

        def pct(n):
            return f"{round(100 * n / len(roster))}"

        def esc(names):
            return ", ".join("\\texttt{" + n.replace("_", "\\_") + "}" for n in sorted(names))

        lines = [
            "% Generated by droneserver/scripts/tool_test_coverage.py -- do not hand-edit.",
            "% Regenerate: .venv/bin/python scripts/tool_test_coverage.py --tex <this file>",
            f"\\newcommand{{\\ttcTools}}{{{len(roster)}}}",
            f"\\newcommand{{\\ttcUnitTests}}{{{n_unit if n_unit is not None else '?'}}}",
            f"\\newcommand{{\\ttcSitlTests}}{{{n_sitl if n_sitl is not None else '?'}}}",
            f"\\newcommand{{\\ttcUnitAny}}{{{len(unit_any)}}}",
            f"\\newcommand{{\\ttcUnitDirect}}{{{len(direct)}}}",
            f"\\newcommand{{\\ttcSitl}}{{{len(sitl_any)}}}",
            f"\\newcommand{{\\ttcCorpus}}{{{len(corpus_any)}}}",
            f"\\newcommand{{\\ttcAnywhere}}{{{len(anywhere_any)}}}",
            f"\\newcommand{{\\ttcAllThree}}{{{len(all_three)}}}",
            f"\\newcommand{{\\ttcAnyLayer}}{{{len(unit_any | sitl_any | corpus_any)}}}",
            f"\\newcommand{{\\ttcMatrixNames}}{{{len(matrix_names)}}}",
            f"\\newcommand{{\\ttcSourceOnlyList}}{{{esc(only_source) if only_source else 'none'}}}",
            f"\\newcommand{{\\ttcSourceOnly}}{{{len(only_source)}}}",
            f"\\newcommand{{\\ttcNoLayer}}{{{len(no_layer)}}}",
            f"\\newcommand{{\\ttcNoEvidence}}{{{len(no_evidence)}}}",
            f"\\newcommand{{\\ttcNoEvidenceList}}{{{esc(no_evidence) if no_evidence else 'none'}}}",
            f"\\newcommand{{\\ttcFlownOnly}}{{{len(flown_only)}}}",
            f"\\newcommand{{\\ttcFlownOnlyList}}{{{esc(flown_only) if flown_only else 'none'}}}",
            f"\\newcommand{{\\ttcVerifyTrials}}{{{verify[0]}}}",
            f"\\newcommand{{\\ttcVerifyMismatches}}{{{len(verify[1])}}}",
            f"\\newcommand{{\\ttcRegistryOnly}}{{{len(no_unit_no_sitl)}}}",
            f"\\newcommand{{\\ttcTestedNeverChosen}}{{{len(tested_never_chosen)}}}",
            f"\\newcommand{{\\ttcScoredCalls}}{{{sum(scored.values()):,}}}".replace(",", "{,}"),
            f"\\newcommand{{\\ttcAuditedCalls}}{{{audited:,}}}".replace(",", "{,}"),
            f"\\newcommand{{\\ttcHallucinatedNames}}{{{len(hallucinated)}}}",
            f"\\newcommand{{\\ttcHallucinatedCalls}}{{{sum(hallucinated.values())}}}",
            f"\\newcommand{{\\ttcHallucinatedRejectedClientSide}}"
            f"{{{unknown_disposal.get('client_rejected/unknown_tool', 0)}}}",
            f"\\newcommand{{\\ttcHallucinatedList}}{{{esc(hallucinated)}}}",
            f"\\newcommand{{\\ttcNoLayerList}}{{{esc(no_layer) if no_layer else 'none'}}}",
            f"\\newcommand{{\\ttcRegistryOnlyList}}{{{esc(no_unit_no_sitl) if no_unit_no_sitl else 'none'}}}",
            f"\\newcommand{{\\ttcTestedNeverChosenList}}{{{esc(tested_never_chosen)}}}",
            f"\\newcommand{{\\ttcRegistryModules}}{{{len(registry_modules)}}}",
            f"\\newcommand{{\\ttcSitlModules}}{{{len(sitl_files)}}}",
            f"\\newcommand{{\\ttcSitlSkips}}{{{len(sitl_skips)}}}",
            f"\\newcommand{{\\ttcAdversarialOnly}}{{{len(adversarial_only)}}}",
            f"\\newcommand{{\\ttcAdversarialOnlyList}}{{{esc(adversarial_only) if adversarial_only else 'none'}}}",
            f"\\newcommand{{\\ttcApDirs}}{{{len(AP_DIRS)}}}",
            f"\\newcommand{{\\ttcPxDirs}}{{{len(PX4_DIRS)}}}",
            # percentages, so the manuscript never hand-types one
            f"\\newcommand{{\\ttcUnitAnyPct}}{{{pct(len(unit_any))}}}",
            f"\\newcommand{{\\ttcUnitDirectPct}}{{{pct(len(direct))}}}",
            f"\\newcommand{{\\ttcSitlPct}}{{{pct(len(sitl_any))}}}",
            f"\\newcommand{{\\ttcAdversarialOnlyPct}}{{{pct(len(adversarial_only))}}}",
            f"\\newcommand{{\\ttcCorpusPct}}{{{pct(len(corpus_any))}}}",
            f"\\newcommand{{\\ttcAnywherePct}}{{{pct(len(anywhere_any))}}}",
            f"\\newcommand{{\\ttcAllThreePct}}{{{pct(len(all_three))}}}",
            f"\\newcommand{{\\ttcAnyLayerPct}}{{{pct(len(unit_any | sitl_any | corpus_any))}}}",
            f"\\newcommand{{\\ttcNoLayerPct}}{{{pct(len(no_layer))}}}",
            f"\\newcommand{{\\ttcHallucinatedPct}}{{{100 * sum(hallucinated.values()) / sum(scored.values()):.2f}}}",
            f"\\newcommand{{\\ttcImplemented}}{{{totals['implemented']}}}",
            f"\\newcommand{{\\ttcClientMethods}}{{{totals['methods_client_side']}}}",
        ]
        args.tex.write_text("\n".join(lines) + "\n")
        print(f"\n[tex] wrote {args.tex}")

    print(f"\n[csv] wrote {csv_path}")
    # machine-readable side channel for any downstream checker
    (DOCS / "tool_test_coverage.json").write_text(
        json.dumps(
            {
                "tools": len(roster),
                "unit_any": sorted(unit_any),
                "unit_direct": sorted(direct),
                "sitl": sorted(sitl_any),
                "corpus": sorted(corpus_any),
                "anywhere": sorted(anywhere_any),
                "all_three": sorted(all_three),
                "no_layer": sorted(no_layer),
                "no_evidence": sorted(no_evidence),
                "flown_outside_scored_corpus_only": sorted(flown_only),
                "verify_trials": verify[0],
                "verify_mismatches": verify[1],
                "registry_only": sorted(no_unit_no_sitl),
                "tested_never_chosen": sorted(tested_never_chosen),
                "adversarial_only": sorted(adversarial_only),
                "sitl_skips": sitl_skips,
                "hallucinated": dict(hallucinated),
                "unknown_tool_disposal": dict(unknown_disposal),
                "audited_calls": audited,
                "scored_calls": sum(scored.values()),
                "unit_tests_collected": n_unit,
                "sitl_tests_collected": n_sitl,
            },
            indent=2,
            sort_keys=True,
        )
        + "\n"
    )


if __name__ == "__main__":
    main()
