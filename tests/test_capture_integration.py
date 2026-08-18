"""Integration test for wiring the Plan 19 capture layer into the mission suite.

Runs :func:`droneserver.benchmark.runner.run_suite` against a FAKE client and a
FAKE mission, with the two live recorders (``MavlinkTap`` / ``TelemetryRecorder``)
monkeypatched to no-op writers that just drop a small artifact in the trial dir.
No live SITL, no pymavlink, no mavsdk.

Two things are asserted:

1. With ``capture`` set, each trial gets ``<run>/<mission>/trial_<n>/`` containing
   ``manifest.json`` and ``events.jsonl`` (plus the fakes' ``mavlink.jsonl`` /
   ``telemetry.csv`` / ``transcript.jsonl``), and the manifest carries the §6
   provenance and hashes the artifacts.
2. Without ``capture`` the runner is byte-for-byte unchanged: only the run-level
   CSVs exist and NO per-trial directory is created.
"""

import contextlib
import json

import pytest

from droneserver.benchmark import runner
from droneserver.benchmark.client import CallRecord
from droneserver.benchmark.missions import Mission

# --- fakes ----------------------------------------------------------------


class FakeClient:
    """Minimal stand-in for BenchmarkClient: records CallRecords, no network."""

    def __init__(self):
        self.calls: list[CallRecord] = []

    def call(self, tool, timeout=120.0, **arguments):
        import time

        self.calls.append(CallRecord(tool, time.time(), 1.0, "success"))
        if tool == "get_home_position":
            return {
                "status": "success",
                "home": {"latitude_deg": 47.397, "longitude_deg": 8.545, "absolute_altitude_m": 488.0},
            }
        if tool == "get_armed":
            return {"status": "success", "armed": False}
        return {"status": "success"}


def _fake_mission_run(client, ctx):
    client.call("takeoff")  # one tool call inside the trial window
    return True, "ok", {"note": "fake"}


class _FakeTap:
    """No-op MavlinkTap: writes a tiny mavlink.jsonl so events has an input."""

    def __init__(self, endpoint, out_dir, t0=None, *, vehicle_sysid=1):
        from pathlib import Path

        self.out_dir = Path(out_dir)

    def start(self):
        self.out_dir.mkdir(parents=True, exist_ok=True)
        line = {
            "ts": "2026-08-08T00:00:01+00:00",
            "t_rel_s": 1.0,
            "direction": "recv",
            "msg_type": "HEARTBEAT",
            "fields": {"custom_mode": 0, "base_mode": 0},
        }
        (self.out_dir / "mavlink.jsonl").write_text(json.dumps(line) + "\n", encoding="utf-8")

    def stop(self):
        pass


class _FakeRecorder:
    """No-op async TelemetryRecorder: writes a tiny telemetry.csv."""

    def __init__(self, system_address, out_dir, rate_hz=10.0, t0=None, raw_source=None, allow_shared_bind=False):
        from pathlib import Path

        self.out_dir = Path(out_dir)

    async def start(self):
        self.out_dir.mkdir(parents=True, exist_ok=True)
        (self.out_dir / "telemetry.csv").write_text(
            "t_iso,t_rel_s,in_air\n2026-08-08T00:00:01+00:00,1.0,False\n", encoding="utf-8"
        )

    async def stop(self):
        pass


def _install_fakes(monkeypatch):
    import droneserver.benchmark.capture_session as cs

    monkeypatch.setattr(cs, "MavlinkTap", _FakeTap)
    monkeypatch.setattr(cs, "TelemetryRecorder", _FakeRecorder)


def _install_fake_mission(monkeypatch):
    fake = Mission("T1", "fake hover", False, _fake_mission_run)
    monkeypatch.setattr(runner, "SUITE_BY_ID", {"T1": fake})


# --- tests ----------------------------------------------------------------


def test_capture_enabled_writes_per_trial_artifacts(tmp_path, monkeypatch):
    _install_fakes(monkeypatch)
    _install_fake_mission(monkeypatch)
    from droneserver.benchmark.capture_session import CaptureConfig

    out_dir = tmp_path / "run1"
    cfg = CaptureConfig(
        model="claude-test",
        provider="anthropic",
        decoding={"temperature": 0},
        firmware="ArduCopter",
        firmware_version="4.5.7",
        sim_params={"frame": "quad"},
    )

    results = runner.run_suite(
        client=FakeClient(),
        mission_ids=["T1"],
        trials=2,
        out_dir=out_dir,
        context={"target_label": "fake"},
        audit_log=None,
        capture=cfg,
    )

    assert len(results) == 2

    # run-level CSVs still present, in the same place as before.
    assert (out_dir / "missions.csv").exists()
    assert (out_dir / "summary.md").exists()

    for trial in (1, 2):
        trial_dir = out_dir / "T1" / f"trial_{trial}"
        assert trial_dir.is_dir(), f"missing per-trial dir for trial {trial}"
        assert (trial_dir / "manifest.json").exists()
        assert (trial_dir / "events.jsonl").exists()
        assert (trial_dir / "transcript.jsonl").exists()
        # the fakes' artifacts
        assert (trial_dir / "mavlink.jsonl").exists()
        assert (trial_dir / "telemetry.csv").exists()

        manifest = json.loads((trial_dir / "manifest.json").read_text())
        trial_meta = manifest["trial"]
        assert trial_meta["run_id"] == "run1"
        assert trial_meta["mission_id"] == "T1"
        assert trial_meta["trial_idx"] == trial
        assert trial_meta["model"] == "claude-test"
        assert trial_meta["provider"] == "anthropic"
        assert trial_meta["firmware"] == "ArduCopter"
        assert trial_meta["firmware_version"] == "4.5.7"
        assert trial_meta["decoding"] == {"temperature": 0}
        assert trial_meta["sim_params"] == {"frame": "quad"}
        assert "started_ts" in trial_meta and "ended_ts" in trial_meta
        # manifest hashed real artifacts (mavlink.jsonl at least).
        names = {a["name"] for a in manifest["artifacts"]}
        assert "mavlink.jsonl" in names
        assert "manifest.json" not in names  # never hashes itself

        # transcript has the system + user prompt turns and the tool turn.
        turns = [json.loads(x) for x in (trial_dir / "transcript.jsonl").read_text().splitlines() if x.strip()]
        roles = [t["role"] for t in turns]
        assert roles[0] == "system"
        assert roles[1] == "user"
        assert "tool" in roles  # the takeoff call was recorded


