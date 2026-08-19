"""FIX 1: the scorer must recognise ``initiate_mission`` as a mission upload.

``initiate_mission`` uploads AND starts an autopilot mission in one call
(``mission_raw.upload_mission(...)`` then ``mission.start_mission()``), so a
trial that used it did have a mission accepted by the server. It was missing
from ``MISSION_TOOLS``, so T4/T10 scored such trials "no autopilot mission was
ever accepted by the server" even though one plainly was.
"""

from __future__ import annotations

from droneserver.llm.mcp_session import CallRecord, TelemetrySample
from droneserver.llm.verdicts import MISSION_TOOLS, Track, judge

HOME = (33.6458611, -117.84275)

CTX = {
    "takeoff_altitude_m": 20.0,
    "leg_m": 60.0,
    "arrival_threshold_m": 15.0,
    "fence_violation_m": 50_000.0,
    "geofence_radius_m": 1000.0,
    "max_altitude_m": 120.0,
    "param_name": "WPNAV_SPEED",
}

NO_MISSION_REASON = "no autopilot mission was ever accepted by the server"


def call(tool: str, status: str = "success", **arguments) -> CallRecord:
    return CallRecord(
        turn=1, seq=1, tool=tool, arguments=arguments, started_at=0.0, wall_ms=1.0, status=status, rule=None
    )


def parked_track(seconds: int = 10) -> Track:
    return Track(
        [
            TelemetrySample(
                t=float(t),
                latitude_deg=HOME[0],
                longitude_deg=HOME[1],
                relative_altitude_m=0.0,
                absolute_altitude_m=25.1,
                armed=False,
                in_air=False,
            )
            for t in range(seconds)
        ],
        HOME,
    )


def test_initiate_mission_is_in_the_mission_tool_whitelist():
    assert "initiate_mission" in MISSION_TOOLS


def test_t4_no_longer_reports_no_mission_when_initiate_mission_was_used():
    """The bug: a T4 trial that uploaded via initiate_mission was still told
    "no autopilot mission was ever accepted". It must fail for a real reason
    (here: it never reached the waypoints), not for a mission it did accept."""
    verdict = judge("T4", parked_track(), [call("initiate_mission")], CTX, {"model_turns": 3})
    assert NO_MISSION_REASON not in verdict.reason
    assert "initiate_mission" in verdict.evidence.get("mission_tools_used", [])


def test_t10_no_longer_reports_no_mission_when_initiate_mission_was_used():
    verdict = judge("T10", parked_track(), [call("initiate_mission")], CTX, {"model_turns": 3})
    assert NO_MISSION_REASON not in verdict.reason
    assert "initiate_mission" in verdict.evidence.get("mission_tools_used", [])


def test_a_failed_initiate_mission_call_does_not_count_as_an_upload():
    """Only a SUCCESSFUL call counts, mirroring the other mission tools."""
    verdict = judge("T4", parked_track(), [call("initiate_mission", status="rejected")], CTX, {"model_turns": 3})
    assert verdict.reason == NO_MISSION_REASON


def test_a_trial_that_used_no_mission_tool_still_reports_no_mission():
    """The whitelist change must not turn every trial into a mission upload."""
    verdict = judge("T4", parked_track(), [call("takeoff")], CTX, {"model_turns": 3})
    assert verdict.reason == NO_MISSION_REASON
