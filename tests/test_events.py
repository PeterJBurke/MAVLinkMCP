"""Unit tests for the post-hoc event deriver (droneserver.capture.events)."""

import json

from droneserver.capture.events import derive_events


def _write(path, text):
    path.write_text(text, encoding="utf-8")


def _read_events(path):
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


def test_derive_events_full_trial(tmp_path):
    # --- audit_slice.csv: one rejected + one allowed row ---
    _write(
        tmp_path / "audit_slice.csv",
        "ts,call_id,tool,tier,verdict,rule,outcome_status,model\n"
        "2026-08-06T21:00:00+00:00,c1,takeoff,normal,rejected,bounds.max_altitude,,gpt\n"
        "2026-08-06T21:00:05+00:00,c2,arm,normal,allowed,,success,gpt\n",
    )

    # --- mavlink.jsonl: two HEARTBEATs (custom_mode change + arm bit),
    #     a COMMAND_ACK, a geofence STATUSTEXT, a MISSION_ITEM_REACHED ---
    lines = [
        {
            "ts": "2026-08-06T21:00:01+00:00",
            "t_rel_s": 1.0,
            "direction": "in",
            "msg_type": "HEARTBEAT",
            "fields": {"custom_mode": 0, "base_mode": 0},
        },
        {
            "ts": "2026-08-06T21:00:06+00:00",
            "t_rel_s": 6.0,
            "direction": "in",
            "msg_type": "HEARTBEAT",
            "fields": {"custom_mode": 4, "base_mode": 0x81},
        },
        {
            "ts": "2026-08-06T21:00:07+00:00",
            "t_rel_s": 7.0,
            "direction": "in",
            "msg_type": "COMMAND_ACK",
            "fields": {"command": 22, "result": 0},
        },
        {
            "ts": "2026-08-06T21:00:08+00:00",
            "t_rel_s": 8.0,
            "direction": "in",
            "msg_type": "STATUSTEXT",
            "fields": {"severity": 4, "text": "Geofence breach detected"},
        },
        {
            "ts": "2026-08-06T21:00:09+00:00",
            "t_rel_s": 9.0,
            "direction": "in",
            "msg_type": "MISSION_ITEM_REACHED",
            "fields": {"seq": 3},
        },
    ]
    _write(tmp_path / "mavlink.jsonl", "\n".join(json.dumps(x) for x in lines) + "\n")

    # --- telemetry.csv: in_air flips False -> True -> False ---
    _write(
        tmp_path / "telemetry.csv",
        "t_iso,t_rel_s,flight_mode,armed,in_air\n"
        "2026-08-06T21:00:02+00:00,2.0,STABILIZE,false,false\n"
        "2026-08-06T21:00:10+00:00,10.0,GUIDED,true,true\n"
        "2026-08-06T21:00:20+00:00,20.0,GUIDED,true,false\n",
    )

    out = derive_events(tmp_path)
    assert out == tmp_path / "events.jsonl"
    events = _read_events(out)
    cats = [e["category"] for e in events]

    # Required categories present.
    assert "mode_change" in cats
    assert ("arm" in cats) or ("mode_change" in cats)  # arm-or-mode event
    assert "arm" in cats  # base_mode 0x81 has the SAFETY_ARMED bit set
    assert "rejection" in cats
    assert "command_ack" in cats
    assert "mission_item_reached" in cats
    assert "takeoff" in cats
    assert "land" in cats

    # Geofence STATUSTEXT is re-categorised (not left as plain statustext).
    geofence = [e for e in events if e["category"] == "geofence"]
    assert len(geofence) == 1
    assert "Geofence" in geofence[0]["detail"]
    assert "statustext" not in cats

    # Rejection carries call_id and the rule in detail.
    rej = [e for e in events if e["category"] == "rejection"][0]
    assert rej["call_id"] == "c1"
    assert "bounds.max_altitude" in rej["detail"]
    assert rej["source"] == "audit"

    # Every tool call produced a "command" event (2 audit rows).
    assert cats.count("command") == 2

    # mode_change resolved 4 -> GUIDED.
    mode = [e for e in events if e["category"] == "mode_change"][0]
    assert mode["detail"] == "GUIDED"
    assert mode["source"] == "mavlink"

    # Sorted by t_rel_s (nulls last), then ts. Audit rows (t_rel_s=None) sink
    # to the end; the timestamped rows are non-decreasing.
    non_null = [e["t_rel_s"] for e in events if e["t_rel_s"] is not None]
    assert non_null == sorted(non_null)
    null_positions = [i for i, e in enumerate(events) if e["t_rel_s"] is None]
    non_null_positions = [i for i, e in enumerate(events) if e["t_rel_s"] is not None]
    assert min(null_positions) > max(non_null_positions)


