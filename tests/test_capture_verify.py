"""Post-trial bundle verification: catch the silent capture failures.

Every capture defect this project has hit was silent - the harness exited 0 and
the trial directory looked full. These tests build a trial directory by hand,
break it one way at a time, and check that
:func:`droneserver.capture.verify.verify_bundle` says so. Each broken case is
one that actually happened (or one whose absence would have hidden it):

- a ``mavlink.tlog`` with only the vehicle's half of the link (blocker B-6: a
  MAVProxy ``--out`` forward never carries the server's commands);
- a ``telemetry.csv`` that is a header and nothing else (a MavSDK recorder that
  never connected);
- a flight that armed but retained no dataflash log (blocker B-3);
- an artifact written *after* the manifest was sealed, so nothing verifies it -
  which is what ``events.jsonl`` was until the build order was fixed.
"""

import json
from datetime import datetime, timedelta, timezone

import pytest

from droneserver.capture.manifest import write_manifest
from droneserver.capture.verify import verify_bundle

VEHICLE_SYSID = 1


def _write_mavlink(trial_dir, *, recv=5, sent=2):
    lines = []
    for i in range(recv):
        lines.append(
            {
                "ts": "2026-08-09T19:00:00+00:00",
                "t_rel_s": float(i),
                "direction": "recv",
                "msg_type": "GLOBAL_POSITION_INT",
                "sysid": VEHICLE_SYSID,
                "compid": 1,
                "seq": i,
                "fields": {},
            }
        )
    for i in range(sent):
        lines.append(
            {
                "ts": "2026-08-09T19:00:01+00:00",
                "t_rel_s": float(i),
                "direction": "sent",
                "msg_type": "COMMAND_LONG",
                "sysid": 255,
                "compid": 190,
                "seq": i,
                "fields": {"command": 22},
            }
        )
    (trial_dir / "mavlink.jsonl").write_text("\n".join(json.dumps(x) for x in lines) + "\n", encoding="utf-8")
    (trial_dir / "mavlink.tlog").write_bytes(b"\x00" * 64 * (recv + sent))


def _write_telemetry(trial_dir, rows=20, armed=True, step_s=1.0):
    header = "t_iso,t_rel_s,lat_deg,lon_deg,rel_alt_m,armed,in_air\n"
    body = "".join(
        f"2026-08-09T19:00:{i:02d}+00:00,{i * step_s:.1f},33.6,-117.8,{i}.0,{armed},{armed}\n" for i in range(rows)
    )
    (trial_dir / "telemetry.csv").write_text(header + body, encoding="utf-8")


def _write_events(trial_dir, rows=3):
    (trial_dir / "events.jsonl").write_text(
        "".join(
            json.dumps(
                {
                    "ts": "2026-08-09T19:00:00+00:00",
                    "t_rel_s": float(i),
                    "category": "command",
                    "detail": "takeoff allowed",
                    "source": "audit",
                    "call_id": None,
                }
            )
            + "\n"
            for i in range(rows)
        ),
        encoding="utf-8",
    )


def _write_audit_slice(trial_dir, rows=2):
    lines = ["ts,call_id,tool,tier,verdict,rule,latency_ms"]
    lines += [f"2026-08-09T19:00:0{i}+00:00,c{i},takeoff,critical,allowed,,120" for i in range(rows)]
    (trial_dir / "audit_slice.csv").write_text("\n".join(lines) + "\n", encoding="utf-8")


def _write_transcript(trial_dir):
    turns = [
        {"role": "system", "content": "you fly a drone", "turn_idx": 0},
        {"role": "user", "content": "take off to 20 m", "turn_idx": 1},
        {"role": "assistant", "content": "taking off", "turn_idx": 2},
        {"role": "tool", "tool_result": {"status": "success"}, "turn_idx": 3},
    ]
    (trial_dir / "transcript.jsonl").write_text("".join(json.dumps(t) + "\n" for t in turns), encoding="utf-8")


