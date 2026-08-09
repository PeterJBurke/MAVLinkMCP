"""Passive MAVLink wire-tap logger for the flight-data reproducibility package.

This is the capture side of Plan 19 (logging capture specification). It is a
**passive** listener: SITL / mavlink-router is configured to forward a *copy*
of the vehicle<->server MAVLink stream to a spare UDP port, and this recorder
sits on that port and writes everything it hears to disk. It never sends a
byte back onto the link, so it cannot perturb the flight it is documenting.

Output (two files, both in ``out_dir``, both append-only)
---------------------------------------------------------
1. ``mavlink.tlog`` - the raw wire bytes in the standard telemetry-log format
   understood by MAVProxy / mavutil / QGroundControl: each message is written
   as an 8-byte big-endian microsecond timestamp followed by the raw message
   buffer, i.e. ``struct.pack('>Q', int(t_us)) + msg.get_msgbuf()``. Because it
   is the exact bytes off the wire, it is the ground-truth artifact and can be
   replayed with ``mavutil.mavlink_connection('mavlink.tlog')``.

2. ``mavlink.jsonl`` - one decoded JSON object per message, for analysis
   scripts that should not have to speak MAVLink. Schema (one line each)::

       {
         "ts":        "2026-08-08T21:34:59.123456+00:00",  # UTC ISO-8601, wall clock
         "t_rel_s":   12.345,        # seconds since t0 (see clock note below)
         "direction": "recv"|"sent", # heuristic, see below
         "msg_type":  "HEARTBEAT",   # msg.get_type()
         "sysid":     1,             # msg.get_srcSystem()
         "compid":    1,             # msg.get_srcComponent()
         "seq":       42,            # msg.get_seq()
         "fields":    {...}          # msg.to_dict() minus the "mavpackettype" key
       }

Direction heuristic
-------------------
The tap sees a single merged byte stream and cannot observe which socket a
message arrived on, so ``direction`` is inferred from the source system id:

- The autopilot / vehicle is MAVLink sysid ``1`` (ArduPilot / PX4 default).
  Messages it originates are traffic *from* the vehicle *to* our server, and
  are labelled ``"recv"`` (received by the server).
- Everything else - a GCS or our offboard commander, conventionally sysid
  ``255`` or any id ``> 1`` - is labelled ``"sent"`` (server -> vehicle).

This is a heuristic: it is exactly right for the standard one-vehicle +
one-commander topology used in the reproducibility runs, but it will
mis-label traffic in a multi-vehicle swarm or if the autopilot is renumbered.
The vehicle sysid is therefore overridable via the constructor
(``vehicle_sysid``, default ``1``) and exposed as an attribute.

Clock alignment
---------------
``t0`` is a wall-clock epoch (``time.time()``). If not supplied it is captured
at :meth:`start`. To keep ``t_rel_s`` monotonic and immune to wall-clock
stepping (NTP), the tap records the ``time.monotonic()`` reading that
corresponds to ``t0`` and computes ``t_rel_s`` from the monotonic clock while
still stamping ``ts`` and the tlog timestamp from the wall clock. Callers that
run several recorders in parallel can pass a shared ``t0`` (and read back
``self.t0`` / ``self.t0_monotonic``) so every recorder's ``t_rel_s`` shares an
origin.

Messages downstream analysis relies on (Plan 19 s2a)
----------------------------------------------------
The tap records **every** message it hears; this list only documents which
types the reproducibility analysis expects to find.

SENT (server -> vehicle): COMMAND_LONG, COMMAND_INT, SET_MODE, MISSION_COUNT,
    MISSION_ITEM_INT, MISSION_CLEAR_ALL, MISSION_SET_CURRENT, PARAM_SET,
    SET_POSITION_TARGET_GLOBAL_INT, SET_POSITION_TARGET_LOCAL_NED,
    MANUAL_CONTROL, RC_CHANNELS_OVERRIDE, MISSION_REQUEST_LIST,
    LOG_REQUEST_LIST.

RECEIVED (vehicle -> server): HEARTBEAT, COMMAND_ACK, MISSION_ACK,
    MISSION_CURRENT, MISSION_ITEM_REACHED, STATUSTEXT, SYS_STATUS,
    GLOBAL_POSITION_INT, LOCAL_POSITION_NED, ATTITUDE, GPS_RAW_INT, VFR_HUD,
    BATTERY_STATUS, EKF_STATUS_REPORT, HOME_POSITION, NAV_CONTROLLER_OUTPUT,
    VIBRATION, RC_CHANNELS, SERVO_OUTPUT_RAW.

Robustness
----------
The recorder runs in a background thread, reads with a short timeout, and
checks a stop flag each loop. Every file write and every per-message decode is
guarded: a message that fails to serialize is skipped (and counted in
``self.decode_errors``) rather than killing the thread, and the recorder never
raises into the caller's flight path. :meth:`stop` flushes, joins the thread,
and closes both files.
"""

