"""A transport must not be able to cancel the trial that is using it (FIX 16).

**The defect (2026-08-23, three trials, $0.00 each).** ``z-ai/glm-5.2`` was asked
to fly Mission 6 - the composition task, where the drone server and Google's
hosted Maps server are merged into one tool list. Three attempts, three
identical deaths at 23-27 s, always on the call *after* the batch of read-only
drone reads, which for this mission is the first call into the Maps server::

    asyncio.CancelledError: Cancelled by cancel scope 0x...

No capture bundle, no verdict, no spend: the cell simply had no result.

**What is actually happening.** An MCP transport is
``async with anyio.create_task_group() as tg:`` with background tasks inside it -
for streamable HTTP, a ``post_writer`` pumping requests at the far end. When one
of those background tasks dies, the group cancels its cancel scope, and an anyio
cancel scope cancels **the task that entered it**. That task is the trial,
sitting inside ``call_tool`` waiting for a reply that is never coming. It gets a
``CancelledError``.

``CancelledError`` is a ``BaseException``. So it walks straight through the
``except Exception`` in :meth:`LiveMCPSession.call_raw` that exists to reconnect
and retry, straight through the ``except Exception`` in
:meth:`LiveMCPSession.call` that exists to turn a broken connection into one
recorded ``transport_error``, and out of the trial. **The retry logic written for
exactly this failure never ran, because the failure did not arrive as an
Exception.** The same thing happens one layer down: the reconnect's own
``_connect`` against a far end that is still gone is cancelled the same way.

This is the sibling of the 2026-08-20 defect in
:mod:`tests.test_harness_cancel_scope_crash`. That one was a scope unwound by the
*wrong task*; this one is a scope cancelled from *inside*, in the right task.
Both end as a bare cancellation nothing is expecting.

**The fix, and why it does not mask a real cancellation.** The transport is
unwound - which is what actually ends the cancellation, because anyio reclaims
(``task.uncancel()``) exactly the cancellations a scope issued when its owner
exits it - and the failure is re-raised as an ``MCPTransportError``, an ordinary
``Exception`` the existing reconnect path already handles. Whether the
cancellation was the transport's is then a measurement, not a guess:
``task.cancelling()`` comes back to where it started for a transport scope, and
stays raised for a genuine ``task.cancel()``. Both are pinned below.
"""

import asyncio
import contextlib
from contextlib import asynccontextmanager

import anyio
import pytest

from droneserver.llm.mcp_session import (
    LiveMCPSession,
    MCPTransportError,
    MultiServerSession,
)

# ------------------------------------------------------------- fake transport
#
# The same shape as mcp.client.streamable_http's client: a task group whose
# background task can die, taking the group's cancel scope - and therefore the
# task that entered it - with it. Nothing that needs a server is reproduced.


class CancellingTransport:
    """A transport whose background task can die while a call is in flight."""

    def __init__(self, tag: str = "drone"):
        self.tag = tag
        #: Call ordinals (1-based) on which the transport's background task
        #: "dies": the group's cancel scope is cancelled and no reply ever
        #: arrives, which is precisely what the far end going away looks like.
        self.die_on: set[int] = set()
        #: While true, even opening a connection dies the same way - the state
        #: a reconnect meets when the far end is still gone.
        self.far_end_gone = False
        #: Ordinals on which call_tool raises an ordinary exception instead.
        self.raise_on: set[int] = set()
        #: Tools whose call never returns - a trial parked mid-command, which is
        #: where a wall-clock guard finds it.
        self.hang_tools: set[str] = {"slow_tool"}
        self.calls = 0
        self.connects = 0
        self.entered_by: list[asyncio.Task] = []
        self.exited_by: list[asyncio.Task] = []
        self.scopes: list = []

    @property
    def client(self):
        outer = self

        @asynccontextmanager
        async def transport(url, headers=None):
            outer.connects += 1
            outer.entered_by.append(asyncio.current_task())
            async with anyio.create_task_group() as tg:

                async def idle():
                    await anyio.sleep(3600)

                tg.start_soon(idle)
                outer.scopes.append(tg.cancel_scope)
                if outer.far_end_gone:
                    tg.cancel_scope.cancel()
                try:
                    yield (f"{outer.tag}-read", f"{outer.tag}-write")
                finally:
                    outer.exited_by.append(asyncio.current_task())
                    tg.cancel_scope.cancel()

        return transport


