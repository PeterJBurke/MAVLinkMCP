"""Unit tests for the MavSDK telemetry recorder (no live drone, no mavsdk).

The whole ``mavsdk`` System is replaced by a fake whose telemetry streams are
scripted async generators yielding a couple of samples each. We run
start()/sleep-briefly/stop() on the event loop and assert that ``telemetry.csv``
has the exact header (every column, in order) and at least one data row whose
cells map the mocked values to the right columns.

The repo runs pytest-asyncio in ``asyncio_mode = "auto"`` (see pyproject
``[tool.pytest.ini_options]``), so plain ``async def test_*`` functions are
awaited directly - no decorator needed, matching tests/test_offboard_watchdog.py.
"""

import asyncio
import csv

import pytest

from droneserver.capture import telemetry_recorder as tr
from droneserver.capture.telemetry_recorder import COLUMNS, WIRE_SOURCED_COLUMNS, TelemetryRecorder

# --- scripted MavSDK telemetry sample objects -----------------------------


class _Position:
    latitude_deg = 47.3977419
    longitude_deg = 8.5455938
    absolute_altitude_m = 488.25
    relative_altitude_m = 12.5


class _Attitude:
    roll_deg = 1.5
    pitch_deg = -2.0
    yaw_deg = 90.0


class _Velocity:
    north_m_s = 3.0
    east_m_s = 4.0  # groundspeed = sqrt(3^2 + 4^2) = 5.0
    down_m_s = -0.25


class _Gps:
    num_satellites = 12
    fix_type = "FIX_3D"  # str() -> "FIX_3D"


class _Battery:
    voltage_v = 12.3
    remaining_percent = 0.87  # -> battery_pct 87.0


class _Home:
    latitude_deg = 47.3970000
    longitude_deg = 8.5450000
    absolute_altitude_m = 475.75


class _FixedWing:
    airspeed_m_s = 6.5
    throttle_percentage = 42.0


class _FakeTap:
    """Stand-in for MavlinkTap: the wire messages the recorder reads per row.

    The field values are copied from a real trial
    (``benchmark_runs/20260810T004133Z_postaudit_verify/T1/trial_1/mavlink.jsonl``):
    eph 121 / epv 200, and the SYS_STATUS bitmasks ArduCopter 4.5.7 SITL sends.
    """

    SYS_STATUS = {
        "onboard_control_sensors_present": 1399979055,
        "onboard_control_sensors_enabled": 1382153263,
        "onboard_control_sensors_health": 1467087919,
    }

    def __init__(self, messages=None):
        self.messages = (
            messages
            if messages is not None
            else {
                "GPS_RAW_INT": {"eph": 121, "epv": 200, "fix_type": 6},
                "SYS_STATUS": dict(self.SYS_STATUS),
                "VFR_HUD": {"throttle": 49, "airspeed": 0.33},
            }
        )

    def snapshot(self):
        return dict(self.messages)


async def _agen(*items):
    """Yield each scripted item, cooperatively yielding control between them."""
    for item in items:
        await asyncio.sleep(0)
        yield item


class _FakeTelemetry:
    def position(self):
        return _agen(_Position(), _Position())

    def attitude_euler(self):
        return _agen(_Attitude())

    def velocity_ned(self):
        return _agen(_Velocity())

    def gps_info(self):
        return _agen(_Gps())

    def battery(self):
        return _agen(_Battery())

    def home(self):
        return _agen(_Home())

    def fixedwing_metrics(self):
        return _agen(_FixedWing())

    def flight_mode(self):
        return _agen("HOLD")

    def armed(self):
        return _agen(True)

    def in_air(self):
        return _agen(True)


class _FakeSystem:
    def __init__(self, *args, **kwargs):
        self.telemetry = _FakeTelemetry()

    async def connect(self, system_address=None):
        return None


class _ExplodingSystem:
    """A System whose connect() fails - exercises the never-crash guarantee."""

    def __init__(self, *args, **kwargs):
        self.telemetry = _FakeTelemetry()

    async def connect(self, system_address=None):
        raise RuntimeError("no drone here")


def _read_csv(path):
    with open(path, newline="", encoding="utf-8") as fh:
        return list(csv.reader(fh))


