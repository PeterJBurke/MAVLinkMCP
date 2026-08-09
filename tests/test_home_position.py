"""Regression tests: reading home on an autopilot that only sends it on request.

ArduPilot does not stream HOME_POSITION unsolicited, so MAVSDK's passive
``telemetry.home()`` subscription never emits and ``get_home_position`` timed
out on a vehicle whose home was set. These tests pin the request-then-retry
behaviour with fake MAVSDK stand-ins - no SITL required.
"""

import asyncio

import pytest

from droneserver.telemetry import home as home_mod
from droneserver.telemetry.home import read_home


class FakeHome:
    def __init__(self, lat=33.6458611, lon=-117.84275, alt=25.1):
        self.latitude_deg = lat
        self.longitude_deg = lon
        self.absolute_altitude_m = alt


class FakeTelemetry:
    """A telemetry plugin whose ``home()`` stream behaves like a firmware's.

    ``streams_unsolicited`` models PX4 (home arrives on its own);
    otherwise the stream stays silent until ``set_rate_home`` is called,
    which is exactly ArduPilot's behaviour.
    """

    def __init__(self, streams_unsolicited: bool, rate_supported: bool = True):
        self.streaming = streams_unsolicited
        self.rate_supported = rate_supported
        self.rate_calls: list[float] = []

    async def set_rate_home(self, rate_hz: float):
        self.rate_calls.append(rate_hz)
        if not self.rate_supported:
            raise RuntimeError("COMMAND_DENIED")
        self.streaming = True

    async def home(self):
        while True:
            if self.streaming:
                yield FakeHome()
                return
            await asyncio.sleep(0.01)


class FakeDrone:
    def __init__(self, telemetry):
        self.telemetry = telemetry


@pytest.fixture(autouse=True)
def fast_timeouts(monkeypatch):
    """Keep the tests sub-second; the production budget is 10 s."""
    monkeypatch.setattr(home_mod, "PROBE_TIMEOUT_S", 0.1)
    monkeypatch.setattr(home_mod, "REQUEST_TIMEOUT_S", 0.5)


async def test_ardupilot_home_is_requested_then_read():
    """The bug: a silent subscription must trigger a rate request, not a timeout."""
    telemetry = FakeTelemetry(streams_unsolicited=False)
    home = await read_home(FakeDrone(telemetry), timeout_s=2.0)
    assert telemetry.rate_calls, "a silent home stream must be requested, not waited on"
    assert home.latitude_deg == pytest.approx(33.6458611)


async def test_px4_home_needs_no_request():
    """A firmware that already streams home must not be sent a rate request."""
    telemetry = FakeTelemetry(streams_unsolicited=True)
    home = await read_home(FakeDrone(telemetry), timeout_s=2.0)
    assert telemetry.rate_calls == [], "home already streaming - no request should be sent"
    assert home.absolute_altitude_m == pytest.approx(25.1)


async def test_denied_rate_request_still_times_out_cleanly():
    """A firmware that has no home at all must raise TimeoutError, not hang."""
    telemetry = FakeTelemetry(streams_unsolicited=False, rate_supported=False)
    with pytest.raises(TimeoutError):
        await read_home(FakeDrone(telemetry), timeout_s=0.3)
    assert telemetry.rate_calls, "the request must still be attempted before giving up"


async def test_rate_request_that_never_answers_is_bounded():
    """A set_rate_home that hangs must not consume the caller's whole budget."""

    class HangingTelemetry(FakeTelemetry):
        async def set_rate_home(self, rate_hz: float):
            self.rate_calls.append(rate_hz)
            await asyncio.sleep(30)

    telemetry = HangingTelemetry(streams_unsolicited=False)
    started = asyncio.get_running_loop().time()
    with pytest.raises(TimeoutError):
        await read_home(FakeDrone(telemetry), timeout_s=0.3)
    elapsed = asyncio.get_running_loop().time() - started
    assert elapsed < 2.0, f"read_home hung for {elapsed:.1f}s on an unanswered rate request"
