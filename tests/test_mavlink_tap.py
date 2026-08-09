"""Unit tests for the passive MAVLink wire-tap logger (Plan 19 capture spec).

No live SITL required. Two layers:

1. Pure decode/serialize tests drive :func:`decode_message` / :func:`tlog_frame`
   with hand-constructed mavlink messages - deterministic, socket-free, always
   run.
2. A loopback test binds a real ``udpin`` tap and feeds it from a ``udpout``
   sender in the same process. If binding UDP sockets is not possible in the
   harness, that single test skips; the pure tests still cover the schema.
"""

import json
import socket
import time
from pathlib import Path

import pytest
from pymavlink import mavutil

from droneserver.capture.mavlink_tap import (
    JSONL_NAME,
    RECEIVED_MSG_TYPES,
    SENT_MSG_TYPES,
    TLOG_NAME,
    MavlinkTap,
    decode_message,
    direction_for,
    tlog_frame,
)


def _free_udp_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _make_mav(sysid: int, compid: int = 1) -> mavutil.mavlink.MAVLink:
    """A MAVLink protocol object stamped with a source system/component, used
    to build messages with correct src ids and running sequence numbers."""
    mav = mavutil.mavlink.MAVLink(None, srcSystem=sysid, srcComponent=compid)
    return mav


def _heartbeat(mav) -> "mavutil.mavlink.MAVLink_message":
    return mav.heartbeat_encode(
        mavutil.mavlink.MAV_TYPE_QUADROTOR,
        mavutil.mavlink.MAV_AUTOPILOT_ARDUPILOTMEGA,
        0,
        0,
        mavutil.mavlink.MAV_STATE_ACTIVE,
    )


def _command_long(mav) -> "mavutil.mavlink.MAVLink_message":
    return mav.command_long_encode(
        1,
        1,
        mavutil.mavlink.MAV_CMD_NAV_TAKEOFF,
        0,
        0,
        0,
        0,
        0,
        0,
        0,
        10.0,
    )


def _global_position_int(mav) -> "mavutil.mavlink.MAVLink_message":
    return mav.global_position_int_encode(
        12345,
        -353632620,
        1491652370,
        584090,
        10000,
        0,
        0,
        0,
        65535,
    )


def _stamp(msg, mav) -> "mavutil.mavlink.MAVLink_message":
    """Populate the src ids / seq on a freshly *_encode()'d message by packing
    it through its owning MAVLink object (encode leaves them at zero)."""
    msg.pack(mav)
    return msg


# --------------------------------------------------------------------------
# Pure decode / serialize (no socket) - always run
# --------------------------------------------------------------------------


def test_direction_heuristic():
    assert direction_for(1) == "recv"  # vehicle -> server
    assert direction_for(255) == "sent"  # GCS/commander -> vehicle
    assert direction_for(2) == "sent"
    # overridable vehicle sysid
    assert direction_for(7, vehicle_sysid=7) == "recv"
    assert direction_for(1, vehicle_sysid=7) == "sent"


def test_decode_message_schema_and_fields():
    mav = _make_mav(sysid=1, compid=1)
    msg = _stamp(_global_position_int(mav), mav)

    rec = decode_message(
        msg,
        wall_time=1_000_000.5,
        t0=1_000_000.0,
        t0_monotonic=500.0,
        mono_time=500.5,
        vehicle_sysid=1,
    )
    obj = json.loads(rec.to_json())

    assert set(obj) == {
        "ts",
        "t_rel_s",
        "direction",
        "msg_type",
        "sysid",
        "compid",
        "seq",
        "fields",
    }
    assert obj["msg_type"] == "GLOBAL_POSITION_INT"
    assert obj["direction"] == "recv"
    assert obj["sysid"] == 1
    assert obj["compid"] == 1
    assert obj["ts"].endswith("+00:00")  # UTC ISO-8601
    assert obj["t_rel_s"] == pytest.approx(0.5)  # from the monotonic clock
    assert "mavpackettype" not in obj["fields"]  # dropped per spec
    assert obj["fields"]["lat"] == -353632620  # decoded payload preserved


def test_decode_uses_wall_clock_when_no_monotonic():
    mav = _make_mav(sysid=255)
    msg = _stamp(_heartbeat(mav), mav)
    rec = decode_message(
        msg,
        wall_time=1_000_002.0,
        t0=1_000_000.0,
        t0_monotonic=0.0,
    )
    assert rec.t_rel_s == pytest.approx(2.0)
    assert rec.direction == "sent"