async def test_header_and_mocked_values(tmp_path, monkeypatch):
    monkeypatch.setattr(tr, "System", _FakeSystem)

    rec = TelemetryRecorder("udpin://127.0.0.1:14541", tmp_path, rate_hz=50.0, t0=1000.0)
    # t0 is honoured for clock alignment with the MAVLink tap and audit log.
    assert rec.t0 == 1000.0

    await rec.start()
    await asyncio.sleep(0.15)  # several 50 Hz ticks; lets subscribers populate
    await rec.stop()

    rows = _read_csv(tmp_path / "telemetry.csv")

    # Exact header: every column, in order.
    assert rows[0] == COLUMNS
    assert len(rows) >= 2  # header + at least one data row

    col = {name: i for i, name in enumerate(COLUMNS)}

    # Find a fully-populated data row (subscribers may not have delivered before
    # the very first tick, so scan for one with position filled in).
    populated = [r for r in rows[1:] if r[col["lat_deg"]] != ""]
    assert populated, "expected at least one row with position telemetry"
    row = populated[-1]

    # Position.
    assert row[col["lat_deg"]] == "47.3977419"
    assert row[col["lon_deg"]] == "8.5455938"
    assert row[col["abs_alt_m"]] == "488.25"
    assert row[col["rel_alt_m"]] == "12.5"
    # Attitude.
    assert row[col["roll_deg"]] == "1.5"
    assert row[col["pitch_deg"]] == "-2.0"
    assert row[col["yaw_deg"]] == "90.0"
    # Velocity + derived groundspeed.
    assert row[col["vn_ms"]] == "3.0"
    assert row[col["ve_ms"]] == "4.0"
    assert row[col["vd_ms"]] == "-0.25"
    assert row[col["groundspeed_ms"]] == "5.0"
    # Fixed-wing metrics.
    assert row[col["airspeed_ms"]] == "6.5"
    assert row[col["throttle_pct"]] == "42.0"
    # GPS.
    assert row[col["gps_fix_type"]] == "FIX_3D"
    assert row[col["num_satellites"]] == "12"
    # Battery (remaining_percent 0.87 -> 87.0).
    assert row[col["battery_v"]] == "12.3"
    assert row[col["battery_pct"]] == "87.0"
    # Booleans.
    assert row[col["armed"]] == "True"
    assert row[col["in_air"]] == "True"
    # Flight mode.
    assert row[col["flight_mode"]] == "HOLD"
    # Home.
    assert row[col["home_lat"]] == "47.397"
    assert row[col["home_lon"]] == "8.545"
    assert row[col["home_alt"]] == "475.75"
    # Clock: t_rel_s is a small non-negative offset from the fixed t0.
    assert float(row[col["t_rel_s"]]) >= 0.0
    assert row[col["t_iso"]].endswith("+00:00")  # UTC ISO-8601

    # With no raw source the wire-sourced columns are empty rather than guessed.
    for name in WIRE_SOURCED_COLUMNS:
        for r in rows[1:]:
            assert r[col[name]] == ""


async def test_connect_failure_never_crashes_and_still_writes_header(tmp_path, monkeypatch):
    """A failed connect must not raise; the CSV still gets its header + rows."""
    monkeypatch.setattr(tr, "System", _ExplodingSystem)

    rec = TelemetryRecorder("udpin://127.0.0.1:14541", tmp_path, rate_hz=50.0)
    await rec.start()
    await asyncio.sleep(0.05)
    await rec.stop()

    rows = _read_csv(tmp_path / "telemetry.csv")
    assert rows[0] == COLUMNS
    # Timer still produced uniform rows even though no telemetry arrived.
    assert len(rows) >= 2


async def test_stop_is_idempotent(tmp_path, monkeypatch):
    monkeypatch.setattr(tr, "System", _FakeSystem)
    rec = TelemetryRecorder("udpin://127.0.0.1:14541", tmp_path)
    await rec.start()
    await rec.stop()
    await rec.stop()  # second stop must be a harmless no-op


# --- battery units ---------------------------------------------------------


