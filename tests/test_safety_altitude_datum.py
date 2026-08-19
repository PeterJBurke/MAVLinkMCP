"""FIX 12: the safety layer's ceiling must not sit on a datum that moves.

Fourth and last consumer of the defect found on 2026-08-19 (verdicts FIX 8b,
monitor_flight FIX 10, the mission runner FIX 11). ArduPilot re-zeroes
``relative_altitude_m`` wherever the aircraft last ARMED, and it moves HOME to
the same place - so both of the elevations the safety layer used to measure
heights against travel with the aircraft.

Three consumers, and the direction of each error is the point:

1. ``_relative_altitude`` converted an AMSL argument with ``home_altitude_m``.
   A home 4.1 m below the launch field enforces a 120 m ceiling at 124.1 m -
   the safety layer permitting what it was configured to refuse.
2. ``check_parameter_bounds`` resolved ``move_to_relative``'s ``down_m``
   against the raw relative reading, so a climb command was bounds-checked
   from a height that was off by the same offset, in the same direction.
3. ``resolve_target`` fed that same raw reading to the geofence ceiling.

And in ``safety.state``, the last-resort airborne test compared
``relative_altitude_m > 1.0``: a parked aircraft carrying a +4.1 m offset reads
as flying, which is the safety layer's own version of the phantom return - the
navigation preconditions would clear a "return home" from a vehicle standing on
its pad.

All four now measure against ``session_launch_amsl_m``: the elevation the
connector recorded at link-up, before anything armed, which does not move. The
autopilot's home remains the fallback, so with no session launch recorded every
behaviour below is exactly what it was.

FAIL-CLOSED IS PRESERVED THROUGHOUT, and the tests at the end prove it: the new
datum never widens what the layer permits, and an unknown datum declines to
range-check exactly as before rather than passing a command through.
"""

from __future__ import annotations

import types

import pytest

from droneserver.safety import state as state_mod
from droneserver.safety.config import SafetySettings
from droneserver.safety.geofence import Geofence
from droneserver.safety.validation import (
    _current_height_m,
    _launch_amsl,
    _relative_altitude,
    check_geofence,
    check_parameter_bounds,
    resolve_target,
)

HOME = (-35.363262, 149.165237)

#: The launch field, and the autopilot's home after the aircraft re-armed 4.1 m
#: lower down. Every altitude the autopilot reports is now 4.1 m too high.
LAUNCH_AMSL = 584.0
MOVED_HOME_AMSL = 579.9
DATUM_OFFSET_M = LAUNCH_AMSL - MOVED_HOME_AMSL


@pytest.fixture
def s():
    return SafetySettings(_env_file=None, max_altitude_m=120.0, max_speed_m_s=20.0, max_distance_from_home_m=2000.0)


def _state(*, launch=None, home=LAUNCH_AMSL, relative=None, absolute=None, **kw) -> dict:
    """A state snapshot of the shape the middleware hands the rules."""
    position = None
    if relative is not None or absolute is not None:
        position = {
            "latitude_deg": HOME[0],
            "longitude_deg": HOME[1],
            "relative_altitude_m": relative,
            "absolute_altitude_m": absolute,
        }
    return {
        "armed": True,
        "in_air": True,
        "unknown": False,
        "seconds_since_takeoff": 60.0,
        "home_altitude_m": home,
        "session_launch_amsl_m": launch,
        "position": position,
        **kw,
    }


# ------------------------------------------------------------- which datum


def test_the_session_launch_outranks_the_autopilots_home():
    assert _launch_amsl(_state(launch=LAUNCH_AMSL, home=MOVED_HOME_AMSL)) == LAUNCH_AMSL


def test_the_home_is_still_the_fallback():
    """No session launch recorded: exactly the previous behaviour."""
    assert _launch_amsl(_state(launch=None, home=MOVED_HOME_AMSL)) == MOVED_HOME_AMSL


def test_no_elevation_at_all_is_no_answer():
    assert _launch_amsl(_state(launch=None, home=None)) is None


def test_current_height_is_measured_from_the_launch_field():
    """Parked on the launch field, reading +4.1 m off the moved datum."""
    st = _state(launch=LAUNCH_AMSL, home=MOVED_HOME_AMSL, relative=DATUM_OFFSET_M, absolute=LAUNCH_AMSL)
    assert _current_height_m(st) == pytest.approx(0.0)