def complete_bundle(
    tmp_path,
    *,
    armed=True,
    dataflash=True,
    transcript=True,
    telemetry_rows=20,
    telemetry_step_s=1.0,
    trial_seconds=20.0,
    **mavlink,
):
    """A trial directory with everything Plan 19 §8 asks for."""
    trial_dir = tmp_path / "T1" / "trial_1"
    trial_dir.mkdir(parents=True)
    _write_mavlink(trial_dir, **mavlink)
    _write_telemetry(trial_dir, rows=telemetry_rows, armed=armed, step_s=telemetry_step_s)
    _write_audit_slice(trial_dir)
    _write_events(trial_dir)
    if transcript:
        _write_transcript(trial_dir)
    if dataflash:
        (trial_dir / "T1_t1.BIN").write_bytes(b"ArduPilot" * 1000)
    started = datetime(2026, 8, 9, 19, 0, 0, tzinfo=timezone.utc)
    write_manifest(
        trial_dir,
        {
            "run_id": "test",
            "mission_id": "T1",
            "trial_idx": 1,
            "started_ts": started.isoformat(),
            "ended_ts": (started + timedelta(seconds=trial_seconds)).isoformat(),
        },
    )
    return trial_dir


# --- the good case ---------------------------------------------------------


def test_a_complete_bundle_is_complete(tmp_path):
    check = verify_bundle(complete_bundle(tmp_path), require_transcript=True)
    assert check.complete, check.problems
    assert check.status == "complete"
    assert check.as_dict()["capture_status"] == "complete"


def test_a_trial_that_never_armed_needs_no_dataflash(tmp_path):
    """T7-T9 never take off, so the autopilot writes no log at all.

    Requiring one there would mean copying somebody else's flight.
    """
    trial_dir = complete_bundle(tmp_path, armed=False, dataflash=False)
    check = verify_bundle(trial_dir, require_transcript=True)
    assert check.complete, check.problems
    assert any("never armed" in c.detail for c in check.checks if c.name == "dataflash")


# --- the silent failures ---------------------------------------------------


def test_a_telemetry_only_tap_is_degraded(tmp_path):
    """Blocker B-6: every message from the vehicle, not one command."""
    trial_dir = complete_bundle(tmp_path, sent=0)
    check = verify_bundle(trial_dir, require_transcript=True)
    assert not check.complete
    assert any("one direction only" in p for p in check.problems)
    assert any("recorded no commands" in p for p in check.problems)


def test_a_tap_that_heard_only_the_server_is_degraded(tmp_path):
    trial_dir = complete_bundle(tmp_path, recv=0)
    check = verify_bundle(trial_dir)
    assert not check.complete
    assert any("nothing from the vehicle" in p for p in check.problems)


def test_an_empty_tlog_is_degraded(tmp_path):
    trial_dir = complete_bundle(tmp_path)
    (trial_dir / "mavlink.tlog").write_bytes(b"")
    check = verify_bundle(trial_dir)
    assert not check.complete
    assert any(p.startswith("mavlink.tlog: empty") for p in check.problems)


def test_a_telemetry_recorder_that_never_connected_is_degraded(tmp_path):
    """A header and no rows: the file exists, the recording does not."""
    trial_dir = complete_bundle(tmp_path)
    (trial_dir / "telemetry.csv").write_text("t_iso,t_rel_s,armed\n", encoding="utf-8")
    check = verify_bundle(trial_dir, min_telemetry_rows=10)
    assert not check.complete
    assert any("below the floor" in p for p in check.problems)


def test_a_two_second_mission_is_not_degraded_for_having_few_rows(tmp_path):
    """T7 reads a parameter and is over in seconds; 9 rows is all there is.

    The first version of this check used a flat floor of 10 and reported a
    perfectly healthy scripted T7 as degraded — which is how a warning becomes
    noise. The floor is now one row per second of trial, capped at the
    configured value.
    """
    trial_dir = complete_bundle(
        tmp_path, telemetry_rows=9, telemetry_step_s=0.2, trial_seconds=2.0, armed=False, dataflash=False
    )
    check = verify_bundle(trial_dir, min_telemetry_rows=10)
    assert check.complete, check.problems


def test_a_long_trial_with_a_handful_of_rows_is_still_degraded(tmp_path):
    trial_dir = complete_bundle(
        tmp_path, telemetry_rows=4, telemetry_step_s=0.1, trial_seconds=600.0, armed=False, dataflash=False
    )
    check = verify_bundle(trial_dir, min_telemetry_rows=10)
    assert not check.complete
    assert any("below the floor of 10 for a 600s trial" in p for p in check.problems)