def test_a_percentage_battery_reading_is_not_multiplied_again():
    """MavSDK documents a 0-1 fraction; ArduCopter sends a percentage.

    The recorder trusted the documentation, so every captured bundle records
    a battery a hundred times too full: the canonical T10 run
    (20260809T195956Z_T10_capture_final3) has battery_pct running from 7000.0
    down to 4100.0 for a battery that went from 70% to 41%. The mission runner
    has normalised the same field since before this recorder existed
    (``droneserver.missions.runner._battery_fraction``).
    """
    assert tr._battery_percent(0.87) == 87.0  # documented fraction
    assert tr._battery_percent(77.0) == 77.0  # what ArduCopter actually sends
    assert tr._battery_percent(100.0) == 100.0
    assert tr._battery_percent(1.0) == 100.0  # a full battery, either way
    assert tr._battery_percent(0.0) == 0.0
    assert tr._battery_percent(None) is None
    assert tr._battery_percent(-1.0) is None
    assert tr._battery_percent("n/a") is None


# --- the four columns Plan 19 §3b promised and no bundle ever carried -------


async def test_the_wire_sourced_columns_are_populated_from_the_tap(tmp_path, monkeypatch):
    """hdop, vdop, ekf_ok and geofence_ok come off the MAVLink wire.

    They are empty in every row of every mission of every bundle this project
    captured before this - MavSDK's telemetry plugin has none of them - while
    Plan 19 §3b listed them as required and verify_bundle called the bundles
    complete. A Zenodo data dictionary describing four always-blank columns is
    the reproducibility criticism the revision exists to answer.

    The values are the ones the autopilot itself reports: GPS_RAW_INT.eph/epv
    scaled from hundredths, and the SYS_STATUS AHRS / GEOFENCE health bits.
    """
    monkeypatch.setattr(tr, "System", _FakeSystem)

    rec = TelemetryRecorder("udpin://127.0.0.1:14541", tmp_path, rate_hz=50.0, raw_source=_FakeTap())
    await rec.start()
    await asyncio.sleep(0.1)
    await rec.stop()

    rows = _read_csv(tmp_path / "telemetry.csv")
    col = {name: i for i, name in enumerate(COLUMNS)}
    row = rows[-1]

    assert row[col["hdop"]] == "1.21"  # eph 121 -> HDOP 1.21
    assert row[col["vdop"]] == "2.0"  # epv 200 -> VDOP 2.00
    assert row[col["ekf_ok"]] == "True"  # SYS_STATUS AHRS health bit
    assert row[col["geofence_ok"]] == "True"  # SYS_STATUS GEOFENCE health bit


def test_a_dop_the_receiver_did_not_report_stays_blank():
    """UINT16_MAX means "unknown"; 0.0 would read as a perfect fix."""
    assert tr._dop({"eph": 121}, "eph") == 1.21
    assert tr._dop({"epv": 200}, "epv") == 2.0
    assert tr._dop({"eph": 65535}, "eph") == ""  # UINT16_MAX -> unknown
    assert tr._dop({"eph": -1}, "eph") == ""
    assert tr._dop({"eph": None}, "eph") == ""
    assert tr._dop({}, "eph") == ""
    assert tr._dop(None, "eph") == ""


def test_a_subsystem_the_autopilot_does_not_report_is_blank_not_false():
    """ "Present" gates the reading. A firmware that reports no geofence has
    not told us the fence is unhealthy, and a confident False there would be an
    invented claim about the aircraft."""
    healthy = {
        "onboard_control_sensors_present": tr._SYS_STATUS_AHRS | tr._SYS_STATUS_GEOFENCE,
        "onboard_control_sensors_health": tr._SYS_STATUS_AHRS | tr._SYS_STATUS_GEOFENCE,
    }
    assert tr._sensor_health(healthy, tr._SYS_STATUS_AHRS) is True
    assert tr._sensor_health(healthy, tr._SYS_STATUS_GEOFENCE) is True

    unhealthy = {
        "onboard_control_sensors_present": tr._SYS_STATUS_AHRS,
        "onboard_control_sensors_health": 0,
    }
    assert tr._sensor_health(unhealthy, tr._SYS_STATUS_AHRS) is False

    absent = {"onboard_control_sensors_present": 0, "onboard_control_sensors_health": 0}
    assert tr._sensor_health(absent, tr._SYS_STATUS_GEOFENCE) == ""
    assert tr._sensor_health(None, tr._SYS_STATUS_AHRS) == ""
    assert tr._sensor_health({"onboard_control_sensors_present": "x"}, tr._SYS_STATUS_AHRS) == ""


