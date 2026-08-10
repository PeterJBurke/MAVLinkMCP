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
    geofence_ok, sample_age_s

Sample-and-hold, and how to tell it apart from flight
-----------------------------------------------------
Because the timer writes a row whether or not anything arrived, a link that
dies mid-flight does not leave a gap: it leaves rows, at the full rate, all the
way to the end of the trial, every one repeating the last position, mode and
armed state the recorder ever saw. A track drawn from that file shows an
aircraft holding station perfectly - which is a claim about the vehicle, made
by a recorder that had stopped listening.

``sample_age_s`` is the seconds since the freshest item on ANY topic arrived,
recorded on every row. Fresh telemetry keeps it near zero; a dead link makes it
climb without bound, so a stale hold is visible in the artifact itself instead
of being indistinguishable from a hover. It is empty until the first sample
ever arrives (nothing to be stale yet).

Two sources, one row (the ``raw_source``)
-----------------------------------------
Most columns are MavSDK values. Four of the columns Plan 19 §3b requires are
**not in the MavSDK telemetry plugin at all**, and for the whole life of this
recorder they were written empty on every row of every trial - a data
dictionary describing columns the data never carried. They are, however, on the
MAVLink wire that the :class:`~droneserver.capture.mavlink_tap.MavlinkTap` is
already recording a few feet away, so the tap is passed in as ``raw_source``
and each row reads its latest decoded messages:

===============  ==========================  ===================================
column           MAVLink source              meaning
===============  ==========================  ===================================
``hdop``         ``GPS_RAW_INT.eph`` / 100   GPS horizontal dilution of precision
``vdop``         ``GPS_RAW_INT.epv`` / 100   GPS vertical dilution of precision
``ekf_ok``       ``SYS_STATUS`` AHRS bit     the autopilot's own AHRS/EKF health
``geofence_ok``  ``SYS_STATUS`` GEOFENCE bit the autopilot's own fence health
``throttle_pct`` ``VFR_HUD.throttle``        throttle, 0-100 %
===============  ==========================  ===================================

``ekf_ok`` / ``geofence_ok`` are the autopilot's *own* health bits, not a
predicate this module invents from estimator variances: ``SYS_STATUS`` carries
a present/enabled/health triple per subsystem, and these columns report the
health bit **only when the autopilot says the subsystem is present**, and stay
empty otherwise. That distinction matters - a firmware that does not report a
geofence at all must leave a blank, not a confident ``False``.

``throttle_pct`` is taken from the wire because MavSDK's
``fixedwing_metrics.throttle_percentage`` is a **0-1 fraction** despite its
name (measured: 0.49 while ``VFR_HUD.throttle`` said 49), so every bundle
captured before this recorded a throttle a hundred times too small, rounded to
one decimal - 0.5 for a hovering copter. The same 100x confusion as
``battery_pct`` (see :func:`_battery_percent`), through a different door. When
no ``raw_source`` is available the MavSDK value is scaled by the same
fraction-or-percentage rule rather than trusted blind.

Without a ``raw_source`` (the tap failed to start, or a caller that has none)
those columns fall back to empty, exactly as before - the recorder is fail-soft
everywhere. :func:`droneserver.capture.verify.verify_bundle` is what notices,
and reports the trial degraded.

Columns that can still legitimately be empty, documented so downstream analysis
does not mistake them for lost data:

- ``airspeed_ms`` - from MavSDK ``fixedwing_metrics``. ArduCopter *does* publish
  it, but with no airspeed sensor the value is a synthesised estimate, not a
  measurement; on a vehicle that publishes no such topic it is empty.
- any column whose topic has not delivered its first sample yet - the first
  second or so of a trial, before MavSDK's subscriptions warm up.
