"""FIX 13: the session's launch point is where the aircraft IS, and it re-anchors.

Two defects, one datum.

**The capture was the autopilot's home.** ``record_launch_point`` read
``telemetry.home()`` first, reasoning that nothing had flown yet on this link.
Nothing had - on THIS link. ArduPilot keeps home wherever the vehicle last
ARMED, across restarts of this server, so a session that came up after a trial
which armed somewhere else inherited that somewhere else as its launch point:
143 m out on one lane and 2.0 km out on another on 2026-08-19, and then handed
to models as ``session_launch_point``, the one coordinate FIX 8a added so they
would have a point that does not move. The capture now asks the aircraft where
it is standing, with the autopilot's own disarmed/on-ground evidence behind it,
and keeps home only as the fallback for a position it cannot read or a vehicle
that is already flying - recording WHICH in ``source`` either way.

**And a link that is up for a whole campaign needs a way to re-anchor.** The
harness ferries the aircraft back onto the run's launch point between trials;
until it does, the point recorded at link-up is genuinely where the aircraft
was, and genuinely not where the next trial starts. So the trial layer can say
so - over a transport header on its own MCP session, never as a tool argument,
because the model must not be able to move this datum by re-arming at a
destination. That is precisely the T6 shape (fly out, land, re-arm, come home)
whose moving datum FIX 8a/10/11/12 exist to escape, and the tests below hold the
line: an arm moves nothing.
"""

from __future__ import annotations

import types

import pytest

from droneserver.mavlink.connection import (
    MAVLinkConnector,
    anchor_launch_point_here,
    record_launch_point,
)
from droneserver.safety import middleware as MW
from droneserver.safety.state import StateTracker

LAUNCH = (33.6458611, -117.84275, 25.1)
HOSPITAL = (33.7543384, -117.8333524, 52.16)
#: The 143 m the observed session was out by, in latitude degrees.
NEARBY = (LAUNCH[0] + 143.0 / 111320.0, LAUNCH[1], 27.4)


class _Telemetry:
    """A vehicle that answers every topic the launch capture asks about."""

    def __init__(self, position=LAUNCH, home=HOSPITAL, armed=False, landed_state="ON_GROUND", in_air=False):
        self._position, self._home = position, home
        self._armed, self._landed_state, self._in_air = armed, landed_state, in_air

    async def position(self):
        yield types.SimpleNamespace(
            latitude_deg=self._position[0],
            longitude_deg=self._position[1],
            absolute_altitude_m=self._position[2],
            relative_altitude_m=0.0,
        )

    async def home(self):
        yield types.SimpleNamespace(
            latitude_deg=self._home[0], longitude_deg=self._home[1], absolute_altitude_m=self._home[2]
        )

    async def armed(self):
        yield self._armed

    async def landed_state(self):
        yield f"LandedState.{self._landed_state}"

    async def in_air(self):
        yield self._in_air


def _drone(**kwargs):
    return types.SimpleNamespace(telemetry=_Telemetry(**kwargs))


# ------------------------------------------------- capture: position, not home


async def test_the_parked_position_beats_a_home_left_at_a_destination():
    """The observed defect: home is the hospital, the aircraft is on the field."""
    drone = _drone(position=LAUNCH, home=HOSPITAL)
    connector = MAVLinkConnector(drone=drone)
    await record_launch_point(drone, connector)

    assert connector.session_launch["latitude_deg"] == pytest.approx(LAUNCH[0])
    assert connector.session_launch["longitude_deg"] == pytest.approx(LAUNCH[1])
    assert connector.session_launch["absolute_altitude_m"] == pytest.approx(LAUNCH[2])
    assert connector.session_launch["source"] == "parked position when the link came up"


async def test_an_aircraft_already_flying_at_link_up_keeps_home():
    """A fix taken in flight is not a launch point; home is its takeoff point."""
    drone = _drone(position=HOSPITAL, home=LAUNCH, armed=True, landed_state="IN_AIR", in_air=True)
    connector = MAVLinkConnector(drone=drone)
    await record_launch_point(drone, connector)

    assert connector.session_launch["latitude_deg"] == pytest.approx(LAUNCH[0])
    assert "armed or airborne" in connector.session_launch["source"]


