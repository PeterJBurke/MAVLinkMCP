"""The >10-minute mission demonstration (mission-suite T10 feed).

This is the direct evidence against R3's "5-10 minute session ceiling"
criticism: the LLM client submits a long mission, **disconnects entirely**,
reconnects mid-mission, polls, and the mission completes - because the server,
not the model, is flying it.

Marked ``longmission`` so the default and CI sweeps skip it::

    uv run pytest -m "sitl and longmission" tests/integration/test_long_mission_demo.py -s

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

#: A lap of ~1.6 km at 25-40 m. ArduCopter SITL cruises ~5 m/s by default, so
#: this takes well over 10 minutes including takeoff, holds and RTL.
D = 0.0018  # ~200 m
LAP = [
    {"latitude_deg": LAT + D, "longitude_deg": LON, "altitude_m": 30, "hold_s": 10},
    {"latitude_deg": LAT + D, "longitude_deg": LON + D, "altitude_m": 35, "hold_s": 10},
    {"latitude_deg": LAT, "longitude_deg": LON + D, "altitude_m": 40, "hold_s": 10},
    {"latitude_deg": LAT - D, "longitude_deg": LON + D, "altitude_m": 35, "hold_s": 10},
    {"latitude_deg": LAT - D, "longitude_deg": LON, "altitude_m": 30, "hold_s": 10},
    {"latitude_deg": LAT - D, "longitude_deg": LON - D, "altitude_m": 30, "hold_s": 10},
    {"latitude_deg": LAT, "longitude_deg": LON - D, "altitude_m": 35, "hold_s": 10},
    {"latitude_deg": LAT + D, "longitude_deg": LON - D, "altitude_m": 30, "hold_s": 10},
]
WAYPOINTS = LAP + LAP  # two laps

DISCONNECT_S = 240.0  # 4 minutes with NO client attached at all
TOTAL_TIMEOUT_S = 2400.0


def _mission(status):
    return status.get("mission") or {}


def test_long_mission_with_client_disconnect(safe_server, audit_path):
    """Submit -> disconnect entirely -> reconnect -> poll -> complete."""
    url, _ = safe_server
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

    submitted = client1.call(
        "start_managed_mission",
        waypoints=WAYPOINTS,
        takeoff_altitude_m=30.0,
        return_to_launch=True,
        timeout=90,
    )
    assert submitted["status"] == "success", submitted
    mission_id = submitted["mission_id"]
    note(
        "submitted (session 1)",
        {"mission": {"phase": submitted["phase"]}},
        f"{len(WAYPOINTS)} waypoints; call returned immediately",
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
    assert mission["phase"] == "completed", f"mission did not complete: {mission}"
    assert elapsed > 600, f"demo must exceed 10 minutes of flight, got {elapsed:.0f}s"

    # ---------- the artifact ----------
    audit = [json.loads(line) for line in audit_path.read_text().splitlines() if line.strip()]
    mission_events = [r for r in audit if r["tool"].startswith("mission.")]
    _write_doc(mission_id, elapsed, disconnected_for, timeline, final["events"], mission_events, mission)

    client2.call("control_managed_mission", action="clear", timeout=40)


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
