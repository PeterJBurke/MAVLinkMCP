"""Mission-suite runner: executes T1-T10 and captures per-run metrics.

This is the shared benchmark harness (Plan 01 Phase 5 item 3, also used by
Plans 04/08). It writes, per run:

- ``<run>/missions.csv``   one row per mission trial
- ``<run>/tool_calls.csv`` one row per tool call (client-side wall clock)
- ``<run>/summary.md``     human-readable summary
- ``<run>/audit_slice.csv``server-side audit rows, if an audit log is readable

Latency is reported from two independent clocks: the client's wall clock
(includes network + MCP framing) and the server's own audit ``latency_ms``
(the guard + tool). Reporting both is deliberate - the gap between them IS the
network cost, which is the interesting number when the drone is remote.
"""

import csv
import json
import statistics
import time
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path

from droneserver.benchmark.client import BenchmarkClient
from droneserver.benchmark.missions import (
    DEFAULT_CONTEXT,
    SUITE,
    SUITE_BY_ID,
    MissionResult,
    SkipMission,
)


def _utc() -> str:
    return datetime.now(timezone.utc).isoformat()


def run_suite(
    client: BenchmarkClient,
    mission_ids: list[str],
    trials: int,
    out_dir: Path,
    context: dict | None = None,
    audit_log: Path | None = None,
    include_slow: bool = False,
    capture=None,
) -> list[MissionResult]:
    """Run the mission suite.

    ``capture`` is an optional ``droneserver.benchmark.capture_session.CaptureConfig``.
    When it is ``None`` (the default) the runner behaves exactly as before: no
    per-trial directories, no recorders, and none of the capture imports (which
    would pull in pymavlink/mavsdk) are touched. When it is supplied, each trial
    additionally produces the Plan 19 §8 per-trial artifact set under
    ``<run>/<mission>/trial_<n>/`` while the run-level CSVs stay where they are.
    """
    ctx = {**DEFAULT_CONTEXT, **(context or {})}
    out_dir.mkdir(parents=True, exist_ok=True)

    # Lazy: only import the capture orchestration (and thus the capture package,
    # pymavlink, mavsdk) when capture is actually requested.
    TrialCapture = None
    run_id = out_dir.name
    if capture is not None:
        from droneserver.benchmark.capture_session import TrialCapture

    # Home altitude is needed to convert the above-sea-level arguments some
    # tools take. Retry: a server that has only just connected may not have a
    # home fix yet, and defaulting it to 0 silently commands the aircraft to
    # fly below ground at any field above sea level.
    for attempt in range(10):
        home_info = client.call("get_home_position", timeout=90)
        if home_info.get("status") == "success":
            ctx["home_amsl_m"] = home_info["home"]["absolute_altitude_m"]
            ctx["home"] = (home_info["home"]["latitude_deg"], home_info["home"]["longitude_deg"])
            ctx["home_amsl_resolved"] = True
            print(f"[{_utc()}] home: {ctx['home'][0]:.6f},{ctx['home'][1]:.6f} "
                  f"at {ctx['home_amsl_m']:.1f} m above sea level", flush=True)
            break
        time.sleep(3)
    else:
        raise RuntimeError(
            "could not read the drone's home position after 10 attempts; refusing to run the "
            "suite, because every above-sea-level altitude it computes would be wrong"
        )

    results: list[MissionResult] = []
    for mission_id in mission_ids:
        mission = SUITE_BY_ID[mission_id]
        if mission.slow and not include_slow:
            results.append(MissionResult(mission.mission_id, mission.name, True,
                                         "skipped (slow; pass --include-slow)", time.time(), 0.0,
                                         skipped=True))
            continue
        for trial in range(1, trials + 1):
            # Single shared t0 for the trial: the wall-clock trial-start time is
            # passed to MavlinkTap, TelemetryRecorder AND TranscriptWriter so all
            # per-trial artifacts share one t_rel_s origin.
            started = time.time()
            clock = time.perf_counter()
            label = f"{mission.mission_id} trial {trial}/{trials}"
            print(f"[{_utc()}] START {label}: {mission.name}", flush=True)

            # Start the recorders BEFORE the mission (capture off => no-op).
            trial_capture = None
            if TrialCapture is not None:
                trial_dir = out_dir / mission.mission_id / f"trial_{trial}"
                trial_capture = TrialCapture(capture, trial_dir, t0=started)
                trial_capture.start(mission=mission, context=ctx)

            try:
                try:
                    passed, reason, detail = mission.run(client, dict(ctx))
                    skipped = False
                except SkipMission as e:
                    passed, reason, detail, skipped = True, f"skipped ({e})", {}, True
                except Exception as e:  # a crashed mission is a failed mission
                    passed, reason, detail, skipped = False, f"harness error: {type(e).__name__}: {e}", {}, False
            finally:
                # Stop + flush recorders even if the mission crashed.
                if trial_capture is not None:
                    trial_capture.stop()

            ended = time.time()
            duration = time.perf_counter() - clock
            result = MissionResult(mission.mission_id, mission.name, passed, reason,
                                   started, round(duration, 1), skipped, detail)
            results.append(result)
            verdict = "SKIP" if skipped else ("PASS" if passed else "FAIL")
            print(f"[{_utc()}] {verdict} {label} in {duration:.0f}s - {reason}", flush=True)

            # Post-trial capture: dataflash, per-trial audit slice, events,
            # manifest, and the verification that says whether any of it is
            # real. The status is carried on the result so the caller can fail
            # a run whose flights were fine but whose evidence was not.
            if trial_capture is not None:
                audit_rows = _read_audit(audit_log, started, ended) if audit_log else []
                check = trial_capture.finalize(
                    run_id=run_id,
                    mission_id=mission.mission_id,
                    trial_idx=trial,
                    client=client,
                    context=ctx,
                    audit_rows=audit_rows,
                    started_ts=started,
                    ended_ts=ended,
                )
                result.capture_status = check.status

            # Between flights, make sure we left the vehicle safe.
            _settle(client)

    _write_outputs(client, results, out_dir, ctx, audit_log)
    return results