class _FakeToolResult:
    def __init__(self, payload: dict):
        self.content: list = []
        self.isError = False
        self.structuredContent = payload


def _client_session_for(registry: dict[str, CancellingTransport]):
    """One fake ClientSession that routes by which transport handed it streams."""

    class FakeClientSession:
        def __init__(self, read, write, client_info=None):
            self.transport = registry[str(read).split("-")[0]]

        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc):
            return None

        async def initialize(self):
            # A real initialize awaits the server; the await is what lets a
            # cancellation fired during connect actually land.
            await anyio.sleep(0.01)

        async def list_tools(self):
            names = ["get_position", "arm_drone"] if self.transport.tag == "drone" else ["search_places"]
            return type("Listing", (), {"tools": [_Tool(n) for n in names]})()

        async def call_tool(self, tool, arguments=None, read_timeout_seconds=None):
            t = self.transport
            t.calls += 1
            if tool in t.hang_tools:
                await anyio.sleep(3600)
            if t.calls in t.raise_on:
                raise RuntimeError("the stream went away")
            if t.calls in t.die_on:
                # The background writer has died. The group cancels its scope -
                # at the task that entered it, which is the one right here
                # waiting for a reply that will now never arrive.
                t.scopes[-1].cancel()
                await anyio.sleep(3600)
            return _FakeToolResult({"status": "success", "tool": tool, "server": t.tag})

    return FakeClientSession


class _Tool:
    def __init__(self, name):
        self.name = name
        self.description = name
        self.inputSchema = {"type": "object", "properties": {}}


@pytest.fixture
def transports(monkeypatch):
    """Wire one or two fake transports in place of the real MCP clients."""

    def _make(*tags: str) -> tuple[CancellingTransport, ...]:
        made = tuple(CancellingTransport(tag) for tag in (tags or ("drone",)))
        registry = {t.tag: t for t in made}
        for t in made:
            target = "streamablehttp_client" if t.tag != "drone" else "sse_client"
            monkeypatch.setattr(f"droneserver.llm.mcp_session.{target}", t.client)
        monkeypatch.setattr("droneserver.llm.mcp_session.ClientSession", _client_session_for(registry))
        return made if len(made) > 1 else made[0]

    return _make


# ------------------- 1. the platform behaviour that makes this a bug at all


async def test_a_transports_own_task_group_cancels_the_task_that_entered_it():
    """Characterization: nothing of ours is under test.

    It records the anyio contract the whole fix rests on. A cancel scope
    cancelled from inside its own task group cancels its *owner*, wherever that
    owner happens to be - which, in the harness, is mid-tool-call. If anyio ever
    stops doing this, the containment below is no longer load-bearing.
    """
    transport = CancellingTransport()
    stack = contextlib.AsyncExitStack()
    await stack.enter_async_context(transport.client("http://maps"))

    task = asyncio.current_task()
    assert task.cancelling() == 0
    transport.scopes[-1].cancel()

    with pytest.raises(asyncio.CancelledError) as caught:
        await anyio.sleep(3600)  # "waiting for the tool result"
    assert "cancel scope" in str(caught.value)
    assert task.cancelling() == 1, "anyio cancels its owner through task.cancel()"

    # ...and unwinding the scope in its owning task is what gives it back.
    await stack.aclose()
    assert task.cancelling() == 0, (
        "anyio no longer reclaims its own cancellation on exit; the transport/genuine "
        "discrimination in _still_owed is no longer sound"
    )


# ---------------------------------- 2. the call path survives and is recorded


async def test_a_transport_that_cancels_mid_call_becomes_a_transport_error(transports):
    """The crash, reproduced, and its fix: an Exception, not a bare cancellation."""
    t = transports()
    t.die_on = {1}
    t.far_end_gone = False  # the reconnect will succeed
    session = LiveMCPSession("http://drone", "key")
    await session.__aenter__()

    # Under the defect this raised asyncio.CancelledError and killed the run.
    result = await session.call_raw("search_places", {"q": "hospital"}, 10)
    assert result["status"] == "success"
    assert session.transport_cancellations == 1
    assert session.reconnects == 1
    await session.aclose()


