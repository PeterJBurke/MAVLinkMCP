"""FIX 15: the ground topics stop arriving, and nobody notices.

``landed_state`` rides MAVLink's EXTENDED_SYS_STATE, which ArduPilot publishes
only after a SET_MESSAGE_INTERVAL request. MAVSDK sends that request ONCE, when
something first subscribes. A request is a message like any other: lose it - or
reboot the autopilot, or drop and remake the link - and the subscription becomes
a passive wait for something that will never arrive again. Two lanes did exactly
that on 2026-08-19, and the silence is indistinguishable from an aircraft that
is simply sitting still.

That silence retires the evidence FIX 10, 11 and 12 all deliberately moved TO -
the autopilot's own landed_state/in_air, the two readings no arming can spoil.
``tools/action._telemetry_now`` waited for them with no bound at all.

The reads now detect it and ask again, on the model of
:mod:`droneserver.telemetry.home` (which solved it for HOME_POSITION), with one
rule that keeps the detector honest: **a dead link is silent on every topic**,
so nothing is re-requested unless something else is demonstrably still
arriving. A whole link that has gone quiet must look exactly as broken as it is.
"""

from __future__ import annotations

import types

import pytest

from droneserver.safety.state import _read_in_air
from droneserver.telemetry import ground_stream
from droneserver.tools.action import _telemetry_now


class _Vehicle:
    """A vehicle whose ground topics speak only when they have been asked.

    ``requested`` starts False, which is the state a lost SET_MESSAGE_INTERVAL
    leaves the link in: everything else is streaming, these two are not.
    """

    def __init__(self, *, requested: bool = False, position: bool = True, accepts_request: bool = True):
        self.requested = requested
        self.publishes_position = position
        self.accepts_request = accepts_request
        self.requests: list[tuple[str, float]] = []
        self.position_reads = 0

    async def position(self):
        self.position_reads += 1
        if not self.publishes_position:
            raise TimeoutError("nothing is arriving")
        yield types.SimpleNamespace(
            latitude_deg=33.6458611,
            longitude_deg=-117.84275,
            relative_altitude_m=0.0,
            absolute_altitude_m=25.1,
        )

    async def landed_state(self):
        if not self.requested:
            raise TimeoutError("EXTENDED_SYS_STATE is not being published")
        yield "LandedState.ON_GROUND"

    async def in_air(self):
        if not self.requested:
            raise TimeoutError("the topic is not being published")
        yield False

    async def _request(self, topic: str, rate_hz: float) -> None:
        self.requests.append((topic, rate_hz))
        if not self.accepts_request:
            raise RuntimeError("UNSUPPORTED")
        self.requested = True

    async def set_rate_landed_state(self, rate_hz: float) -> None:
        await self._request("landed_state", rate_hz)

    async def set_rate_in_air(self, rate_hz: float) -> None:
        await self._request("in_air", rate_hz)

    async def velocity_ned(self):
        raise TimeoutError("not published")
        yield  # pragma: no cover

    async def flight_mode(self):
        raise TimeoutError("not published")
        yield  # pragma: no cover

    async def armed(self):
        yield False


def _drone(**kwargs):
    return types.SimpleNamespace(telemetry=_Vehicle(**kwargs))


@pytest.fixture(autouse=True)
def _fresh_link():
    ground_stream.reset_rerequests()
    yield
    ground_stream.reset_rerequests()


# ------------------------------------------------------- the healthy topic


async def test_a_live_topic_is_read_and_nothing_is_requested():
    drone = _drone(requested=True)
    assert await ground_stream.read_landed_state(drone) == "ON_GROUND"
    assert drone.telemetry.requests == []
    assert ground_stream.rerequests == {}


async def test_the_reading_is_normalised_for_the_ground_helpers():
    """``LandedState.ON_GROUND`` -> ``ON_GROUND``, the form ground.py compares."""
    drone = _drone(requested=True)
    landed_state, in_air = await ground_stream.read_ground_topics(drone)
    assert (landed_state, in_air) == ("ON_GROUND", False)


# ----------------------------------------------------- the silent topic


async def test_a_silent_topic_is_re_requested_and_then_read():
    """The observed defect: the one-shot request was lost, so ask again."""
    drone = _drone(requested=False)
    assert await ground_stream.read_landed_state(drone) == "ON_GROUND"
    assert drone.telemetry.requests == [("landed_state", ground_stream.REQUEST_RATE_HZ)]
    assert ground_stream.rerequests["landed_state"] == 1


class _RecordingLog:
    """The project logger is configured not to propagate, so record it here."""

    def __init__(self) -> None:
        self.lines: list[str] = []

    def warning(self, message, *args) -> None:
        self.lines.append(message % args if args else message)

    def __getattr__(self, _name):
        return lambda *a, **k: None


async def test_the_re_request_is_logged(monkeypatch):
    log = _RecordingLog()
    monkeypatch.setattr(ground_stream, "logger", log)
    drone = _drone(requested=False)
    await ground_stream.read_landed_state(drone)

    assert any("Re-requesting" in line and "landed_state" in line for line in log.lines)
    assert any("re-request #1" in line for line in log.lines), "a limping lane must be countable in the log"


