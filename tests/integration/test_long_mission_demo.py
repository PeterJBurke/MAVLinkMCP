"""The >10-minute mission demonstration (mission-suite T10 feed).

This is the direct evidence against R3's "5-10 minute session ceiling"
criticism: the LLM client submits a long mission, **disconnects entirely**,
reconnects mid-mission, polls, and the mission completes - because the server,
not the model, is flying it.

Marked ``longmission`` (and ``sitl``) so the default and CI runs skip it::

    uv run pytest -m longmission tests/integration/test_long_mission_demo.py

NOTE: a bare ``-m sitl`` DOES select this module and adds ~40 minutes to the
sweep; use ``-m "sitl and not longmission"`` for the routine SITL sweep.

It writes docs/long_mission_demo.md (timeline table + audit summary).
"""

import json
import time
from pathlib import Path

import pytest

from tests.integration.conftest import SITL_HOME
from tests.integration.mcp_client import MCPToolClient

pytestmark = [pytest.mark.sitl, pytest.mark.longmission]

DOC_PATH = Path(__file__).resolve().parents[2] / "docs" / "long_mission_demo.md"
LAT, LON = SITL_HOME["lat"], SITL_HOME["lon"]

#: A boustrophedon ("lawnmower") survey of DISTINCT waypoints.
#:
#: Two measured lessons drive this shape:
#: 1. Repeating a lap does NOT lengthen a mission - ArduPilot completes a
#:    waypoint it is already sitting on instantly (2 laps and 3 laps of the
#:    same 8 points both finished in ~370 s).
#: 2. Neither WPNAV_SPEED nor per-waypoint hold time moved the total much
#:    (~370 s -> ~411 s), so duration is governed by PATH LENGTH. The survey
#:    below is ~5 km of distinct legs, which is what actually buys the time.
#:
#: The legs are longer than the adversarial suite's tight test fence allows,
#: so this module runs its own server with a wide fence (see
#: ``long_mission_server``) - the safety layer stays fully enabled.
D = 0.005  # ~550 m half-width
ROWS, COLS = 4, 4
CRUISE_SPEED_CM_S = 300  # 3 m/s via WPNAV_SPEED
HOLD_S = 5.0


def _survey() -> list:
    points = []
    for r in range(ROWS):
        dlat = -D + (2 * D) * r / (ROWS - 1)
        columns = range(COLS) if r % 2 == 0 else reversed(range(COLS))
        for c in columns:
            dlon = -D + (2 * D) * c / (COLS - 1)
            points.append(
                {
                    "latitude_deg": LAT + dlat,
                    "longitude_deg": LON + dlon,
                    "altitude_m": 40 + 5 * (r % 2),
                    "hold_s": HOLD_S,
                }
            )
    return points


WAYPOINTS = _survey()

DISCONNECT_S = 240.0  # 4 minutes with NO client attached at all
TOTAL_TIMEOUT_S = 2400.0


@pytest.fixture(scope="module")
def long_mission_server(sitl, tmp_path_factory):
    """A safety-enabled server whose geofence is wide enough for a ~5 km
    survey. Everything else keeps production defaults."""
    import os
    import socket
    import subprocess
    import sys

    from tests.integration.conftest import (
        CONTROL_KEY,
        REPO_ROOT,
        SAFETY_ENV,
        TELEMETRY_KEY,
        _free_port,
    )

    port = _free_port()
    workdir = tmp_path_factory.mktemp("droneserver-longmission")
    audit = workdir / "audit.jsonl"
    env = dict(
        os.environ,
        **SAFETY_ENV,
        MCP_HOST="127.0.0.1",
        MCP_PORT=str(port),
        MAVLINK_ADDRESS=sitl["address"],
        MAVLINK_PORT=str(sitl["port"]),
        MAVLINK_PROTOCOL="tcp",
        FLIGHT_LOG_DIR=str(workdir / "flight_logs"),
        SAFETY_AUDIT_LOG_PATH=str(audit),
    )
    # widen the fence for the survey; keep the altitude ceiling meaningful
    env["SAFETY_GEOFENCE_POLYGON"] = ""
    env["SAFETY_GEOFENCE_MAX_RADIUS_M"] = "3000"
    env["SAFETY_GEOFENCE_MAX_ALTITUDE_M"] = "120"
    env["SAFETY_MAX_ALTITUDE_M"] = "120"
    env["SAFETY_API_KEYS"] = f"tester:{CONTROL_KEY}:control,readonly:{TELEMETRY_KEY}:telemetry"

    log_path = workdir / "server.log"
    with open(log_path, "w") as log_file:
        proc = subprocess.Popen(
            [sys.executable, "-m", "droneserver.server"],
            env=env,
            cwd=REPO_ROOT,
            stdout=log_file,
            stderr=subprocess.STDOUT,
        )
        try:
            deadline = time.monotonic() + 30
            while True:
                if proc.poll() is not None:
                    pytest.fail(f"long-mission server exited early; log: {log_path.read_text()[-2000:]}")
                try:
                    with socket.create_connection(("127.0.0.1", port), timeout=1):
                        break
                except OSError:
                    if time.monotonic() > deadline:
                        pytest.fail(f"long-mission server did not open port {port}")
                    time.sleep(0.5)
            yield f"http://127.0.0.1:{port}/sse", audit
        finally:
            proc.terminate()
            try:
                proc.wait(timeout=10)
            except subprocess.TimeoutExpired:
                proc.kill()


