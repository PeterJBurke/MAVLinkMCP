#!/usr/bin/env python3
"""Generate the authoritative MavSDK-Python coverage matrix for droneserver.

Introspects the *installed* ``mavsdk`` package (the exact version pinned by this
project's lockfile) to enumerate every plugin exposed on ``mavsdk.System`` and
every public method of each plugin.  Then AST-parses the server source (every
module under ``src/droneserver/``) to map every registered MCP tool to the
MavSDK plugin method(s) it actually calls.

Outputs (regenerable at any time):
  docs/coverage_matrix.csv   - one row per (plugin, method)
  docs/coverage_summary.md   - per-plugin counts + totals

Status values:
  implemented     - called by at least one v1 MCP tool (or by connection
                    infrastructure shared by all tools)
  missing         - client-side plugin method with no v1 tool calling it
  candidate-N/A   - method of a ``*_server`` plugin (these implement the
                    drone/companion-computer side of MAVLink, not GCS-side
                    vehicle control); flagged as probably-N/A for this project,
                    to be confirmed or implemented in Phase 2

The ``firmware_notes`` column (ArduPilot vs PX4 support caveats) is left blank
here on purpose: per-firmware support is to be recorded from actual SITL
testing, never guessed.

Usage:  .venv/bin/python scripts/generate_coverage_matrix.py
"""

from __future__ import annotations

import ast
import csv
import importlib.metadata
import inspect
import typing
from collections import defaultdict
from pathlib import Path

from mavsdk import System

REPO_ROOT = Path(__file__).resolve().parent.parent
PACKAGE_DIR = REPO_ROOT / "src" / "droneserver"
DOCS_DIR = REPO_ROOT / "docs"
CSV_PATH = DOCS_DIR / "coverage_matrix.csv"
SUMMARY_PATH = DOCS_DIR / "coverage_summary.md"

# Priority tiers from Plans/01-github-code-upgrade.md, Phase 2 table.
# Plugins already (partially) used by v1 are the de-facto "P0-existing" tier.
PLUGIN_PRIORITY = {
    "geofence": "P0",
    "offboard": "P0",
    "camera": "P1",
    "gimbal": "P1",
    "mission_raw": "P1",
    "log_files": "P1",
    "calibration": "P2",
    "manual_control": "P2",
    "follow_me": "P2",
    "failure": "P2",
    "ftp": "P2",
    "shell": "P2",
    "transponder": "P2",
    "tune": "P2",
}


def discover_plugins() -> dict[str, type]:
    """Map every System plugin property name -> plugin class, via type hints."""
    plugins: dict[str, type] = {}
    for name, prop in inspect.getmembers(System, lambda o: isinstance(o, property)):
        if name.startswith("_"):
            continue
        hints = typing.get_type_hints(prop.fget)
        cls = hints.get("return")
        if cls is None or not inspect.isclass(cls):
            raise RuntimeError(f"Cannot resolve plugin class for System.{name}")
        plugins[name] = cls
    return plugins


def plugin_methods(cls: type) -> list[dict]:
    """Public methods defined directly on a plugin class, with metadata."""
    rows = []
    for name, member in sorted(vars(cls).items()):
        if name.startswith("_") or not inspect.isfunction(member):
            continue
        if inspect.isasyncgenfunction(member):
            kind = "stream (async generator)"
        elif inspect.iscoroutinefunction(member):
            kind = "async call"
        else:
            kind = "sync call"
        sig = str(inspect.signature(member)).replace("(self, ", "(").replace("(self)", "()")
        doc = inspect.getdoc(member) or ""
        first_line = next((ln.strip() for ln in doc.splitlines() if ln.strip()), "")
        rows.append({"method": name, "kind": kind, "signature": sig, "doc": first_line})
    return rows


def map_v1_usage(plugin_names: set[str]) -> tuple[dict, dict, set[str]]:
    """AST-parse the server package; return (tool_usage, infra_usage, all_tools).

    tool_usage:  {(plugin, method): set(tool_name)} for @mcp.tool() functions
    infra_usage: {(plugin, method): set(func_name)} for module-level helpers
                 (connection management shared by all tools)
    all_tools:   every @mcp.tool()-decorated function name (including tools
                 that make no direct MavSDK call)
    """
    tool_usage: dict = defaultdict(set)
    infra_usage: dict = defaultdict(set)
    all_tools: set[str] = set()

    def is_mcp_tool(fn: ast.AsyncFunctionDef | ast.FunctionDef) -> bool:
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

    module_nodes = [node for path in sorted(PACKAGE_DIR.rglob("*.py")) for node in ast.parse(path.read_text()).body]
    for node in module_nodes:
        if not isinstance(node, (ast.AsyncFunctionDef, ast.FunctionDef)):
            continue
        if is_mcp_tool(node):
            bucket = tool_usage
            all_tools.add(node.name)
        else:
            bucket = infra_usage
        # Local aliases of a plugin, e.g. `telemetry = drone.telemetry`
        aliases: dict[str, str] = {}
        for stmt in ast.walk(node):
            if (
                isinstance(stmt, ast.Assign)
                and len(stmt.targets) == 1
                and isinstance(stmt.targets[0], ast.Name)
                and isinstance(stmt.value, ast.Attribute)
                and stmt.value.attr in plugin_names
            ):
                aliases[stmt.targets[0].id] = stmt.value.attr
        for call in ast.walk(node):
            if not (isinstance(call, ast.Call) and isinstance(call.func, ast.Attribute)):
                continue
            inner = call.func.value  # e.g. `drone.action` in `drone.action.arm()`
            if isinstance(inner, ast.Attribute) and inner.attr in plugin_names:
                bucket[(inner.attr, call.func.attr)].add(node.name)
            elif isinstance(inner, ast.Name) and inner.id in aliases:
                bucket[(aliases[inner.id], call.func.attr)].add(node.name)
    return tool_usage, infra_usage, all_tools


