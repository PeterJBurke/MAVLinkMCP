"""The drone end of the loop: a live MCP session, and an independent recorder.

**Who this is for:** anyone reading how the model's tool calls actually reach
the drone server, and how we know what the drone really did.

**What this does.** Two things, deliberately kept apart.

:class:`LiveMCPSession` is one long-lived connection to the drone server over
MCP (Model Context Protocol - the open standard the server speaks). It fetches
the real tool schemas and executes tool calls, timing each one. It is what the
model acts through.

:class:`TelemetryRecorder` is a *second, separate* connection that does nothing
but ask the drone where it is, roughly once a second, for the whole trial. It
never takes an instruction from the model. This is the flight recorder: when
the harness later decides whether a mission passed, it reads this track, not
the model's account of itself. A model that says "I have successfully reached
20 metres" is making a claim; the track is evidence.

**Why the two connections announce themselves differently.** The server writes
an audit line per tool call and stores the client's self-reported name. The
agent connects as ``droneserver-llm-agent/<provider>:<model>`` and the recorder
as ``droneserver-telemetry-recorder``. That single field lets the analysis
separate the model's commands from the recorder's polling in the server's own
log - and it also means the audit trail permanently records which model flew.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import time
from dataclasses import dataclass, field
from datetime import timedelta

import mcp.types as mcp_types
from mcp import ClientSession
from mcp.client.sse import sse_client

from droneserver.llm.providers import ToolSpec

AGENT_CLIENT_NAME = "droneserver-llm-agent"
RECORDER_CLIENT_NAME = "droneserver-telemetry-recorder"


class MCPTransportError(RuntimeError):
    """The connection to the drone server failed (not a tool refusing a call)."""


@dataclass
class CallRecord:
    """One tool call as the harness saw it, from the outside.

    ``wall_ms`` is the command-layer latency: request leaves this process,
    result comes back. It includes the network hop to the server and the
    server's own work. The server separately records what it spent inside the
    safety layer and the tool, and the two are joined afterwards - see
    ``runner.py``. Neither number includes any model thinking time.
    """

    turn: int
    seq: int
    tool: str
    arguments: dict
    started_at: float
    wall_ms: float
    status: str
    rule: str | None = None
    error: str | None = None
    confirmation_required: bool = False
    #: Set when the model asked for a tool that does not exist, or sent
    #: arguments that were not valid JSON: the call never reached the server.
    client_side_rejection: str | None = None
    result: dict = field(default_factory=dict)


class LiveMCPSession:
    """One persistent MCP session, with reconnect.

    A single session for a whole mission is closer to how a real MCP client
    behaves than reconnecting per call, and it keeps the connection cost out of
    the per-command latency figures. If the transport does drop mid-mission we
    reconnect once and retry, and the retry is visible in the record rather
    than smoothed away.
    """

    def __init__(self, url: str, api_key: str = "", client_name: str = AGENT_CLIENT_NAME, client_version: str = "2"):
        self.url = url
        self.api_key = api_key
        self.client_name = client_name
        self.client_version = client_version
        self._session: ClientSession | None = None
        self._stack: contextlib.AsyncExitStack | None = None
        self.reconnects = 0

    @property
    def client_label(self) -> str:
        """Exactly what the server will store in its audit log's ``model`` field."""
        return f"{self.client_name}/{self.client_version}" if self.client_version else self.client_name

    @property
    def headers(self) -> dict:
        return {"X-API-Key": self.api_key} if self.api_key else {}

    async def __aenter__(self) -> LiveMCPSession:
        await self._connect()
        return self

    async def __aexit__(self, *exc) -> None:
        await self.aclose()

    async def _connect(self) -> None:
        stack = contextlib.AsyncExitStack()
        read, write = await stack.enter_async_context(sse_client(self.url, headers=self.headers))
        session = await stack.enter_async_context(
            ClientSession(
                read,
                write,
                client_info=mcp_types.Implementation(name=self.client_name, version=self.client_version),
            )
        )
        await session.initialize()
        self._stack, self._session = stack, session

    async def aclose(self) -> None:
        stack, self._stack, self._session = self._stack, None, None
        if stack is not None:
            with contextlib.suppress(Exception):
                await stack.aclose()

    async def _reconnect(self) -> None:
        await self.aclose()
        self.reconnects += 1
        await self._connect()

    async def list_tools(self) -> list[ToolSpec]:
        """The server's real tool schemas, exactly as advertised on the wire."""
        assert self._session is not None
        listing = await self._session.list_tools()
        return [
            ToolSpec(t.name, t.description or "", t.inputSchema or {"type": "object", "properties": {}})
            for t in listing.tools
        ]

    async def call_raw(self, tool: str, arguments: dict, timeout_s: float = 300.0) -> dict:
        """Execute one tool call and return the tool's own result dictionary."""
        assert self._session is not None
        try:
            result = await self._session.call_tool(
                tool, arguments=arguments or None, read_timeout_seconds=timedelta(seconds=timeout_s)
            )
        except Exception:
            await self._reconnect()
            assert self._session is not None
            result = await self._session.call_tool(
                tool, arguments=arguments or None, read_timeout_seconds=timedelta(seconds=timeout_s)
            )
        return _parse(tool, result)

    async def call(self, tool: str, arguments: dict, *, turn: int = 0, seq: int = 0, timeout_s: float = 300.0):
        """Execute one tool call, timed, and return ``(result, CallRecord)``.

        A tool that refuses the call is not an error here: a refusal is a
        result, and one of the more interesting ones. Only a broken connection
        produces a ``transport_error`` record.
        """
        started = time.time()
        clock = time.perf_counter()
        try:
            result = await self.call_raw(tool, arguments, timeout_s)
        except Exception as e:
            record = CallRecord(
                turn=turn,
                seq=seq,
                tool=tool,
                arguments=arguments,
                started_at=started,
                wall_ms=(time.perf_counter() - clock) * 1000,
                status="transport_error",
                error=f"{type(e).__name__}: {e}",
            )
            return {"status": "transport_error", "error": record.error}, record
        wall_ms = (time.perf_counter() - clock) * 1000
        status = result.get("status", "unknown") if isinstance(result, dict) else "unknown"
        record = CallRecord(
            turn=turn,
            seq=seq,
            tool=tool,
            arguments=arguments,
            started_at=started,
            wall_ms=wall_ms,
            status=status,
            rule=result.get("rule") if isinstance(result, dict) else None,
            error=result.get("error") if isinstance(result, dict) else None,
            confirmation_required=status == "confirmation_required",
            result=result if isinstance(result, dict) else {"raw": result},
        )
        return result, record

    async def wait_ready(self, timeout_s: float = 180.0) -> bool:
        """Block until the server reports a live drone link (or give up)."""
        deadline = time.monotonic() + timeout_s
        while time.monotonic() < deadline:
            try:
                if (await self.call_raw("get_armed", {}, 40)).get("status") == "success":
                    return True
            except Exception:
                pass
            await asyncio.sleep(3)
        return False