async def test_an_unreadable_ground_state_still_takes_the_position_and_says_so():
    """Position first, but the record must not claim a certainty it lacks."""

    class _NoGroundEvidence(_Telemetry):
        async def armed(self):
            raise TimeoutError("the topic is not published")
            yield  # pragma: no cover

        async def landed_state(self):
            raise TimeoutError("the topic is not published")
            yield  # pragma: no cover

        async def in_air(self):
            raise TimeoutError("the topic is not published")
            yield  # pragma: no cover

    drone = types.SimpleNamespace(telemetry=_NoGroundEvidence(position=LAUNCH, home=HOSPITAL))
    connector = MAVLinkConnector(drone=drone)
    await record_launch_point(drone, connector)

    assert connector.session_launch["latitude_deg"] == pytest.approx(LAUNCH[0])
    assert "could not be confirmed" in connector.session_launch["source"]


async def test_the_home_fallback_says_home_moves():
    """A reader of the fallback record must know what kind of point it is."""

    class _NoPosition(_Telemetry):
        async def position(self):
            raise TimeoutError("no GPS fix")
            yield  # pragma: no cover

    drone = types.SimpleNamespace(telemetry=_NoPosition(home=HOSPITAL))
    connector = MAVLinkConnector(drone=drone)
    await record_launch_point(drone, connector)

    source = connector.session_launch["source"]
    assert source.startswith("autopilot home when the link came up")
    assert "wherever it last armed" in source


# ------------------------------------------------------------- the re-anchor


async def test_the_trial_layer_can_re_anchor_a_stale_launch_point():
    """The 143 m case: link came up before the ferry put the aircraft back."""
    connector = MAVLinkConnector(drone=_drone(position=NEARBY))
    await record_launch_point(connector.drone, connector)
    assert connector.session_launch["latitude_deg"] == pytest.approx(NEARBY[0])

    connector.drone = _drone(position=LAUNCH)  # the ferry has flown it home
    outcome = await anchor_launch_point_here(connector.drone, connector, "trial start")

    assert outcome["anchored"] is True and outcome["moved"] is True
    assert connector.session_launch["latitude_deg"] == pytest.approx(LAUNCH[0])
    assert connector.session_launch["source"] == "trial start"


async def test_re_anchoring_on_the_same_spot_is_not_a_move():
    connector = MAVLinkConnector(drone=_drone(position=LAUNCH))
    await record_launch_point(connector.drone, connector)
    outcome = await anchor_launch_point_here(connector.drone, connector, "trial start")

    assert outcome["anchored"] is True and outcome["moved"] is False
    assert connector.session_launch["source"] == "parked position when the link came up"


async def test_the_re_anchor_refuses_an_armed_aircraft():
    """Mid-ferry the vehicle is armed, and where it is is not where it starts."""
    connector = MAVLinkConnector(drone=_drone(position=LAUNCH))
    await record_launch_point(connector.drone, connector)

    flying = _drone(position=HOSPITAL, armed=True, landed_state="IN_AIR", in_air=True)
    outcome = await anchor_launch_point_here(flying, connector, "trial start")

    assert outcome["anchored"] is False
    assert connector.session_launch["latitude_deg"] == pytest.approx(LAUNCH[0])


async def test_the_re_anchor_refuses_an_aircraft_the_autopilot_will_not_call_landed():
    connector = MAVLinkConnector(drone=_drone(position=LAUNCH))
    await record_launch_point(connector.drone, connector)

    hovering = _drone(position=HOSPITAL, armed=False, landed_state="UNKNOWN", in_air=True)
    outcome = await anchor_launch_point_here(hovering, connector, "trial start")

    assert outcome["anchored"] is False
    assert "on the ground" in outcome["reason"]
    assert connector.session_launch["latitude_deg"] == pytest.approx(LAUNCH[0])


async def test_the_re_anchor_never_raises_on_a_dead_link():
    class _Dead:
        def __getattr__(self, name):
            raise RuntimeError("link down")

    connector = MAVLinkConnector(drone=_drone(position=LAUNCH))
    await record_launch_point(connector.drone, connector)
    outcome = await anchor_launch_point_here(types.SimpleNamespace(telemetry=_Dead()), connector, "trial start")

    assert outcome["anchored"] is False
    assert connector.session_launch["latitude_deg"] == pytest.approx(LAUNCH[0])


# ------------------------------------------ nothing the AIRCRAFT does moves it