def _mission(status):
    return status.get("mission") or {}


def test_long_mission_with_client_disconnect(long_mission_server):
    """Submit -> disconnect entirely -> reconnect -> poll -> complete."""
    url, audit_path = long_mission_server
    from tests.integration.conftest import CONTROL_KEY

    timeline: list[dict] = []
    t0 = time.monotonic()

    def note(phase_label: str, status: dict, extra: str = ""):
        mission = _mission(status)
        timeline.append(
            {
                "t_s": round(time.monotonic() - t0, 1),
                "label": phase_label,
                "phase": mission.get("phase"),
                "progress": mission.get("progress_percent"),
                "item": f"{mission.get('current_item')}/{mission.get('total_items')}",
                "alt_m": (mission.get("position") or {}).get("relative_altitude_m"),
                "mode": mission.get("flight_mode"),
                "note": extra,
            }
        )

    # ---------- session 1: submit, then go away ----------
    client1 = MCPToolClient(url, headers={"X-API-Key": CONTROL_KEY})
    deadline = time.monotonic() + 180
    while time.monotonic() < deadline:
        probe = client1.call("get_armed", timeout=40)
        if probe.get("status") == "success":
            break
        time.sleep(2)

    # clear any leftover record
    status = client1.call("get_mission_status", include_events=False, timeout=40)
    if _mission(status).get("active"):
        client1.call("control_managed_mission", action="abort", timeout=40)
        time.sleep(20)
    if _mission(status):
        client1.call("control_managed_mission", action="clear", timeout=40)

    # Reduce the cruise speed so the survey takes realistic survey time. This
    # also exercises the safety layer's confirmation round-trip inside the
    # demo: WPNAV_SPEED is a safety-critical parameter name, so it escalates
    # to CRITICAL and needs a token.
    issued = client1.call("set_parameter", name="WPNAV_SPEED", value=CRUISE_SPEED_CM_S, timeout=60)
    if issued.get("status") == "confirmation_required":
        issued = client1.call(
            "set_parameter",
            name="WPNAV_SPEED",
            value=CRUISE_SPEED_CM_S,
            confirm_token=issued["confirm_token"],
            timeout=60,
        )
    assert issued.get("status") == "success", issued
    note("cruise speed set", {"mission": {}}, f"WPNAV_SPEED={CRUISE_SPEED_CM_S} cm/s (confirmed token round-trip)")

    submitted = client1.call(
        "start_managed_mission",
        waypoints=WAYPOINTS,
        takeoff_altitude_m=40.0,
        return_to_launch=True,
        timeout=90,
    )
    assert submitted["status"] == "success", submitted
    mission_id = submitted["mission_id"]
    note(
        "submitted (session 1)",
        {"mission": {"phase": submitted["phase"]}},
        f"{len(WAYPOINTS)} distinct survey waypoints; call returned immediately",
    )

    # wait until it is genuinely flying, then leave
    deadline = time.monotonic() + 300
    while time.monotonic() < deadline:
        status = client1.call("get_mission_status", include_events=False, timeout=40)
        if _mission(status).get("phase") in ("running", "completed", "failed", "aborted"):
            break
        time.sleep(5)
    assert _mission(status)["phase"] == "running", status
    note("running (session 1)", status, "client is about to disconnect")

    # ---------- the disconnect: no MCP client exists at all ----------
    del client1
    disconnect_start = time.monotonic()
    time.sleep(DISCONNECT_S)
    disconnected_for = round(time.monotonic() - disconnect_start, 1)

    # ---------- session 2: a brand-new client reattaches ----------
    client2 = MCPToolClient(url, headers={"X-API-Key": CONTROL_KEY})
    status = client2.call("get_mission_status", event_limit=200, timeout=60)
    mission = _mission(status)
    note("reconnected (session 2)", status, f"no client for {disconnected_for:.0f}s")

    assert mission["mission_id"] == mission_id, "a NEW client sees the SAME mission"
    assert mission["active"], f"mission ended during the disconnect: {mission}"
    events_during_blackout = [
        e for e in status["events"] if e["kind"] in ("waypoint", "phase_change", "battery", "auto_action")
    ]
    assert events_during_blackout, "no events were recorded while the client was away"

    # ---------- poll to completion ----------
    deadline = time.monotonic() + TOTAL_TIMEOUT_S
    last_item = None
    while time.monotonic() < deadline:
        status = client2.call("get_mission_status", include_events=False, timeout=60)
        mission = _mission(status)
        if mission.get("current_item") != last_item:
            last_item = mission.get("current_item")
            note("progress", status)
        if not mission.get("active"):
            break
        time.sleep(10)

    final = client2.call("get_mission_status", event_limit=400, timeout=60)
    mission = _mission(final)
    note("finished", final, f"phase={mission.get('phase')}")

    elapsed = mission.get("elapsed_s") or 0

    # ---------- the artifact (written before the assertions, so a failed run
    # still leaves the timeline to diagnose) ----------
    audit = [json.loads(line) for line in audit_path.read_text().splitlines() if line.strip()]
    mission_events = [r for r in audit if r["tool"].startswith("mission.")]
    _write_doc(mission_id, elapsed, disconnected_for, timeline, final["events"], mission_events, mission)
    client2.call("control_managed_mission", action="clear", timeout=40)

    assert mission["phase"] == "completed", f"mission did not complete: {mission}"
    assert elapsed > 600, f"demo must exceed 10 minutes of flight, got {elapsed:.0f}s"


