"""Integration tests for the server-executed (managed) mission path."""

import time

import pytest

from tests.integration.conftest import SITL_HOME

pytestmark = pytest.mark.sitl

LAT, LON = SITL_HOME["lat"], SITL_HOME["lon"]


def wp(dlat=0.0, dlon=0.0, alt=25.0, hold_s=0.0):
    return {
        "latitude_deg": LAT + dlat,
        "longitude_deg": LON + dlon,
        "altitude_m": alt,
        "hold_s": hold_s,
    }


def wait_for_phase(client, phases, timeout_s=240, poll_s=4):
    """Poll get_mission_status until the mission reaches one of `phases`."""
    deadline = time.monotonic() + timeout_s
    last = None
    while time.monotonic() < deadline:
        last = client.call("get_mission_status", include_events=False, timeout=40)
        mission = last.get("mission")
        if mission and mission["phase"] in phases:
            return last
        time.sleep(poll_s)
    pytest.fail(f"mission never reached {phases}; last status: {last}")


def cleanup(client):
    status = client.call("get_mission_status", include_events=False, timeout=40)
    mission = status.get("mission")
    if mission and mission["active"]:
        client.call("control_managed_mission", action="abort", timeout=40)
        wait_for_phase(client, {"aborted", "completed", "failed"}, timeout_s=180)
    client.call("control_managed_mission", action="clear", timeout=40)


def test_validation_rejects_bad_input(drone_tools):
    assert drone_tools.call("start_managed_mission", waypoints=[])["status"] == "failed"
    bad = drone_tools.call("start_managed_mission", waypoints=[{"altitude_m": 20}])
    assert bad["status"] == "failed" and "latitude_deg" in bad["error"]
    assert drone_tools.call("control_managed_mission", action="explode")["status"] == "failed"


def test_status_before_any_mission(drone_tools):
    status = drone_tools.call("get_mission_status")
    assert status["status"] == "success"
    assert status["mission"] is None or isinstance(status["mission"], dict)


def test_managed_mission_flies_and_completes(drone_tools):
    """Submit, poll, complete - the core long-mission path (short version)."""
    cleanup(drone_tools)

    submitted = drone_tools.call(
        "start_managed_mission",
        waypoints=[wp(dlat=0.0008), wp(dlat=0.0008, dlon=0.0008), wp()],
        takeoff_altitude_m=20.0,
        return_to_launch=True,
        timeout=60,
    )
    assert submitted["status"] == "success", submitted
    mission_id = submitted["mission_id"]

    # The call returned immediately - the flight has NOT finished.
    assert submitted["phase"] in ("submitted", "validating", "uploading", "arming")

    # The server flies it on its own.
    running = wait_for_phase(drone_tools, {"running"}, timeout_s=240)
    assert running["mission"]["mission_id"] == mission_id

    # Progress is observable while it flies.
    time.sleep(20)
    mid = drone_tools.call("get_mission_status", event_limit=50, timeout=40)
    assert mid["mission"]["total_items"] >= 4
    assert mid["mission"]["position"] is not None
    assert mid["mission"]["position"]["relative_altitude_m"] > 5
    assert any(e["kind"] == "phase_change" for e in mid["events"])

    done = wait_for_phase(drone_tools, {"completed", "failed", "aborted"}, timeout_s=600)
    assert done["mission"]["phase"] == "completed", done
    assert done["mission"]["elapsed_s"] > 0

    events = drone_tools.call("get_mission_status", event_limit=200, timeout=40)["events"]
    kinds = {e["kind"] for e in events}
    assert "phase_change" in kinds
    assert any(e["kind"] == "info" and "armed" in e["message"] for e in events)

    cleanup(drone_tools)


def test_pause_resume_and_abort(drone_tools):
    cleanup(drone_tools)
    submitted = drone_tools.call(
        "start_managed_mission",
        waypoints=[wp(dlat=0.0012), wp(dlat=0.0012, dlon=0.0012), wp(dlon=0.0012)],
        takeoff_altitude_m=20.0,
        return_to_launch=True,
        timeout=60,
    )
    assert submitted["status"] == "success", submitted
    wait_for_phase(drone_tools, {"running"}, timeout_s=240)

    assert drone_tools.call("control_managed_mission", action="pause", timeout=40)["status"] == "success"
    paused = wait_for_phase(drone_tools, {"paused"}, timeout_s=90)
    assert paused["mission"]["phase"] == "paused"

    assert drone_tools.call("control_managed_mission", action="resume", timeout=40)["status"] == "success"
    wait_for_phase(drone_tools, {"running"}, timeout_s=90)

    assert drone_tools.call("control_managed_mission", action="abort", timeout=40)["status"] == "success"
    aborted = wait_for_phase(drone_tools, {"aborted", "completed"}, timeout_s=300)
    assert aborted["mission"]["phase"] in ("aborted", "completed")

    cleanup(drone_tools)


def test_second_mission_refused_while_one_is_active(drone_tools):
    cleanup(drone_tools)
    first = drone_tools.call("start_managed_mission", waypoints=[wp(dlat=0.0008)], takeoff_altitude_m=20.0, timeout=60)
    assert first["status"] == "success", first
    second = drone_tools.call("start_managed_mission", waypoints=[wp(dlon=0.0008)], timeout=60)
    assert second["status"] == "failed"
    assert "still" in second["error"]
    cleanup(drone_tools)


def test_geofence_rejects_mission_before_upload(drone_tools):
    """The managed path validates the whole mission against the SERVER fence
    before anything is uploaded.

    Uses the module's existing server rather than the safety-configured one:
    ArduPilot's serial-over-TCP accepts a single client per port, so a second
    server in this module cannot attach to the same simulator. (That used to
    "work" only because both servers shared one mavsdk_server helper - i.e.
    they were unknowingly flying through the same connection.) The default
    fence has a 1 km radius, and the bad waypoint below is ~55 km out, so the
    rejection is just as decisive.
    """
    cleanup(drone_tools)
    submitted = drone_tools.call(
        "start_managed_mission",
        waypoints=[wp(dlat=0.0008), {"latitude_deg": LAT + 0.5, "longitude_deg": LON, "altitude_m": 25}],
        takeoff_altitude_m=20.0,
        timeout=60,
    )
    assert submitted["status"] == "success", submitted  # accepted, then validated
    failed = wait_for_phase(drone_tools, {"failed"}, timeout_s=120)
    assert "geofence" in (failed["mission"]["error"] or "")
    assert failed["mission"]["current_item"] == 0  # nothing was flown
    cleanup(drone_tools)
