"""Stale-setpoint watchdog for offboard control.

First concrete component of the Phase 3 safety layer.

Rationale: mavsdk_server re-streams the *last* offboard setpoint indefinitely,
so a single MCP tool call like "fly north at 2 m/s" would keep the vehicle
moving forever if the LLM never follows up. Motion setpoints (velocity,
attitude, acceleration, actuator) therefore arm a timer here; if no new
setpoint arrives before it expires, the ``brake`` callback is invoked (the
offboard tools pass one that commands a zero-velocity hover at the current
heading). Position setpoints are self-terminating - the vehicle stops at the
target - so they clear any pending timer instead.
"""

import asyncio
import time
from collections.abc import Awaitable, Callable

from droneserver.logging_setup import logger

BrakeCallback = Callable[[], Awaitable[None]]


class OffboardWatchdog:
    def __init__(self) -> None:
        self._task: asyncio.Task | None = None
        self.last_kind: str | None = None
        self.last_set_at: float | None = None
        self.stale_timeout_s: float | None = None
        self.auto_braked: bool = False

    def note_setpoint(self, kind: str, stale_timeout_s: float | None, brake: BrakeCallback | None) -> None:
        """Record a new setpoint. Motion setpoints pass a timeout + brake
        callback; position setpoints pass None to clear any pending timer."""
        self.cancel()
        self.last_kind = kind
        self.last_set_at = time.monotonic()
        self.stale_timeout_s = stale_timeout_s
        self.auto_braked = False
        if stale_timeout_s is not None and brake is not None:
            self._task = asyncio.get_running_loop().create_task(self._expire_after(stale_timeout_s, brake))

    async def _expire_after(self, timeout_s: float, brake: BrakeCallback) -> None:
        await asyncio.sleep(timeout_s)
        self.auto_braked = True
        logger.warning(
            "offboard watchdog: %s setpoint stale after %.1fs - braking to zero velocity",
            self.last_kind,
            timeout_s,
        )
        try:
            await brake()
        except Exception:
            logger.exception("offboard watchdog: brake callback failed")

    def cancel(self) -> None:
        """Stop any pending timer (new setpoint, offboard stop, or shutdown)."""
        if self._task is not None and not self._task.done():
            self._task.cancel()
        self._task = None

    def status(self) -> dict:
        return {
            "last_setpoint": self.last_kind,
            "age_s": round(time.monotonic() - self.last_set_at, 1) if self.last_set_at else None,
            "stale_timeout_s": self.stale_timeout_s,
            "auto_braked": self.auto_braked,
        }
