"""A trial must start with a clean critical-tier rate-limit budget.

The server's safety layer allows only a few CRITICAL calls per 60 s window, and
the limiter keys on the API key's ``client_id`` - which every trial in a run
shares. So the window does not reset between trials: a trial's critical calls
keep occupying the budget while the next trial begins.

This is the same family of defect as the position drift - state from trial N
deciding trial N+1 - and it surfaced in the drift-fix verification as T7
(``set_parameter`` on ``WPNAV_SPEED``, a CRITICAL call) scoring 1/3: T7.1 used
5 of the 6 slots and T7.2, starting 14 s later, hit the wall on its second
write. Worse, it is model-speed dependent - a fast model runs its trials close
enough together to starve itself while a slow one does not - so it reads as a
capability difference that is really a stopwatch artefact.

The fix is harness-side pacing that leaves the measured safety limit intact:
between trials the harness waits for the previous trial's critical calls to age
out of the window. These tests pin that behaviour:

1. the drain deadline is computed from the server's *own* tier classifier, so a
   critical call sets a wait and a read-only one does not;
2. a second trial run straight after a critical-heavy first is paced by ~the
   window - and a second trial after a harmless first is not paced at all;
3. the safety limit itself is never touched (pacing is time, not a looser rule).
"""

import time

import pytest

from droneserver.llm import runner
from droneserver.llm.agent import AgentRun, TurnRecord
from droneserver.llm.mcp_session import CallRecord, TelemetrySample
from droneserver.llm.providers import ToolSpec
from droneserver.llm.runner import DEFAULT_CRITICAL_RATE_WINDOW_S, SuiteConfig, _critical_drain_deadline

LAUNCH = (33.6458611, -117.84275)
LAUNCH_AMSL = 25.1


def _call(tool: str, arguments: dict, started_at: float, wall_ms: float = 50.0) -> CallRecord:
    return CallRecord(
        turn=1, seq=1, tool=tool, arguments=arguments, started_at=started_at, wall_ms=wall_ms, status="success"
    )


def _run(calls: list[CallRecord]) -> AgentRun:
    return AgentRun(
        turns=[
            TurnRecord(
                index=1,
                decision_latency_ms=1.0,
                provider_wait_ms=0.0,
                attempts=1,
                input_tokens=10,
                cached_input_tokens=0,
                output_tokens=2,
                reasoning_tokens=0,
                finish_reason="stop",
                text="done",
            )
        ],
        calls=calls,
        stop_reason="model declared the mission finished",
        final_text="MISSION COMPLETE",
        started_at=0.0,
        duration_s=1.0,
    )


# --------------------------------------------- 1. the drain deadline classifier


def test_a_critical_set_parameter_sets_a_drain_deadline():
    """WPNAV_SPEED escalates set_parameter to CRITICAL on the argument alone."""
    t = time.time()
    run = _run([_call("set_parameter", {"name": "WPNAV_SPEED", "value": 510}, started_at=t)])
    deadline = _critical_drain_deadline(run, DEFAULT_CRITICAL_RATE_WINDOW_S)
    # window after the call FINISHED (started_at + wall_ms), not after it started.
    assert deadline == pytest.approx(t + 0.05 + DEFAULT_CRITICAL_RATE_WINDOW_S, abs=0.01)


def test_the_deadline_follows_the_last_critical_call():
    t = time.time()
    run = _run(
        [
            _call("set_parameter", {"name": "WPNAV_SPEED", "value": 510}, started_at=t),
            _call("get_parameter", {"name": "WPNAV_SPEED"}, started_at=t + 5),  # read-only, ignored
            _call("set_parameter", {"name": "WPNAV_SPEED", "value": 500}, started_at=t + 8),
        ]
    )
    deadline = _critical_drain_deadline(run, DEFAULT_CRITICAL_RATE_WINDOW_S)
    assert deadline == pytest.approx(t + 8 + 0.05 + DEFAULT_CRITICAL_RATE_WINDOW_S, abs=0.01)


