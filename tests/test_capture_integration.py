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

import json

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

    def __init__(self, system_address, out_dir, rate_hz=10.0, t0=None):
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
