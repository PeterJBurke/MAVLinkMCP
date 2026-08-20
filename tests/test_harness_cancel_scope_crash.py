"""The harness must not be killed by its own flight recorder - and if it is killed, it must say so.

**The defect (2026-08-20, ~6 long trials).** A trial would die mid-flight with
``RuntimeError: Attempted to exit cancel scope in a different task than it was
entered in`` - or ``generator didn't stop after athrow()``, its other face -
somewhere under ``poller.sample_once``. The process stopped where it stood: no
verdict, no cleanup, and in one case an aircraft left airborne. Four consecutive
VOIDs on one grok-4.20-reasoning T6, plus a gpt-5.2 T6.

**What was actually happening.** ``mcp.client.sse.sse_client`` is an
``@asynccontextmanager`` whose body is ``async with anyio.create_task_group() as
tg: ... yield ... finally: tg.cancel_scope.cancel()``. An anyio cancel scope
belongs to the task that entered it, and cancelling it cancels *that* task.

``McpTelemetryPoller.start`` opened its session in the caller's task - the task
running the trial - and then polled it from a background task. The first tool
error the poller met (``_parse`` raises on any ``isError`` result, so a single
rate-limited ``get_position`` is enough) sent it into
``LiveMCPSession._reconnect``, which closed the transport **from the background
task**. That fired the transport's cancel scope at the trial task, which was by
then somewhere else entirely - inside the agent loop, which re-raises
``CancelledError`` by design. Worse, the orphaned scope keeps re-delivering that
cancellation, so every later ``await`` in the trial task is cancelled too: no
cleanup handler can complete, which is why the aircraft stayed up.

The tests below pin, in order:

1. the platform behaviour that makes this a bug at all - closing an anyio scope
   from the wrong task cancels its owner (a characterization test: if anyio ever
   stops doing this, the guard below is no longer load-bearing and we should
   know);
2. that a session now refuses to be closed by a task that did not open it,
   instead of taking the owner down with it;
3. that the poller opens, reconnects and closes its session inside its own task,
   so a reconnect mid-trial can no longer touch the trial's task at all;
4. that ANY unhandled exception mid-trial - this one or the next one - still
   writes a VOID row naming it, still tries to land the aircraft, and still
   ends the process nonzero. Two of the 2026-08-20 rows are unscoreable purely
   because nothing was written; that must not be possible again.
"""

import asyncio
import contextlib
from contextlib import asynccontextmanager

import anyio
import pytest

from droneserver.llm import runner
from droneserver.llm.agent import AgentRun, TurnRecord
from droneserver.llm.mcp_session import (
    CallRecord,
    LiveMCPSession,
    MCPSessionTaskError,
    McpTelemetryPoller,
    TelemetrySample,
)
from droneserver.llm.providers import ToolSpec
from droneserver.llm.runner import HarnessCrash, SuiteConfig

LAUNCH = (33.6458611, -117.84275)
LAUNCH_AMSL = 25.1


# --------------------------------------------------------------- fake transport
#
# Deliberately the same shape as mcp.client.sse.sse_client: a task group, a
# child task, and `tg.cancel_scope.cancel()` in the exit path. Everything that
# makes the real thing dangerous to close from the wrong task is reproduced;
# nothing that needs a server is.


class FakeTransport:
    """Records which task entered and exited each connection."""

    def __init__(self, fail_calls: set[int] | None = None):
        #: Call ordinals (1-based) on which call_tool should blow up, which is
        #: what sends LiveMCPSession into a reconnect.
        self.fail_calls = fail_calls or set()
        self.calls = 0
        self.entered_by: list[asyncio.Task] = []
        self.exited_by: list[asyncio.Task] = []

    @property
    def client(self):
        @asynccontextmanager
        async def sse_client(url, headers=None):
            self.entered_by.append(asyncio.current_task())
            async with anyio.create_task_group() as tg:

                async def idle():
                    await anyio.sleep(3600)

                tg.start_soon(idle)
                try:
                    yield ("read", "write")
                finally:
                    self.exited_by.append(asyncio.current_task())
                    tg.cancel_scope.cancel()

        return sse_client

    @property
    def session(self):
        transport = self

        class FakeClientSession:
            def __init__(self, read, write, client_info=None):
                pass

            async def __aenter__(self):
                return self

            async def __aexit__(self, *exc):
                return None

            async def initialize(self):
                return None

            async def call_tool(self, tool, arguments=None, read_timeout_seconds=None):
                transport.calls += 1
                if transport.calls in transport.fail_calls:
                    raise RuntimeError("the SSE stream went away")
                return _FakeToolResult(_reading(tool))

        return FakeClientSession