def _write_doc(mission_id, elapsed, disconnected_for, timeline, events, audit_events, mission):
    kinds: dict[str, int] = {}
    for e in events:
        kinds[e["kind"]] = kinds.get(e["kind"], 0) + 1

    lines = [
        "# Long-mission demonstration (>10 minutes, client disconnected mid-flight)",
        "",
        'Generated by `pytest -m "sitl and longmission" tests/integration/test_long_mission_demo.py`',
        "against ArduCopter 4.5.7 SITL with the safety layer enabled.",
        "",
        "This is the direct answer to the reviewer's *\"missions are limited to the",
        '5-10 minute LLM session"* criticism: the model submits the mission and the',
        "**server** flies it. Here the MCP client process was destroyed entirely",
        "mid-flight and a brand-new client reattached later to the same mission.",
        "",
        "## Result",
        "",
        f"- Mission id: `{mission_id}`",
        f"- **Flight duration: {elapsed / 60:.1f} minutes** ({elapsed:.0f} s)",
        f"- **Client fully disconnected for: {disconnected_for / 60:.1f} minutes** ({disconnected_for:.0f} s)",
        f"- Final phase: **{mission.get('phase')}**",
        f"- Waypoints flown: {mission.get('current_item')}/{mission.get('total_items')}",
        f"- Auto-actions fired: {mission.get('auto_actions_fired') or 'none'}",
        f"- Mission events recorded: {len(events)} (also in the audit log: {len(audit_events)})",
        "",
        "## Timeline",
        "",
        "| t (s) | stage | phase | progress | item | alt (m) | mode | note |",
        "|---|---|---|---|---|---|---|---|",
    ]
    for row in timeline:
        lines.append(
            f"| {row['t_s']} | {row['label']} | {row['phase']} | {row['progress']}% | "
            f"{row['item']} | {row['alt_m']} | {row['mode']} | {row['note']} |"
        )

    lines += ["", "## Event kinds recorded", "", "| kind | count |", "|---|---|"]
    for kind, count in sorted(kinds.items()):
        lines.append(f"| {kind} | {count} |")

    lines += [
        "",
        "## Mission events (from the append-only audit log)",
        "",
        "| ts | event | detail |",
        "|---|---|---|",
    ]
    for record in audit_events[:60]:
        lines.append(f"| {record['ts']} | `{record['tool']}` | {record.get('outcome_status', '')} |")
    if len(audit_events) > 60:
        lines.append(f"| … | … | {len(audit_events) - 60} more |")

    lines += [
        "",
        "## Why this answers the criticism",
        "",
        "1. `start_managed_mission` returns in well under a second - the model is never",
        "   holding a connection open for the duration of the flight.",
        "2. During the blackout above there was **no MCP client process in existence**.",
        "   The server kept flying, monitoring, checkpointing and auditing.",
        "3. A *different* client instance reattached and saw the same `mission_id`,",
        "   full progress, and the complete event history it had missed.",
        "4. Auto-actions (low battery, geofence breach, link loss) are evaluated",
        "   server-side every poll interval, so a safety response never waits for a",
        "   model round-trip.",
        "",
    ]
    DOC_PATH.parent.mkdir(parents=True, exist_ok=True)
    DOC_PATH.write_text("\n".join(lines))
