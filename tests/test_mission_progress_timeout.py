"""A silent mission-progress stream must answer "unknown", never hang.

MavSDK publishes ``mission_progress`` when the vehicle crosses a waypoint, not
on a timer. Three tools read it with an unbounded ``async for ... break``, so a
mission flying a long leg - or one that never started - left the read waiting
for a transition that was minutes away or would never come. The MCP call stayed
open until the *client's* 300 s timeout and then returned nothing at all.

That is not hypothetical: in the halted 2026-08-10 N=5 campaign it cost T4
trial 1 five minutes and an answer, after which the model abandoned the mission
tools and polled ``get_position`` 46 times in a 64-call trial - which is how a
2,000,000-token budget gets spent watching a three-waypoint flight.

Progress is the *optional* part of every one of those answers, so "we do not
know how far along it is" is a perfectly good reply and a hang is not.
"""

import asyncio

import pytest

from droneserver.tools.mission import MISSION_PROGRESS_TIMEOUT_S, _mission_progress_or_unknown


class _Progress:
    def __init__(self, current: int, total: int):
        self.current, self.total = current, total


class _Mission:
    """A mission plugin whose progress stream behaves as configured."""

    def __init__(self, mode: str):
        self.mode = mode

    async def mission_progress(self):
        if self.mode == "reports":
            yield _Progress(2, 5)
        elif self.mode == "silent":
            # The real failure: publishes on transitions, and there is no
            # transition coming.
            await asyncio.sleep(3600)
        elif self.mode == "ended":
            return
        elif self.mode == "raises":
            raise RuntimeError("mission plugin is not available on this firmware")
            yield  # pragma: no cover - unreachable, keeps this a generator


class _Drone:
    def __init__(self, mode: str):
        self.mission = _Mission(mode)


async def test_a_reporting_stream_is_read_normally():
    assert await _mission_progress_or_unknown(_Drone("reports")) == (2, 5)


async def test_a_silent_stream_answers_unknown_within_the_timeout():
    """The regression. Without the bound this never returns."""
    started = asyncio.get_running_loop().time()
    assert await asyncio.wait_for(
        _mission_progress_or_unknown(_Drone("silent")), timeout=MISSION_PROGRESS_TIMEOUT_S + 5
    ) == (0, 0)
    elapsed = asyncio.get_running_loop().time() - started
    assert elapsed < MISSION_PROGRESS_TIMEOUT_S + 2, f"waited {elapsed:.1f}s for a stream that never speaks"


@pytest.mark.parametrize("mode", ["ended", "raises"])
async def test_a_broken_stream_costs_the_progress_and_not_the_call(mode):
    """A firmware without the plugin must not turn every caller into an error.

    ``is_mission_finished`` answers "is it finished" from a different call
    entirely; losing the waypoint count is a degraded answer, not a failed one.
    """
    assert await _mission_progress_or_unknown(_Drone(mode)) == (0, 0)


def test_the_timeout_is_shorter_than_a_client_would_wait():
    """Bounded well below the 300 s MCP tool timeout, or it is not a bound."""
    from droneserver.llm.agent import Limits

    assert MISSION_PROGRESS_TIMEOUT_S < Limits.tool_timeout_s / 10