def test_arming_does_not_move_the_launch_point():
    """The T6 shape: land at the hospital, re-arm there, fly home.

    ``reset_flight_latches`` is the trial-start reset (FIX 2) that every arm
    runs. If the launch point moved with it, an aircraft standing 1.4 km away
    would report itself 0 m from its launch point.
    """
    connector = MAVLinkConnector(drone=types.SimpleNamespace())
    connector.record_session_launch(*LAUNCH, "parked position when the link came up")
    connector.reset_flight_latches()
    connector.record_session_launch(*HOSPITAL, "parked position when the link came up")

    assert connector.session_launch["latitude_deg"] == pytest.approx(LAUNCH[0])


def test_only_the_deliberate_re_anchor_overwrites():
    connector = MAVLinkConnector(drone=types.SimpleNamespace())
    connector.record_session_launch(*LAUNCH, "parked position when the link came up")
    assert connector.reanchor_session_launch(*HOSPITAL, "trial start") is True
    assert connector.session_launch["latitude_deg"] == pytest.approx(HOSPITAL[0])


def test_a_gps_wobble_is_not_a_re_anchor():
    connector = MAVLinkConnector(drone=types.SimpleNamespace())
    connector.record_session_launch(*LAUNCH, "parked position when the link came up")
    wobbled = (LAUNCH[0] + 1.0 / 111320.0, LAUNCH[1], LAUNCH[2] + 0.4)
    assert connector.reanchor_session_launch(*wobbled, "trial start") is False
    assert connector.session_launch["source"] == "parked position when the link came up"


# --------------------------------------------- the header the harness carries


class _Headers(dict):
    def get(self, key, default=None):  # httpx headers are case-insensitive
        return dict.get(self, key.lower(), default)


def _ctx(connector, headers: dict | None = None):
    request = types.SimpleNamespace(headers=_Headers(headers or {}))
    return types.SimpleNamespace(request_context=types.SimpleNamespace(lifespan_context=connector, request=request))


@pytest.fixture(autouse=True)
def _no_anchor_throttle(monkeypatch):
    monkeypatch.setattr(MW, "_last_anchor_attempt", 0.0)
    monkeypatch.setattr(MW, "ANCHOR_MIN_INTERVAL_S", 0.0)


def test_only_a_session_carrying_the_header_re_anchors():
    connector = MAVLinkConnector(drone=_drone())
    assert MW._anchor_requested(_ctx(connector)) is False
    assert MW._anchor_requested(_ctx(connector, {MW.ANCHOR_HEADER: "1"})) is True
    assert MW._anchor_requested(_ctx(connector, {MW.ANCHOR_HEADER: "0"})) is False


def test_the_harness_and_the_server_agree_on_the_header():
    from droneserver.llm.runner import HARNESS_ANCHOR_HEADERS

    assert HARNESS_ANCHOR_HEADERS == {MW.ANCHOR_HEADER: "1"}


def test_the_agents_own_session_carries_no_anchor_header():
    """The model must not be able to redefine where the flight started."""
    from droneserver.llm.mcp_session import LiveMCPSession

    agent = LiveMCPSession("http://server/mcp", "key")
    assert MW.ANCHOR_HEADER not in {k.lower() for k in agent.headers}


async def test_the_middleware_hook_moves_the_point_and_the_safety_datum():
    connector = MAVLinkConnector(drone=_drone(position=NEARBY))
    await record_launch_point(connector.drone, connector)
    MW.LAYER.state_tracker = StateTracker()
    MW.LAYER.state_tracker.note_session_launch(connector.session_launch)
    assert MW.LAYER.state_tracker.state.session_launch_amsl_m == pytest.approx(NEARBY[2])

    connector.drone = _drone(position=LAUNCH)
    outcome = await MW.maybe_anchor_launch_point(_ctx(connector, {MW.ANCHOR_HEADER: "1"}))

    assert outcome["moved"] is True
    assert connector.session_launch["latitude_deg"] == pytest.approx(LAUNCH[0])
    # The ceiling must be measured from the same point, or the two layers
    # disagree about how high the aircraft is (FIX 12).
    assert MW.LAYER.state_tracker.state.session_launch_amsl_m == pytest.approx(LAUNCH[2])


async def test_the_middleware_hook_never_raises_without_a_link():
    outcome = await MW.maybe_anchor_launch_point(types.SimpleNamespace())
    assert outcome["anchored"] is False