def test_a_recorder_that_died_mid_trial_is_degraded(tmp_path):
    """The row count stays plausible; the coverage does not.

    600 rows is not a suspicious number for a ten-minute flight — but they all
    fall in the first minute, so the last nine minutes were never recorded.
    """
    trial_dir = complete_bundle(
        tmp_path, telemetry_rows=600, telemetry_step_s=0.1, trial_seconds=600.0, armed=False, dataflash=False
    )
    check = verify_bundle(trial_dir)
    assert not check.complete
    assert any("the recorder died mid-trial" in p for p in check.problems)
    assert any("stops 540s before the trial ends" in p for p in check.problems)


def test_a_recording_that_reaches_the_end_is_complete(tmp_path):
    trial_dir = complete_bundle(
        tmp_path, telemetry_rows=6000, telemetry_step_s=0.1, trial_seconds=600.0, armed=False, dataflash=False
    )
    check = verify_bundle(trial_dir)
    assert check.complete, check.problems
    detail = next(c.detail for c in check.checks if c.name == "telemetry.csv")
    assert "spanning 600s of a 600s trial" in detail


def test_a_flight_that_armed_without_a_dataflash_log_is_degraded(tmp_path):
    """Blocker B-3: the log directory is on the simulator's machine."""
    trial_dir = complete_bundle(tmp_path, armed=True, dataflash=False)
    check = verify_bundle(trial_dir)
    assert not check.complete
    assert any("the aircraft armed but no .BIN/.ulg was retained" in p for p in check.problems)


def test_a_missing_audit_slice_is_degraded_and_says_why(tmp_path):
    trial_dir = complete_bundle(tmp_path)
    (trial_dir / "audit_slice.csv").unlink()
    check = verify_bundle(trial_dir)
    assert not check.complete
    assert any("--audit-log" in p for p in check.problems)


def test_unparsable_events_are_degraded(tmp_path):
    trial_dir = complete_bundle(tmp_path)
    (trial_dir / "events.jsonl").write_text("{not json\n", encoding="utf-8")
    check = verify_bundle(trial_dir)
    assert not check.complete
    assert any("events.jsonl" in p for p in check.problems)


def test_a_missing_transcript_is_degraded_only_when_required(tmp_path):
    """The scripted suite has no model conversation; an LLM trial must have one."""
    trial_dir = complete_bundle(tmp_path, transcript=False)
    assert verify_bundle(trial_dir).complete
    assert not verify_bundle(trial_dir, require_transcript=True).complete


def test_an_artifact_written_after_the_manifest_is_degraded(tmp_path):
    """Nothing can verify a file the manifest never saw.

    This is the shape of the old build order, in which ``events.jsonl`` was
    derived after ``write_manifest`` and so appeared in no manifest at all.
    """
    trial_dir = complete_bundle(tmp_path)
    (trial_dir / "late_arrival.csv").write_text("a,b\n1,2\n", encoding="utf-8")
    check = verify_bundle(trial_dir)
    assert not check.complete
    assert any("does not list late_arrival.csv" in p for p in check.problems)


def test_an_artifact_that_grew_after_the_manifest_is_degraded(tmp_path):
    trial_dir = complete_bundle(tmp_path)
    with (trial_dir / "telemetry.csv").open("a", encoding="utf-8") as fh:
        fh.write("2026-08-09T19:01:00+00:00,60.0,33.6,-117.8,1.0,True,True\n")
    check = verify_bundle(trial_dir)
    assert not check.complete
    assert any("recorded size differs" in p for p in check.problems)


def test_a_missing_manifest_is_degraded(tmp_path):
    trial_dir = complete_bundle(tmp_path)
    (trial_dir / "manifest.json").unlink()
    check = verify_bundle(trial_dir)
    assert not check.complete
    assert any(p.startswith("manifest.json: missing") for p in check.problems)


def test_a_missing_trial_directory_is_reported_not_raised(tmp_path):
    check = verify_bundle(tmp_path / "nope" / "trial_1")
    assert not check.complete
    assert "trial directory: missing" in check.problems