async def test_a_firmware_that_denies_the_rate_is_not_an_error():
    drone = _drone(requested=False, accepts_request=False)
    assert await ground_stream.read_landed_state(drone) is None
    assert drone.telemetry.requests == [("landed_state", ground_stream.REQUEST_RATE_HZ)]


# ------------------------------------- never mask a link that is genuinely dead


async def test_a_dead_link_is_not_re_requested():
    """Everything is silent. That is the aircraft, not a lost message."""
    drone = _drone(requested=False, position=False)
    assert await ground_stream.read_landed_state(drone) is None
    assert drone.telemetry.requests == [], "nothing may be asked of a link that is not answering"
    assert ground_stream.rerequests == {}


async def test_a_dead_link_says_so_in_the_log(monkeypatch):
    log = _RecordingLog()
    monkeypatch.setattr(ground_stream, "logger", log)
    drone = _drone(requested=False, position=False)
    await ground_stream.read_landed_state(drone)

    assert any("the LINK" in line and "No rate request sent" in line for line in log.lines)


async def test_a_caller_that_knows_the_link_is_down_stops_it_earlier():
    drone = _drone(requested=False)
    assert await ground_stream.read_landed_state(drone, link_live=False) is None
    assert drone.telemetry.requests == []


async def test_a_caller_that_has_already_read_position_is_not_charged_for_a_witness():
    drone = _drone(requested=False)
    assert await ground_stream.read_landed_state(drone, link_live=True) == "ON_GROUND"
    assert drone.telemetry.position_reads == 0
    assert drone.telemetry.requests == [("landed_state", ground_stream.REQUEST_RATE_HZ)]


# ------------------------------------------------------------ conservative


async def test_a_topic_that_stays_silent_is_not_asked_again_immediately(monkeypatch):
    """One SET_MESSAGE_INTERVAL per topic per cooldown, not one per poll."""
    drone = _drone(requested=False, accepts_request=False)
    for _ in range(5):
        assert await ground_stream.read_landed_state(drone) is None
    assert len(drone.telemetry.requests) == 1


async def test_it_asks_again_once_the_cooldown_is_up(monkeypatch):
    drone = _drone(requested=False, accepts_request=False)
    assert await ground_stream.read_landed_state(drone) is None
    monkeypatch.setattr(ground_stream, "REREQUEST_COOLDOWN_S", 0.0)
    assert await ground_stream.read_landed_state(drone) is None
    assert len(drone.telemetry.requests) == 2
    assert ground_stream.rerequests["landed_state"] == 2


async def test_the_two_topics_are_tracked_separately():
    drone = _drone(requested=False)
    landed_state, in_air = await ground_stream.read_ground_topics(drone)
    assert (landed_state, in_air) == ("ON_GROUND", False)
    # The landed_state request revived both topics on this vehicle, so in_air
    # answered from its probe and was never asked for.
    assert drone.telemetry.requests == [("landed_state", ground_stream.REQUEST_RATE_HZ)]


async def test_a_hanging_topic_is_bounded(monkeypatch):
    """Silence that never raises must not become a wait that never ends."""

    class _Hangs(_Vehicle):
        async def landed_state(self):
            import asyncio

            await asyncio.sleep(3600)
            yield "LandedState.ON_GROUND"  # pragma: no cover

    monkeypatch.setattr(ground_stream, "PROBE_TIMEOUT_S", 0.05)
    drone = types.SimpleNamespace(telemetry=_Hangs(requested=False, accepts_request=False))
    assert await ground_stream.read_landed_state(drone, timeout_s=0.05) is None


# ---------------------------------------------------------- the consumers


async def test_monitor_flights_reading_recovers_a_silent_landed_state():
    """The function that used to wait on it forever, with no timeout at all."""
    drone = _drone(requested=False)
    reading = await _telemetry_now(drone)

    assert reading["landed_state"] == "ON_GROUND"
    assert reading["in_air"] is False
    assert reading["latitude_deg"] is not None
    assert drone.telemetry.requests == [("landed_state", ground_stream.REQUEST_RATE_HZ)]


async def test_monitor_flights_reading_does_not_re_request_on_a_dead_link():
    drone = _drone(requested=False, position=False)
    reading = await _telemetry_now(drone)

    assert reading["landed_state"] is None and reading["in_air"] is None
    assert reading["latitude_deg"] is None
    assert drone.telemetry.requests == []


async def test_the_safety_layer_gets_its_evidence_back_instead_of_guessing():
    """Without the re-request this falls through to the altitude fallback -
    the one reading in the precedence that can be wrong (FIX 12)."""
    drone = _drone(requested=False)
    assert await _read_in_air(drone, 2.0, link_live=True) is False
    assert drone.telemetry.requests == [("in_air", ground_stream.REQUEST_RATE_HZ)]


async def test_a_fresh_link_forgets_the_previous_links_re_requests():
    drone = _drone(requested=False, accepts_request=False)
    await ground_stream.read_landed_state(drone)
    assert ground_stream.rerequests["landed_state"] == 1

    ground_stream.reset_rerequests()
    assert ground_stream.rerequests == {}
    await ground_stream.read_landed_state(drone)
    assert len(drone.telemetry.requests) == 2