def test_missing_inputs_are_skipped(tmp_path):
    # Only telemetry present; no mavlink, no audit.
    _write(
        tmp_path / "telemetry.csv",
        "t_iso,t_rel_s,flight_mode,armed,in_air\n"
        "2026-08-06T21:00:00+00:00,0.0,STABILIZE,false,false\n"
        "2026-08-06T21:00:01+00:00,1.0,GUIDED,true,false\n",
    )
    out = derive_events(tmp_path)
    events = _read_events(out)
    cats = [e["category"] for e in events]
    # flight_mode fallback fires because mavlink is absent.
    assert "mode_change" in cats
    assert all(e["source"] == "telemetry" for e in events)


def test_empty_dir_yields_empty_file(tmp_path):
    out = derive_events(tmp_path)
    assert out.exists()
    assert _read_events(out) == []


def test_telemetry_gap_detected(tmp_path):
    _write(
        tmp_path / "telemetry.csv",
        "t_iso,t_rel_s,flight_mode,armed,in_air\n"
        "2026-08-06T21:00:00+00:00,0.0,GUIDED,true,true\n"
        "2026-08-06T21:01:00+00:00,60.0,GUIDED,true,true\n",
    )
    out = derive_events(tmp_path)
    cats = [e["category"] for e in _read_events(out)]
    assert "telemetry_gap" in cats


def test_ground_station_heartbeats_do_not_toggle_vehicle_state(tmp_path):
    """Regression: the tap sees a merged stream, and the GCS heartbeats too.

    Before the direction guard, one arm/takeoff/land trial derived 56 "arm"
    plus 56 "disarm" events - one per heartbeat - because the ground station's
    disarmed, mode-0 heartbeats were interleaved with the autopilot's.
    """
    vehicle_armed = {
        "ts": "2026-08-06T21:00:01+00:00",
        "t_rel_s": 1.0,
        "direction": "recv",
        "msg_type": "HEARTBEAT",
        "sysid": 1,
        "fields": {"custom_mode": 4, "base_mode": 0x81},
    }
    gcs = {
        "ts": "2026-08-06T21:00:02+00:00",
        "t_rel_s": 2.0,
        "direction": "sent",
        "msg_type": "HEARTBEAT",
        "sysid": 245,
        "fields": {"custom_mode": 0, "base_mode": 0},
    }
    lines = []
    for i in range(10):  # ten alternating heartbeats from each side
        lines.append({**vehicle_armed, "t_rel_s": 1.0 + i})
        lines.append({**gcs, "t_rel_s": 1.5 + i})
    _write(tmp_path / "mavlink.jsonl", "\n".join(json.dumps(x) for x in lines) + "\n")

    events = _read_events(derive_events(tmp_path))
    cats = [e["category"] for e in events]
    assert cats.count("arm") <= 1, f"one arming, not one per heartbeat: {cats}"
    assert cats.count("disarm") == 0, f"the vehicle never disarmed: {cats}"
    assert cats.count("mode_change") == 0, "the vehicle never changed mode"


def test_streaming_home_position_reports_one_event(tmp_path):
    """HOME_POSITION streams at 1 Hz once requested; home_set is an event."""
    lines = [
        {
            "ts": "2026-08-06T21:00:0%d+00:00" % i,
            "t_rel_s": float(i),
            "direction": "recv",
            "msg_type": "HOME_POSITION",
            "fields": {"latitude": 336458611, "longitude": -1178427500},
        }
        for i in range(9)
    ]
    lines.append(
        {
            "ts": "2026-08-06T21:00:10+00:00",
            "t_rel_s": 10.0,
            "direction": "recv",
            "msg_type": "HOME_POSITION",
            "fields": {"latitude": 336459999, "longitude": -1178427500},
        }
    )
    _write(tmp_path / "mavlink.jsonl", "\n".join(json.dumps(x) for x in lines) + "\n")

    events = _read_events(derive_events(tmp_path))
    home = [e for e in events if e["category"] == "home_set"]
    assert len(home) == 2, f"one per distinct home, not one per message: {len(home)}"