def test_current_height_falls_back_to_the_relative_reading():
    st = _state(launch=None, home=None, relative=30.0, absolute=None)
    assert _current_height_m(st) == 30.0


def test_current_height_is_none_without_a_position():
    assert _current_height_m(_state()) is None


# ------------------------------------------- (1) the AMSL conversion, fail-open


def test_an_amsl_target_is_converted_against_the_launch_field(s):
    """THE BUG. 704.0 m AMSL over a 584 m launch field is 120 m: at the ceiling.

    Against the moved home (579.9 m) the same command computes 124.1 m and the
    layer would have refused it; against a home that moved the OTHER way it
    computes 115.9 m and the layer would have permitted 124 m of real height.
    Neither is the ceiling the operator configured.
    """
    args = {"latitude_deg": HOME[0], "longitude_deg": HOME[1], "absolute_altitude_m": LAUNCH_AMSL + 120.0}
    st = _state(launch=LAUNCH_AMSL, home=MOVED_HOME_AMSL)
    assert _relative_altitude("go_to_location", args, st) == pytest.approx(120.0)
    assert check_parameter_bounds("go_to_location", args, s, st) is None


def test_the_ceiling_still_refuses_what_is_genuinely_too_high(s):
    args = {"latitude_deg": HOME[0], "longitude_deg": HOME[1], "absolute_altitude_m": LAUNCH_AMSL + 200.0}
    st = _state(launch=LAUNCH_AMSL, home=MOVED_HOME_AMSL)
    r = check_parameter_bounds("go_to_location", args, s, st)
    assert r is not None and r.rule == "bounds.max_altitude"


def test_a_command_the_moved_home_would_have_let_through_is_refused(s):
    """The fail-OPEN direction, which is the one that matters.

    A home 10 m ABOVE the launch field makes every AMSL target look 10 m lower
    than it is. 125 m of real height read as 115 m and was permitted.
    """
    high_home = LAUNCH_AMSL + 10.0
    args = {"latitude_deg": HOME[0], "longitude_deg": HOME[1], "absolute_altitude_m": LAUNCH_AMSL + 125.0}

    old = _state(launch=None, home=high_home)
    assert check_parameter_bounds("go_to_location", args, s, old) is None, "what the old datum permitted"

    fixed = _state(launch=LAUNCH_AMSL, home=high_home)
    r = check_parameter_bounds("go_to_location", args, s, fixed)
    assert r is not None and r.rule == "bounds.max_altitude"


def test_without_any_elevation_amsl_is_still_not_range_checked(s):
    """Unchanged: checking against a datum we do not have would reject valid
    commands. The horizontal fence still applies - see the geofence tests."""
    args = {"latitude_deg": HOME[0], "longitude_deg": HOME[1], "absolute_altitude_m": 604.0}
    assert check_parameter_bounds("go_to_location", args, s, _state(launch=None, home=None)) is None


# ------------------------------------ (2) offsets resolved against live height


def test_a_climb_offset_is_bounded_from_the_real_height(s):
    """move_to_relative down_m=-5 from 118 m real (reading 113.9 m) is 123 m."""
    st = _state(
        launch=LAUNCH_AMSL,
        home=MOVED_HOME_AMSL,
        relative=118.0 - DATUM_OFFSET_M,
        absolute=LAUNCH_AMSL + 118.0,
    )
    args = {"north_m": 0.0, "east_m": 0.0, "down_m": -5.0}

    r = check_parameter_bounds("move_to_relative", args, s, st)
    assert r is not None and r.rule == "bounds.max_altitude"

    # And the same command from the same aircraft, judged the old way, passed.
    old = dict(st, session_launch_amsl_m=None, home_altitude_m=None)
    assert check_parameter_bounds("move_to_relative", args, s, old) is None


def test_a_legitimate_climb_is_still_allowed(s):
    st = _state(launch=LAUNCH_AMSL, home=MOVED_HOME_AMSL, relative=30.0 + DATUM_OFFSET_M, absolute=LAUNCH_AMSL + 30.0)
    assert check_parameter_bounds("move_to_relative", {"north_m": 10.0, "east_m": 0.0, "down_m": -5.0}, s, st) is None


def test_the_offset_bound_still_applies_without_any_position(s):
    """B3's distance bound never depended on altitude and still does not."""
    args = {"north_m": 5000.0, "east_m": 0.0, "down_m": -5.0}
    r = check_parameter_bounds("move_to_relative", args, s, _state(launch=None, home=None))
    assert r is not None and r.rule == "bounds.max_offset"