def test_the_status_string_names_every_problem(tmp_path):
    trial_dir = complete_bundle(tmp_path, sent=0, dataflash=False)
    status = verify_bundle(trial_dir).status
    assert status.startswith("degraded[")
    assert "mavlink.jsonl" in status and "dataflash" in status


@pytest.mark.parametrize("required", ["mavlink.tlog", "mavlink.jsonl", "telemetry.csv", "events.jsonl"])
def test_every_required_artifact_is_actually_checked(tmp_path, required):
    trial_dir = complete_bundle(tmp_path)
    (trial_dir / required).unlink()
    check = verify_bundle(trial_dir)
    assert not check.complete
    assert any(p.startswith(f"{required}: ") for p in check.problems)


# --- the silent failures the first version of this verifier passed ----------
#
# Each of the five below was demonstrated against ``verify_bundle`` as it stood
# on 2026-08-09: every one of them reported ``complete``.


def _reseal(trial_dir, seconds=20.0):
    """Rewrite the manifest over whatever the test has just changed."""
    started = datetime(2026, 8, 9, 19, 0, 0, tzinfo=timezone.utc)
    write_manifest(
        trial_dir,
        {
            "started_ts": started.isoformat(),
            "ended_ts": (started + timedelta(seconds=seconds)).isoformat(),
        },
    )


def _append_mavlink(trial_dir, record):
    with (trial_dir / "mavlink.jsonl").open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(record) + "\n")


def _armed_heartbeat():
    """The vehicle's own heartbeat with MAV_MODE_FLAG_SAFETY_ARMED set."""
    return {
        "ts": "2026-08-09T19:00:02+00:00",
        "t_rel_s": 2.0,
        "direction": "recv",
        "msg_type": "HEARTBEAT",
        "sysid": VEHICLE_SYSID,
        "compid": 1,
        "seq": 99,
        "fields": {"base_mode": 0x81, "custom_mode": 4},
    }


def _gcs_heartbeat():
    return {
        "ts": "2026-08-09T19:00:01+00:00",
        "t_rel_s": 1.0,
        "direction": "sent",
        "msg_type": "HEARTBEAT",
        "sysid": 245,
        "compid": 190,
        "seq": 1,
        "fields": {},
    }


def test_a_recorder_that_never_connected_is_not_a_recording(tmp_path):
    """Evenly-spaced rows, every cell empty - and a real flight underneath.

    A MavSDK recorder whose ``connect`` never succeeds still runs its sampling
    timer, so the file has the right shape, the right row count and full
    coverage of the trial. It was passing. Worse, its empty ``armed`` column was
    read as "the aircraft never armed", which waived the dataflash requirement
    on a trial that flew - so a bundle with no telemetry AND no autopilot log
    was reported complete. This is the exact content of ``T6/trial_1`` in the
    canonical T1-T9 run: two rows, timestamps only.
    """
    trial_dir = complete_bundle(tmp_path, dataflash=False, telemetry_rows=0)
    header = "t_iso,t_rel_s,lat_deg,lon_deg,abs_alt_m,rel_alt_m,flight_mode,armed,in_air\n"
    body = "".join(f"2026-08-09T19:00:{i:02d}+00:00,{i}.0,,,,,,,\n" for i in range(20))
    (trial_dir / "telemetry.csv").write_text(header + body, encoding="utf-8")
    _append_mavlink(trial_dir, _armed_heartbeat())
    _reseal(trial_dir)

    check = verify_bundle(trial_dir)
    assert not check.complete
    assert any("not one carries any vehicle state" in p for p in check.problems)
    # …and the flight is still known to have happened, from the vehicle's own
    # heartbeats, so the missing dataflash log is reported too.
    assert any(p.startswith("dataflash: the aircraft armed") for p in check.problems)


def test_a_flight_is_recognised_from_the_heartbeats_when_the_telemetry_is_empty(tmp_path):
    """Arming is a fact about the aircraft, not about one recorder."""
    trial_dir = complete_bundle(tmp_path, armed=False, dataflash=False)
    _append_mavlink(trial_dir, _armed_heartbeat())
    _reseal(trial_dir)
    check = verify_bundle(trial_dir)
    assert any(p.startswith("dataflash: the aircraft armed") for p in check.problems)