def _settle(client: BenchmarkClient) -> None:
    """Leave the vehicle disarmed between missions, whatever happened."""
    try:
        armed = client.call("get_armed", timeout=60)
        if armed.get("status") == "success" and armed.get("armed"):
            client.call("land", force=True, timeout=90)
            deadline = time.monotonic() + 180
            while time.monotonic() < deadline:
                state = client.call("get_armed", timeout=60)
                if state.get("status") == "success" and state.get("armed") is False:
                    break
                time.sleep(5)
    except Exception:
        pass


def _read_audit(audit_log: Path, window_start: float, window_end: float | None = None) -> list[dict]:
    if not audit_log or not audit_log.exists():
        return []
    rows = []
    for line in audit_log.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            record = json.loads(line)
        except json.JSONDecodeError:
            continue
        try:
            ts = datetime.fromisoformat(record["ts"]).timestamp()
        except Exception:
            continue
        if ts >= window_start and (window_end is None or ts <= window_end):
            rows.append(record)
    return rows


def _write_outputs(client: BenchmarkClient, results: list[MissionResult], out_dir: Path,
                   ctx: dict, audit_log: Path | None) -> None:
    with (out_dir / "missions.csv").open("w", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(["mission_id", "name", "verdict", "reason", "duration_s", "started_utc",
                    "capture_status", "detail"])
        for r in results:
            verdict = "SKIP" if r.skipped else ("PASS" if r.passed else "FAIL")
            w.writerow([r.mission_id, r.name, verdict, r.reason, r.duration_s,
                        datetime.fromtimestamp(r.started_at, timezone.utc).isoformat(),
                        r.capture_status,
                        json.dumps(r.detail, default=str)])

    with (out_dir / "tool_calls.csv").open("w", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(["tool", "started_utc", "client_wall_ms", "status", "rule", "confirmation_required"])
        for call in client.calls:
            w.writerow([call.tool,
                        datetime.fromtimestamp(call.started_at, timezone.utc).isoformat(),
                        round(call.wall_ms, 2), call.status, call.rule or "",
                        int(call.confirmation_required)])

    window_start = min((r.started_at for r in results), default=time.time())
    audit_rows = _read_audit(audit_log, window_start) if audit_log else []
    if audit_rows:
        fields = ["ts", "tool", "tier", "verdict", "rule", "latency_ms", "safety_ms",
                  "audit_write_ms", "client_id", "outcome_status"]
        with (out_dir / "audit_slice.csv").open("w", newline="") as fh:
            w = csv.writer(fh)
            w.writerow(fields)
            for record in audit_rows:
                w.writerow([record.get(f, "") for f in fields])

    _write_summary(client, results, out_dir, ctx, audit_rows)


def _write_summary(client: BenchmarkClient, results: list[MissionResult], out_dir: Path,
                   ctx: dict, audit_rows: list[dict]) -> None:
    ran = [r for r in results if not r.skipped]
    passed = [r for r in ran if r.passed]
    client_ms = [c.wall_ms for c in client.calls] or [0.0]
    server_ms = [r["latency_ms"] for r in audit_rows if isinstance(r.get("latency_ms"), (int, float))]
    interventions = [r for r in audit_rows if r.get("verdict") in ("rejected", "confirmation_required")]
    confirmations = sum(1 for c in client.calls if c.confirmation_required)

    lines = [
        "# Mission suite run",
        "",
        f"- Run at: {_utc()}",
        f"- Target: `{ctx.get('target_label', 'unknown')}`",
        f"- Client: `{ctx.get('client_label', 'unknown')}`",
        f"- Missions run: **{len(ran)}** ({len(results) - len(ran)} skipped)",
        f"- Passed: **{len(passed)}/{len(ran)}**",
        "",
        "| Mission | Task | Verdict | Duration (s) | Reason |",
        "|---|---|---|---|---|",
    ]
    for r in results:
        verdict = "SKIP" if r.skipped else ("PASS" if r.passed else "**FAIL**")
        lines.append(f"| {r.mission_id} | {r.name} | {verdict} | {r.duration_s} | {r.reason} |")

    lines += [
        "",
        "## Latency",
        "",
        "Two independent clocks. The client wall clock includes the network hop and MCP",
        "framing; the server's audit latency is the guard plus the tool itself. The gap",
        "between them is the network cost of putting the drone on the other side of a link.",
        "",
        "| Clock | Calls | Mean (ms) | Median (ms) | p95 (ms) | Max (ms) |",
        "|---|---|---|---|---|---|",
        _latency_row("client wall clock", client_ms),
    ]
    if server_ms:
        lines.append(_latency_row("server audit latency_ms", server_ms))
    captured = [r for r in results if r.capture_status]
    if captured:
        degraded = [r for r in captured if not r.capture_status.startswith("complete")]
        lines += [
            "",
            "## Capture (Plan 19 bundles)",
            "",
            "Checked against the files on disk, not the exit code: the recorders are fail-soft, "
            "so a run that captured nothing would still finish cleanly.",
            "",
            f"- Trials with capture on: **{len(captured)}**",
            f"- Bundles degraded: **{len(degraded)}**",
            "",
        ]
        lines += [f"- **{r.mission_id}**: {r.capture_status}" for r in degraded]
        if degraded:
            lines.append("")

    lines += [
        "",
        "## Safety",
        "",
        f"- Tool calls made: **{len(client.calls)}**",
        f"- Safety interventions (rejected / confirmation required): **{len(interventions)}**",
        f"- Confirmation round-trips the suite had to complete: **{confirmations}**",
        "",
    ]
    if interventions:
        lines += ["| Tool | Verdict | Rule |", "|---|---|---|"]
        seen: set[tuple] = set()
        for record in interventions:
            key = (record.get("tool"), record.get("verdict"), record.get("rule"))
            if key in seen:
                continue
            seen.add(key)
            lines.append(f"| {record.get('tool')} | {record.get('verdict')} | `{record.get('rule') or '-'}` |")
        lines.append("")

    (out_dir / "summary.md").write_text("\n".join(lines))


def _latency_row(label: str, values: list[float]) -> str:
    ordered = sorted(values)
    p95 = ordered[min(int(len(ordered) * 0.95), len(ordered) - 1)] if ordered else 0.0
    return (f"| {label} | {len(values)} | {statistics.mean(values):.1f} | "
            f"{statistics.median(values):.1f} | {p95:.1f} | {max(values):.1f} |")


def default_mission_ids() -> list[str]:
    return [m.mission_id for m in SUITE]


def results_to_dicts(results: list[MissionResult]) -> list[dict]:
    return [asdict(r) for r in results]
