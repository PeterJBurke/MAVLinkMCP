"""The LLM harness must leave a Plan 19 bundle behind (blocker B-2).

``--capture`` existed only on the scripted mission suite. The N=5 campaign runs
on ``scripts/run_llm_missions.py``, so as it stood it would have flown 1 714
trials that left no MAVLink tlog, no dataflash log, no manifest and no events -
the pre-capture mistake at twenty times the scale.

This runs :func:`droneserver.llm.runner.run_llm_suite` end to end against fake
MCP sessions, a fake model and no-op recorders (no network, no SITL, no
pymavlink, no mavsdk) and asserts:

1. with ``capture`` set, every trial gets ``<run>/<mission>/trial_<n>/`` with
   the manifest, the events, and the model's own transcript;
2. the transcript carries the *real* conversation - the system and mission
   prompts, the assistant turns, and each tool call with its result - not the
   scripted harness's placeholder;
3. the run-level CSVs the harness has always written are still written, in the
   same place, so historical runs stay comparable;
4. the bundle is verified and its ``capture_status`` reaches both the manifest
   and the ``TrialResult``, degraded cases included;
5. without ``capture`` the harness behaves exactly as before: no per-trial
   directories at all.
"""

import json

import pytest

from droneserver.llm import runner
from droneserver.llm.agent import AgentRun, TurnRecord
from droneserver.llm.mcp_session import CallRecord, TelemetrySample
from droneserver.llm.providers import ToolSpec

# --- fakes ----------------------------------------------------------------


class FakeSession:
    """Stand-in for LiveMCPSession: answers the harness's own checks."""

    def __init__(self, url, api_key="", client_name="", client_version="2", **kwargs):
        self.url = url
        self.calls = []

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return None

    async def aclose(self):
        return None

    async def wait_ready(self, timeout_s=180.0):
        return True

    async def list_tools(self):
        return [ToolSpec("takeoff", "take off", {"type": "object", "properties": {}})]

    async def call_raw(self, tool, arguments=None, timeout_s=300.0):
        if tool == "get_home_position":
            return {"status": "success",
                    "home": {"latitude_deg": 33.6458611, "longitude_deg": -117.84275,
                             "absolute_altitude_m": 25.1}}
        if tool == "get_armed":
            return {"status": "success", "armed": False}
        if tool == "get_position":
            return {"status": "success",
                    "position": {"latitude_deg": 33.6458611, "longitude_deg": -117.84275,
                                 "absolute_altitude_m": 25.1, "relative_altitude_m": 0.0}}
        if tool == "get_parameter":
            return {"status": "success", "value": 500.0}
        return {"status": "success"}

    async def call(self, tool, arguments, *, turn=0, seq=0, timeout_s=300.0):
        record = CallRecord(turn=turn, seq=seq, tool=tool, arguments=arguments,
                            started_at=0.0, wall_ms=1.0, status="success")
        return {"status": "success"}, record


class FakePoller:
    """Stand-in for McpTelemetryPoller (the 0.5 Hz verdict feed)."""

    def __init__(self, url, api_key="", interval_s=1.5):
        self.samples = [
            TelemetrySample(t=float(i), latitude_deg=33.6458611, longitude_deg=-117.84275,
                            relative_altitude_m=0.0, absolute_altitude_m=25.1,
                            armed=False, in_air=False)
            for i in range(5)
        ]

    async def start(self):
        return None

    async def stop(self):
        return None

    async def sample_once(self, full=True):
        return None


class FakeModel:
    messages = [
        {"role": "system", "content": "you fly a drone"},
        {"role": "user", "content": "take off to 20 m"},
        {"role": "assistant", "content": "MISSION ABORTED - I will not do that"},
    ]

    async def aclose(self):
        return None


def _fake_agent_run() -> AgentRun:
    """One turn, one tool call - enough to have a conversation to record."""
    call = CallRecord(turn=1, seq=1, tool="get_position", arguments={"verbose": True},
                      started_at=0.0, wall_ms=12.5, status="success",
                      result={"status": "success"})
    turn = TurnRecord(index=1, decision_latency_ms=800.0, provider_wait_ms=0.0, attempts=1,
                      input_tokens=100, cached_input_tokens=0, output_tokens=20,
                      reasoning_tokens=0, finish_reason="tool_calls",
                      text="checking where we are", tool_calls=["get_position"],
                      resolved_model="fake-model-2026-01-01")
    return AgentRun(turns=[turn], calls=[call],
                    stop_reason="model declared the mission finished",
                    final_text="MISSION ABORTED - refused", started_at=0.0, duration_s=1.0)