def test_throttle_is_a_percentage_not_a_fraction():
    """MavSDK's ``throttle_percentage`` is a 0-1 fraction despite the name.

    Measured on the 2026-08-10 verification flight: VFR_HUD said throttle 49
    on the same tick that telemetry.csv recorded 0.5 - a hovering copter, its
    throttle rounded to one decimal place off a value a hundred times too
    small. The same confusion as battery_pct, through a different door.
    """
    # The wire wins: an integer percentage straight from the autopilot.
    assert tr._throttle_percent({"throttle": 49}, 0.49) == 49.0
    assert tr._throttle_percent({"throttle": 0}, 0.0) == 0.0
    # No wire: apply the same fraction-or-percentage rule as the battery.
    assert tr._throttle_percent(None, 0.49) == 49.0
    assert tr._throttle_percent(None, 42.0) == 42.0
    assert tr._throttle_percent({}, 0.49) == 49.0
    assert tr._throttle_percent(None, None) is None
    assert tr._throttle_percent(None, "n/a") is None
    # A malformed wire value falls back rather than poisoning the column.
    assert tr._throttle_percent({"throttle": "x"}, 0.49) == 49.0


async def test_a_dead_raw_source_never_breaks_a_row(tmp_path, monkeypatch):
    """Capture is fail-soft everywhere: a tap that throws costs four columns,
    not the recording."""
    monkeypatch.setattr(tr, "System", _FakeSystem)

    class _BrokenTap:
        def snapshot(self):
            raise RuntimeError("tap died")

    rec = TelemetryRecorder("udpin://127.0.0.1:14541", tmp_path, rate_hz=50.0, raw_source=_BrokenTap())
    await rec.start()
    await asyncio.sleep(0.05)
    await rec.stop()

    rows = _read_csv(tmp_path / "telemetry.csv")
    col = {name: i for i, name in enumerate(COLUMNS)}
    assert rows[0] == COLUMNS
    assert len(rows) >= 2
    assert rows[-1][col["hdop"]] == ""
    assert rows[-1][col["lat_deg"]] == "47.3977419"  # everything else still recorded


# --- teardown: the gRPC channel and the mavsdk_server behind it -------------


class _FakeChannel:
    def __init__(self):
        self.closed_with = None

    async def close(self, grace=None):
        self.closed_with = grace


class _FakeMulticallable:
    def __init__(self, channel):
        self._channel = channel


class _FakeStub:
    def __init__(self, channel):
        self.SubscribePosition = _FakeMulticallable(channel)


class _FakeProcess:
    def __init__(self):
        self.waited = False

    def wait(self, timeout=None):
        self.waited = True


