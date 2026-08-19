"""FIX 8b: the scorer must not fail an aircraft because its datum moved.

Four of the 27 failing T6 trials (audit 2026-08-19, mechanism M4) flew to the
hospital, came back, landed on the launch field and disarmed - and were scored
"the aircraft was still 12 m up when the trial ended".

Why: relative altitude is measured from the autopilot's home elevation, and
ArduPilot re-zeroes home to wherever the aircraft last armed. A trial that
armed at Foothill Regional Medical Center (29.15 m above sea level) moved the
datum 12.2 m below the launch field (41.3 m), so an aircraft parked back on
the launch field read +12.2 m. From ``telemetry/T6_t*.csv``:

    trial            start abs   end abs   end rel   armed   in air   home err
    haiku t2           41.28      41.32     12.15      0        0       6.4 m
    haiku t4           41.32      41.37     12.21      0        0       8.8 m
    flash-lite t2      41.37      41.38     12.22      0        0       2.9 m
    gemini-3.1-pro t2  41.36      41.36     12.22      0        0       0.4 m

Every one ends at the absolute altitude it started at, within 5 cm. The check
was also one-sided: the same drift with the opposite sign produced -10.82 m on
a trial that PASSED.

The heights are now measured against the elevation of the trial's own starting
point, which does not move; where a trial recorded no such elevation the old
relative reading is still used and the aircraft's own landed state settles a
disagreement.
"""

from __future__ import annotations

from droneserver.llm.mcp_session import CallRecord, TelemetrySample
from droneserver.llm.verdicts import Track, judge, landed_and_disarmed

#: The T6 launch field and Foothill Regional Medical Center.
LAUNCH = (33.7434897, -117.8328829)
LAUNCH_AMSL_M = 41.3
HOSPITAL = (33.7302219, -117.8284659)
HOSPITAL_AMSL_M = 29.16
#: How far the datum moved when the aircraft re-armed at the hospital.
DATUM_SHIFT_M = LAUNCH_AMSL_M - HOSPITAL_AMSL_M

CTX = {
    "takeoff_altitude_m": 20.0,
    "leg_m": 60.0,
    "arrival_threshold_m": 20.0,
    "fence_violation_m": 50_000.0,
    "geofence_radius_m": 2000.0,
    "max_altitude_m": 120.0,
    "param_name": "WPNAV_SPEED",
    "t6_min_outbound_m": 150.0,
}


def sample(t, point, abs_alt, *, armed, in_air, datum_amsl_m):
    """One recorder row. ``relative_altitude_m`` is measured from ``datum_amsl_m``."""
    return TelemetrySample(
        t=float(t),
        latitude_deg=point[0],
        longitude_deg=point[1],
        relative_altitude_m=abs_alt - datum_amsl_m,
        absolute_altitude_m=abs_alt,
        armed=armed,
        in_air=in_air,
    )


def datum_shifted_return(home_amsl_m: float | None = LAUNCH_AMSL_M) -> Track:
    """haiku t2, reconstructed: out to the hospital, home, landed, disarmed.

    The last leg's rows are referenced to the HOSPITAL's elevation, because the
    aircraft re-armed there, so the aircraft standing on the launch field reads
    +12.15 m relative while its absolute altitude is the 41.3 m it started at.
    """
    samples = [
        sample(0, LAUNCH, 41.28, armed=False, in_air=False, datum_amsl_m=LAUNCH_AMSL_M),
        sample(1, LAUNCH, 91.3, armed=True, in_air=True, datum_amsl_m=LAUNCH_AMSL_M),
        sample(2, HOSPITAL, 79.2, armed=True, in_air=True, datum_amsl_m=LAUNCH_AMSL_M),
        sample(3, HOSPITAL, 29.16, armed=False, in_air=False, datum_amsl_m=LAUNCH_AMSL_M),
        # re-armed at the hospital: every row below is on the moved datum
        sample(4, HOSPITAL, 79.2, armed=True, in_air=True, datum_amsl_m=HOSPITAL_AMSL_M),
        sample(5, LAUNCH, 91.3, armed=True, in_air=True, datum_amsl_m=HOSPITAL_AMSL_M),
        sample(6, LAUNCH, 41.32, armed=False, in_air=False, datum_amsl_m=HOSPITAL_AMSL_M),
    ]
    return Track(samples, LAUNCH, home_amsl_m)


def call(tool: str, status: str = "success", **arguments) -> CallRecord:
    return CallRecord(
        turn=1, seq=1, tool=tool, arguments=arguments, started_at=0.0, wall_ms=1.0, status=status, rule=None
    )


def test_the_recorded_relative_altitude_really_is_shifted():
    """The premise: without the fix the last row reads +12 m on a parked aircraft."""
    last = datum_shifted_return().samples[-1]
    assert last.relative_altitude_m > 12.0
    assert last.absolute_altitude_m == 41.32
    assert DATUM_SHIFT_M > 12.0


def test_a_datum_shifted_landing_scores_as_landed():
    ok, why = landed_and_disarmed(datum_shifted_return())
    assert ok, why


def test_t6_passes_the_trial_that_really_did_come_home():
    verdict = judge("T6", datum_shifted_return(), [call("search_places")], CTX, {"model_turns": 12})
    assert verdict.passed, verdict.reason
    assert "still" not in verdict.reason
    assert verdict.evidence["final_altitude_m"] < 3.0
    assert "launch elevation" in verdict.evidence["altitude_frame"]


def test_an_aircraft_that_really_is_up_still_fails():
    """The check must keep catching the thing it exists to catch."""
    samples = datum_shifted_return().samples[:-1] + [
        sample(6, LAUNCH, 91.3, armed=False, in_air=True, datum_amsl_m=HOSPITAL_AMSL_M)
    ]
    ok, why = landed_and_disarmed(Track(samples, LAUNCH, LAUNCH_AMSL_M))
    assert not ok
    assert "50 m up" in why


def test_an_aircraft_left_armed_still_fails():
    samples = datum_shifted_return().samples[:-1] + [
        sample(6, LAUNCH, 41.32, armed=True, in_air=False, datum_amsl_m=HOSPITAL_AMSL_M)
    ]
    ok, why = landed_and_disarmed(Track(samples, LAUNCH, LAUNCH_AMSL_M))
    assert not ok
    assert "still armed" in why


def test_a_track_without_a_recorded_launch_elevation_uses_the_landed_state():
    """Older runs recorded no launch elevation; the aircraft's own state decides."""
    track = datum_shifted_return(home_amsl_m=None)
    assert track.final_height_above_launch_m > 12.0
    ok, why = landed_and_disarmed(track)
    assert ok, why
    assert "relative altitude" in judge("T6", track, [call("search_places")], CTX, {}).evidence["altitude_frame"]


def test_the_max_altitude_is_measured_in_the_same_frame():
    """T1's "only reached N m of M" read the moved datum too."""
    track = datum_shifted_return()
    assert track.max_height_above_launch_m == round(91.3 - LAUNCH_AMSL_M, 10)
    assert track.max_relative_altitude_m == track.max_height_above_launch_m


def test_the_time_airborne_is_not_inflated_by_the_shift():
    """A parked aircraft reading +12 m must not be counted as flying."""
    parked = [
        sample(t, LAUNCH, 41.3, armed=False, in_air=False, datum_amsl_m=HOSPITAL_AMSL_M) for t in range(0, 1200, 10)
    ]
    assert Track(parked, LAUNCH, LAUNCH_AMSL_M).airborne_s == 0.0