def test_capture_disabled_is_unchanged(tmp_path, monkeypatch):
    _install_fake_mission(monkeypatch)

    out_dir = tmp_path / "run_nocap"
    results = runner.run_suite(
        client=FakeClient(),
        mission_ids=["T1"],
        trials=1,
        out_dir=out_dir,
        context={"target_label": "fake"},
        audit_log=None,
    )  # no capture=

    assert len(results) == 1
    # run-level outputs unchanged...
    assert (out_dir / "missions.csv").exists()
    assert (out_dir / "tool_calls.csv").exists()
    assert (out_dir / "summary.md").exists()
    # ...and NO per-trial directory was created.
    assert not (out_dir / "T1").exists()


# --- the shared capture loop ------------------------------------------------


def test_the_capture_loop_is_shared_by_every_trial_and_survives_them():
    """One event loop per process, not one per trial.

    ``grpc.aio`` - which MavSDK speaks over - starts a completion-queue poller
    bound to whichever event loop existed when the first channel was created,
    and posts every completion into it with ``call_soon_threadsafe`` for the
    life of the process. Close that loop at the end of trial 1 and every
    subsequent completion lands in a dead loop::

        RuntimeError: Event loop is closed
          grpc/_cython/_cygrpc/aio/completion_queue.pyx:170  _handle_events

    which is the flood the 2026-08-10 verification flight produced: 22
    tracebacks across eight missions. Closing the channel first does not help -
    the poller is refcounted globally and this grpc release does not retire it
    on ``Channel.close``. The loop must simply outlive the channels.
    """
    from droneserver.benchmark import capture_session as cs

    cs.shutdown_capture_loop()
    try:
        first = cs.capture_loop()
        assert first.alive
        for _ in range(3):  # three "trials"
            assert cs.capture_loop() is first, "a per-trial loop is the bug itself"
            assert first.run(_ping()) == "pong"
    finally:
        cs.shutdown_capture_loop()

    assert not first.alive
    # A closed loop is replaced rather than handed out dead.
    try:
        second = cs.capture_loop()
        assert second is not first and second.alive
    finally:
        cs.shutdown_capture_loop()


async def _ping():
    return "pong"


def test_closing_the_capture_loop_drains_what_is_still_scheduled():
    """A subscriber left suspended in ``async for`` is finalised while the loop
    still runs; otherwise it resurfaces as "Task was destroyed but it is
    pending" - eight of them across the same eight missions."""
    import asyncio

    from droneserver.benchmark import capture_session as cs

    cs.shutdown_capture_loop()
    loop = cs.capture_loop()
    state = {"cancelled": False, "closed": False}

    async def _stream():
        try:
            while True:
                yield 1
        finally:
            state["closed"] = True

    async def _subscriber():
        try:
            async for _ in _stream():
                await asyncio.sleep(0.01)
        except asyncio.CancelledError:
            state["cancelled"] = True
            raise

    async def _spawn():
        asyncio.ensure_future(_subscriber())
        await asyncio.sleep(0.05)

    loop.run(_spawn())
    cs.shutdown_capture_loop()

    assert state["cancelled"], "a task left running was not cancelled before the loop closed"
    assert state["closed"], "the async generator was never finalised (shutdown_asyncgens)"


def test_real_grpc_channels_across_trials_leave_the_log_clean():
    """End-to-end against the actual library that produced the flood.

    Three "trials", each opening and closing a real ``grpc.aio`` channel on the
    shared capture loop - the shape of a run, minus the drone. The gRPC
    completion-queue poller reports into the loop it adopted, so anything it
    could not deliver arrives at that loop's exception handler. Nothing may.
    """
    import asyncio

    grpc = pytest.importorskip("grpc")

    from droneserver.benchmark import capture_session as cs
    from droneserver.capture.telemetry_recorder import _close_channel

    cs.shutdown_capture_loop()
    loop = cs.capture_loop()
    errors: list[dict] = []
    loop._loop.call_soon_threadsafe(loop._loop.set_exception_handler, lambda _l, ctx: errors.append(ctx))

    async def _open():
        channel = grpc.aio.insecure_channel("127.0.0.1:59999")  # nothing listens; we never call
        with contextlib.suppress(Exception):
            await asyncio.wait_for(channel.channel_ready(), timeout=0.3)
        return channel

    try:
        for _ in range(3):
            channel = loop.run(_open(), timeout=30)
            loop.run(_close_channel(channel), timeout=30)
    finally:
        cs.shutdown_capture_loop()

    messages = [str(e.get("message", "")) + str(e.get("exception", "")) for e in errors]
    assert not any("Event loop is closed" in m for m in messages), messages
    assert not any("Task was destroyed" in m for m in messages), messages