# --------------------------------------- (3) the fence ceiling on live height


def test_resolve_target_reports_the_height_above_the_launch_field(s):
    st = _state(launch=LAUNCH_AMSL, home=MOVED_HOME_AMSL, relative=44.1, absolute=LAUNCH_AMSL + 40.0)
    lat, lon, alt, error = resolve_target("move_to_relative", {"north_m": 10.0, "east_m": 0.0, "down_m": -10.0}, st, s)
    assert error is None
    assert lat is not None and lon is not None
    assert alt == pytest.approx(50.0), "40 m above the launch field, climbing 10"


def test_the_fence_ceiling_catches_the_climb_the_moved_datum_hid(s):
    fence = Geofence(max_altitude_m=120.0, max_radius_m=300.0, home=HOME)
    args = {"north_m": 5.0, "east_m": 0.0, "down_m": -10.0}
    st = _state(
        launch=LAUNCH_AMSL,
        home=MOVED_HOME_AMSL,
        relative=115.0 - DATUM_OFFSET_M,
        absolute=LAUNCH_AMSL + 115.0,
    )

    r = check_geofence("move_to_relative", args, fence, s, st)
    assert r is not None and r.rule == "geofence.altitude_ceiling"


def test_an_unresolvable_target_is_still_refused_not_skipped(s):
    """The review's finding: never silently skip the fence."""
    fence = Geofence(max_altitude_m=120.0, max_radius_m=300.0, home=HOME)
    r = check_geofence("move_to_relative", {"north_m": 10.0, "east_m": 0.0}, fence, s, _state())
    assert r is not None and r.rule == "geofence.target_unresolvable"


# ------------------------------------------ (4) the last-resort airborne test


class _LandedState:
    def __init__(self, name):
        self.name = name

    def __str__(self):
        return f"LandedState.{self.name}"


class _Telemetry:
    """Only the topics named are published; the rest raise, as ArduPilot does."""

    def __init__(self, *, relative=None, absolute=None, in_air=None, landed_state=None):
        self._relative, self._absolute = relative, absolute
        self._in_air, self._landed_state = in_air, landed_state

    async def in_air(self):
        if self._in_air is None:
            raise RuntimeError("in_air is not published on this setup")
        yield self._in_air

    async def landed_state(self):
        if self._landed_state is None:
            raise RuntimeError("landed_state is not published on this setup")
        yield _LandedState(self._landed_state)

    async def position(self):
        yield types.SimpleNamespace(
            latitude_deg=HOME[0],
            longitude_deg=HOME[1],
            relative_altitude_m=self._relative,
            absolute_altitude_m=self._absolute,
        )


async def test_a_parked_aircraft_with_a_moved_datum_is_not_flying():
    """THE SAFETY-LAYER BUG: +4.1 m on the pad used to read as airborne.

    "Airborne" is what the navigation preconditions require, so this is the
    layer clearing a return-to-launch for an aircraft standing still.
    """
    drone = types.SimpleNamespace(
        telemetry=_Telemetry(relative=DATUM_OFFSET_M, absolute=LAUNCH_AMSL),
    )
    assert await state_mod._read_in_air(drone, 1.0, LAUNCH_AMSL) is False
    # And the old datum is what it looked like before.
    assert await state_mod._read_in_air(drone, 1.0, None) is True


async def test_a_genuinely_flying_aircraft_is_still_flying():
    drone = types.SimpleNamespace(
        telemetry=_Telemetry(relative=30.0, absolute=LAUNCH_AMSL + 30.0),
    )
    assert await state_mod._read_in_air(drone, 1.0, LAUNCH_AMSL) is True


async def test_the_autopilots_own_topics_still_come_first():
    """The altitude branch is the LAST resort and must stay that way."""
    by_in_air = types.SimpleNamespace(
        telemetry=_Telemetry(relative=DATUM_OFFSET_M, absolute=LAUNCH_AMSL, in_air=True),
    )
    assert await state_mod._read_in_air(by_in_air, 1.0, LAUNCH_AMSL) is True

    # landed_state answers when in_air is silent, and still outranks altitude.
    by_landed_state = types.SimpleNamespace(
        telemetry=_Telemetry(relative=0.0, absolute=LAUNCH_AMSL, landed_state="TAKING_OFF"),
    )
    assert await state_mod._read_in_air(by_landed_state, 1.0, LAUNCH_AMSL) is True

    on_ground = types.SimpleNamespace(
        telemetry=_Telemetry(relative=50.0, absolute=LAUNCH_AMSL + 50.0, landed_state="ON_GROUND"),
    )
    assert await state_mod._read_in_air(on_ground, 1.0, LAUNCH_AMSL) is False