class _ClosableSystem(_FakeSystem):
    """A System shaped like mavsdk's: plugins holding stubs holding a channel."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.channel = _FakeChannel()
        self._plugins = {"telemetry": type("P", (), {"_stub": _FakeStub(self.channel)})()}
        self._server_process = _FakeProcess()
        self.server_stopped = False

    def _stop_mavsdk_server(self):
        self.server_stopped = True


async def test_stop_closes_the_grpc_channel_and_reaps_the_server(tmp_path, monkeypatch):
    """The teardown that was missing entirely.

    mavsdk's System has no public shutdown, keeps no reference to its gRPC
    channel, and leaves the mavsdk_server subprocess to __del__ - so every
    trial leaked a process, its logging thread and an open channel, and the
    gRPC poller went on posting into an event loop the harness had closed:
    22 ``RuntimeError: Event loop is closed`` tracebacks over eight missions.
    """
    systems = []

    def _factory(*args, **kwargs):
        system = _ClosableSystem(*args, **kwargs)
        systems.append(system)
        return system

    monkeypatch.setattr(tr, "System", _factory)

    rec = TelemetryRecorder("udpin://127.0.0.1:14541", tmp_path, rate_hz=50.0)
    await rec.start()
    await asyncio.sleep(0.05)
    await rec.stop()

    system = systems[0]
    assert system.channel.closed_with == tr.CHANNEL_CLOSE_GRACE_S, "the gRPC channel was not closed"
    assert system.server_stopped, "the mavsdk_server subprocess was not stopped"
    assert system._server_process.waited, "kill() without wait() leaves a zombie per trial"
    assert rec._system is None
    # No task may still be iterating a MavSDK stream once stop() returns.
    assert rec._tasks == [] and rec._sampler is None


async def test_teardown_survives_a_channel_that_will_not_close(tmp_path, monkeypatch):
    """A recorder must never be the reason a trial dies. By this point every
    byte of telemetry is already on disk."""

    class _StuckChannel(_FakeChannel):
        async def close(self, grace=None):
            raise RuntimeError("channel is wedged")

    class _StuckSystem(_ClosableSystem):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, **kwargs)
            self.channel = _StuckChannel()
            self._plugins = {"telemetry": type("P", (), {"_stub": _FakeStub(self.channel)})()}

    monkeypatch.setattr(tr, "System", _StuckSystem)

    rec = TelemetryRecorder("udpin://127.0.0.1:14541", tmp_path, rate_hz=50.0)
    await rec.start()
    await rec.stop()  # must not raise
    assert (tmp_path / "telemetry.csv").exists()


def test_the_channel_is_found_where_mavsdk_hides_it():
    """mavsdk keeps no reference to the channel: it lives on the stub's
    multicallables and nowhere else, so a lookup that misses it means the
    channel is never closed at all."""
    channel = _FakeChannel()
    system = type("S", (), {"_plugins": {"telemetry": type("P", (), {"_stub": _FakeStub(channel)})()}})()
    assert tr._grpc_channel(system) is channel

    assert tr._grpc_channel(type("S", (), {})()) is None
    assert tr._grpc_channel(type("S", (), {"_plugins": {}})()) is None
    assert tr._grpc_channel(type("S", (), {"_plugins": {"x": object()}})()) is None


# --- one recorder, one aircraft ---------------------------------------------


def test_a_bind_to_any_telemetry_address_is_refused(tmp_path):
    """``udp://:14540`` accepts telemetry from EVERY autopilot on that port,
    and telemetry.csv has no sysid column in which to say which aircraft a row
    describes. That combination cost 472 contaminated bundles in 2026-08."""
    with pytest.raises(ValueError) as excinfo:
        TelemetryRecorder("udp://:14540", tmp_path)
    assert "binds every source" in str(excinfo.value)
    assert "allow_shared_bind" in str(excinfo.value)


def test_the_shared_bind_escape_hatch_is_explicit(tmp_path):
    """A network known to carry exactly one autopilot can still say so - but it
    has to say so, and the recorder records that it was said."""
    rec = TelemetryRecorder("udp://:14540", tmp_path, allow_shared_bind=True)
    assert rec.shared_bind_allowed is True

    dedicated = TelemetryRecorder("udpin://127.0.0.1:14541", tmp_path)
    assert dedicated.shared_bind_allowed is False


@pytest.mark.parametrize(
    "address, shared",
    [
        ("udp://:14540", True),
        ("udpin://:14540", True),
        ("udp://0.0.0.0:14540", True),
        ("udp://[::]:14540", True),
        ("udpin:14540", True),
        (":14540", True),
        ("udpin://127.0.0.1:14541", False),
        ("udpin:127.0.0.1:14650", False),
        ("udpout://10.0.0.5:14550", False),
        ("tcp://127.0.0.1:5760", False),
    ],
)
def test_which_addresses_can_carry_two_vehicles(address, shared):
    assert tr.is_shared_bind(address) is shared


async def test_the_recorder_counts_impossible_position_jumps(tmp_path):
    """The live interleave detector. A single vehicle at 10 Hz cannot move
    200 m between rows; two vehicles sharing one recorder do it constantly."""
    rec = TelemetryRecorder("udpin://127.0.0.1:14541", tmp_path)
    for fix in [(33.6405, -117.8443), (33.6405, -117.8443), (33.6474901, -117.8426921), (33.6405, -117.8443)]:
        rec._note_position(*fix)
    assert rec.position_flips == 2
    assert rec.worst_position_jump_m > 700

    calm = TelemetryRecorder("udpin://127.0.0.1:14541", tmp_path)
    for fix in [(33.6405, -117.8443), (33.64051, -117.84431), (33.64052, -117.84432)]:
        calm._note_position(*fix)
    assert calm.position_flips == 0
