"""Synchronous MCP client used by the mission-suite runner.

Deliberately the same shape as the integration-test helper: one short-lived
HTTP/SSE session per tool call, so a benchmark run exercises the same path a
real LLM client would, including authentication headers.
"""

import asyncio
import json
import time
from dataclasses import dataclass, field
from datetime import timedelta

from mcp import ClientSession
from mcp.client.sse import sse_client


class ToolCallError(RuntimeError):
    """The server reported an MCP-level error (not a tool-level failure)."""


@dataclass
class CallRecord:
    """One tool call as the benchmark saw it (client-side view)."""

    tool: str
    started_at: float
    wall_ms: float
    status: str
    rule: str | None = None
    error: str | None = None
    confirmation_required: bool = False


@dataclass
class BenchmarkClient:
    url: str
    api_key: str = ""
    #: Every call made through this client, in order.
    calls: list[CallRecord] = field(default_factory=list)

    @property
    def headers(self) -> dict:
        return {"X-API-Key": self.api_key} if self.api_key else {}

    def call(self, tool: str, timeout: float = 120.0, **arguments) -> dict:
        started = time.time()
        clock = time.perf_counter()
        try:
            result = asyncio.run(self._call(tool, arguments, timeout))
        except Exception as e:
            self.calls.append(
                CallRecord(
                    tool,
                    started,
                    (time.perf_counter() - clock) * 1000,
                    "transport_error",
                    error=f"{type(e).__name__}: {e}",
                )
            )
            raise
        wall_ms = (time.perf_counter() - clock) * 1000
        status = result.get("status", "unknown") if isinstance(result, dict) else "unknown"
        self.calls.append(
            CallRecord(
                tool=tool,
                started_at=started,
                wall_ms=wall_ms,
                status=status,
                rule=result.get("rule") if isinstance(result, dict) else None,
                error=result.get("error") if isinstance(result, dict) else None,
                confirmation_required=status == "confirmation_required",
            )
        )
        return result

    def call_confirmed(self, tool: str, timeout: float = 120.0, **arguments) -> dict:
        """Call a tool, completing the confirmation round-trip if one is demanded.

        Used by missions that legitimately need a CRITICAL action; the fact
        that a token was required is recorded in the metrics.
        """
        result = self.call(tool, timeout=timeout, **arguments)
        if isinstance(result, dict) and result.get("status") == "confirmation_required":
            token = result.get("confirm_token")
            result = self.call(tool, timeout=timeout, confirm_token=token, **arguments)
        return result

    async def _call(self, tool: str, arguments: dict, timeout: float) -> dict:
        async with sse_client(self.url, headers=self.headers) as (read, write):
            async with ClientSession(read, write) as session:
                await session.initialize()
                result = await session.call_tool(
                    tool,
                    arguments=arguments or None,
                    read_timeout_seconds=timedelta(seconds=timeout),
                )
                return _parse(tool, result)

    def wait_ready(self, timeout_s: float = 180.0) -> bool:
        """Block until the server's drone link is up (or give up)."""
        deadline = time.monotonic() + timeout_s
        while time.monotonic() < deadline:
            try:
                if self.call("get_armed", timeout=40).get("status") == "success":
                    return True
            except Exception:
                pass
            time.sleep(3)
        return False


def _parse(tool: str, result) -> dict:
    text = "\n".join(i.text for i in result.content if getattr(i, "text", None) is not None)
    if result.isError:
        raise ToolCallError(f"{tool}: {text}")
    structured = getattr(result, "structuredContent", None)
    if isinstance(structured, dict):
        if set(structured) == {"result"}:
            return structured["result"]
        return structured
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        return {"raw": text}