def _parse(tool: str, result) -> dict:
    text = "\n".join(i.text for i in result.content if getattr(i, "text", None) is not None)
    if result.isError:
        raise MCPTransportError(f"{tool}: {text}")
    structured = getattr(result, "structuredContent", None)
    if isinstance(structured, dict):
        # FastMCP wraps plain (untyped) returns as {"result": ...}.
        return structured["result"] if set(structured) == {"result"} else structured
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        return {"raw": text}


# ------------------------------------------------------------------- recorder


@dataclass
class TelemetrySample:
    t: float
    latitude_deg: float | None = None
    longitude_deg: float | None = None
    relative_altitude_m: float | None = None
    absolute_altitude_m: float | None = None
    armed: bool | None = None
    in_air: bool | None = None


class TelemetryRecorder:
    """Polls the drone's own telemetry for the whole trial, on its own session.

    Runs as a background task. It only ever calls read-only tools, so it cannot
    change what it is measuring, and it swallows its own errors: a hiccup in
    the recorder must not fail a flight.
    """

    def __init__(self, url: str, api_key: str = "", interval_s: float = 1.5):
        self.session = LiveMCPSession(url, api_key, client_name=RECORDER_CLIENT_NAME, client_version="2")
        self.interval_s = interval_s
        self.samples: list[TelemetrySample] = []
        self.errors = 0
        self._task: asyncio.Task | None = None
        self._stop = asyncio.Event()

    async def start(self) -> None:
        await self.session.__aenter__()
        self._stop.clear()
        self._task = asyncio.create_task(self._loop())

    async def stop(self) -> None:
        self._stop.set()
        if self._task is not None:
            with contextlib.suppress(Exception):
                await asyncio.wait_for(self._task, timeout=30)
            self._task = None
        await self.session.aclose()

    async def _loop(self) -> None:
        while not self._stop.is_set():
            await self.sample_once()
            with contextlib.suppress(asyncio.TimeoutError):
                await asyncio.wait_for(self._stop.wait(), timeout=self.interval_s)

    async def sample_once(self) -> TelemetrySample | None:
        sample = TelemetrySample(t=time.time())
        try:
            position = await self.session.call_raw("get_position", {}, 30)
            if position.get("status") == "success":
                p = position.get("position") or {}
                sample.latitude_deg = p.get("latitude_deg")
                sample.longitude_deg = p.get("longitude_deg")
                sample.relative_altitude_m = p.get("relative_altitude_m")
                sample.absolute_altitude_m = p.get("absolute_altitude_m")
            armed = await self.session.call_raw("get_armed", {}, 30)
            if armed.get("status") == "success":
                sample.armed = bool(armed.get("armed"))
            in_air = await self.session.call_raw("get_in_air", {}, 30)
            if in_air.get("status") == "success":
                sample.in_air = bool(in_air.get("in_air", in_air.get("is_in_air")))
        except Exception:
            self.errors += 1
            return None
        self.samples.append(sample)
        return sample