async def test_the_cancellation_is_not_a_baseexception_the_retry_path_cannot_catch(transports):
    """The precise reason the old code died: `except Exception` cannot see it."""
    t = transports()
    session = LiveMCPSession("http://drone", "key")
    await session.__aenter__()
    t.die_on = {1}
    t.far_end_gone = True  # stays gone, so the reconnect cannot rescue the call

    with pytest.raises(MCPTransportError) as caught:
        await session.call_raw("search_places", {}, 10)
    assert isinstance(caught.value, Exception), "a retry path written around Exception must be able to catch it"
    assert "transport cancelled" in str(caught.value)


async def test_the_trial_task_is_left_usable_afterwards(transports):
    """The run must go on: no leftover cancellation, no dead session."""
    t = transports()
    session = LiveMCPSession("http://drone", "key")
    await session.__aenter__()
    t.die_on = {1}
    t.far_end_gone = True

    result, record = await session.call("search_places", {}, turn=3, seq=1, timeout_s=10)
    assert record.status == "transport_error"
    assert "transport cancelled" in (record.error or "")
    assert result["status"] == "transport_error"

    # The task itself is clean - under the defect it was cancelled, and an
    # orphaned scope went on cancelling every later await, which is why an
    # aircraft was once left airborne.
    assert asyncio.current_task().cancelling() == 0
    await asyncio.sleep(0.01)

    # And once the far end comes back, the very next call works: the session
    # re-opens itself rather than failing the rest of the trial on a stale None.
    t.far_end_gone = False
    result, record = await session.call("get_position", {}, timeout_s=10)
    assert record.status == "success", record.error
    await session.aclose()


async def test_a_connect_cancelled_by_its_transport_is_also_an_exception(transports):
    """The second half of the crash: the reconnect died the same way the call did."""
    t = transports()
    t.far_end_gone = True
    session = LiveMCPSession("http://drone", "key")

    with pytest.raises(MCPTransportError) as caught:
        await session.__aenter__()
    assert "connecting to" in str(caught.value)
    assert asyncio.current_task().cancelling() == 0


# ------------------------------------ 3. a real cancellation is NOT masked


async def test_a_genuine_cancellation_of_the_trial_still_cancels_it(transports):
    """The guard must not become a way to ignore the wall clock, or Ctrl-C."""
    t = transports()
    outcome: dict = {}
    ready = asyncio.Event()

    async def trial():
        session = LiveMCPSession("http://drone", "key")
        await session.__aenter__()  # opened in this task, as the harness does
        ready.set()
        try:
            await session.call_raw("slow_tool", {}, 60)  # parks, as a long command does
        except asyncio.CancelledError:
            outcome["result"] = "cancelled"
            raise
        except BaseException as e:  # pragma: no cover - would mean it was masked
            outcome["result"] = f"masked as {type(e).__name__}"
            raise

    task = asyncio.create_task(trial())
    await ready.wait()
    await asyncio.sleep(0.05)
    task.cancel()  # the way a wall-clock guard, or Ctrl-C, does it
    with pytest.raises(asyncio.CancelledError):
        await task
    assert outcome.get("result") == "cancelled", outcome
    assert t.exited_by == [task], "a cancelled trial must still have closed its own transport"


async def test_a_cancellation_that_outlives_the_teardown_is_re_raised(transports):
    """The discrimination, at its sharpest: two cancellations, one of them real.

    The transport cancels its owner, and a genuine ``task.cancel()`` lands as
    well. Unwinding gives back only the transport's, so one is still owed - and
    a cancellation that is still owed must win.
    """
    t = transports()
    outcome: dict = {}
    ready = asyncio.Event()

    async def trial():
        session = LiveMCPSession("http://drone", "key")
        await session.__aenter__()
        ready.set()
        await asyncio.sleep(0.05)
        task = asyncio.current_task()
        owed = task.cancelling()
        try:
            t.scopes[-1].cancel()  # the transport dies...
            task.cancel()  # ...and the wall clock fires too
            await anyio.sleep(3600)
        except asyncio.CancelledError:
            await session.aclose()
            outcome["still_owed"] = task.cancelling() > owed
            raise

    task = asyncio.create_task(trial())
    await ready.wait()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert outcome["still_owed"] is True, "the genuine cancellation was reclaimed along with the transport's"