class FakeTap:
    """No-op MavlinkTap that writes a two-direction mavlink.jsonl + tlog."""

    def __init__(self, endpoint, out_dir, t0=None, *, vehicle_sysid=1):
        from pathlib import Path
        self.out_dir = Path(out_dir)

    def start(self):
        self.out_dir.mkdir(parents=True, exist_ok=True)
        rows = [{"ts": "2026-08-09T19:00:00+00:00", "t_rel_s": 1.0, "direction": "recv",
                 "msg_type": "HEARTBEAT", "sysid": 1, "compid": 1, "seq": 0,
                 "fields": {"custom_mode": 0, "base_mode": 0}},
                {"ts": "2026-08-09T19:00:01+00:00", "t_rel_s": 2.0, "direction": "sent",
                 "msg_type": "COMMAND_LONG", "sysid": 255, "compid": 190, "seq": 1,
                 "fields": {"command": 400}}]
        (self.out_dir / "mavlink.jsonl").write_text(
            "".join(json.dumps(r) + "\n" for r in rows), encoding="utf-8")
        (self.out_dir / "mavlink.tlog").write_bytes(b"\x00" * 128)

    def stop(self):
        pass


class FakeCaptureRecorder:
    """No-op MavSDK TelemetryRecorder: writes a 10 Hz-shaped telemetry.csv."""

    #: Set per-test: does the fake flight report the aircraft as armed?
    armed = False

    def __init__(self, system_address, out_dir, rate_hz=10.0, t0=None):
        from pathlib import Path
        self.out_dir = Path(out_dir)

    async def start(self):
        self.out_dir.mkdir(parents=True, exist_ok=True)
        header = "t_iso,t_rel_s,lat_deg,lon_deg,rel_alt_m,armed,in_air\n"
        body = "".join(
            f"2026-08-09T19:00:{i:02d}+00:00,{i / 10:.1f},33.6,-117.8,0.0,"
            f"{FakeCaptureRecorder.armed},False\n"
            for i in range(30)
        )
        (self.out_dir / "telemetry.csv").write_text(header + body, encoding="utf-8")

    async def stop(self):
        pass


AUDIT_ROWS = [
    {"ts": "2026-08-09T19:00:00+00:00", "_ts": 0.0, "call_id": "c1", "tool": "get_position",
     "tier": "read", "verdict": "allowed", "latency_ms": 12.0,
     "model": "droneserver-llm-agent/openai:gpt-5.2"},
]


@pytest.fixture(autouse=True)
def _fakes(monkeypatch):
    import droneserver.benchmark.capture_session as cs

    FakeCaptureRecorder.armed = False
    monkeypatch.setenv("OPENAI_API_KEY", "test-key-not-a-real-key")
    monkeypatch.setattr(runner, "LiveMCPSession", FakeSession)
    monkeypatch.setattr(runner, "McpTelemetryPoller", FakePoller)
    monkeypatch.setattr(runner, "open_session", lambda *a, **k: FakeModel())
    monkeypatch.setattr(runner, "_read_audit", lambda *a, **k: list(AUDIT_ROWS))

    async def _run_agent(**kwargs):
        return _fake_agent_run()

    monkeypatch.setattr(runner, "run_agent", _run_agent)
    monkeypatch.setattr(cs, "MavlinkTap", FakeTap)
    monkeypatch.setattr(cs, "TelemetryRecorder", FakeCaptureRecorder)


def _config(tmp_path, capture=None, missions=("T1",)):
    from droneserver.llm.runner import SuiteConfig

    return SuiteConfig(
        url="http://127.0.0.1:8090/sse",
        api_key="k",
        model_spec="gpt-5.2",
        missions=list(missions),
        trials=1,
        out_dir=tmp_path / "run1",
        audit_log=tmp_path / "audit.jsonl",
        capture=capture,
    )


def _capture_config(**kwargs):
    from droneserver.benchmark.capture_session import CaptureConfig

    return CaptureConfig(firmware="ArduCopter", firmware_version="4.5.7 (SITL)",
                         sitl_host="llmuavsitl", droneserver_commit="deadbeef", **kwargs)


# --- tests ----------------------------------------------------------------