def test_a_sampler_that_stalled_leaves_a_hole_the_row_count_cannot_see(tmp_path):
    """Ten rows spread over a twelve-minute trial used to be "complete".

    The row floor is capped at ten and the coverage check only asks that the
    *last* row is near the end, so a recording of one sample a minute satisfied
    both.
    """
    trial_dir = complete_bundle(tmp_path, telemetry_rows=10, telemetry_step_s=74.0, trial_seconds=740.0)
    check = verify_bundle(trial_dir)
    assert not check.complete
    assert any("hole in the recording" in p for p in check.problems)


def test_a_ten_hz_recording_is_not_reported_as_holey(tmp_path):
    """The real thing: 0.1 s between rows, worst-case gap ~0.2 s."""
    trial_dir = complete_bundle(tmp_path, telemetry_rows=200, telemetry_step_s=0.1, trial_seconds=20.0)
    assert verify_bundle(trial_dir).complete


def test_a_ground_station_side_of_pure_heartbeats_is_not_evidence_of_commands(tmp_path):
    """One heartbeat a second satisfied "both directions" on its own.

    A ground station heartbeats whether or not the tap is on a path that
    carries its commands, so ``sent > 0`` proves nothing. If the aircraft
    armed, the arm command crossed this wire and has to be in the file.
    """
    trial_dir = complete_bundle(tmp_path, sent=0)
    _append_mavlink(trial_dir, _gcs_heartbeat())
    _reseal(trial_dir)
    check = verify_bundle(trial_dir)
    assert not check.complete
    assert any("HEARTBEATs and nothing else" in p for p in check.problems)


def test_a_trial_that_never_armed_may_legitimately_send_no_commands(tmp_path):
    """T6 and T8: every tool call was refused, so nothing reached the wire."""
    trial_dir = complete_bundle(tmp_path, armed=False, dataflash=False, sent=0)
    _append_mavlink(trial_dir, _gcs_heartbeat())
    _reseal(trial_dir)
    check = verify_bundle(trial_dir)
    assert not any("HEARTBEATs and nothing else" in p for p in check.problems), check.problems


def test_an_artifact_that_vanished_after_the_manifest_is_degraded(tmp_path):
    """The manifest swears to a file that is not in the bundle.

    Only files the verifier knows by name were ever looked for, so an archive
    could lose a screenshot, a transcript or a dataflash log between hashing
    and shipping and still be reported complete.
    """
    trial_dir = complete_bundle(tmp_path)
    (trial_dir / "extra_screenshot.png").write_bytes(b"\x89PNG\r\n\x1a\n")
    _reseal(trial_dir)
    (trial_dir / "extra_screenshot.png").unlink()
    check = verify_bundle(trial_dir)
    assert not check.complete
    assert any("extra_screenshot.png, which is not in the bundle" in p for p in check.problems)


def test_an_artifact_rewritten_at_the_same_length_is_degraded(tmp_path):
    """Size agreement is not integrity: the sha256 has to be re-computed.

    A third party checking the downloaded archive is the whole audience for
    this check - a dataflash log corrupted in transit keeps its length.
    """
    trial_dir = complete_bundle(tmp_path)
    log = trial_dir / "T1_t1.BIN"
    log.write_bytes(b"\x00" * log.stat().st_size)
    check = verify_bundle(trial_dir)
    assert not check.complete
    assert any("sha256 does not match on T1_t1.BIN" in p for p in check.problems)
    # …and the same bundle passes when the caller explicitly opts out.
    assert verify_bundle(trial_dir, verify_hashes=False).complete


def test_the_manifest_check_says_whether_hashes_were_verified(tmp_path):
    """A check that was skipped must never read as a check that passed."""
    trial_dir = complete_bundle(tmp_path)
    verified = [c for c in verify_bundle(trial_dir).checks if c.name == "manifest.json"][0]
    skipped = [c for c in verify_bundle(trial_dir, verify_hashes=False).checks if c.name == "manifest.json"][0]
    assert "all sha256 verified" in verified.detail
    assert "sha256 not re-computed" in skipped.detail