async def test_a_borrowed_session_does_not_unwind_a_scope_it_does_not_own(transports):
    """The same-task rule outranks the containment.

    Containing a cancellation means unwinding the transport, and unwinding a
    transport from a task that did not open it is the *2026-08-20* defect. So a
    stranger meeting a cancellation re-raises it rather than tidying up.
    """
    t = transports()
    session = LiveMCPSession("http://drone", "key")
    await session.__aenter__()
    owner = asyncio.current_task()
    in_call = asyncio.Event()

    async def stranger():
        in_call.set()
        await session.call_raw("slow_tool", {}, 60)  # borrowed, and parked

    borrower = asyncio.create_task(stranger())
    await in_call.wait()
    await asyncio.sleep(0.05)
    borrower.cancel()
    with pytest.raises(asyncio.CancelledError):
        await borrower

    assert t.exited_by == [], "a stranger must not have unwound a transport it does not own"
    assert owner.cancelling() == 0, "the owner was cancelled by somebody else's cleanup"
    await session.aclose()
    assert t.exited_by == [owner]


# --------------------- 4. the two-server path this was actually found on


async def test_the_maps_server_dying_does_not_end_the_trial(transports):
    """Mission 6's shape: two servers, one tool list, and the second one dies.

    This is glm-5.2's trial. The drone reads land, the first Maps call meets a
    dead transport, and the trial must go on flying: one recorded
    ``transport_error`` for the Maps call, and the drone tools still working.
    """
    drone, maps = transports("drone", "maps")
    agent = LiveMCPSession("http://drone", "key")
    hosted = LiveMCPSession("http://maps", "maps-key", transport="http", auth_header="X-Goog-Api-Key")
    session = MultiServerSession(agent, [("google-maps", hosted)])
    await session.__aenter__()
    tools = [t.name for t in await session.list_tools()]
    assert tools == ["get_position", "arm_drone", "search_places"]
    maps.die_on = {1}
    maps.far_end_gone = True

    _, drone_read = await session.call("get_position", {}, turn=1, seq=1, timeout_s=10)
    assert drone_read.status == "success"

    _, lookup = await session.call("search_places", {"q": "hospital"}, turn=1, seq=2, timeout_s=10)
    assert lookup.status == "transport_error"
    assert "transport cancelled" in (lookup.error or "")

    # The aircraft is still flyable, which is the whole point.
    assert asyncio.current_task().cancelling() == 0
    _, after = await session.call("arm_drone", {}, turn=2, seq=1, timeout_s=10)
    assert after.status == "success"
    await session.aclose()


async def test_the_drone_session_is_closed_even_if_an_extra_server_will_not_close(transports):
    """An extra server's bad exit must not leave the drone connection open."""
    drone, maps = transports("drone", "maps")
    agent = LiveMCPSession("http://drone", "key")
    hosted = LiveMCPSession("http://maps", "", transport="http")
    session = MultiServerSession(agent, [("google-maps", hosted)])
    await session.__aenter__()

    async def refuses():
        raise RuntimeError("this server will not shut down")

    hosted.aclose = refuses  # type: ignore[method-assign]
    await session.aclose()
    assert drone.exited_by == [asyncio.current_task()], "the drone session was left open"


async def test_an_extra_server_that_will_not_open_does_not_strand_the_drone_session(transports):
    """A half-open MultiServerSession leaves an anyio scope alive in this task."""
    drone, maps = transports("drone", "maps")
    maps.far_end_gone = True  # the Maps server refuses to come up

    agent = LiveMCPSession("http://drone", "key")
    hosted = LiveMCPSession("http://maps", "", transport="http")
    session = MultiServerSession(agent, [("google-maps", hosted)])

    with pytest.raises(MCPTransportError):
        await session.__aenter__()
    assert drone.exited_by == [asyncio.current_task()], "the drone transport was left open with no owner"
    assert asyncio.current_task().cancelling() == 0