import json
import struct
import threading
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, BinaryIO, TextIO

#: Message types the SENT/RECEIVED analysis (Plan 19 s2a) depends on. Recorded
#: for documentation only - the tap logs every type it hears.
SENT_MSG_TYPES = (
    "COMMAND_LONG",
    "COMMAND_INT",
    "SET_MODE",
    "MISSION_COUNT",
    "MISSION_ITEM_INT",
    "MISSION_CLEAR_ALL",
    "MISSION_SET_CURRENT",
    "PARAM_SET",
    "SET_POSITION_TARGET_GLOBAL_INT",
    "SET_POSITION_TARGET_LOCAL_NED",
    "MANUAL_CONTROL",
    "RC_CHANNELS_OVERRIDE",
    "MISSION_REQUEST_LIST",
    "LOG_REQUEST_LIST",
)
RECEIVED_MSG_TYPES = (
    "HEARTBEAT",
    "COMMAND_ACK",
    "MISSION_ACK",
    "MISSION_CURRENT",
    "MISSION_ITEM_REACHED",
    "STATUSTEXT",
    "SYS_STATUS",
    "GLOBAL_POSITION_INT",
    "LOCAL_POSITION_NED",
    "ATTITUDE",
    "GPS_RAW_INT",
    "VFR_HUD",
    "BATTERY_STATUS",
    "EKF_STATUS_REPORT",
    "HOME_POSITION",
    "NAV_CONTROLLER_OUTPUT",
    "VIBRATION",
    "RC_CHANNELS",
    "SERVO_OUTPUT_RAW",
)

#: MAVLink sysid of the autopilot in the standard reproducibility topology.
DEFAULT_VEHICLE_SYSID = 1

TLOG_NAME = "mavlink.tlog"
JSONL_NAME = "mavlink.jsonl"


@dataclass
class _DecodedRecord:
    """One decoded MAVLink message, ready to serialize to a JSONL line.

    Kept as a pure dataclass built by :func:`decode_message` so the decode /
    serialize path is unit-testable without any socket.
    """

    ts: str
    t_rel_s: float
    direction: str
    msg_type: str
    sysid: int
    compid: int
    seq: int
    fields: dict

    def to_json(self) -> str:
        return json.dumps(
            {
                "ts": self.ts,
                "t_rel_s": self.t_rel_s,
                "direction": self.direction,
                "msg_type": self.msg_type,
                "sysid": self.sysid,
                "compid": self.compid,
                "seq": self.seq,
                "fields": self.fields,
            },
            default=str,
        )


def direction_for(sysid: int, vehicle_sysid: int = DEFAULT_VEHICLE_SYSID) -> str:
    """Map a source system id to a link direction (see module docstring)."""
    return "recv" if sysid == vehicle_sysid else "sent"


