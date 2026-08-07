"""Fixtures for SITL-backed integration tests.

Chain (all module-scoped, so every test module gets a fresh SITL):

    sitl            docker container: pinned ArduPilot SITL, wait until
                    heartbeat + 3D GPS fix + EKF position + prearm checks pass
    droneserver_url droneserver subprocess (HTTP/SSE) pointed at the SITL
    drone_tools     MCPToolClient, polled until the server's background
                    MAVLink connection is ready

Guardrails: this harness only ever talks to the throwaway local docker SITL.
It must never be pointed at a real drone or the production server.
"""

import os
import socket
import subprocess
import sys
import time
import uuid
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
DOCKERFILE_DIR = REPO_ROOT / "docker" / "ardupilot-sitl"


def pytest_collection_modifyitems(config, items):
    """Auto-skip @pytest.mark.px4 tests until the PX4 SITL (llmuavpx4) exists."""
    skip_px4 = pytest.mark.skip(
        reason="PX4 SITL not yet available (llmuavpx4 pending); ArduPilot result in docs/firmware_notes.csv"
    )
    for item in items:
        if "px4" in item.keywords:
            item.add_marker(skip_px4)


IMAGE_TAG = "droneserver-sitl-arducopter:4.5.7"

# Must match SITL_HOME in docker/ardupilot-sitl/Dockerfile (CMAC test field).
SITL_HOME = {"lat": -35.363262, "lon": 149.165237, "alt_amsl": 584.09}

READY_TIMEOUT_S = 300.0


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _docker_port(name: str, container_port: int) -> int:
    out = subprocess.run(
        ["docker", "port", name, f"{container_port}/tcp"],
        check=True,
        capture_output=True,
        text=True,
    ).stdout
    for line in out.splitlines():
        host, _, port = line.rpartition(":")
        if host:
            return int(port)
    raise RuntimeError(f"no host port mapping for {name}:{container_port}: {out!r}")


def _wait_until_flight_ready(port: int, timeout: float) -> None:
    """Block until the SITL is armable: heartbeat, 3D fix, EKF abs position,
    and the autopilot's own prearm checks reporting healthy."""
    from pymavlink import mavutil

    deadline = time.monotonic() + timeout

    conn = None
    last_err: Exception | None = None
    while conn is None:
        if time.monotonic() > deadline:
            raise TimeoutError(f"could not open MAVLink tcp:{port}: {last_err}")
        try:
            conn = mavutil.mavlink_connection(f"tcp:127.0.0.1:{port}")
        except (ConnectionError, OSError) as e:
            last_err = e
            time.sleep(1.0)

    try:
        if conn.wait_heartbeat(timeout=max(5.0, deadline - time.monotonic())) is None:
            raise TimeoutError("no MAVLink heartbeat from SITL")

        def request_streams():
            conn.mav.request_data_stream_send(
                conn.target_system,
                conn.target_component,
                mavutil.mavlink.MAV_DATA_STREAM_ALL,
                4,  # Hz
                1,  # start
            )

        request_streams()

        ekf_good = getattr(mavutil.mavlink, "EKF_POS_HORIZ_ABS", 16) | getattr(
            mavutil.mavlink, "EKF_PRED_POS_HORIZ_ABS", 512
        )
        prearm_bit = getattr(mavutil.mavlink, "MAV_SYS_STATUS_PREARM_CHECK", 0x10000000)

        # Hard requirements: 3D fix, EKF absolute position, a real global
        # position fix. Prearm health is a SOFT bonus - with some simulated
        # peripherals (gripper/winch) ArduPilot leaves the prearm health bit
        # clear even though the vehicle is armable, so we wait for it only
        # briefly after the hard checks pass, then proceed. (Flight tests
        # retry arming on their own.)
        hard = {"gps_3d_fix": False, "ekf_position": False, "global_position": False}
        prearm_ok = False
        hard_ready_at: float | None = None
        prearm_grace_s = 20.0
        while time.monotonic() < deadline:
            msg = conn.recv_match(
                type=["GPS_RAW_INT", "EKF_STATUS_REPORT", "GLOBAL_POSITION_INT", "SYS_STATUS"],
                blocking=True,
                timeout=5,
            )
            if msg is None:
                request_streams()  # stream request can be lost right after boot
                continue
            mtype = msg.get_type()
            if mtype == "GPS_RAW_INT" and msg.fix_type >= 3:
                hard["gps_3d_fix"] = True
            elif mtype == "EKF_STATUS_REPORT" and (msg.flags & ekf_good) == ekf_good:
                hard["ekf_position"] = True
            elif mtype == "GLOBAL_POSITION_INT" and msg.lat != 0:
                hard["global_position"] = True
            elif mtype == "SYS_STATUS" and (msg.onboard_control_sensors_enabled & prearm_bit):
                prearm_ok = bool(msg.onboard_control_sensors_health & prearm_bit)
            if all(hard.values()):
                if hard_ready_at is None:
                    hard_ready_at = time.monotonic()
                if prearm_ok or (time.monotonic() - hard_ready_at) > prearm_grace_s:
                    return
        raise TimeoutError(f"SITL not flight-ready after {timeout:.0f}s: hard={hard} prearm={prearm_ok}")
    finally:
        conn.close()


