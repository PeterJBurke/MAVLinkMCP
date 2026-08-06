"""SITL integration tests for the v2 mission_raw tools (ArduCopter docker SITL).

One fresh SITL per module; the flight test flies the QGC-imported mission.
"""

import time
from pathlib import Path

import pytest

from tests.integration.conftest import SITL_HOME
from tests.integration.flight import land_and_wait_disarm
from tests.integration.mcp_client import ToolCallError

pytestmark = pytest.mark.sitl

PLAN_PATH = Path(__file__).parent / "fixtures" / "demo_mission.plan"
LAT, LON = SITL_HOME["lat"], SITL_HOME["lon"]


# ------------------------------------------------------------ validation


def test_import_requires_exactly_one_source(drone_tools):
    result = drone_tools.call("import_qgc_mission")
    assert result["status"] == "failed"
    assert "exactly one" in result["error"]

    result = drone_tools.call("import_qgc_mission", plan_json="{}", plan_path="/x.plan")
    assert result["status"] == "failed"


def test_import_rejects_invalid_json(drone_tools):
    result = drone_tools.call("import_qgc_mission", plan_json="{not json")
    assert result["status"] == "failed"
    assert "not valid JSON" in result["error"]


def test_rally_upload_requires_points(drone_tools):
    result = drone_tools.call("rally_points", action="upload", points=[])
    assert result["status"] == "failed"
    assert "at least one rally point" in result["error"]


def test_bad_actions_rejected(drone_tools):
    for tool in ("rally_points", "raw_geofence_transfer", "raw_mission_control"):
        result = drone_tools.call(tool, action="explode")
        assert result["status"] == "failed", tool


def test_cancel_without_transfer_fails_cleanly(drone_tools):
    result = drone_tools.call("raw_mission_control", action="cancel_upload")
    assert result["status"] == "failed"  # documented: only valid mid-transfer


# ------------------------------------------------------------ transfers


def test_import_qgc_plan_uploads_mission_and_rally(drone_tools):
    result = drone_tools.call("import_qgc_mission", plan_json=PLAN_PATH.read_text())
    assert result["status"] == "success", result
    assert result["imported"]["mission_items"] == 5  # home + takeoff + 2 wp + RTL
    assert result["imported"]["rally_items"] == 1
    assert result["uploaded"].get("mission") is True
    assert result["uploaded"].get("rally") is True
    # NOTE: no cross-check via the v1 download_mission tool here - it blocks
    # on the mission-plugin progress stream, which never emits for missions
    # uploaded via the raw path (v1 bug, deferred to the Phase 2 rewrite).
    # The real verification is test_fly_imported_mission_with_raw_control.


def test_rally_points_roundtrip(drone_tools):
    up = drone_tools.call(
        "rally_points",
        action="upload",
        points=[
            {"latitude_deg": LAT + 0.0015, "longitude_deg": LON, "altitude_m": 20.0},
            {"latitude_deg": LAT - 0.0015, "longitude_deg": LON, "altitude_m": 30.0},
        ],
    )
    assert up["status"] == "success", up

    down = drone_tools.call("rally_points", action="download")
    assert down["status"] == "success"
    assert len(down["rally_points"]) == 2
    assert down["rally_points"][0]["latitude_deg"] == pytest.approx(LAT + 0.0015)


def test_raw_geofence_download_cross_checks_geofence_plugin(drone_tools):
    d = 0.002
    up = drone_tools.call(
        "upload_geofence",  # the P0 geofence-plugin tool
        polygons=[
            {
                "points": [
                    {"latitude_deg": LAT - d, "longitude_deg": LON - d},
                    {"latitude_deg": LAT - d, "longitude_deg": LON + d},
                    {"latitude_deg": LAT + d, "longitude_deg": LON + d},
                    {"latitude_deg": LAT + d, "longitude_deg": LON - d},
                ],
                "fence_type": "inclusion",
            }
        ],
    )
    assert up["status"] == "success", up

    down = drone_tools.call("raw_geofence_transfer", action="download")
    assert down["status"] == "success"
    assert len(down["geofence_items"]) == 4  # one vertex item per polygon point


# ------------------------------------------------------------ flight


def test_fly_imported_mission_with_raw_control(drone_tools):
    # mission from test_import_qgc_plan_uploads_mission_and_rally is on board;
    # re-upload to be independent of test ordering.
    result = drone_tools.call("import_qgc_mission", plan_json=PLAN_PATH.read_text())
    assert result["status"] == "success", result

    # arm (prearm settle) then raw start (transient UNKNOWN ack observed once
    # right after arming - retry is part of the documented behavior)
    deadline = time.monotonic() + 90
    armed = {}
    while time.monotonic() < deadline:
        try:
            armed = drone_tools.call("arm_drone", timeout=60)
        except ToolCallError as e:
            armed = {"status": "failed", "error": str(e)}
        if armed.get("status") == "success":
            break
        time.sleep(3)
    assert armed.get("status") == "success", f"arm failed: {armed}"

    started = {}
    for _ in range(3):
        started = drone_tools.call("raw_mission_control", action="start", timeout=60)
        if started.get("status") == "success":
            break
        time.sleep(3)
    assert started.get("status") == "success", started

    # decisive check that the mission is actually running: flight mode
    time.sleep(5)
    mode = drone_tools.call("get_flight_mode")
    assert mode["status"] == "success", mode
    assert any(m in str(mode).upper() for m in ("MISSION", "AUTO")), mode

    # progress is advisory on ArduPilot: fresh subscriptions can catch the
    # takeoff-leg MISSION_CURRENT with total=1 instead of the uploaded count
    # (see firmware_notes.csv) - so only assert an emission arrives.
    progress = drone_tools.call("raw_mission_control", action="progress", timeout_s=90, timeout=120)
    assert progress.get("status") == "success", progress
    assert progress["progress"]["total"] >= 1, progress

    paused = drone_tools.call("raw_mission_control", action="pause")
    assert paused["status"] == "success", paused
    mode = drone_tools.call("get_flight_mode")
    assert mode["status"] == "success" and "HOLD" in str(mode).upper(), mode

    # set_current back to mission start. Index 0 on purpose: mavsdk validates
    # the index against its cached mission total, which can collapse to 1 on
    # ArduPilot mid-flight (see firmware_notes.csv) - higher indexes may be
    # rejected with INVALID_ARGUMENT even though the vehicle holds 5 items.
    setcur = drone_tools.call("raw_mission_control", action="set_current", index=0)
    assert setcur["status"] == "success", setcur

    land_and_wait_disarm(drone_tools)

    cleared = drone_tools.call("raw_mission_control", action="clear")
    assert cleared["status"] == "success", cleared