def test_tlog_frame_is_replayable(tmp_path: Path):
    mav = _make_mav(sysid=1)
    msg = _stamp(_heartbeat(mav), mav)
    frame = tlog_frame(msg, wall_time=1_700_000_000.0)

    tlog = tmp_path / "one.tlog"
    tlog.write_bytes(frame)
    assert tlog.stat().st_size > 8  # timestamp prefix + payload

    replay = mavutil.mavlink_connection(str(tlog))
    got = replay.recv_match(blocking=True)
    replay.close()
    assert got is not None
    assert got.get_type() == "HEARTBEAT"


def test_documented_type_lists_are_disjoint_and_populated():
    assert SENT_MSG_TYPES and RECEIVED_MSG_TYPES
    assert "COMMAND_LONG" in SENT_MSG_TYPES
    assert "HEARTBEAT" in RECEIVED_MSG_TYPES
    assert set(SENT_MSG_TYPES).isdisjoint(RECEIVED_MSG_TYPES)


# --------------------------------------------------------------------------
# Loopback: real udpin tap fed by a udpout sender in-process
# --------------------------------------------------------------------------


def _drain_until(predicate, timeout=3.0, interval=0.05):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(interval)
    return predicate()


def test_loopback_capture(tmp_path: Path):
    port = _free_udp_port()
    endpoint = f"udpin:127.0.0.1:{port}"

    tap = MavlinkTap(endpoint, tmp_path)
    try:
        tap.start()
    except OSError as e:
        pytest.skip(f"cannot bind UDP tap in this harness: {e}")

    assert tap.t0 is not None and tap.t0_monotonic is not None

    # Sender side: a udpout connection reaches the tap's udpin socket.
    sender = mavutil.mavlink_connection(f"udpout:127.0.0.1:{port}")
    try:
        # Vehicle-origin traffic (sysid 1) -> should be "recv".
        veh = _make_mav(sysid=1, compid=1)
        sender.mav.srcSystem = 1
        sender.mav.srcComponent = 1
        sender.mav.send(_heartbeat(veh))
        sender.mav.send(_global_position_int(veh))

        # Commander-origin traffic (sysid 255) -> should be "sent".
        gcs = _make_mav(sysid=255, compid=190)
        sender.mav.srcSystem = 255
        sender.mav.srcComponent = 190
        sender.mav.send(_command_long(gcs))

        assert _drain_until(lambda: tap.message_count >= 3), f"tap saw only {tap.message_count} msgs"
    finally:
        sender.close()
        tap.stop()

    # -- mavlink.jsonl -----------------------------------------------------
    jsonl = tmp_path / JSONL_NAME
    rows = [json.loads(line) for line in jsonl.read_text().splitlines() if line.strip()]
    by_type = {r["msg_type"]: r for r in rows}

    assert "HEARTBEAT" in by_type
    assert "GLOBAL_POSITION_INT" in by_type
    assert "COMMAND_LONG" in by_type

    assert by_type["HEARTBEAT"]["direction"] == "recv"
    assert by_type["HEARTBEAT"]["sysid"] == 1
    assert by_type["GLOBAL_POSITION_INT"]["direction"] == "recv"
    assert by_type["GLOBAL_POSITION_INT"]["fields"]["lat"] == -353632620

    assert by_type["COMMAND_LONG"]["direction"] == "sent"
    assert by_type["COMMAND_LONG"]["sysid"] == 255
    assert by_type["COMMAND_LONG"]["compid"] == 190
    assert by_type["COMMAND_LONG"]["fields"]["command"] == mavutil.mavlink.MAV_CMD_NAV_TAKEOFF

    for r in rows:
        assert "mavpackettype" not in r["fields"]
        assert r["ts"].endswith("+00:00")
        assert r["t_rel_s"] >= 0.0

    # -- mavlink.tlog: non-empty and re-readable by mavutil ----------------
    tlog = tmp_path / TLOG_NAME
    assert tlog.stat().st_size > 0

    replay = mavutil.mavlink_connection(str(tlog))
    seen = set()
    while True:
        m = replay.recv_match(blocking=False)
        if m is None:
            break
        if m.get_type() != "BAD_DATA":
            seen.add(m.get_type())
    replay.close()
    assert {"HEARTBEAT", "GLOBAL_POSITION_INT", "COMMAND_LONG"} <= seen


def test_stop_is_idempotent_and_files_closed(tmp_path: Path):
    port = _free_udp_port()
    tap = MavlinkTap(f"udpin:127.0.0.1:{port}", tmp_path)
    try:
        tap.start()
    except OSError as e:
        pytest.skip(f"cannot bind UDP tap in this harness: {e}")
    tap.stop()
    tap.stop()  # second call must not raise
    assert (tmp_path / JSONL_NAME).exists()
    assert (tmp_path / TLOG_NAME).exists()
