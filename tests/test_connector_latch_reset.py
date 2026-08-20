"""FIX 2: per-flight latches must not leak from one trial into the next.

``landing_in_progress`` is set while a landing is underway and cleared only in
monitor_flight's on-ground branch. A trial that ends mid-landing leaves it set;
the process-wide connector persists, so the NEXT trial inherits it and
monitor_flight keeps answering "LANDING... call monitor_flight again" on a
motionless aircraft (an 88-call loop was observed this way). ``was_airborne``
and ``pending_destination`` leak the same way. All three are reset at the start
of a fresh flight: on a fresh connection, and on every arm_drone (the
between-trial ferry arms once per trial, so this fires every trial).
"""

from __future__ import annotations

import asyncio

from droneserver.mavlink.connection import MAVLinkConnector
from droneserver.tools.action import arm_drone


class _FakeAction:
    def __init__(self) -> None:
        self.armed = False

    async def arm(self) -> None:
        self.armed = True

    async def arm_force(self) -> None:
        self.armed = True


class _FakeTelemetry:
    """The armed topic arm_drone confirms itself against (FIX 14)."""

    def __init__(self, action: _FakeAction) -> None:
        self._action = action

    async def armed(self):
        yield self._action.armed


class _FakeDrone:
    def __init__(self) -> None:
        self.action = _FakeAction()
        self.telemetry = _FakeTelemetry(self.action)


class _FakeRequestContext:
    def __init__(self, connector: MAVLinkConnector) -> None:
        self.lifespan_context = connector


class _FakeCtx:
    def __init__(self, connector: MAVLinkConnector) -> None:
        self.request_context = _FakeRequestContext(connector)


def _dirty_connector() -> MAVLinkConnector:
    connector = MAVLinkConnector(drone=_FakeDrone())
    connector.landing_in_progress = True
    connector.was_airborne = True
    connector.pending_destination = {"latitude": 1.0, "longitude": 2.0}
    return connector


def test_reset_flight_latches_clears_all_three():
    connector = _dirty_connector()
    connector.reset_flight_latches()
    assert connector.landing_in_progress is False
    assert connector.was_airborne is False
    assert connector.pending_destination is None


def test_arming_clears_a_leaked_landing_state():
    """The real path: a new trial inherits landing_in_progress, then arms.
    After arming, no leaked landing state remains to drive the monitor loop."""
    connector = _dirty_connector()
    connector.connection_ready.set()  # ensure_connection returns immediately
    ctx = _FakeCtx(connector)

    result = asyncio.run(arm_drone.__wrapped__(ctx=ctx))

    assert result["status"] == "success"
    assert connector.drone.action.armed is True
    assert connector.landing_in_progress is False
    assert connector.was_airborne is False
    assert connector.pending_destination is None


def test_force_arm_also_clears_leaked_latches():
    connector = _dirty_connector()
    connector.connection_ready.set()
    ctx = _FakeCtx(connector)

    result = asyncio.run(arm_drone.__wrapped__(ctx=ctx, force=True))

    assert result["status"] == "success"
    assert connector.landing_in_progress is False
    assert connector.was_airborne is False
    assert connector.pending_destination is None
