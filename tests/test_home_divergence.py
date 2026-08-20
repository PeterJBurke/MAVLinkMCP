"""FIX 8a: the autopilot's home and the session's launch point are two things.

ArduPilot re-sets home to wherever the aircraft last armed. Ten of the 27
failing T6 trials (audit 2026-08-19, mechanism M3) read a home coordinate
between 437 m and 1528 m from where the aircraft was actually parked, and three
flew to it believing it was the launch point. ``claude-sonnet-5`` trial 2 read::

    RES {"position": {"latitude_deg": 33.7433185, ...}}
    RES {"home": {"latitude_deg": 33.7543384, "longitude_deg": -117.8333524, ...}}

- the aircraft on the launch field, home 1226 m away at Orange County Global
Medical Center, left there by the previous trial's arming. It flew "home", a
3.7 m hop, and reported that it had returned to the launch point.

``get_home_position`` now answers with both coordinates, their separation and
an explicit warning when they disagree.
"""

from __future__ import annotations

import types

import pytest

from droneserver.mavlink.connection import MAVLinkConnector, record_launch_point
from droneserver.tools import _common
from droneserver.tools import telemetry as telemetry_tools

LAUNCH = (33.7433185, -117.8328833, 41.3)
HOSPITAL = (33.7543384, -117.8333524, 52.16)


class _Telemetry:
    def __init__(self, home, position=None):
        self._home = home
        self._position = position or home

    async def home(self):
        yield types.SimpleNamespace(
            latitude_deg=self._home[0], longitude_deg=self._home[1], absolute_altitude_m=self._home[2]
        )

    async def position(self):
        yield types.SimpleNamespace(
            latitude_deg=self._position[0],
            longitude_deg=self._position[1],
            absolute_altitude_m=self._position[2],
            relative_altitude_m=0.0,
        )


def _connector(home, launch=None):
    connector = MAVLinkConnector(drone=types.SimpleNamespace(telemetry=_Telemetry(home)))
    if launch is not None:
        connector.record_session_launch(launch[0], launch[1], launch[2], "autopilot home when the link came up")
    return connector


def _ctx(connector):
    return types.SimpleNamespace(request_context=types.SimpleNamespace(lifespan_context=connector))


get_home_position = telemetry_tools.get_home_position.__wrapped__


@pytest.fixture(autouse=True)
def _always_connected(monkeypatch):
    async def ok(_connector, timeout=30.0):
        return True

    monkeypatch.setattr(_common, "ensure_connection", ok)


async def test_home_that_has_drifted_is_reported_with_a_warning():
    """The sonnet trial: home is the hospital, the aircraft launched elsewhere."""
    connector = _connector(HOSPITAL, launch=LAUNCH)
    result = await get_home_position(_ctx(connector))

    assert result["status"] == "success"
    assert result["home"]["latitude_deg"] == pytest.approx(HOSPITAL[0])
    assert result["session_launch_point"]["latitude_deg"] == pytest.approx(LAUNCH[0])
    assert result["home_matches_session_launch"] is False
    assert 1200 < result["distance_between_m"] < 1300
    assert "RTL will fly to" in result["warning"]
    assert "session_launch_point" in result["warning"]


async def test_home_that_agrees_carries_no_warning():
    connector = _connector(LAUNCH, launch=LAUNCH)
    result = await get_home_position(_ctx(connector))

    assert result["home_matches_session_launch"] is True
    assert result["distance_between_m"] < 1.0
    assert "warning" not in result


async def test_both_coordinates_are_always_present():
    """A model must never have to ask a second tool to see the difference."""
    result = await get_home_position(_ctx(_connector(HOSPITAL, launch=LAUNCH)))
    assert set(result) >= {"home", "session_launch_point", "distance_between_m", "home_matches_session_launch"}
    assert "last armed" in result["home"]["meaning"]


async def test_a_session_with_no_recorded_launch_point_says_so():
    connector = _connector(HOSPITAL, launch=None)
    result = await get_home_position(_ctx(connector))
    assert result["session_launch_point"] is None
    assert result["home_matches_session_launch"] is None
    assert "did not record a launch point" in result["warning"]


# ---------------------------------------------------- recording the point


async def test_the_launch_point_is_recorded_from_the_aircrafts_own_position():
    """FIX 13: where the aircraft is standing, not where it last armed.

    The autopilot's home here is the HOSPITAL - left there by a previous
    session's arming, which is exactly the state a restarted server finds.
    """
    drone = types.SimpleNamespace(telemetry=_Telemetry(HOSPITAL, position=LAUNCH))
    connector = MAVLinkConnector(drone=drone)
    await record_launch_point(drone, connector)
    assert connector.session_launch["latitude_deg"] == pytest.approx(LAUNCH[0])
    assert connector.session_launch["source"].startswith("position when the link came up")


async def test_the_launch_point_falls_back_to_home_when_the_position_cannot_be_read():
    class _NoPosition(_Telemetry):
        async def position(self):
            raise RuntimeError("no GPS fix yet")
            yield  # pragma: no cover

    drone = types.SimpleNamespace(telemetry=_NoPosition(LAUNCH))
    connector = MAVLinkConnector(drone=drone)
    await record_launch_point(drone, connector)
    assert connector.session_launch["latitude_deg"] == pytest.approx(LAUNCH[0])
    assert connector.session_launch["source"].startswith("autopilot home when the link came up")
    assert "position could not be read" in connector.session_launch["source"]


async def test_recording_never_raises_when_nothing_can_be_read():
    class _Blind:
        async def home(self):
            raise RuntimeError("link down")
            yield  # pragma: no cover

        async def position(self):
            raise RuntimeError("link down")
            yield  # pragma: no cover

    drone = types.SimpleNamespace(telemetry=_Blind())
    connector = MAVLinkConnector(drone=drone)
    await record_launch_point(drone, connector)
    assert connector.session_launch is None


def test_the_launch_point_does_not_follow_the_aircraft():
    """First writer wins, and a new flight does not move it."""
    connector = MAVLinkConnector(drone=types.SimpleNamespace())
    connector.record_session_launch(*LAUNCH, "autopilot home when the link came up")
    connector.record_session_launch(*HOSPITAL, "autopilot home when the link came up")
    assert connector.session_launch["latitude_deg"] == pytest.approx(LAUNCH[0])

    connector.reset_flight_latches()
    assert connector.session_launch["latitude_deg"] == pytest.approx(LAUNCH[0])
