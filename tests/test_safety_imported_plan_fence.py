"""A QGC plan import must not fly unfenced when home is unknown (case 30).

A radius geofence is inert until the drone's home position is known: with
``home=None`` the radius branch of :func:`check_position` is skipped and every
waypoint on Earth is inside the fence. Two of the three upload paths already
refuse rather than pass silently -

* the single-target/mission tool path, via ``geofence.home_unknown``
  (:func:`droneserver.safety.validation.check_geofence`, review item S8);
* the managed-mission runner, which refreshes state and fails the mission with
  the same rule (:mod:`droneserver.missions.runner`)

- and the third, ``import_qgc_mission``, did not. It builds its own fence with
  ``home=LAYER.state_tracker.state.home`` and hands it straight to
  ``check_mission``, so a plan whose waypoints are kilometres outside the fence
  is accepted and uploaded whenever home has not been read yet.

That state is real, not hypothetical: ArduPilot does not publish a home
position until it has set one, the tracker retries a failed home read only
every 30 s, and ``import_qgc_mission`` is the one upload path that also carries
the plan's **own** geofence and rally points onto the vehicle - so the command
that redefines containment is the command that escaped the check on it.
"""

from __future__ import annotations

from dataclasses import dataclass

import pytest

from droneserver.safety.config import SafetySettings
from droneserver.safety.middleware import LAYER
from droneserver.tools.mission_raw import _validate_imported_mission

HOME = (33.6458611, -117.84275)
#: Roughly 55 km north of home - far outside any radius fence this project uses.
FAR = (34.1458611, -117.84275)


@dataclass
class Item:
    """The fields ``_validate_imported_mission`` reads off a MavSDK item."""

    seq: int
    command: int
    x: int
    y: int
    z: float
    frame: int = 3  # relative to home


def plan(point: tuple[float, float]) -> list[Item]:
    return [
        Item(seq=0, command=16, x=int(HOME[0] * 1e7), y=int(HOME[1] * 1e7), z=0.0),
        Item(seq=1, command=16, x=int(point[0] * 1e7), y=int(point[1] * 1e7), z=30.0),
    ]


@pytest.fixture
def fenced(monkeypatch):
    """A 1 km radius fence configured, exactly as the deployment runs it."""
    settings = SafetySettings(
        _env_file=None,
        geofence_enabled=True,
        geofence_max_radius_m=1000.0,
        geofence_max_altitude_m=120.0,
        geofence_polygon="",
    )
    monkeypatch.setattr("droneserver.safety.config.get_safety_settings", lambda: settings)
    return settings


@pytest.fixture(autouse=True)
def _restore_home():
    original = LAYER.state_tracker.state.home
    yield
    LAYER.state_tracker.state.home = original


def test_an_imported_plan_is_refused_while_home_is_unknown(fenced):
    """The gap: with no home the radius fence passes a 55 km waypoint."""
    LAYER.state_tracker.state.home = None
    rejection = _validate_imported_mission(plan(FAR))
    assert rejection is not None, "a radius fence that cannot be enforced must refuse, not wave through"
    assert rejection["rule"] == "geofence.home_unknown.imported_plan"
    assert rejection["status"] == "rejected"


def test_an_inside_plan_is_also_refused_while_home_is_unknown(fenced):
    """Refusing only the far ones would need the check it does not have."""
    LAYER.state_tracker.state.home = None
    assert _validate_imported_mission(plan(HOME)) is not None


def test_with_home_known_the_fence_does_its_ordinary_job(fenced):
    LAYER.state_tracker.state.home = HOME
    far = _validate_imported_mission(plan(FAR))
    assert far is not None and far["rule"].startswith("geofence.radius")
    assert _validate_imported_mission(plan(HOME)) is None


def test_no_radius_fence_configured_needs_no_home(monkeypatch):
    """An altitude-only fence is enforceable without home; do not block it."""
    settings = SafetySettings(
        _env_file=None,
        geofence_enabled=True,
        geofence_max_radius_m=0.0,
        geofence_max_altitude_m=120.0,
        geofence_polygon="",
    )
    monkeypatch.setattr("droneserver.safety.config.get_safety_settings", lambda: settings)
    LAYER.state_tracker.state.home = None
    assert _validate_imported_mission(plan(FAR)) is None