def decode_message(
    msg,
    *,
    wall_time: float,
    t0: float,
    t0_monotonic: float,
    mono_time: float | None = None,
    vehicle_sysid: int = DEFAULT_VEHICLE_SYSID,
) -> _DecodedRecord:
    """Decode a pymavlink message object into a :class:`_DecodedRecord`.

    Pure function (no I/O) so the JSONL schema can be tested directly against a
    hand-constructed message. ``t_rel_s`` is computed from the monotonic clock
    when ``mono_time`` is given (the live path), else falls back to the
    wall-clock delta (replay / tests that only have a wall time).

    Args:
        msg: a pymavlink ``MAVLink_message`` (has ``get_type``, ``get_srcSystem``,
            ``get_srcComponent``, ``get_seq``, ``to_dict``).
        wall_time: ``time.time()`` when the message was observed (for ``ts``).
        t0: wall-clock origin for ``t_rel_s``.
        t0_monotonic: ``time.monotonic()`` reading that corresponds to ``t0``.
        mono_time: ``time.monotonic()`` when the message was observed.
        vehicle_sysid: sysid treated as the vehicle for the direction heuristic.
    """
    if mono_time is not None:
        t_rel = mono_time - t0_monotonic
    else:
        t_rel = wall_time - t0

    fields = dict(msg.to_dict())
    fields.pop("mavpackettype", None)

    return _DecodedRecord(
        ts=datetime.fromtimestamp(wall_time, timezone.utc).isoformat(),
        t_rel_s=round(t_rel, 6),
        direction=direction_for(msg.get_srcSystem(), vehicle_sysid),
        msg_type=msg.get_type(),
        sysid=msg.get_srcSystem(),
        compid=msg.get_srcComponent(),
        seq=msg.get_seq(),
        fields=fields,
    )


def tlog_frame(msg, wall_time: float) -> bytes:
    """Return the standard tlog frame for ``msg``: 8-byte big-endian
    microsecond timestamp prefix + the raw message buffer."""
    return struct.pack(">Q", int(wall_time * 1_000_000)) + msg.get_msgbuf()


