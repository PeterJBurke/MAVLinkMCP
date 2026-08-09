"""MavSDK telemetry recorder - the drone's own state as a uniform time series.

Plan 19 capture spec (3b). While a trial runs, this recorder subscribes to the
autopilot's MavSDK telemetry streams and, on a fixed-rate timer, writes ONE row
per tick to ``telemetry.csv``. It is a *sample-and-hold* recorder: each stream
runs in its own background task and updates the "latest value" for its topic;
the timer thread then snapshots whatever the latest values are and emits a row.
A stream that stalls, errors, or a topic the autopilot never publishes simply
leaves its columns at their last-known value (or empty), and the recorder keeps
producing evenly-spaced rows regardless. Nothing here is ever allowed to crash
the caller / the flight-control loop.

The row clock is aligned to a shared ``t0`` (seconds since the Unix epoch, from
``time.time()``) so this CSV, the raw-MAVLink tap, the LLM transcript, and the
JSONL audit log can all be laid on one timeline. ``t_iso`` is the UTC wall-clock
ISO-8601 stamp for each row; ``t_rel_s`` is ``time.time() - t0``.

Column schema (STABLE - fixed order, see ``COLUMNS``)::

    t_iso, t_rel_s, lat_deg, lon_deg, abs_alt_m, rel_alt_m, flight_mode, armed,
    roll_deg, pitch_deg, yaw_deg, vn_ms, ve_ms, vd_ms, groundspeed_ms,
    airspeed_ms, gps_fix_type, num_satellites, hdop, vdop, battery_v,
    battery_pct, throttle_pct, in_air, home_lat, home_lon, home_alt, ekf_ok,
    geofence_ok

Columns MavSDK does NOT expose are written empty every row and documented here
so downstream analysis does not mistake them for missing data:

- ``hdop`` / ``vdop`` - MavSDK ``gps_info`` reports only ``num_satellites`` and
  ``fix_type``; the horizontal/vertical dilution-of-precision values are not in
  the plugin. (They live in the raw ``GPS_RAW_INT`` MAVLink message, captured by
  the separate MAVLink tap.)
- ``throttle_pct`` - only published on fixed-wing via ``fixedwing_metrics``;
  empty on a multirotor that does not emit that topic.
- ``airspeed_ms`` - likewise from ``fixedwing_metrics``; empty when absent.
- ``ekf_ok`` - MavSDK's ``health`` stream exposes calibration/position-lock
  flags but no single EKF-status boolean, so this is left empty here (the raw
  ``EKF_STATUS_REPORT`` is in the MAVLink tap).
- ``geofence_ok`` - not surfaced by the MavSDK telemetry plugin; left empty.

Every other column is a real MavSDK value.
"""

import asyncio
import csv
import math
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, TextIO

# mavsdk is a hard runtime dependency (see pyproject) but is imported lazily-
# tolerantly so that unit tests can run without the (heavy, native-gRPC) package
# installed: the test monkeypatches ``System`` on this module. The name is
# looked up as a module global at connect time, so patching it works.
try:  # pragma: no cover - trivial import guard
    from mavsdk import System
except ImportError:  # pragma: no cover - exercised only where mavsdk is absent
    System = None  # type: ignore[assignment,misc]

#: The exact CSV column order. Importable so the manifest / analysis code and
#: the tests share one source of truth. Do not reorder or rename (STABLE).
COLUMNS = [
    "t_iso",
    "t_rel_s",
    "lat_deg",
    "lon_deg",
    "abs_alt_m",
    "rel_alt_m",
    "flight_mode",
    "armed",
    "roll_deg",
    "pitch_deg",
    "yaw_deg",
    "vn_ms",
    "ve_ms",
    "vd_ms",
    "groundspeed_ms",
    "airspeed_ms",
    "gps_fix_type",
    "num_satellites",
    "hdop",
    "vdop",
    "battery_v",
    "battery_pct",
    "throttle_pct",
    "in_air",
    "home_lat",
    "home_lon",
    "home_alt",
    "ekf_ok",
    "geofence_ok",
]

#: Columns MavSDK's telemetry plugin cannot fill; written empty every row.
UNAVAILABLE_COLUMNS = ("hdop", "vdop", "ekf_ok", "geofence_ok")