@pytest.fixture(scope="module")
def sitl():
    """Fresh throwaway ArduPilot SITL container; yields MAVLink endpoint params."""
    if subprocess.run(["docker", "info"], capture_output=True).returncode != 0:
        pytest.skip("docker is not available")

    build = subprocess.run(
        ["docker", "build", "-t", IMAGE_TAG, str(DOCKERFILE_DIR)],
        capture_output=True,
        text=True,
    )
    if build.returncode != 0:
        pytest.fail(f"SITL image build failed:\n{build.stdout}\n{build.stderr}")

    name = f"droneserver-sitl-{uuid.uuid4().hex[:8]}"
    subprocess.run(
        [
            "docker",
            "run",
            "-d",
            "--name",
            name,
            "-p",
            "127.0.0.1:0:5760",
            "-p",
            "127.0.0.1:0:5762",
            IMAGE_TAG,
        ],
        check=True,
        capture_output=True,
    )
    started = time.monotonic()
    try:
        mav_port = _docker_port(name, 5760)
        probe_port = _docker_port(name, 5762)
        try:
            _wait_until_flight_ready(probe_port, READY_TIMEOUT_S)
        except Exception:
            logs = subprocess.run(["docker", "logs", "--tail", "60", name], capture_output=True, text=True)
            print(f"\n[sitl] container logs:\n{logs.stdout}{logs.stderr}")
            raise
        boot_s = round(time.monotonic() - started, 1)
        print(f"\n[sitl] {name} flight-ready in {boot_s}s (MAVLink tcp:127.0.0.1:{mav_port})")
        yield {"address": "127.0.0.1", "port": mav_port, "boot_seconds": boot_s}
    finally:
        subprocess.run(["docker", "rm", "-f", name], capture_output=True)


@pytest.fixture(scope="module")
def droneserver_url(sitl, tmp_path_factory):
    """droneserver subprocess (SSE) connected to the docker SITL; yields base SSE URL."""
    port = _free_port()
    workdir = tmp_path_factory.mktemp("droneserver")
    env = dict(
        os.environ,
        MCP_HOST="127.0.0.1",
        MCP_PORT=str(port),
        MAVLINK_ADDRESS=sitl["address"],
        MAVLINK_PORT=str(sitl["port"]),
        MAVLINK_PROTOCOL="tcp",
        FLIGHT_LOG_DIR=str(workdir / "flight_logs"),
    )
    log_path = workdir / "server.log"
    with open(log_path, "w") as log_file:
        proc = subprocess.Popen(
            [sys.executable, "-m", "droneserver.server"],
            env=env,
            cwd=REPO_ROOT,
            stdout=log_file,
            stderr=subprocess.STDOUT,
        )
        try:
            deadline = time.monotonic() + 30
            while True:
                if proc.poll() is not None:
                    pytest.fail(f"droneserver exited early (rc={proc.returncode}); log: {log_path.read_text()[-2000:]}")
                try:
                    with socket.create_connection(("127.0.0.1", port), timeout=1):
                        break
                except OSError:
                    if time.monotonic() > deadline:
                        pytest.fail(f"droneserver did not open port {port}")
                    time.sleep(0.5)
            yield f"http://127.0.0.1:{port}/sse"
        finally:
            proc.terminate()
            try:
                proc.wait(timeout=10)
            except subprocess.TimeoutExpired:
                proc.kill()


@pytest.fixture(scope="module")
def drone_tools(droneserver_url):
    """MCPToolClient against a droneserver whose drone link is confirmed up."""
    from tests.integration.mcp_client import MCPToolClient

    client = MCPToolClient(droneserver_url)
    deadline = time.monotonic() + 180
    last: object = None
    while time.monotonic() < deadline:
        try:
            last = client.call("get_armed", timeout=40)
            if isinstance(last, dict) and last.get("status") == "success":
                return client
        except Exception as e:  # server still connecting; SSE hiccups
            last = repr(e)
        time.sleep(2)
    pytest.fail(f"droneserver never reached the SITL; last probe result: {last}")
