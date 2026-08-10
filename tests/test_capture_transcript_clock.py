"""The transcript has to say when each turn happened, not when it was filed.

The capture layer receives the model's whole conversation *after* the flight
(``TrialCapture.finalize`` runs once the mission is over), so a writer that
stamps ``time.time()`` on each line records the filing time, not the turn time.
Measured on the real bundle
``llm_runs/20260809T204849Z_capture_llm_smoke/T1/trial_1``: 36 of the 38 turns
of an 8m51s trial are stamped inside the same 35 ms, at ``t_rel_s`` 531.99 to
532.03 - and one second AFTER the ``ended_ts`` the same bundle's manifest
records. Every check passed; the file simply cannot be laid alongside the
telemetry, which is what Plan 19 §0 asks a transcript for.

These tests hold the reconstruction to what the harness actually measured.
"""

import json
from dataclasses import dataclass, field

from droneserver.benchmark.capture_session import CaptureConfig, TrialCapture
from droneserver.capture import TranscriptWriter

T0 = 1_770_000_000.0  # trial start, wall clock


@dataclass
class _Call:
    turn: int
    seq: int
    tool: str
    started_at: float
    wall_ms: float
    arguments: dict = field(default_factory=dict)
    status: str = "success"
    rule: str | None = None
    error: str | None = None
    confirmation_required: bool = False
    client_side_rejection: str | None = None
    result: dict = field(default_factory=dict)


@dataclass
class _Turn:
    index: int
    text: str = ""
    decision_latency_ms: float = 800.0
    input_tokens: int = 10
    cached_input_tokens: int = 0
    cache_write_tokens: int = 0
    output_tokens: int = 5
    reasoning_tokens: int = 0
    uncounted_reasoning_tokens: int = 0
    finish_reason: str = "stop"
    resolved_model: str = "test-model"
    served_by: str = ""
    quantization: str = ""


@dataclass
class _Run:
    turns: list
    calls: list
    started_at: float = T0
    duration_s: float = 530.0
    stop_reason: str = "model finished"


def _capture(tmp_path):
    capture = TrialCapture(CaptureConfig(), tmp_path, t0=T0)
    capture.transcript = TranscriptWriter(tmp_path, t0=T0)
    return capture


def _records(tmp_path):
    return [json.loads(line) for line in (tmp_path / "transcript.jsonl").read_text(encoding="utf-8").splitlines()]


def test_model_turns_are_spread_across_the_trial_not_stacked_at_the_end(tmp_path):
    """A ten-minute conversation must occupy ten minutes of the timeline."""
    run = _Run(
        turns=[_Turn(0, "taking off"), _Turn(1, "flying there"), _Turn(2, "done", decision_latency_ms=1500.0)],
        calls=[
            _Call(0, 0, "arm_drone", T0 + 12.0, 900.0),
            _Call(1, 0, "go_to_location", T0 + 300.0, 1200.0),
        ],
    )
    _capture(tmp_path)._write_model_turns(run)

    records = _records(tmp_path)
    times = [r["t_rel_s"] for r in records]
    assert times == sorted(times)
    assert max(times) - min(times) > 400, times

    by_role = {}
    for record in records:
        by_role.setdefault(record["role"], []).append(record)
    # The model turn is stamped when the harness began executing what it asked
    # for; the tool turn when the result came back.
    assert by_role["assistant"][0]["t_rel_s"] == 12.0
    assert by_role["assistant"][0]["ts_source"] == "observed"
    assert by_role["tool"][0]["t_rel_s"] == 12.9
    assert by_role["assistant"][1]["t_rel_s"] == 300.0
    assert by_role["tool"][1]["t_rel_s"] == 301.2


def test_a_turn_nothing_timestamped_is_labelled_reconstructed(tmp_path):
    """The final answer calls no tool, so its time is inferred - and says so."""
    run = _Run(
        turns=[_Turn(0, "taking off"), _Turn(1, "all done", decision_latency_ms=2000.0)],
        calls=[_Call(0, 0, "arm_drone", T0 + 12.0, 900.0)],
    )
    _capture(tmp_path)._write_model_turns(run)

    assistant = [r for r in _records(tmp_path) if r["role"] == "assistant"]
    assert assistant[1]["ts_source"] == "reconstructed"
    assert assistant[1]["t_rel_s"] == 14.9  # last known time + its own latency


def test_no_turn_is_stamped_after_the_trial_ended(tmp_path):
    """The shipped smoke bundle stamps every model turn after its ended_ts."""
    run = _Run(
        turns=[_Turn(0, "taking off"), _Turn(1, "done")],
        calls=[_Call(0, 0, "arm_drone", T0 + 12.0, 900.0)],
    )
    _capture(tmp_path)._write_model_turns(run)
    assert all(r["t_rel_s"] <= run.duration_s for r in _records(tmp_path))


def test_the_scripted_harness_stamps_each_call_when_it_returned(tmp_path):
    """Same defect, same fix, on the mission-suite side of the harness."""

    class _Client:
        calls = [
            _Call(0, 0, "takeoff", T0 + 5.0, 250.0),
            _Call(0, 1, "land", T0 + 40.0, 400.0),
        ]

    _capture(tmp_path)._write_tool_turns(_Client(), T0, T0 + 60.0)
    assert [r["t_rel_s"] for r in _records(tmp_path)] == [5.25, 40.4]
    assert all(r["ts_source"] == "observed" for r in _records(tmp_path))


def test_a_turn_written_as_it_happens_says_so(tmp_path):
    """The opening prompts are written live; that stamp is the event."""
    writer = TranscriptWriter(tmp_path, t0=T0)
    writer.turn("system", content="you fly a drone")
    assert _records(tmp_path)[0]["ts_source"] == "live"


# --- provenance the manifest must not invent -------------------------------


def test_the_simulator_host_is_never_guessed_from_a_loopback_endpoint(tmp_path):
    """ "Which machine flew this?" is not answerable from a local relay.

    The documented capture topology puts scripts/mavlink_relay.py in the link,
    so both recorder endpoints are 127.0.0.1 and neither names the simulator's
    machine. Guessing wrote "the simulator ran on this host" into the permanent
    record of any trial run without --sitl-host.
    """
    from droneserver.benchmark.capture_session import _host_of

    assert _host_of("tcp://127.0.0.1:5679") is None
    assert _host_of("udpin:127.0.0.1:14650") is None
    assert _host_of("udp://:14540") is None
    assert _host_of("udp://localhost:14540") is None
    # A real remote endpoint still names its machine.
    assert _host_of("tcp://100.80.7.20:5760") == "100.80.7.20"
    assert _host_of("udpin:llmuavsitl:14650") == "llmuavsitl"

    capture = TrialCapture(CaptureConfig(), tmp_path, t0=T0)
    meta = capture._manifest_meta("run", "T1", 1, {}, T0, T0 + 10.0)
    assert meta["sitl_host"] is None
    assert meta["clock_offset_ms"] is None