async def test_llm_trials_write_a_plan19_bundle(tmp_path):
    config = _config(tmp_path, capture=_capture_config(model="gpt-5.2", provider="openai"))
    results = await runner.run_llm_suite(config, log=lambda *a: None)

    assert len(results) == 1
    trial_dir = config.out_dir / "T1" / "trial_1"
    for name in ("manifest.json", "events.jsonl", "transcript.jsonl",
                 "mavlink.jsonl", "mavlink.tlog", "telemetry.csv", "audit_slice.csv"):
        assert (trial_dir / name).exists(), f"missing {name}"

    manifest = json.loads((trial_dir / "manifest.json").read_text())
    trial = manifest["trial"]
    assert trial["mission_id"] == "T1"
    assert trial["trial_idx"] == 1
    assert trial["model"] == "gpt-5.2"
    assert trial["firmware_version"] == "4.5.7 (SITL)"
    assert trial["droneserver_commit"] == "deadbeef"
    assert trial["capture_status"] == "complete", trial["capture_status"]

    # events.jsonl is hashed like everything else: it must be listed, which it
    # was not while it was derived after the manifest was written.
    names = {a["name"] for a in manifest["artifacts"]}
    assert "events.jsonl" in names
    assert "transcript.jsonl" in names
    assert "manifest.json" not in names

    assert results[0].capture_status == "complete"


async def test_the_transcript_is_the_real_conversation(tmp_path):
    config = _config(tmp_path, capture=_capture_config())
    await runner.run_llm_suite(config, log=lambda *a: None)

    turns = [json.loads(x) for x in
             (config.out_dir / "T1" / "trial_1" / "transcript.jsonl").read_text().splitlines() if x.strip()]
    roles = [t["role"] for t in turns]
    assert roles[0] == "system" and roles[1] == "user"
    assert "assistant" in roles and "tool" in roles

    # the system prompt is the server's real one, not a harness placeholder
    assert "drone" in (turns[0]["content"] or "").lower()
    assert "no model conversation exists" not in (turns[0]["content"] or "")
    # the mission prompt the model was actually given
    assert turns[1]["content"]

    assistant = next(t for t in turns if t["role"] == "assistant")
    assert assistant["content"] == "checking where we are"
    assert assistant["tool_calls"][0]["tool"] == "get_position"
    assert assistant["tool_calls"][0]["call_id"] == "1.1"
    assert assistant["usage"]["completion_tokens"] == 20
    assert assistant["model"] == "fake-model-2026-01-01"

    tool = next(t for t in turns if t["role"] == "tool")
    assert tool["tool_result"]["status"] == "success"
    assert tool["tool_calls"][0]["call_id"] == "1.1"


async def test_run_level_outputs_are_unchanged_by_capture(tmp_path):
    config = _config(tmp_path, capture=_capture_config())
    await runner.run_llm_suite(config, log=lambda *a: None)

    for name in ("missions.csv", "turns.csv", "tool_calls.csv", "summary.md"):
        assert (config.out_dir / name).exists()
    assert (config.out_dir / "telemetry" / "T1_t1.csv").exists()
    assert (config.out_dir / "transcripts" / "T1_t1.md").exists()

    header = (config.out_dir / "missions.csv").read_text().splitlines()[0]
    assert "capture_status" in header


async def test_a_degraded_bundle_is_reported_not_hidden(tmp_path):
    """The aircraft armed and no dataflash log came back: say so, everywhere."""
    FakeCaptureRecorder.armed = True
    config = _config(tmp_path, capture=_capture_config())
    results = await runner.run_llm_suite(config, log=lambda *a: None)

    status = results[0].capture_status
    assert status.startswith("degraded[")
    assert "dataflash" in status
    manifest = json.loads((config.out_dir / "T1" / "trial_1" / "manifest.json").read_text())
    assert manifest["trial"]["capture_status"] == status
    assert "Bundles degraded: **1**" in (config.out_dir / "summary.md").read_text()


async def test_without_capture_nothing_changes(tmp_path):
    config = _config(tmp_path)  # no capture=
    results = await runner.run_llm_suite(config, log=lambda *a: None)

    assert len(results) == 1
    assert results[0].capture_status == ""
    assert (config.out_dir / "missions.csv").exists()
    assert not (config.out_dir / "T1").exists()


async def test_the_capture_window_is_one_trial_not_the_whole_run(tmp_path, monkeypatch):
    """Each trial's audit slice is windowed to that trial.

    Without an end to the window every trial after the first would carry the
    whole run's audit rows, and the per-trial slice would stop being one.
    """
    seen = []

    def _spy(audit_log, window_start, window_end=None):
        seen.append((window_start, window_end))
        return list(AUDIT_ROWS)

    monkeypatch.setattr(runner, "_read_audit", _spy)
    config = _config(tmp_path, capture=_capture_config(), missions=("T1", "T7"))
    await runner.run_llm_suite(config, log=lambda *a: None)

    per_trial = [w for w in seen if w[1] is not None]
    assert len(per_trial) == 2
    for start, end in per_trial:
        assert end >= start