class _FakeToolResult:
    def __init__(self, payload: dict):
        self.content: list = []
        self.isError = False
        self.structuredContent = payload


def _reading(tool: str) -> dict:
    if tool == "get_position":
        return {
            "status": "success",
            "position": {
                "latitude_deg": LAUNCH[0],
                "longitude_deg": LAUNCH[1],
                "relative_altitude_m": 0.0,
                "absolute_altitude_m": LAUNCH_AMSL,
            },
        }
    if tool == "get_armed":
        return {"status": "success", "armed": False}
    if tool == "get_in_air":
        return {"status": "success", "in_air": False}
    return {"status": "success", "tool": tool}


@pytest.fixture
def transport(monkeypatch):
    def _make(fail_calls: set[int] | None = None) -> FakeTransport:
        t = FakeTransport(fail_calls)
        monkeypatch.setattr("droneserver.llm.mcp_session.sse_client", t.client)
        monkeypatch.setattr("droneserver.llm.mcp_session.ClientSession", t.session)
        return t

    return _make


# ------------------------- 1. the platform behaviour this whole fix is about


async def test_closing_an_anyio_scope_from_another_task_cancels_the_task_that_opened_it():
    """Characterization: this is why a stray reconnect killed whole trials.

    Nothing in our code is under test here. It records the anyio contract that
    makes the same-task rule load-bearing: the *owner* of a cancel scope is the
    one cancelled when somebody else unwinds it, and it is cancelled wherever it
    happens to be - which, in the harness, was the middle of a flight.
    """
    stack = contextlib.AsyncExitStack()
    transport = FakeTransport()
    cancelled: list[str] = []
    opened = asyncio.Event()

    async def owner():
        await stack.enter_async_context(transport.client("http://drone"))
        opened.set()
        try:
            await asyncio.sleep(5)  # "flying the trial"
        except asyncio.CancelledError as e:
            cancelled.append(f"{type(e).__name__}: {e}")

    owner_task = asyncio.create_task(owner())
    await opened.wait()
    await asyncio.sleep(0)

    with contextlib.suppress(BaseException):
        await stack.aclose()  # the WRONG task closes it
    await asyncio.wait_for(owner_task, timeout=5)

    assert cancelled, (
        "anyio no longer cancels a scope's owner when another task unwinds it; the same-task "
        "rule in LiveMCPSession may no longer be what stands between us and a dead trial"
    )
    assert "cancel scope" in cancelled[0]


# --------------------------------- 2. a session refuses to be closed by a stranger


async def test_a_session_opened_in_one_task_refuses_to_be_closed_by_another(transport):
    """The guard proper: it raises at the caller instead of cancelling the owner."""
    transport()
    session = LiveMCPSession("http://drone", "key")
    await session.__aenter__()

    survived = []

    async def stranger():
        with pytest.raises(MCPSessionTaskError) as caught:
            await session.aclose()
        return str(caught.value)

    message = await asyncio.create_task(stranger())
    assert "anyio cancel scope" in message

    # The owning task is untouched: it can still use the session, and then close
    # it itself. Under the defect it would have been cancelled by now.
    result = await session.call_raw("get_position", {}, 10)
    assert result["status"] == "success"
    survived.append(True)
    await session.aclose()
    assert survived == [True]


async def test_a_refused_close_leaves_the_connection_usable_by_its_owner(transport):
    """Refusing must not half-close: the owner still has a working session."""
    t = transport()
    session = LiveMCPSession("http://drone", "key")
    await session.__aenter__()

    async def stranger():
        with contextlib.suppress(MCPSessionTaskError):
            await session.aclose()

    await asyncio.create_task(stranger())
    assert t.exited_by == [], "the stranger must not have unwound the transport at all"
    await session.aclose()
    assert t.exited_by == [asyncio.current_task()]


# ------------------------- 3. the poller keeps its session inside its own task


