# Contributing

Contributions are welcome. This is the software artifact behind a journal paper,
so the bar is that a change must not make a published claim untrue.

## Issues

Open issues at
<https://github.com/PeterJBurke/droneserver/issues>. For anything with a security
or safety-bypass angle, follow [`SECURITY.md`](SECURITY.md) instead — report it
privately.

A useful issue says which version or commit, whether the aircraft was SITL or
real, which autopilot and firmware, and what the audit log recorded for the call
in question.

## Pull requests

- Branch from, and target, the current `v-next` development branch — not `main`.
  `main` carries tagged releases. If you are unsure which branch that is, ask in
  the issue first.
- Keep the change focused. One defect or one capability per PR.
- Explain *why* in the commit message, not just what. The repository's history is
  used as a record of engineering decisions and reads as prose.

## Required checks

Everything below must pass locally before you open the PR; CI runs the same set
on every push.

```bash
uv sync
uv run ruff check .
uv run ruff format --check .
uv run mypy
uv run pytest                    # unit suite, no aircraft needed
```

- **ruff and mypy must be clean.** No new `# type: ignore` and no loosened mypy
  settings — the repository currently carries zero exemptions and that claim is
  made in the paper.
- **Tests are required** for any behaviour change. A new MCP tool additionally
  needs a criticality tier in `src/droneserver/safety/tiers.py`; the whole-registry
  invariant tests fail if it is missing, by design.
- Python 3.11 is the floor and is what mypy is pinned to. Do not use syntax or
  stdlib behaviour newer than that without moving the floor deliberately.

The SITL integration suite needs docker and takes roughly fifteen minutes:

```bash
uv run pytest -m "sitl and not longmission" tests/integration
```

Run it if you touched anything that talks to an aircraft. It is not run on every
push in CI; it runs nightly.

## Documentation

Generated docs are generated — do not hand-edit `docs/coverage_matrix.csv`,
`docs/coverage_summary.md`, `docs/tool_test_coverage.*`, or
`docs/adversarial_results.md`. Re-run the script that produces them
(`scripts/generate_coverage_matrix.py`, `scripts/tool_test_coverage.py`, or the
adversarial suite) and commit the result.
