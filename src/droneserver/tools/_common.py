"""Shared helpers for v2 tool modules."""

import asyncio

from mcp.server.fastmcp import Context

from droneserver.mavlink.connection import ensure_connection

CONN_ERROR = {"status": "failed", "error": "Drone connection timeout. Please wait and try again."}


async def get_drone(ctx: Context):
    """Return the connected System, or None if the drone link is not up."""
    connector = ctx.request_context.lifespan_context
    if not await ensure_connection(connector):
        return None
    return connector.drone


async def first_stream_item(stream, timeout_s: float = 8.0):
    """Read one item from a MavSDK subscription stream (or raise TimeoutError)."""

    async def read():
        async for item in stream:
            return item
        raise TimeoutError("stream ended without an item")

    return await asyncio.wait_for(read(), timeout=timeout_s)