def test_a_read_only_trial_needs_no_pacing():
    run = _run([_call("get_position", {}, started_at=time.time()), _call("get_armed", {}, started_at=time.time())])
    assert _critical_drain_deadline(run, DEFAULT_CRITICAL_RATE_WINDOW_S) == 0.0


def test_a_non_safety_parameter_is_not_critical():
    """set_parameter is NORMAL unless the name is safety-critical - so pacing
    fires on the parameters the safety layer actually gates, not on all writes."""
    run = _run([_call("set_parameter", {"name": "LOG_BITMASK", "value": 1}, started_at=time.time())])
    assert _critical_drain_deadline(run, DEFAULT_CRITICAL_RATE_WINDOW_S) == 0.0


def test_an_always_critical_tool_sets_a_deadline():
    run = _run([_call("kill_motors", {}, started_at=time.time())])
    assert _critical_drain_deadline(run, DEFAULT_CRITICAL_RATE_WINDOW_S) > 0.0


def test_disarm_on_the_ground_is_not_paced():
    """Between trials the aircraft is landed, so disarm_drone is NORMAL on the
    server - it spends no critical slot, and the harness must not pace for it.
    Over-pacing routine disarms would roughly double the campaign's added time."""
    run = _run([_call("disarm_drone", {}, started_at=time.time())])
    assert _critical_drain_deadline(run, DEFAULT_CRITICAL_RATE_WINDOW_S) == 0.0


def test_a_force_arm_is_still_critical():
    """arm_drone escalates on the argument, not the state, so force-arming is
    correctly counted whatever the assumed vehicle state."""
    run = _run([_call("arm_drone", {"force": True}, started_at=time.time())])
    assert _critical_drain_deadline(run, DEFAULT_CRITICAL_RATE_WINDOW_S) > 0.0


def test_a_client_rejected_call_never_reached_the_server_so_is_not_paced():
    """A hallucinated tool name is rejected in the client and never touches the
    server's limiter, so it consumed no slot and must not extend the deadline."""
    call = _call("get_altitude", {}, started_at=time.time())  # not a registered tool -> would classify critical
    call.status = "client_rejected"
    assert _critical_drain_deadline(_run([call]), DEFAULT_CRITICAL_RATE_WINDOW_S) == 0.0


def test_pacing_can_be_disabled_with_a_zero_window():
    run = _run([_call("set_parameter", {"name": "WPNAV_SPEED", "value": 510}, started_at=time.time())])
    assert _critical_drain_deadline(run, 0.0) == 0.0


# ------------------------------------------- 2. two trials, end to end, is paced


class _T7Session:
    """A drone server whose set_parameter succeeds and stays parked at launch."""

    def __init__(self, *a, **k):
        self.value = 500.0

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return None

    async def aclose(self):
        return None

    async def wait_ready(self, timeout_s=180.0):
        return True

    async def list_tools(self):
        return [ToolSpec("set_parameter", "set a parameter", {"type": "object", "properties": {}})]

    async def call_raw(self, tool, arguments=None, timeout_s=300.0):
        if tool == "get_armed":
            return {"status": "success", "armed": False}
        if tool == "get_position":
            return {
                "status": "success",
                "position": {
                    "latitude_deg": LAUNCH[0],
                    "longitude_deg": LAUNCH[1],
                    "absolute_altitude_m": LAUNCH_AMSL,
                    "relative_altitude_m": 0.0,
                },
            }
        if tool == "get_home_position":
            return {
                "status": "success",
                "home": {"latitude_deg": LAUNCH[0], "longitude_deg": LAUNCH[1], "absolute_altitude_m": LAUNCH_AMSL},
            }
        if tool == "get_parameter":
            return {"status": "success", "value": self.value}
        return {"status": "success"}

    async def call(self, tool, arguments, *, turn=0, seq=0, timeout_s=300.0):
        return {"status": "success"}, _call(tool, arguments, started_at=time.time())


class _FakePoller:
    def __init__(self, *a, **k):
        self.samples = [
            TelemetrySample(
                t=float(i),
                latitude_deg=LAUNCH[0],
                longitude_deg=LAUNCH[1],
                relative_altitude_m=0.0,
                absolute_altitude_m=LAUNCH_AMSL,
                armed=False,
                in_air=False,
            )
            for i in range(5)
        ]

    async def start(self):
        return None

    async def stop(self, final_sample=False):
        return None

    async def sample_once(self, full=True):
        return None