class MavlinkTap:
    """Passive MAVLink wire-tap recorder. See the module docstring for the
    full behaviour, file schemas, and the direction heuristic.

    Typical use (composed with the benchmark harness, which owns ``out_dir``)::

        tap = MavlinkTap("udpin:127.0.0.1:14650", trial_dir, t0=shared_t0)
        tap.start()
        ...  # fly the mission
        tap.stop()
    """

    #: How long each blocking read waits before the loop re-checks the stop
    #: flag. Short enough that :meth:`stop` returns promptly.
    READ_TIMEOUT_S = 0.2

    def __init__(
        self,
        endpoint: str,
        out_dir: Path,
        t0: float | None = None,
        *,
        vehicle_sysid: int = DEFAULT_VEHICLE_SYSID,
    ):
        self.endpoint = endpoint
        self.out_dir = Path(out_dir)
        self.vehicle_sysid = vehicle_sysid

        # Clock origin. If a shared t0 is provided we still need the monotonic
        # reading that lines up with it; approximate it from "now" on both
        # clocks. When t0 is None it is finalized in start().
        self.t0: float | None = t0
        self.t0_monotonic: float | None = None

        self.tlog_path = self.out_dir / TLOG_NAME
        self.jsonl_path = self.out_dir / JSONL_NAME

        # pymavlink is imported lazily in start(), so the connection has no
        # importable static type here; the file handles do.
        self._conn: Any = None
        self._tlog_fh: BinaryIO | None = None
        self._jsonl_fh: TextIO | None = None
        self._thread: threading.Thread | None = None
        self._stop = threading.Event()
        self._lock = threading.Lock()

        #: Observability counters (read after stop() in tests / summaries).
        self.message_count = 0
        self.decode_errors = 0
        self.write_errors = 0

    # -- lifecycle ---------------------------------------------------------

    def start(self) -> None:
        """Open the connection + files and begin recording in a background
        thread. Idempotent-safe: a second call while running is a no-op."""
        if self._thread is not None and self._thread.is_alive():
            return

        from pymavlink import mavutil

        now_wall = time.time()
        now_mono = time.monotonic()
        if self.t0 is None:
            self.t0 = now_wall
            self.t0_monotonic = now_mono
        else:
            # Anchor the caller-supplied wall-clock t0 onto our monotonic clock
            # so t_rel_s stays consistent with it.
            self.t0_monotonic = now_mono - (now_wall - self.t0)

        self.out_dir.mkdir(parents=True, exist_ok=True)
        # Binary append for the tlog (raw bytes), text append for the JSONL.
        self._tlog_fh = open(self.tlog_path, "ab")
        self._jsonl_fh = open(self.jsonl_path, "a", encoding="utf-8")

        # Passive listener: mavutil opens the udpin/udpout socket for us.
        self._conn = mavutil.mavlink_connection(self.endpoint)

        self._stop.clear()
        self._thread = threading.Thread(target=self._run, name="mavlink-tap", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        """Signal the thread to stop, join it, flush and close both files and
        the connection. Safe to call more than once."""
        self._stop.set()
        thread = self._thread
        if thread is not None:
            thread.join(timeout=5.0)
        self._thread = None

        with self._lock:
            for closer in (self._flush_files,):
                closer()
            for fh in (self._tlog_fh, self._jsonl_fh):
                if fh is not None:
                    try:
                        fh.close()
                    except OSError:
                        pass
            self._tlog_fh = None
            self._jsonl_fh = None
        if self._conn is not None:
            try:
                self._conn.close()
            except Exception:
                pass
            self._conn = None

    # -- internals ---------------------------------------------------------

    def _run(self) -> None:
        """Read loop. Never raises: a bad message is skipped, and any
        unexpected error is swallowed so the caller's flight is never
        disturbed by the recorder."""
        conn = self._conn
        if conn is None:  # start() failed to open one; nothing to read
            return
        while not self._stop.is_set():
            try:
                msg = conn.recv_match(blocking=True, timeout=self.READ_TIMEOUT_S)
            except Exception:
                # Socket hiccup / decode error inside pymavlink: back off a
                # touch and keep listening.
                time.sleep(0.01)
                continue
            if msg is None:
                continue
            if msg.get_type() == "BAD_DATA":
                continue
            self._record(msg)

    def _record(self, msg) -> None:
        wall = time.time()
        mono = time.monotonic()
        with self._lock:
            # tlog: raw bytes, ground truth. Written first and independently of
            # the JSON decode so a decode failure never costs us the wire copy.
            try:
                if self._tlog_fh is not None:
                    self._tlog_fh.write(tlog_frame(msg, wall))
            except (OSError, ValueError):
                self.write_errors += 1

            # start() always sets both, but a caller that reached _record
            # without it must still produce a usable row rather than a
            # TypeError inside the recorder: t_rel then starts from this
            # message, which is the only origin available.
            t0 = self.t0 if self.t0 is not None else wall
            t0_monotonic = self.t0_monotonic if self.t0_monotonic is not None else mono
            try:
                record = decode_message(
                    msg,
                    wall_time=wall,
                    t0=t0,
                    t0_monotonic=t0_monotonic,
                    mono_time=mono,
                    vehicle_sysid=self.vehicle_sysid,
                )
                line = record.to_json()
            except Exception:
                self.decode_errors += 1
                return

            try:
                if self._jsonl_fh is not None:
                    self._jsonl_fh.write(line + "\n")
            except (OSError, ValueError):
                self.write_errors += 1
                return

            self.message_count += 1

    def _flush_files(self) -> None:
        for fh in (self._tlog_fh, self._jsonl_fh):
            if fh is not None:
                try:
                    fh.flush()
                except (OSError, ValueError):
                    pass

    # -- context manager sugar --------------------------------------------

    def __enter__(self) -> "MavlinkTap":
        self.start()
        return self

    def __exit__(self, *exc) -> None:
        self.stop()
