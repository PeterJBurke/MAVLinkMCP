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

from droneserver.capture import telemetry_recorder as tr
from droneserver.capture.telemetry_recorder import COLUMNS, TelemetryRecorder

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

    rec = TelemetryRecorder("udp://:14540", tmp_path, rate_hz=50.0, t0=1000.0)
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

    # Columns MavSDK cannot provide are empty on every data row.
    for name in ("hdop", "vdop", "ekf_ok", "geofence_ok"):
        for r in rows[1:]:
            assert r[col[name]] == ""


async def test_connect_failure_never_crashes_and_still_writes_header(tmp_path, monkeypatch):
    """A failed connect must not raise; the CSV still gets its header + rows."""
    monkeypatch.setattr(tr, "System", _ExplodingSystem)

    rec = TelemetryRecorder("udp://:14540", tmp_path, rate_hz=50.0)
    await rec.start()
    await asyncio.sleep(0.05)
    await rec.stop()

    rows = _read_csv(tmp_path / "telemetry.csv")
    assert rows[0] == COLUMNS
    # Timer still produced uniform rows even though no telemetry arrived.
    assert len(rows) >= 2


async def test_stop_is_idempotent(tmp_path, monkeypatch):
    monkeypatch.setattr(tr, "System", _FakeSystem)
    rec = TelemetryRecorder("udp://:14540", tmp_path)
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