def main() -> None:
    mavsdk_version = importlib.metadata.version("mavsdk")
    plugins = discover_plugins()
    tool_usage, infra_usage, all_tools = map_v1_usage(set(plugins))

    DOCS_DIR.mkdir(exist_ok=True)
    rows = []
    for pname, cls in sorted(plugins.items()):
        is_server_side = pname.endswith("_server")
        for m in plugin_methods(cls):
            key = (pname, m["method"])
            tools = sorted(tool_usage.get(key, ()))
            infra = sorted(infra_usage.get(key, ()))
            implemented_in = ", ".join(tools + [f"[infra: {f}]" for f in infra])
            if tools or infra:
                status = "implemented"
            elif is_server_side:
                status = "candidate-N/A"
            else:
                status = "missing"
            rows.append(
                {
                    "plugin": pname,
                    "plugin_class": cls.__name__,
                    "method": m["method"],
                    "kind": m["kind"],
                    "signature": m["signature"],
                    "description": m["doc"],
                    "implemented_in_v1": implemented_in,
                    "status": status,
                    "priority": PLUGIN_PRIORITY.get(pname, ""),
                    "firmware_notes": "",  # fill from SITL testing only - never guess
                }
            )

    with CSV_PATH.open("w", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=list(rows[0].keys()))
        writer.writeheader()
        writer.writerows(rows)

    # ---- summary ----
    per_plugin: dict = defaultdict(lambda: {"total": 0, "implemented": 0, "missing": 0, "candidate-N/A": 0})
    for r in rows:
        per_plugin[r["plugin"]]["total"] += 1
        per_plugin[r["plugin"]][r["status"]] += 1

    tool_names = sorted(all_tools)
    tools_without_mavsdk = sorted(all_tools - {t for tools in tool_usage.values() for t in tools})
    total = len(rows)
    implemented = sum(1 for r in rows if r["status"] == "implemented")
    missing = sum(1 for r in rows if r["status"] == "missing")
    na = sum(1 for r in rows if r["status"] == "candidate-N/A")
    client_total = total - na

    lines = [
        "# MavSDK Coverage Summary (v1 baseline)",
        "",
        f"Generated by `scripts/generate_coverage_matrix.py` against installed "
        f"`mavsdk=={mavsdk_version}` and `src/droneserver/`.",
        "Full matrix: [`coverage_matrix.csv`](coverage_matrix.csv).",
        "",
        "## Totals",
        "",
        f"- MavSDK plugins exposed on `System`: **{len(plugins)}**",
        f"- Total plugin methods: **{total}**",
        f"  - client-side (excluding `*_server` plugins): **{client_total}**",
        f"  - `*_server` plugin methods (candidate-N/A, drone-side API): **{na}**",
        f"- Implemented by v1: **{implemented}** methods "
        f"({implemented}/{client_total} = {100 * implemented / client_total:.1f}% of client-side)",
        f"- Missing (client-side): **{missing}**",
        f"- v1 MCP tools registered (`@mcp.tool()` in `src/droneserver/`): **{len(tool_names)}**",
        "",
        "Paper Table 1 claimed ~40 of ~155 methods implemented; the real numbers "
        f"from introspection are {implemented} of {client_total} client-side "
        f"({total} including server-side plugins).",
        "",
        "## Per-plugin counts",
        "",
        "| Plugin | Priority (Plan 01 Phase 2) | Methods | Implemented | Missing | Candidate-N/A |",
        "|---|---|---|---|---|---|",
    ]
    for pname in sorted(per_plugin):
        c = per_plugin[pname]
        used = "in use (v1)" if c["implemented"] else PLUGIN_PRIORITY.get(pname, "P3")
        lines.append(
            f"| {pname} | {used} | {c['total']} | {c['implemented']} | {c['missing']} | {c['candidate-N/A']} |"
        )
    lines += [
        "",
        "## v1 MCP tools",
        "",
        f"{len(tool_names)} tools: " + ", ".join(f"`{t}`" for t in tool_names),
        "",
        f"Tools with no direct MavSDK call ({len(tools_without_mavsdk)}; "
        "e.g. deprecated stubs that only return an error message): "
        + (", ".join(f"`{t}`" for t in tools_without_mavsdk) or "none"),
        "",
        "## Notes",
        "",
        "- `firmware_notes` (ArduPilot vs PX4 caveats) is deliberately blank until "
        "verified in SITL; per-firmware support is recorded from testing, not guessed.",
        "- `candidate-N/A` marks `*_server` plugins, which implement the drone/"
        "companion side of MAVLink rather than GCS-side vehicle control; each will "
        "be confirmed implemented-or-N/A-with-reason during Phase 2.",
        "- Connection infrastructure (e.g. `core.connection_state`) is credited as "
        "`[infra: ...]` rather than to any single tool.",
        "",
    ]
    SUMMARY_PATH.write_text("\n".join(lines))

    print(f"mavsdk {mavsdk_version}: {len(plugins)} plugins, {total} methods")
    print(f"implemented {implemented} / client-side {client_total} (missing {missing}, candidate-N/A {na})")
    print(f"v1 tools: {len(tool_names)} (no direct MavSDK call: {tools_without_mavsdk})")
    print(f"wrote {CSV_PATH}\nwrote {SUMMARY_PATH}")


if __name__ == "__main__":
    main()