async def test_nothing_readable_is_still_unknown():
    """Unknown must stay unknown: the fail-closed policy decides, not a guess."""

    class _Silent:
        async def in_air(self):
            raise RuntimeError("no")
            yield  # pragma: no cover

        async def landed_state(self):
            raise RuntimeError("no")
            yield  # pragma: no cover

        async def position(self):
            raise RuntimeError("no")
            yield  # pragma: no cover

    assert await state_mod._read_in_air(types.SimpleNamespace(telemetry=_Silent()), 1.0, LAUNCH_AMSL) is None


# ------------------------------------------------------- the datum's plumbing


def test_the_tracker_adopts_the_connectors_launch_point():
    tracker = state_mod.StateTracker()
    tracker.note_session_launch({"latitude_deg": 0.0, "longitude_deg": 0.0, "absolute_altitude_m": LAUNCH_AMSL})
    assert tracker.state.session_launch_amsl_m == LAUNCH_AMSL
    assert tracker.state.snapshot()["session_launch_amsl_m"] == LAUNCH_AMSL


def test_the_launch_point_is_never_overwritten():
    """First writer wins - a datum that can be moved is the whole defect."""
    tracker = state_mod.StateTracker()
    tracker.note_session_launch({"absolute_altitude_m": LAUNCH_AMSL})
    tracker.note_session_launch({"absolute_altitude_m": 999.0})
    assert tracker.state.session_launch_amsl_m == LAUNCH_AMSL


@pytest.mark.parametrize("launch", [None, {}, {"absolute_altitude_m": None}])
def test_a_missing_launch_point_leaves_the_fallback_in_place(launch):
    tracker = state_mod.StateTracker()
    tracker.note_session_launch(launch)
    assert tracker.state.session_launch_amsl_m is None


def test_reset_clears_the_launch_point():
    tracker = state_mod.StateTracker()
    tracker.note_session_launch({"absolute_altitude_m": LAUNCH_AMSL})
    tracker.reset()
    assert tracker.state.session_launch_amsl_m is None


# ----------------------------------------------------------- fail-closed proof


def test_the_new_datum_never_widens_what_is_permitted(s):
    """The invariant, swept: for every altitude either datum can produce, a
    command the FIXED layer permits is one the OLD layer permitted too.

    Not a coincidence of the cases above - the whole point of the change is
    that it can only ever move a decision towards refusal or leave it alone,
    because it replaces an offset datum with the true one and the ceiling is
    one-sided.
    """
    for home_offset in (-10.0, -4.1, 0.0, 4.1, 10.0):
        home = LAUNCH_AMSL + home_offset
        for target in (0.0, 50.0, 115.0, 119.9, 120.0, 120.1, 125.0, 200.0):
            args = {
                "latitude_deg": HOME[0],
                "longitude_deg": HOME[1],
                "absolute_altitude_m": LAUNCH_AMSL + target,
            }
            fixed = check_parameter_bounds("go_to_location", args, s, _state(launch=LAUNCH_AMSL, home=home))
            truly_too_high = target > s.max_altitude_m
            assert (fixed is not None) is truly_too_high, (
                f"home{home_offset:+.1f} target {target}: the fixed layer must decide on the REAL height"
            )


def test_an_unknown_datum_does_not_pass_a_command_through(s):
    """Declining to range-check is not the same as approving: the horizontal
    fence, the offset bound and the preconditions all still run."""
    fence = Geofence(max_altitude_m=120.0, max_radius_m=300.0, home=HOME)
    blind = _state(launch=None, home=None)
    args = {"latitude_deg": HOME[0] + 0.5, "longitude_deg": HOME[1], "absolute_altitude_m": 9999.0}

    assert check_parameter_bounds("go_to_location", args, s, blind) is None, "no datum, no altitude verdict"
    r = check_geofence("go_to_location", args, fence, s, blind)
    assert r is not None and r.rule == "geofence.radius", "the horizontal fence is unaffected"