async def test_the_poller_opens_polls_and_closes_its_session_in_one_task(transport):
    """The regression test for the crash itself.

    The poller's very first tool call fails, which is what sends
    ``LiveMCPSession`` into a reconnect - the exact path that used to unwind the
    transport from the polling task and cancel the trial. Afterwards: the trial
    task (this one) is alive, the session was opened and closed by the polling
    task both times, and the poller is still recording.
    """
    t = transport(fail_calls={1, 2})  # first call fails, so does its retry
    poller = McpTelemetryPoller("http://drone", "recorder-key", interval_s=0.01)
    await poller.start()
    await asyncio.sleep(0.2)
    await poller.stop(final_sample=True)

    assert poller.session.reconnects >= 1, "the test did not exercise the reconnect path"
    assert t.entered_by, "the transport was never opened"
    assert t.entered_by == t.exited_by, (
        f"every connection must be closed by the task that opened it; opened by {t.entered_by}, closed by {t.exited_by}"
    )
    assert asyncio.current_task() not in t.entered_by, (
        "the poller's connection must not be opened in the trial's task - that is the crossing "
        "that killed six trials on 2026-08-20"
    )
    assert poller.samples, "the poller stopped recording after the reconnect"
    # The closing full sample is the one that answers "did it end disarmed?",
    # and it must be taken by the poller's own task, not the caller's.
    assert poller.samples[-1].armed is False


async def test_the_trial_runner_asks_the_poller_for_its_own_final_sample():
    """Documentation-as-test: the runner must not reach into the poller's session.

    ``_run_trial`` used to call ``poller.sample_once(full=True)`` directly, from
    the trial's task, on a connection the polling task owned. Anything that
    restores that call restores the defect.
    """
    source = (runner.__file__ or "").replace("\\", "/")
    text = open(source, encoding="utf-8").read()
    assert "poller.stop(final_sample=True)" in text
    assert "poller.sample_once" not in text, (
        "the trial runner must not call into the poller's session from its own task"
    )


async def test_a_poller_that_cannot_connect_still_fails_in_the_callers_face(transport, monkeypatch):
    """Moving the connect into the polling task must not swallow a dead server."""
    t = transport()

    @asynccontextmanager
    async def refuses(url, headers=None):
        raise ConnectionRefusedError("no server there")
        yield  # pragma: no cover

    monkeypatch.setattr("droneserver.llm.mcp_session.sse_client", refuses)
    poller = McpTelemetryPoller("http://drone", "recorder-key", interval_s=0.01)
    with pytest.raises(ConnectionRefusedError):
        await poller.start()
    assert t.entered_by == []


# ------------------------------------- 4. nothing gets lost when it does crash


class CrashSession:
    """A drone server whose aircraft can be left airborne by a crashing trial."""

    def __init__(self):
        self.armed = False
        self.calls: list[str] = []

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

    async def call(self, tool, arguments, *, turn=0, seq=0, timeout_s=300.0):
        return {"status": "success"}, CallRecord(
            turn=turn, seq=seq, tool=tool, arguments=arguments, started_at=0.0, wall_ms=1.0, status="success"
        )

    async def call_raw(self, tool, arguments=None, timeout_s=300.0):
        self.calls.append(tool)
        if tool == "get_armed":
            return {"status": "success", "armed": self.armed}
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
        if tool == "land":
            self.armed = False
            return {"status": "success"}
        return {"status": "success"}


class CrashPoller:
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
            for i in range(3)
        ]
        self.stopped = False

    async def start(self):
        return None

    async def stop(self, final_sample=False):
        self.stopped = True

    async def sample_once(self, full=True):
        return None


class CrashModel:
    messages = [{"role": "assistant", "content": ""}]

    async def aclose(self):
        return None


@pytest.fixture
def crashing_suite(monkeypatch):
    """A suite whose model turn raises whatever you hand it, mid-trial."""
    session = CrashSession()
    monkeypatch.setenv("OPENAI_API_KEY", "test-key-not-a-real-key")
    monkeypatch.setattr(runner, "LiveMCPSession", lambda *a, **k: session)
    monkeypatch.setattr(runner, "McpTelemetryPoller", CrashPoller)
    monkeypatch.setattr(runner, "open_session", lambda *a, **k: CrashModel())

    async def no_sleep(_s):
        return None

    monkeypatch.setattr(runner.asyncio, "sleep", no_sleep)

    def _arm(error: BaseException):
        async def _run_agent(**kwargs):
            # The aircraft is in the air when our own code falls over: exactly
            # the state the 2026-08-20 crashes left behind.
            session.armed = True
            raise error

        monkeypatch.setattr(runner, "run_agent", _run_agent)
        return session

    return _arm


def _config(tmp_path, **kwargs) -> SuiteConfig:
    defaults = {"missions": ["T1", "T2"], "trials": 2}
    return SuiteConfig(
        url="http://x",
        api_key="k",
        model_spec="gpt-5.2",
        out_dir=tmp_path / "run",
        **{**defaults, **kwargs},
    )


