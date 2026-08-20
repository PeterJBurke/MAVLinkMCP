"""FIX 14: "Drone FORCE-armed" said of a vehicle that did not arm.

``arm_drone(force=True)`` sent MAVSDK's ``arm_force`` - MAV_CMD_COMPONENT_ARM_
DISARM carrying the force magic - and returned::

    {"status": "success", "message": "Drone FORCE-armed (prearm checks bypassed)"}

the instant the autopilot ACCEPTED the command. Acceptance is not arming.
ArduPilot accepted it and left the vehicle disarmed on 2026-08-19, and the
model, told it had an armed aircraft, commanded a takeoff of a machine standing
inert on its pad. The plain ``arm`` path had the same shape: it raises on a
refusal it is TOLD about, and reports success on everything else.

Both paths now finish the way every other honesty fix in ``action.py``
finishes - by asking the autopilot, and reporting what it said:

* armed        -> success, with the evidence and how long it took
* still disarmed after the bound -> failed, saying so in the words a model
  needs (do not command a takeoff), and telling it force=True cannot help
* the topic will not answer -> failed, because "we cannot see an armed
  aircraft" is not evidence of one, and a takeoff is the wrong thing to do
  next either way
"""

from __future__ import annotations

import types

import pytest

from droneserver.mavlink.connection import MAVLinkConnector
from droneserver.tools import action as action_tools

arm_drone = action_tools.arm_drone.__wrapped__


class _Action:
    """An autopilot that may or may not act on the command it accepts."""

    def __init__(self, arms: bool = True, raises: Exception | None = None) -> None:
        self.arms, self.raises = arms, raises
        self.armed = False
        self.calls: list[str] = []

    async def _command(self, name: str) -> None:
        self.calls.append(name)
        if self.raises is not None:
            raise self.raises
        if self.arms:
            self.armed = True

    async def arm(self) -> None:
        await self._command("arm")

    async def arm_force(self) -> None:
        await self._command("arm_force")


class _Telemetry:
    def __init__(self, action: _Action, silent: bool = False) -> None:
        self._action, self._silent = action, silent
        self.reads = 0

    async def armed(self):
        self.reads += 1
        if self._silent:
            raise TimeoutError("the armed topic is not answering")
        yield self._action.armed


def _ctx(arms: bool = True, silent: bool = False, raises: Exception | None = None):
    act = _Action(arms=arms, raises=raises)
    drone = types.SimpleNamespace(action=act, telemetry=_Telemetry(act, silent=silent))
    connector = MAVLinkConnector(drone=drone)
    connector.connection_ready.set()
    ctx = types.SimpleNamespace(request_context=types.SimpleNamespace(lifespan_context=connector))
    return ctx, act


@pytest.fixture(autouse=True)
def _dont_spend_the_timeout(monkeypatch):
    """The bound is 10 s of real waiting; the behaviour under test is not."""
    monkeypatch.setattr(action_tools, "ARM_CONFIRM_TIMEOUT_S", 0.05)
    monkeypatch.setattr(action_tools, "ARM_POLL_INTERVAL_S", 0.01)


async def test_a_vehicle_that_arms_is_reported_armed_with_evidence():
    ctx, act = _ctx(arms=True)
    result = await arm_drone(ctx=ctx)

    assert result["status"] == "success"
    assert result["armed"] is True
    assert "armed" in result["evidence"]
    assert act.calls == ["arm"]


async def test_force_arm_that_does_not_arm_is_not_a_success():
    """The observed defect, exactly."""
    ctx, act = _ctx(arms=False)
    result = await arm_drone(ctx=ctx, force=True)

    assert result["status"] == "failed"
    assert result["armed"] is False
    assert "still" in result["error"] and "DISARMED" in result["error"]
    assert act.calls == ["arm_force"]


async def test_the_failure_tells_the_model_not_to_take_off():
    ctx, _ = _ctx(arms=False)
    result = await arm_drone(ctx=ctx, force=True)

    assert "takeoff" in result["error"]
    assert "Do not command a takeoff" in result["remedy"]


async def test_the_plain_path_that_does_not_arm_is_not_a_success_either():
    ctx, act = _ctx(arms=False)
    result = await arm_drone(ctx=ctx)

    assert result["status"] == "failed"
    assert result["armed"] is False
    # ... and it is told what force=True can and cannot do, since that is the
    # next thing a model reaches for.
    assert "force=True bypasses PREARM checks only" in result["remedy"]
    assert act.calls == ["arm"]


async def test_force_arm_that_does_arm_still_says_so():
    ctx, _ = _ctx(arms=True)
    result = await arm_drone(ctx=ctx, force=True)

    assert result["status"] == "success"
    assert result["armed"] is True
    assert "FORCE-armed" in result["message"]


async def test_an_unreadable_armed_topic_is_reported_as_unknown_not_armed():
    ctx, act = _ctx(arms=True, silent=True)
    result = await arm_drone(ctx=ctx)

    assert result["status"] == "failed"
    assert result["armed"] is None
    assert "could not be read" in result["error"]
    assert "get_armed" in result["remedy"]
    # The command WAS sent - the tool is honest about not knowing, not about
    # having refused.
    assert act.calls == ["arm"]


async def test_a_refused_command_still_reports_the_refusal():
    ctx, _ = _ctx(raises=RuntimeError("COMMAND_DENIED"))
    result = await arm_drone(ctx=ctx)

    assert result["status"] == "failed"
    assert result["armed"] is False
    assert "COMMAND_DENIED" in result["error"]


async def test_a_vehicle_that_arms_late_is_still_reported_armed():
    """Confirmation is a bounded WAIT, not a single read."""
    ctx, act = _ctx(arms=False)

    reads = {"n": 0}

    class _Slow(_Telemetry):
        async def armed(self):
            reads["n"] += 1
            if reads["n"] >= 3:
                act.armed = True
            yield act.armed

    ctx.request_context.lifespan_context.drone.telemetry = _Slow(act)
    result = await arm_drone(ctx=ctx)

    assert result["status"] == "success"
    assert reads["n"] >= 3


async def test_a_lost_connection_is_still_reported_before_anything_is_sent():
    ctx, act = _ctx()
    ctx.request_context.lifespan_context.connection_ready.clear()

    async def never(_connector, timeout=30.0):
        return False

    original = action_tools.ensure_connection
    action_tools.ensure_connection = never
    try:
        result = await arm_drone(ctx=ctx)
    finally:
        action_tools.ensure_connection = original

    assert result["status"] == "failed"
    assert act.calls == []
