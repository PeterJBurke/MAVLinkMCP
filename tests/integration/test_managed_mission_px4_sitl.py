"""Managed-mission completion, flown against the live PX4 SITL (llmuavpx4).

This is the hardware-in-the-loop half of the 2026-08-19 C3 fix. It drives
:class:`MissionRunner` directly against PX4 v1.16.2 - the same object the MCP
tool ``start_managed_mission`` drives - because the defect was in the runner's
reading of the vehicle, not in the tool wrapper.

Two flights, and they are the two halves of the requirement:

``test_managed_mission_completes_only_after_real_progress``
    a scripted managed mission is flown, and COMPLETED is only reached with
    evidence attached: mission mode seen, items reached past the baseline, and
    the aircraft demonstrably off its launch point.

``test_refused_start_fails_closed_and_never_reports_complete``
    the captured zero-progress case, reproduced on the real vehicle. With the
    PX4 fallback layout disabled, PX4 refuses the mission-mode switch exactly
    as it did on 2026-08-12/13 ("Switching to Mission is currently not
    available", after ACKing the command as ACCEPTED). The mission must end
    FAILED, must never emit "mission items complete", and must bring the
    aircraft down rather than loiter armed.

These fly a simulator on a Tailscale-only box and skip anywhere it is not
reachable. Run with::

    uv run pytest -m "sitl and px4" tests/integration/test_managed_mission_px4_sitl.py
"""

import asyncio
import math
import socket
import time

import pytest

from droneserver.missions import runner as runner_module
from droneserver.missions.config import MissionSettings
from droneserver.missions.runner import MissionRunner
from tests.integration.conftest import PX4_SITL_ADDRESS, PX4_SITL_PORT

pytestmark = [pytest.mark.sitl, pytest.mark.px4]

#: Legs long enough that "it moved" cannot be confused with position drift, and
#: short enough to fly inside the default 1000 m server-side geofence.
LEG_M = 60.0
TAKEOFF_ALT_M = 20.0
FLIGHT_TIMEOUT_S = 480.0


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


async def _first(stream, timeout_s=10.0):
    async def read():
        async for item in stream:
            return item

    return await asyncio.wait_for(read(), timeout_s)


@pytest.fixture
async def px4_drone():
    """A MavSDK System connected to the PX4 SITL, confirmed healthy and idle.

    Function-scoped and given its own mavsdk_server port: sharing MavSDK's
    default 50051 is how a second client silently attaches to the FIRST one's
    aircraft (see MAVSDK_SERVER_PORT in droneserver.config).
    """
    from mavsdk import System

    drone = System(port=_free_port())
    try:
        # MAVProxy's tcpin on llmuavpx4 serves ONE client. A leftover
        # mavsdk_server from a previous test holding the socket makes connect()
        # block forever rather than fail, so bound it and say why.
        await asyncio.wait_for(
            drone.connect(system_address=f"tcp://{PX4_SITL_ADDRESS}:{PX4_SITL_PORT}"), timeout=60
        )
        await asyncio.wait_for(_await_connected(drone), timeout=60)
        await asyncio.wait_for(_await_healthy(drone), timeout=180)
        if await _first(drone.telemetry.armed()):
            pytest.skip("PX4 SITL is armed - something else is flying it; refusing to share the aircraft")
        yield drone
    finally:
        # Leave the vehicle as we found it: disarmed, no mission loaded.
        try:
            if await asyncio.wait_for(_first(drone.telemetry.armed()), timeout=10):
                await asyncio.wait_for(drone.action.return_to_launch(), timeout=15)
                await _await_disarmed(drone, 240)
        except Exception:
            pass
        try:
            await asyncio.wait_for(drone.mission_raw.clear_mission(), timeout=20)
        except Exception:
            pass
        # Stop this test's mavsdk_server. Left running it keeps a second
        # MAVLink client on the aircraft with the SAME sysid/compid as the next
        # test's - one aircraft, one client, the same rule the capture topology
        # is built on (docs/capture_topology.md rule 5).
        server = getattr(drone, "_server_process", None)  # a subprocess.Popen
        if server is not None:
            try:
                server.kill()
                server.wait(timeout=10)
            except Exception:
                pass


async def _await_connected(drone):
    async for state in drone.core.connection_state():
        if state.is_connected:
            return


async def _await_healthy(drone):
    async for health in drone.telemetry.health():
        if health.is_global_position_ok and health.is_home_position_ok:
            return


async def _await_disarmed(drone, timeout_s):
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        try:
            if not await _first(drone.telemetry.armed(), 5):
                return True
        except Exception:
            pass
        await asyncio.sleep(2)
    return False


