"""Is the aircraft on the ground, and how high is it really?

One place for the two questions that a moving altitude datum kept answering
wrongly, so every consumer answers them the same way.

**The datum moves.** ArduPilot re-zeroes ``relative_altitude_m`` wherever the
aircraft last ARMED. A flight that lands away from where it started, re-arms
there and comes home therefore returns an aircraft whose relative altitude
reads the terrain difference between the two arming points - forever, parked
and disarmed included. +4.1 m was measured on eight independent fresh SITL
lanes on 2026-08-19.

Three consumers believed that number in turn:

* ``llm/verdicts.py`` failed four aircraft as "still 12 m up" when they had
  landed exactly where they took off (FIX 8b, 33de5ec).
* ``tools/action.py`` could not confirm a touchdown, so every T6-shape landing
  ran the full 120 s ``landing_timeout`` (FIX 10, 680ee81).
* ``missions/runner.py`` gated managed-mission completion on the same
  threshold, and its "am I still airborne?" check on the inverse (FIX 11).

The order below is the whole lesson: ask the AUTOPILOT whether it is on the
ground - ``landed_state`` and ``in_air`` are its own assessment and no arming
anywhere moves them - and only when it will not answer fall back to a height,
measured against an elevation that does not move.
"""

from __future__ import annotations

#: ``landed_state`` values that mean the aircraft is NOT on the ground. Anything
#: else (UNKNOWN, an empty string, a value a future firmware invents) is treated
#: as "no answer" rather than as a guess in either direction.
IN_THE_AIR_STATES = ("IN_AIR", "TAKING_OFF", "LANDING")

#: Vertical rate above which "ON_GROUND" is not believed. Generous on purpose:
#: it is here to veto an autopilot asserting touchdown in the middle of a 3 m/s
#: descent, not to second-guess a settled aircraft's noise floor.
SETTLED_RATE_M_S = 1.0


def ground_evidence(landed_state: str | None, in_air: bool | None) -> bool | None:
    """Is it on the ground, per the autopilot? ``None`` when it will not say.

    ``True``/``False`` are positive readings. ``None`` is the honest third
    answer, and callers must handle it deliberately: for a completion check
    "unknown" must not mean landed, and for a bring-it-down check "unknown"
    must not mean safe.
    """
    if landed_state == "ON_GROUND":
        return True
    if landed_state in IN_THE_AIR_STATES:
        return False
    if in_air is not None:
        return not in_air
    return None


def settled_on_ground(landed_state: str | None, in_air: bool | None, vertical_speed_m_s: float | None = None) -> bool:
    """Touchdown, from evidence no moving altitude datum can spoil.

    The autopilot says ON_GROUND and not in the air, and - where the rate is
    readable at all - the aircraft is not still moving vertically. There is
    deliberately no altitude term. An unreadable rate vetoes nothing.
    """
    if landed_state != "ON_GROUND":
        return False
    if in_air:
        return False
    if vertical_speed_m_s is not None and abs(vertical_speed_m_s) > SETTLED_RATE_M_S:
        return False
    return True


def height_above_launch_m(
    launch_amsl_m: float | None,
    absolute_altitude_m: float | None,
    relative_altitude_m: float | None,
) -> float | None:
    """Height above a launch point, measured from a datum that cannot move.

    Absolute altitude does not move, so where the caller recorded the elevation
    of its own starting point this measures against that. Where it did not, it
    falls back to the autopilot's relative reading and the old behaviour is
    unchanged - a fallback, never a preference.

    NOTE what this is NOT: it is not height above the ground *under* the
    aircraft. Over terrain that differs from the launch field it reads the
    terrain difference even when parked, which is exactly why it must never be
    the sole evidence that an aircraft has touched down - see
    :func:`settled_on_ground`.
    """
    if launch_amsl_m is not None and absolute_altitude_m is not None:
        return absolute_altitude_m - launch_amsl_m
    return relative_altitude_m