# (topic key, telemetry-stream method name). Each stream runs in its own task
# and stores its most-recent item under the topic key in ``self._latest``.
_STREAMS = (
    ("position", "position"),
    ("attitude", "attitude_euler"),
    ("velocity", "velocity_ned"),
    ("gps", "gps_info"),
    ("battery", "battery"),
    ("home", "home"),
    ("fixedwing", "fixedwing_metrics"),
    ("flight_mode", "flight_mode"),
    ("armed", "armed"),
    ("in_air", "in_air"),
)


def _round(value, ndigits):
    """Round a float for CSV, mapping None/NaN/non-numeric to "" (empty cell)."""
    if value is None:
        return ""
    try:
        f = float(value)
    except (TypeError, ValueError):
        return ""
    if math.isnan(f) or math.isinf(f):
        return ""
    return round(f, ndigits)


def _get(obj, attr):
    """Return ``obj.attr`` or None if ``obj`` is None / lacks the attribute."""
    return getattr(obj, attr, None) if obj is not None else None


class TelemetryRecorder:
    """Fixed-rate CSV recorder of the drone's MavSDK telemetry state.

    Usage::

        rec = TelemetryRecorder("udp://:14540", out_dir, rate_hz=10.0)
        await rec.start()   # connect + spawn subscribers + timer
        ...                 # trial runs
        await rec.stop()    # cancel tasks, flush + close telemetry.csv

    The recorder never raises out of its background tasks: a failing stream is
    logged-by-omission (its columns hold their last value or stay empty) and the
    timer keeps writing evenly-spaced rows.
    """

    def __init__(
        self,
        system_address: str,
        out_dir: Path,
        rate_hz: float = 10.0,
        t0: float | None = None,
    ):
        self.system_address = system_address
        self.out_dir = Path(out_dir)
        self.path = self.out_dir / "telemetry.csv"
        # Timer rate; the spec calls for >= 10 Hz. Guard against non-positive.
        self.rate_hz = float(rate_hz) if float(rate_hz) > 0 else 10.0
        #: Shared clock origin (Unix seconds) for alignment with the other logs.
        self.t0 = time.time() if t0 is None else float(t0)

        # mavsdk types are only present when mavsdk is installed (it is
        # monkeypatched out in the unit tests), and the per-topic objects it
        # yields are all different shapes, so both of these are deliberately
        # dynamic rather than dishonestly narrow.
        self._system: Any = None
        self._latest: dict[str, Any] = {key: None for key, _ in _STREAMS}
        self._tasks: list[asyncio.Task] = []
        self._sampler: asyncio.Task | None = None
        self._file: TextIO | None = None
        self._writer: Any = None  # csv.writer's return type is not public
        self._started = False
        self._stopped = False

    # -- lifecycle ---------------------------------------------------------

    async def start(self) -> None:
        """Connect to the drone, open the CSV, and spawn the background tasks."""
        if self._started:
            return
        self._started = True
        if self.t0 is None:
            self.t0 = time.time()

        # Open the CSV and write the header before any data can arrive.
        self.out_dir.mkdir(parents=True, exist_ok=True)
        self._file = open(self.path, "w", newline="", encoding="utf-8")
        self._writer = csv.writer(self._file)
        self._writer.writerow(COLUMNS)
        self._file.flush()

        # Construct and connect the MavSDK System. Looked up as a module global
        # so tests can monkeypatch ``System``.
        if System is None:  # pragma: no cover - only where mavsdk is absent
            raise RuntimeError("mavsdk is not installed; cannot start TelemetryRecorder")
        self._system = System()
        try:
            await self._system.connect(system_address=self.system_address)
        except Exception:
            # A failed connect must not crash the caller; subscribers will
            # simply never receive data and rows stay empty. The timer still
            # runs so the trial has an (empty) uniform time base.
            pass

        # One subscriber task per telemetry topic.
        for key, method_name in _STREAMS:
            self._tasks.append(asyncio.ensure_future(self._subscribe(key, method_name)))
        # The fixed-rate row writer.
        self._sampler = asyncio.ensure_future(self._sampling_loop())

    async def stop(self) -> None:
        """Cancel all tasks, flush the final buffered data, and close the CSV."""
        if self._stopped:
            return
        self._stopped = True

        tasks = list(self._tasks)
        if self._sampler is not None:
            tasks.append(self._sampler)
        for task in tasks:
            task.cancel()
        for task in tasks:
            try:
                await task
            except (asyncio.CancelledError, Exception):
                # Swallow the cancellation (and any straggling error) so stop()
                # is always clean.
                pass
        self._tasks.clear()
        self._sampler = None

        if self._file is not None:
            try:
                self._file.flush()
                self._file.close()
            except OSError:
                pass
            self._file = None
            self._writer = None

    # -- background tasks --------------------------------------------------

    async def _subscribe(self, key: str, method_name: str) -> None:
        """Consume one telemetry stream, sample-and-holding its latest item.

        Robust by design: an error while opening or iterating the stream ends
        this one subscriber (leaving its columns at their last value); it never
        propagates. Cancellation on stop() is re-raised so the task exits.
        """
        try:
            stream_factory = getattr(self._system.telemetry, method_name)
            async for item in stream_factory():
                self._latest[key] = item
        except asyncio.CancelledError:
            raise
        except Exception:
            # Topic unsupported / stream broke: stop updating this topic, keep
            # the last-known value, and let the rest of the recorder run on.
            return

    async def _sampling_loop(self) -> None:
        """Write one CSV row every ``1 / rate_hz`` seconds until cancelled."""
        period = 1.0 / self.rate_hz
        try:
            while True:
                self._write_row()
                await asyncio.sleep(period)
        except asyncio.CancelledError:
            # Emit one final row so the last state is captured, then exit.
            self._write_row()
            raise

    # -- row assembly ------------------------------------------------------

    def _write_row(self) -> None:
        """Snapshot the latest values and append one row. Never raises."""
        if self._writer is None or self._file is None:
            return
        try:
            row = self._build_row()
            self._writer.writerow(row)
            self._file.flush()
        except Exception:
            # A serialisation / IO hiccup must not kill the timer or the caller.
            return

    def _build_row(self) -> list:
        latest = self._latest
        pos = latest.get("position")
        att = latest.get("attitude")
        vel = latest.get("velocity")
        gps = latest.get("gps")
        batt = latest.get("battery")
        home = latest.get("home")
        fw = latest.get("fixedwing")
        mode = latest.get("flight_mode")
        armed = latest.get("armed")
        in_air = latest.get("in_air")

        # groundspeed from the horizontal NED components.
        vn = _get(vel, "north_m_s")
        ve = _get(vel, "east_m_s")
        vd = _get(vel, "down_m_s")
        if vn is not None and ve is not None:
            groundspeed = math.sqrt(vn * vn + ve * ve)
        else:
            groundspeed = None

        # battery remaining_percent is 0.0-1.0 in MavSDK -> percent.
        batt_remaining = _get(batt, "remaining_percent")
        battery_pct = None if batt_remaining is None else batt_remaining * 100.0

        values = {
            "t_iso": datetime.now(timezone.utc).isoformat(),
            "t_rel_s": round(time.time() - self.t0, 3),
            "lat_deg": _round(_get(pos, "latitude_deg"), 7),
            "lon_deg": _round(_get(pos, "longitude_deg"), 7),
            "abs_alt_m": _round(_get(pos, "absolute_altitude_m"), 3),
            "rel_alt_m": _round(_get(pos, "relative_altitude_m"), 3),
            "flight_mode": "" if mode is None else str(mode),
            "armed": "" if armed is None else bool(armed),
            "roll_deg": _round(_get(att, "roll_deg"), 3),
            "pitch_deg": _round(_get(att, "pitch_deg"), 3),
            "yaw_deg": _round(_get(att, "yaw_deg"), 3),
            "vn_ms": _round(vn, 3),
            "ve_ms": _round(ve, 3),
            "vd_ms": _round(vd, 3),
            "groundspeed_ms": _round(groundspeed, 3),
            "airspeed_ms": _round(_get(fw, "airspeed_m_s"), 3),
            "gps_fix_type": "" if gps is None else str(_get(gps, "fix_type")),
            "num_satellites": "" if (sats := _get(gps, "num_satellites")) is None else int(sats),
            "hdop": "",  # not exposed by MavSDK gps_info
            "vdop": "",  # not exposed by MavSDK gps_info
            "battery_v": _round(_get(batt, "voltage_v"), 3),
            "battery_pct": _round(battery_pct, 1),
            "throttle_pct": _round(_get(fw, "throttle_percentage"), 1),
            "in_air": "" if in_air is None else bool(in_air),
            "home_lat": _round(_get(home, "latitude_deg"), 7),
            "home_lon": _round(_get(home, "longitude_deg"), 7),
            "home_alt": _round(_get(home, "absolute_altitude_m"), 3),
            "ekf_ok": "",  # no single EKF-status flag in MavSDK
            "geofence_ok": "",  # not surfaced by MavSDK telemetry
        }
        return [values[col] for col in COLUMNS]