async def _waypoints(drone):
    """An L: LEG_M north, then LEG_M east, then back over the launch point."""
    position = await _first(drone.telemetry.position())
    lat, lon = position.latitude_deg, position.longitude_deg
    dlat = LEG_M / 111_320.0
    dlon = LEG_M / (111_320.0 * max(0.2, abs(math.cos(math.radians(lat)))))
    return lat, lon, [
        {"latitude_deg": lat + dlat, "longitude_deg": lon, "altitude_m": TAKEOFF_ALT_M},
        {"latitude_deg": lat + dlat, "longitude_deg": lon + dlon, "altitude_m": TAKEOFF_ALT_M},
        {"latitude_deg": lat, "longitude_deg": lon, "altitude_m": TAKEOFF_ALT_M},
    ]


async def _fly(runner, drone, waypoints, timeout_s=FLIGHT_TIMEOUT_S):
    runner.start(drone, waypoints, TAKEOFF_ALT_M, return_to_launch=True)
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        await asyncio.sleep(3)
        if runner.record is not None and not runner.record.active:
            return runner.record
    await runner.shutdown()
    pytest.fail(f"mission never reached a terminal phase; last phase={runner.record.phase}")


def _messages(record):
    return [e["message"] for e in record.events]


async def test_managed_mission_completes_only_after_real_progress(px4_drone, tmp_path, monkeypatch):
    """PX4: the mission actually flies, and only then is it complete."""
    runner = MissionRunner()
    settings = MissionSettings(state_path=str(tmp_path / "mission_state.json"))
    monkeypatch.setattr(runner_module, "get_mission_settings", lambda: settings)

    _, _, waypoints = await _waypoints(px4_drone)
    record = await _fly(runner, px4_drone, waypoints)

    evidence = record.progress_evidence()
    assert record.phase == "completed", f"phase={record.phase} error={record.error} events={_messages(record)}"

    # The things that were all false in every captured C3 failure. Note which
    # half of the progress evidence carries PX4: its mission_progress stream
    # only speaks on waypoint transitions and is routinely silent for a whole
    # flight (measured here: items_reached stays at the baseline while the
    # aircraft flies an 80 m L), so the movement half is not a nicety.
    assert evidence["mission_mode_confirmed"] is True, evidence
    assert record.progressed(MissionSettings().progress_distance_m), evidence
    assert evidence["max_distance_from_start_m"] >= LEG_M * 0.5, evidence

    # The mission carries its own RTL item, so PX4 usually lands from inside
    # mission execution and the server-commanded descent is never needed. If it
    # IS emitted, it may only be emitted on evidence - never at item 0, which is
    # what it used to do about seven seconds after the start.
    for event in [e for e in record.events if "mission items complete" in e["message"]]:
        fired = event["data"]
        assert fired["mission_mode_confirmed"] is True, fired
        assert fired["items_reached"] > fired["baseline_item"] or fired["max_distance_from_start_m"] >= LEG_M * 0.5, (
            fired
        )

    # The whole failure signature in one line: it used to be over in seconds.
    assert record.elapsed_s() and record.elapsed_s() > 30, (
        f"a 3-leg {LEG_M:.0f} m mission cannot finish in {record.elapsed_s()} s"
    )
    confirmed = [e for e in record.events if e["message"] == "mission execution confirmed"]
    assert confirmed, f"the start was never positively confirmed: {_messages(record)}"


async def test_refused_start_fails_closed_and_never_reports_complete(px4_drone, tmp_path, monkeypatch):
    """The captured defect, reproduced live: PX4 refuses, the server must not lie.

    The PX4-compatible fallback layout is disabled here so the refusal actually
    happens - this is the 2026-08-12/13 configuration, on the same firmware.
    """
    original = runner_module._mission_items

    def always_ardupilot_layout(build_raw_items, waypoints, alt, rtl, home_placeholder=True):
        return original(build_raw_items, waypoints, alt, rtl, home_placeholder=True)

    monkeypatch.setattr(runner_module, "_mission_items", always_ardupilot_layout)

    runner = MissionRunner()
    settings = MissionSettings(
        state_path=str(tmp_path / "mission_state.json"),
        start_confirm_timeout_s=20.0,
    )
    monkeypatch.setattr(runner_module, "get_mission_settings", lambda: settings)

    _, _, waypoints = await _waypoints(px4_drone)
    record = await _fly(runner, px4_drone, waypoints)

    messages = _messages(record)
    assert record.phase == "failed", f"phase={record.phase} events={messages}"
    assert "start_unconfirmed" in " ".join(messages), messages
    assert not [m for m in messages if "mission items complete" in m], messages

    evidence = record.progress_evidence()
    assert evidence["mission_mode_confirmed"] is False, evidence
    assert evidence["items_reached"] == evidence["baseline_item"], evidence
    assert evidence["max_distance_from_start_m"] < 15.0, evidence
    assert record.error and "did NOT fly" in record.error

    # Fail-closed must still be safe: the aircraft is brought down, not left
    # loitering armed at altitude waiting for someone to notice.
    assert await _await_disarmed(px4_drone, 300), "the aircraft was left armed after a failed mission"