class _FakeModel:
    messages = [{"role": "assistant", "content": "MISSION COMPLETE"}]

    async def aclose(self):
        return None


@pytest.fixture
def _harness(monkeypatch):
    """Wire the fakes and make asyncio.sleep record its duration instead of waiting."""
    session = _T7Session()
    monkeypatch.setenv("OPENAI_API_KEY", "test-key-not-a-real-key")
    monkeypatch.setattr(runner, "LiveMCPSession", lambda *a, **k: session)
    monkeypatch.setattr(runner, "McpTelemetryPoller", _FakePoller)
    monkeypatch.setattr(runner, "open_session", lambda *a, **k: _FakeModel())

    slept: list[float] = []

    async def fake_sleep(seconds):
        slept.append(seconds)

    monkeypatch.setattr(runner.asyncio, "sleep", fake_sleep)
    return slept


def _t7_agent_run(**kwargs) -> AgentRun:
    # The model makes two CRITICAL writes, as a real T7 does (raise, restore).
    now = time.time()
    return _run(
        [
            _call("get_parameter", {"name": "WPNAV_SPEED"}, started_at=now),
            _call("set_parameter", {"name": "WPNAV_SPEED", "value": 510}, started_at=now),
            _call("set_parameter", {"name": "WPNAV_SPEED", "value": 500}, started_at=now),
        ]
    )


async def test_the_second_consecutive_t7_trial_is_paced_by_the_window(monkeypatch, tmp_path, _harness):
    """The regression proper: run two T7 trials back to back; the second must
    wait ~one window for the first's critical writes to drain."""

    async def _run_agent(**kwargs):
        return _t7_agent_run()

    monkeypatch.setattr(runner, "run_agent", _run_agent)

    config = SuiteConfig(
        url="http://x", api_key="k", model_spec="gpt-5.2", missions=["T7"], trials=2, out_dir=tmp_path / "run"
    )
    results = await runner.run_llm_suite(config, log=lambda *a: None)

    assert len(results) == 2
    # A pacing wait close to the full window happened for the SECOND trial.
    paces = [s for s in _harness if s > DEFAULT_CRITICAL_RATE_WINDOW_S * 0.8]
    assert len(paces) == 1, f"expected exactly one ~window pacing wait, saw waits {_harness}"
    assert paces[0] <= DEFAULT_CRITICAL_RATE_WINDOW_S + 0.5
    # It is recorded on the trial it paced, and not on the first.
    assert results[0].evidence.get("paced_before_trial_s") in (None, 0, 0.0)
    assert results[1].evidence["paced_before_trial_s"] > DEFAULT_CRITICAL_RATE_WINDOW_S * 0.8


async def test_a_read_only_first_trial_does_not_pace_the_second(monkeypatch, tmp_path, _harness):
    """Control: if the first trial makes no critical call, the second is not paced."""

    async def _run_agent(**kwargs):
        return _run([_call("get_position", {}, started_at=time.time())])

    monkeypatch.setattr(runner, "run_agent", _run_agent)

    config = SuiteConfig(
        url="http://x", api_key="k", model_spec="gpt-5.2", missions=["T1"], trials=2, out_dir=tmp_path / "run"
    )
    results = await runner.run_llm_suite(config, log=lambda *a: None)

    assert len(results) == 2
    assert not [s for s in _harness if s > 1.0], f"nothing critical happened, so nothing should pace: {_harness}"
    assert all(r.evidence.get("paced_before_trial_s") in (None, 0, 0.0) for r in results)


# ------------------------------------------- 3. the safety limit is not loosened


def test_pacing_does_not_touch_the_safety_limit():
    """The window the harness paces to is the server's declared limit, read from
    the safety config - the fix spaces trials out, it does not widen the rule."""
    from droneserver.safety.config import SafetySettings

    assert DEFAULT_CRITICAL_RATE_WINDOW_S == SafetySettings.model_fields["rate_limit_critical_window_s"].default
