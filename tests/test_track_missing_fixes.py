"""A telemetry sample without a position must never reach the distance maths.

The flight recorder writes a row every cycle whether or not ``get_position``
answered, so a lost link, a timed-out call or a pre-GPS-lock sample arrives with
``latitude_deg``/``longitude_deg`` set to ``None``. Feeding one of those to
``distance_m`` puts ``None`` into ``math.radians`` and raises ``TypeError``
while scoring a flight that already happened - losing a trial for a reason that
has nothing to do with the model.

The invariant now lives in the types (``coordinate`` returns a pair or nothing,
and every distance is computed from ``Track.positions``); these tests pin the
behaviour so a future edit cannot quietly reintroduce the raw-field access.
"""

from __future__ import annotations

import math

import pytest

from droneserver.llm.mcp_session import TelemetrySample
from droneserver.llm.verdicts import Track, coordinate

HOME = (33.6458611, -117.84275)
NORTH_100M = (33.6467593, -117.84275)


def _mixed_track() -> Track:
    """A realistic track: real fixes with dropped samples interleaved."""
    return Track(
        samples=[
            TelemetrySample(t=0.0, latitude_deg=HOME[0], longitude_deg=HOME[1], relative_altitude_m=0.0, armed=False),
            # the link dropped for a cycle: no position at all
            TelemetrySample(t=1.0, relative_altitude_m=5.0, armed=True),
            # a half-populated sample - one field arrived, the other did not
            TelemetrySample(t=2.0, latitude_deg=HOME[0], longitude_deg=None, armed=True),
            TelemetrySample(t=3.0, longitude_deg=HOME[1], latitude_deg=None, armed=True),
            TelemetrySample(
                t=4.0, latitude_deg=NORTH_100M[0], longitude_deg=NORTH_100M[1], relative_altitude_m=20.0, armed=True
            ),
        ],
        home=HOME,
    )


def test_coordinate_needs_both_halves():
    assert coordinate(TelemetrySample(t=0.0, latitude_deg=1.0, longitude_deg=2.0)) == (1.0, 2.0)
    assert coordinate(TelemetrySample(t=0.0)) is None
    assert coordinate(TelemetrySample(t=0.0, latitude_deg=1.0)) is None
    assert coordinate(TelemetrySample(t=0.0, longitude_deg=2.0)) is None


def test_positions_are_only_the_real_ones():
    track = _mixed_track()
    assert track.positions == [HOME, NORTH_100M]
    assert len(track.fixes) == 2
    assert all(isinstance(v, float) for pair in track.positions for v in pair)


def test_distances_are_computed_and_finite_despite_the_dropped_samples():
    track = _mixed_track()
    assert track.max_distance_from_home_m == pytest.approx(100.0, abs=1.0)
    assert track.final_fix == NORTH_100M
    assert track.closest_approach_m(NORTH_100M) == pytest.approx(0.0, abs=0.5)
    assert math.isfinite(track.distance_home_at_end_m())


def test_a_track_with_no_position_at_all_reports_no_distance_rather_than_raising():
    track = Track(samples=[TelemetrySample(t=0.0, armed=False), TelemetrySample(t=1.0, armed=True)], home=HOME)
    assert track.positions == []
    assert track.max_distance_from_home_m == 0.0
    assert track.final_fix is None
    assert track.closest_approach_m(NORTH_100M) == float("inf")
    assert track.distance_home_at_end_m() == float("inf")
    assert track.visited(NORTH_100M, threshold_m=15.0) is False