"""

import asyncio
import csv
import inspect
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
    "sample_age_s",
]

#: Columns MavSDK's telemetry plugin cannot fill. They are read from the
#: MAVLink wire instead (see the module docstring, ``raw_source``); without a
#: raw source they are empty, and verify_bundle reports the trial degraded.
WIRE_SOURCED_COLUMNS = ("hdop", "vdop", "ekf_ok", "geofence_ok")

#: MAVLink message types :meth:`TelemetryRecorder._build_row` reads out of the
#: ``raw_source`` snapshot. Kept in step with
#: :data:`droneserver.capture.mavlink_tap.SNAPSHOT_MSG_TYPES`.
RAW_SOURCE_MSG_TYPES = ("GPS_RAW_INT", "SYS_STATUS", "VFR_HUD")

#: ``GPS_RAW_INT.eph``/``epv`` are UINT16 hundredths of a DOP unit, with
#: UINT16_MAX meaning "unknown". Anything at or above it is not a measurement.
_DOP_UNKNOWN = 65535

#: MAV_SYS_STATUS_SENSOR bits. The autopilot reports a present / enabled /
#: health triple against each; we read health, gated on present.
_SYS_STATUS_GEOFENCE = 0x00100000  # MAV_SYS_STATUS_GEOFENCE
_SYS_STATUS_AHRS = 0x00200000  # MAV_SYS_STATUS_AHRS (the EKF / attitude estimator)

#: Grace given to the gRPC channel to finish in-flight calls on close.
CHANNEL_CLOSE_GRACE_S = 2.0

#: Ceiling on the whole MavSDK teardown. It runs inside ``stop()``, which runs
#: in the trial's ``finally``, so it must be bounded however badly gRPC is
#: behaving.
SYSTEM_CLOSE_TIMEOUT_S = 15.0

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


def _battery_percent(reported):
    """MavSDK's ``remaining_percent`` as an actual percentage, 0-100.

    The field is *documented* as a 0.0-1.0 fraction and this recorder duly
    multiplied it by 100. ArduCopter through mavsdk 3.0.1 reports a PERCENTAGE
    (measured: 77.0 for a 77% battery - see
    ``droneserver.missions.runner._battery_fraction``, which has normalised it
    for the auto-actions since before this recorder existed), so every
    ``battery_pct`` in every captured bundle came out a hundred times too big:
    the canonical T10 run records 4100.0 to 7000.0 for a battery that went from
    70% to 41%.

    A reading above 1.0 can only be a percentage; below it, take the documented
    fraction. That mis-reads a genuinely 0.8%-charged battery as 80%, which is
    the same trade the mission runner already makes and is not a state any
    trial flies in.
    """
    if reported is None:
        return None
    try:
        value = float(reported)
    except (TypeError, ValueError):
        return None
    if value < 0 or math.isnan(value) or math.isinf(value):
        return None
    return value if value > 1.0 else value * 100.0


def _dop(gps_raw: dict | None, field: str):
    """``GPS_RAW_INT.eph``/``epv`` as a dilution-of-precision number.

    The wire value is hundredths of a DOP unit (121 -> HDOP 1.21) with
    UINT16_MAX for "the receiver did not report one". Returns "" for absent,
    unparsable or unknown, so a blank cell always means *not measured* and
    never means zero - a HDOP of 0.0 would read as a perfect fix.
    """
    if not gps_raw:
        return ""
    try:
        value = int(gps_raw.get(field))  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return ""
    if value < 0 or value >= _DOP_UNKNOWN:
        return ""
    return round(value / 100.0, 2)


def _sensor_health(sys_status: dict | None, bit: int):
    """One subsystem's health, as the autopilot itself reports it.

    ``SYS_STATUS`` carries three bitmasks - present, enabled, health - over the
    MAV_SYS_STATUS_SENSOR bits. This returns the health bit **only when the
    autopilot declares the subsystem present**; otherwise "" (unknown), because
    a firmware that does not report a geofence at all has not told us the fence
    is unhealthy. Note "present but not enabled" still yields a health reading:
    that is ArduPilot's normal state for a fence that is configured but off,
    and the health bit is meaningful there.
    """
    if not sys_status:
        return ""
    try:
        present = int(sys_status.get("onboard_control_sensors_present") or 0)
        health = int(sys_status.get("onboard_control_sensors_health") or 0)
    except (TypeError, ValueError):
        return ""
    if not present & bit:
        return ""
    return bool(health & bit)


def _throttle_percent(vfr_hud: dict | None, mavsdk_value):
    """Throttle as an actual percentage, 0-100.

    Prefers ``VFR_HUD.throttle`` off the wire, which ArduPilot and PX4 both
    send as an integer percentage. MavSDK's ``fixedwing_metrics``
    ``throttle_percentage`` is - despite the name and the documentation - a 0-1
    fraction: measured 0.49 on the same tick that ``VFR_HUD`` reported 49. So
    the fallback applies the same fraction-or-percentage rule as
    :func:`_battery_percent`, which mis-reads a genuine 0.8% throttle as 80% -
    a state no trial flies in, and far better than recording 40% hover as 0.4.
    """
    if vfr_hud:
        try:
            wire = float(vfr_hud.get("throttle"))  # type: ignore[arg-type]
        except (TypeError, ValueError):
            wire = None
        if wire is not None and not (math.isnan(wire) or math.isinf(wire)) and wire >= 0:
            return wire
    if mavsdk_value is None:
        return None
    try:
        value = float(mavsdk_value)
    except (TypeError, ValueError):
        return None
    if value < 0 or math.isnan(value) or math.isinf(value):
        return None
    return value if value > 1.0 else value * 100.0


async def _close_channel(channel) -> None:
    """``await channel.close(grace)``, tolerating older/synchronous channels."""
    try:
        result = channel.close(grace=CHANNEL_CLOSE_GRACE_S)
    except TypeError:  # a channel whose close() takes no grace argument
        result = channel.close()
    if inspect.isawaitable(result):
        await result


def _grpc_channel(system):
    """The gRPC channel a mavsdk ``System`` talks to its backend over.

    mavsdk keeps no reference to it. ``AsyncPluginManager`` is a local variable
    inside ``System._init_plugins``, and each plugin stores only the generated
    stub built from ``manager.channel``; the stub's multicallables are the last
    objects holding the channel (``grpc.aio`` stores it on each as
    ``_channel``). Since nothing else can reach it, a channel that is never
    closed here is never closed at all - see :meth:`TelemetryRecorder._close_system`.

    Deliberately tolerant of every mavsdk/grpc version: anything unexpected
    yields ``None`` and the caller simply skips the close.
    """
    plugins = getattr(system, "_plugins", None)
    if not isinstance(plugins, dict):
        return None
    for plugin in plugins.values():
        stub = getattr(plugin, "_stub", None)
        if stub is None:
            continue
        try:
            members = vars(stub).values()
        except TypeError:  # a stub without a __dict__
            continue
        for member in members:
            channel = getattr(member, "_channel", None)
            if channel is not None and callable(getattr(channel, "close", None)):
                return channel
    return None


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
        raw_source: Any = None,
    ):
        self.system_address = system_address
        #: Anything with a ``snapshot() -> {msg_type: fields}`` method - in
        #: practice the trial's :class:`~droneserver.capture.mavlink_tap.MavlinkTap`.
        #: Supplies the columns MavSDK does not expose (see module docstring).
        #: ``None`` leaves those columns empty rather than guessing.
        self.raw_source = raw_source
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
        #: ``time.monotonic()`` when ANY topic last yielded an item. None until
        #: the first one does. This is what tells a held sample from a live one.
        self._last_sample_mono: float | None = None
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
        """Shut down in the one order that leaves nothing behind.

        1. **Cancel the subscriber and sampler tasks** and await them, so no
           task is still iterating a MavSDK stream.
        2. **Flush and close the CSV** - the artifact is safe before anything
           that can hang is attempted.
        3. **Close the gRPC channel and stop the mavsdk_server** behind it
           (:meth:`_close_system`).

        Step 3 used to be missing entirely: the channel and the server
        subprocess were left to garbage collection, so every trial leaked a
        mavsdk_server process, its stdout-logging thread and an open channel,
        and the gRPC completion-queue poller went on posting callbacks into an
        event loop the harness had already closed - 22 ``RuntimeError: Event
        loop is closed`` tracebacks over an eight-mission flight. The other
        half of that fix is on the caller's side, in
        :class:`droneserver.benchmark.capture_session._AsyncLoopThread`.
        """
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

        await self._close_system()

    async def _close_system(self) -> None:
        """Close the MavSDK gRPC channel and reap the mavsdk_server subprocess.

        mavsdk's ``System`` has no public shutdown. It exposes
        ``_stop_mavsdk_server()``, which kills the subprocess it spawned, and
        its ``__del__`` calls that - so the process does eventually die, at an
        unpredictable moment, and only once the interpreter gets round to
        collecting the object. Two things are wrong with leaving it there:
        ``Popen.kill()`` without a ``wait()`` leaves a zombie, and the gRPC
        channel is not touched at all.

        So this: close the channel (found via :func:`_grpc_channel`, since
        mavsdk keeps no reference), kill the server, and **reap it**, so the
        port is free and the process table is clean before the next trial
        starts its own. Bounded by :data:`SYSTEM_CLOSE_TIMEOUT_S` and silent on
        failure - a recorder must never be the reason a trial dies, and by this
        point every byte of telemetry is already on disk.
        """
        system, self._system = self._system, None
        if system is None:
            return

        channel = _grpc_channel(system)
        if channel is not None:
            try:
                await asyncio.wait_for(_close_channel(channel), timeout=SYSTEM_CLOSE_TIMEOUT_S)
            except Exception:  # noqa: BLE001 - teardown is best-effort by design
                pass

        # Grab the handle before _stop_mavsdk_server(), which re-runs __init__
        # on the System and drops it.
        process = getattr(system, "_server_process", None)
        stop_server = getattr(system, "_stop_mavsdk_server", None)
        if callable(stop_server):
            try:
                stop_server()
            except Exception:  # noqa: BLE001
                pass
        if process is not None and hasattr(process, "wait"):
            try:
                process.wait(timeout=SYSTEM_CLOSE_TIMEOUT_S)
            except Exception:  # noqa: BLE001 - already killed; never block a trial
                pass

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
                self._last_sample_mono = time.monotonic()
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

    def _raw_snapshot(self) -> dict:
        """Latest wire messages from the ``raw_source``; ``{}`` if it has none.

        Never raises: a tap that has died, or was never given, simply leaves
        the wire-sourced columns empty for this row.
        """
        source = self.raw_source
        if source is None:
            return {}
        try:
            snapshot = source.snapshot()
        except Exception:  # noqa: BLE001 - a row must always be written
            return {}
        return snapshot if isinstance(snapshot, dict) else {}

    def _build_row(self) -> list:
        latest = self._latest
        raw = self._raw_snapshot()
        gps_raw = raw.get("GPS_RAW_INT")
        sys_status = raw.get("SYS_STATUS")
        vfr_hud = raw.get("VFR_HUD")
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

        battery_pct = _battery_percent(_get(batt, "remaining_percent"))
        throttle_pct = _throttle_percent(vfr_hud, _get(fw, "throttle_percentage"))

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
            # Not in the MavSDK plugin: read off the wire via the tap.
            "hdop": _dop(gps_raw, "eph"),
            "vdop": _dop(gps_raw, "epv"),
            "battery_v": _round(_get(batt, "voltage_v"), 3),
            "battery_pct": _round(battery_pct, 1),
            "throttle_pct": _round(throttle_pct, 1),
            "in_air": "" if in_air is None else bool(in_air),
            "home_lat": _round(_get(home, "latitude_deg"), 7),
            "home_lon": _round(_get(home, "longitude_deg"), 7),
            "home_alt": _round(_get(home, "absolute_altitude_m"), 3),
            # The autopilot's own health bits, gated on it declaring the
            # subsystem present (see _sensor_health).
            "ekf_ok": _sensor_health(sys_status, _SYS_STATUS_AHRS),
            "geofence_ok": _sensor_health(sys_status, _SYS_STATUS_GEOFENCE),
            # How old the newest value in this row is. Every other column is
            # sample-and-hold, so without this a dead link is indistinguishable
            # from a stationary aircraft.
            "sample_age_s": (
                "" if self._last_sample_mono is None else round(time.monotonic() - self._last_sample_mono, 3)
            ),
        }
        return [values[col] for col in COLUMNS]
