"""Minimal synchronous MCP client harness for SITL integration tests.

This is the harness every Phase 2 tool test will use: it talks to a running
droneserver over HTTP/SSE exactly like a real MCP client (Claude Desktop,
scripted API clients, ...) would.

Synchronous on purpose - one short-lived MCP session per call keeps the
pytest fixture/event-loop story trivial. The server side holds the persistent
drone connection in its global connector, so per-call sessions are cheap and
see the same drone state.
"""

import asyncio
import json
from datetime import timedelta

from mcp import ClientSession
from mcp.client.sse import sse_client


class ToolCallError(RuntimeError):
    """The server returned an MCP-level error for a tool call."""


class MCPToolClient:
    def __init__(self, url: str):
        self.url = url

    def list_tools(self) -> list[str]:
        """Tool names as advertised over the wire."""
        return asyncio.run(self._list_tools())

    def call(self, tool: str, timeout: float = 120.0, **arguments) -> dict:
        """Call a tool; returns the tool's dict result.

        Raises ToolCallError if the server reports an MCP-level error (e.g. an
        unhandled exception inside the tool).
        """
        return asyncio.run(self._call(tool, arguments, timeout))

    async def _list_tools(self) -> list[str]:
        async with sse_client(self.url) as (read, write):
            async with ClientSession(read, write) as session:
                await session.initialize()
                result = await session.list_tools()
                return [t.name for t in result.tools]

    async def _call(self, tool: str, arguments: dict, timeout: float) -> dict:
        async with sse_client(self.url) as (read, write):
            async with ClientSession(read, write) as session:
                await session.initialize()
                result = await session.call_tool(
                    tool,
                    arguments=arguments or None,
                    read_timeout_seconds=timedelta(seconds=timeout),
                )
                return _parse_result(tool, result)


def _parse_result(tool: str, result) -> dict:
    text = "\n".join(item.text for item in result.content if getattr(item, "text", None) is not None)
    if result.isError:
        raise ToolCallError(f"{tool}: {text}")

    structured = getattr(result, "structuredContent", None)
    if isinstance(structured, dict):
        # FastMCP wraps plain (untyped) results as {"result": ...}
        if set(structured) == {"result"}:
            return structured["result"]
        return structured

    try:
        return json.loads(text)
    except json.JSONDecodeError:
        return {"raw": text}