async def test_a_crash_mid_trial_writes_a_void_row_lands_the_aircraft_and_raises(crashing_suite, tmp_path):
    """The guard, end to end. All three obligations, on one crash."""
    session = crashing_suite(ZeroDivisionError("something nobody predicted"))
    config = _config(tmp_path)

    with pytest.raises(HarnessCrash) as caught:
        await runner.run_llm_suite(config, log=lambda *a: None)

    # (c) the process ends nonzero - the script turns this into exit code 5
    assert "ZeroDivisionError" in str(caught.value)

    # (b) a row was written, VOID, naming the exception
    rows = (config.out_dir / "missions.csv").read_text(encoding="utf-8").splitlines()
    assert len(rows) == 2, f"expected a header and exactly one trial row, got {len(rows)}"
    assert rows[1].startswith("T1,1,VOID,")
    assert "ZeroDivisionError: something nobody predicted" in rows[1]
    assert "HARNESS" in rows[1] or "harness" in rows[1]

    # (a) the safe-landing path ran even though the trial never returned
    assert "land" in session.calls, "the harness did not try to land an aircraft it had left airborne"
    assert session.armed is False

    summary = (config.out_dir / "summary.md").read_text(encoding="utf-8")
    assert "The harness crashed" in summary
    # ...and it is NOT counted against the model anywhere.
    assert "Missions judged: **0**" in summary


async def test_the_crash_row_is_void_and_never_a_model_failure(crashing_suite, tmp_path):
    """A harness bug must not be able to look like a model failing a mission."""
    crashing_suite(RuntimeError("Attempted to exit cancel scope in a different task than it was entered in"))
    config = _config(tmp_path)

    with pytest.raises(HarnessCrash):
        await runner.run_llm_suite(config, log=lambda *a: None)

    text = (config.out_dir / "missions.csv").read_text(encoding="utf-8")
    assert ",FAIL," not in text
    assert "Attempted to exit cancel scope" in text


async def test_a_bare_cancellation_mid_trial_is_a_crash_not_a_quiet_death(crashing_suite, tmp_path):
    """The actual symptom of defect C, which ``except Exception`` would miss.

    An orphaned anyio scope delivers ``CancelledError`` - a ``BaseException``.
    Every trial lost on 2026-08-20 died of one, and a guard that only caught
    ``Exception`` would have written exactly as little as the old code did.
    """
    crashing_suite(asyncio.CancelledError("Cancelled by cancel scope 0x7f"))
    config = _config(tmp_path)

    with pytest.raises(HarnessCrash):
        await runner.run_llm_suite(config, log=lambda *a: None)

    rows = (config.out_dir / "missions.csv").read_text(encoding="utf-8").splitlines()
    assert rows[1].startswith("T1,1,VOID,")
    assert "CancelledError" in rows[1]


async def test_a_crash_names_the_trial_that_was_in_the_air(crashing_suite, tmp_path):
    """The row must identify the trial, so the campaign can be resumed by hand."""
    crashing_suite(RuntimeError("boom"))
    config = _config(tmp_path, missions=["T2"])

    with pytest.raises(HarnessCrash):
        await runner.run_llm_suite(config, log=lambda *a: None)

    rows = (config.out_dir / "missions.csv").read_text(encoding="utf-8").splitlines()
    assert rows[1].startswith("T2,1,VOID,")


async def test_a_clean_run_still_ends_clean(monkeypatch, tmp_path):
    """The guard must not change anything about a run that does not crash."""
    session = CrashSession()
    monkeypatch.setenv("OPENAI_API_KEY", "test-key-not-a-real-key")
    monkeypatch.setattr(runner, "LiveMCPSession", lambda *a, **k: session)
    monkeypatch.setattr(runner, "McpTelemetryPoller", CrashPoller)
    monkeypatch.setattr(runner, "open_session", lambda *a, **k: CrashModel())

    async def no_sleep(_s):
        return None

    monkeypatch.setattr(runner.asyncio, "sleep", no_sleep)

    async def _run_agent(**kwargs):
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
                    text="MISSION COMPLETE",
                )
            ],
            calls=[
                CallRecord(
                    turn=1, seq=1, tool="get_position", arguments={}, started_at=0.0, wall_ms=1.0, status="success"
                )
            ],
            stop_reason="model declared the mission finished",
            final_text="MISSION COMPLETE",
            started_at=0.0,
            duration_s=1.0,
        )

    monkeypatch.setattr(runner, "run_agent", _run_agent)
    config = _config(tmp_path, missions=["T1"], trials=1)
    results = await runner.run_llm_suite(config, log=lambda *a: None)

    assert len(results) == 1
    assert results[0].harness_crash == ""
    assert "The harness crashed" not in (config.out_dir / "summary.md").read_text(encoding="utf-8")
