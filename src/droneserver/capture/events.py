"""Post-hoc event deriver for the reproducibility appendix narrative.

Distils the safety/flight-relevant *events* of a single trial into one
append-only ``events.jsonl`` file, so the appendix (Plan 19 capture spec §4)
can tell the story of a run - "the model asked to take off, the guard rejected
an over-altitude command, the vehicle armed, entered AUTO, reached waypoint 3,
a geofence warning fired, then it landed" - without a human trawling three raw
logs.

This is a *best-effort, read-only* distiller. It reads whatever of the three
per-trial inputs exist in ``out_dir`` and skips any that are missing. Nothing
is required; an empty ``out_dir`` yields an empty (but valid) ``events.jsonl``.

Inputs (all optional, read from ``out_dir``)
--------------------------------------------
``audit_slice.csv``
    Server-side audit rows, columns drawn from
    :class:`droneserver.safety.audit.AuditRecord`::

        ts, call_id, tool, tier, verdict, rule, outcome_status, model, ...

    (Only ``ts, call_id, tool, verdict, rule`` are consumed; extra columns are
    ignored, and any of the consumed columns may be absent.)

``mavlink.jsonl``
    One decoded MAVLink message per line::

        {"ts": ISO8601, "t_rel_s": float, "direction": "...",
         "msg_type": "HEARTBEAT", "fields": {...}}

``telemetry.csv``
    Periodic vehicle-state samples::

        t_iso, t_rel_s, flight_mode, armed, in_air, ...

Output
------
``out_dir/events.jsonl`` - append-only, one JSON object per line, sorted by
``t_rel_s`` (nulls last) then ``ts``. Each row::

    {"ts": <UTC ISO-8601 str>,
     "t_rel_s": <float|null>,
     "category": <str>,
     "detail": <str>,
     "source": "audit"|"mavlink"|"telemetry",
     "call_id": <str|null>}

Event categories
----------------
From ``audit_slice.csv`` (source ``"audit"``):
    ``command``
        Every tool call. ``detail = "<tool> <verdict>"``. ``call_id`` carried
        through on this and every audit-derived row below.
    ``rejection``
        Emitted *in addition to* ``command`` when ``verdict == "rejected"``.
        ``detail = "<tool> <rule>"``.
    ``confirmation_required``
        When ``verdict == "confirmation_required"``.
    ``safety_disabled``
        When ``verdict == "allowed_safety_disabled"`` (a guardrails-off trial).
    ``error``
        When ``verdict == "error"``.

From ``mavlink.jsonl`` (source ``"mavlink"``):
    ``mode_change``
        HEARTBEAT ``custom_mode`` changed. ``detail`` is the resolved
        ArduCopter mode name (or the raw number as a fallback).
    ``arm`` / ``disarm``
        HEARTBEAT ``base_mode`` MAV_MODE_FLAG_SAFETY_ARMED (0x80) bit toggled.
        Both this and ``mode_change`` are read only from messages the tap
        labelled ``direction == "recv"`` - i.e. the vehicle's own heartbeats.
        A ground station on the same wire heartbeats too, and mixing the two
        streams toggles the tracked state on every other message.
    ``command_ack``
        COMMAND_ACK. ``detail = "<command> <result>"``.
    ``mission_item_reached``
        MISSION_ITEM_REACHED. ``detail = "seq=<n>"``.
    ``statustext``
        STATUSTEXT. ``detail`` is the message text. Re-categorised as
        ``failsafe`` or ``geofence`` when the text matches those keywords.
    ``home_set``
        HOME_POSITION, emitted only when the coordinates change (the topic
        itself streams continuously on ArduPilot once it has been requested).

From ``telemetry.csv`` (source ``"telemetry"``):
    ``takeoff``
        ``in_air`` False -> True.
    ``land``
        ``in_air`` True -> False.
    ``mode_change``
        ``flight_mode`` column changed - *fallback only*, emitted only when no
        ``mavlink.jsonl`` was present (avoids double-reporting mode changes).
    ``telemetry_gap``
        Gap between consecutive telemetry timestamps > ``GAP_THRESHOLD_S``
        (30 s). A coarse proxy for a client connect/disconnect during a long
        mission; ``detail = "gap=<seconds>s"``. The integrator may refine this.

Assumptions
-----------
- ArduCopter ``custom_mode`` numbers are resolved via :data:`ARDUCOPTER_MODES`;
  unknown numbers fall back to their raw string.
- ``MAV_MODE_FLAG_SAFETY_ARMED`` is bit 0x80 of ``base_mode``.
- ``telemetry_gap`` threshold is 30 s (:data:`GAP_THRESHOLD_S`).
- Truthiness of ``armed`` / ``in_air`` / ``armed`` accepts bools, the strings
  "true"/"1"/"yes"/"t" (case-insensitive), and numeric 1.
"""

import csv
import json
import pathlib

#: ArduCopter flight-mode numbers -> names (custom_mode field of HEARTBEAT).
ARDUCOPTER_MODES = {
    0: "STABILIZE",
    1: "ACRO",
    2: "ALT_HOLD",
    3: "AUTO",
    4: "GUIDED",
    5: "LOITER",
    6: "RTL",
    7: "CIRCLE",
    9: "LAND",
    11: "DRIFT",
    13: "SPORT",
    14: "FLIP",
    15: "AUTOTUNE",
    16: "POSHOLD",
    17: "BRAKE",
    18: "THROW",
    19: "AVOID_ADSB",
    20: "GUIDED_NOGPS",
    21: "SMART_RTL",
    22: "FLOWHOLD",
    23: "FOLLOW",
    24: "ZIGZAG",
    25: "SYSTEMID",
    26: "AUTOROTATE",
    27: "AUTO_RTL",
}

#: MAV_MODE_FLAG_SAFETY_ARMED bit within HEARTBEAT.base_mode.
MAV_MODE_FLAG_SAFETY_ARMED = 0x80

#: Telemetry-gap threshold (seconds) above which a telemetry_gap event fires.
GAP_THRESHOLD_S = 30.0

#: STATUSTEXT keyword -> re-categorisation for the flagged warning classes.
_STATUSTEXT_KEYWORDS = (
    ("geofence", ("geofence", "fence")),
    ("failsafe", ("failsafe", "fail-safe", "fail safe")),
)


def _truthy(value) -> bool:
    """Best-effort truthiness across bools, numbers and log-string encodings."""
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return value != 0
    if value is None:
        return False
    text = str(value).strip().lower()
    return text in ("true", "1", "yes", "t", "y")


def _to_float(value):
    """Parse a float, or return None if impossible/blank."""
    if value is None or value == "":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _to_int(value):
    """Parse an int (tolerating float-ish strings like '4.0'), else None."""
    if value is None or value == "":
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        pass
    f = _to_float(value)
    return int(f) if f is not None else None


def _resolve_mode(custom_mode) -> str:
    num = _to_int(custom_mode)
    if num is None:
        return str(custom_mode)
    return ARDUCOPTER_MODES.get(num, str(num))


def _mk(ts, t_rel_s, category, detail, source, call_id=None) -> dict:
    return {
        "ts": ts,
        "t_rel_s": t_rel_s,
        "category": category,
        "detail": detail,
        "source": source,
        "call_id": call_id,
    }


def _read_audit(path: pathlib.Path) -> list:
    events = []
    with open(path, newline="", encoding="utf-8") as fh:
        for row in csv.DictReader(fh):
            ts = row.get("ts")
            call_id = row.get("call_id") or None
            tool = row.get("tool") or "?"
            verdict = (row.get("verdict") or "").strip()
            rule = row.get("rule") or ""
            # Every call -> a "command" event.
            events.append(
                _mk(ts, None, "command", f"{tool} {verdict}".strip(), "audit", call_id)
            )
            # Verdict-specific flags, in addition to the command event.
            if verdict == "rejected":
                detail = f"{tool} {rule}".strip()
                events.append(_mk(ts, None, "rejection", detail, "audit", call_id))
            elif verdict == "confirmation_required":
                events.append(
                    _mk(ts, None, "confirmation_required", tool, "audit", call_id)
                )
            elif verdict == "allowed_safety_disabled":
                events.append(_mk(ts, None, "safety_disabled", tool, "audit", call_id))
            elif verdict == "error":
                detail = row.get("outcome_error") or tool
                events.append(_mk(ts, None, "error", detail, "audit", call_id))
    return events


def _statustext_category(text: str) -> str:
    low = text.lower()
    for category, keywords in _STATUSTEXT_KEYWORDS:
        if any(k in low for k in keywords):
            return category
    return "statustext"


def _read_mavlink(path: pathlib.Path) -> list:
    events = []
    last_custom_mode = None
    last_armed = None
    last_home: tuple | None = None
    with open(path, encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                msg = json.loads(line)
            except (ValueError, TypeError):
                continue
            ts = msg.get("ts")
            t_rel = _to_float(msg.get("t_rel_s"))
            msg_type = (msg.get("msg_type") or "").upper()
            fields = msg.get("fields") or {}

            # Vehicle state is read ONLY from the vehicle's own messages. The
            # tap sees a merged stream, and a ground station heartbeats too -
            # its base_mode has the ARMED bit clear and its custom_mode is 0,
            # so interleaving it with the autopilot's toggles the tracked state
            # on every other heartbeat. Measured before this guard: a single
            # arm/takeoff/land trial derived 56 "arm" + 56 "disarm" + 122
            # "mode_change" events, one per heartbeat, instead of 1 + 1 + 2.
            # Anything not explicitly labelled "sent" is treated as the
            # vehicle's, so a hand-written or legacy jsonl without the tap's
            # exact vocabulary still yields the vehicle's state changes.
            from_ground_station = msg.get("direction") == "sent"

            if msg_type == "HEARTBEAT" and from_ground_station:
                continue

            if msg_type == "HEARTBEAT":
                custom_mode = fields.get("custom_mode")
                if custom_mode is not None:
                    cm = _to_int(custom_mode)
                    key = cm if cm is not None else custom_mode
                    if last_custom_mode is None:
                        last_custom_mode = key
                    elif key != last_custom_mode:
                        last_custom_mode = key
                        events.append(
                            _mk(ts, t_rel, "mode_change", _resolve_mode(custom_mode),
                                "mavlink")
                        )
                base_mode = _to_int(fields.get("base_mode"))
                if base_mode is not None:
                    armed = bool(base_mode & MAV_MODE_FLAG_SAFETY_ARMED)
                    if last_armed is None:
                        last_armed = armed
                    elif armed != last_armed:
                        last_armed = armed
                        cat = "arm" if armed else "disarm"
                        events.append(_mk(ts, t_rel, cat, cat, "mavlink"))

            elif msg_type == "COMMAND_ACK":
                cmd = fields.get("command")
                result = fields.get("result")
                events.append(
                    _mk(ts, t_rel, "command_ack", f"command={cmd} result={result}",
                        "mavlink")
                )

            elif msg_type == "MISSION_ITEM_REACHED":
                seq = fields.get("seq")
                events.append(
                    _mk(ts, t_rel, "mission_item_reached", f"seq={seq}", "mavlink")
                )

            elif msg_type == "STATUSTEXT":
                text = str(fields.get("text", "")).strip()
                events.append(
                    _mk(ts, t_rel, _statustext_category(text), text, "mavlink")
                )

            elif msg_type == "HOME_POSITION":
                # HOME_POSITION streams once a second on ArduPilot (the server
                # asks for it - see droneserver.telemetry.home), so emit only
                # when home actually moves. "home_set" is an event, not a topic.
                lat = fields.get("latitude")
                lon = fields.get("longitude")
                if (lat, lon) != last_home:
                    last_home = (lat, lon)
                    events.append(
                        _mk(ts, t_rel, "home_set", f"lat={lat} lon={lon}", "mavlink")
                    )
    return events


def _read_telemetry(path: pathlib.Path, mavlink_present: bool) -> list:
    events = []
    last_in_air = None
    last_mode = None
    last_t_rel = None
    with open(path, newline="", encoding="utf-8") as fh:
        reader = csv.DictReader(fh)
        has_in_air = reader.fieldnames is not None and "in_air" in reader.fieldnames
        has_mode = reader.fieldnames is not None and "flight_mode" in reader.fieldnames
        for row in reader:
            ts = row.get("t_iso") or row.get("ts")
            t_rel = _to_float(row.get("t_rel_s"))

            if has_in_air:
                in_air = _truthy(row.get("in_air"))
                if last_in_air is None:
                    last_in_air = in_air
                elif in_air != last_in_air:
                    last_in_air = in_air
                    cat = "takeoff" if in_air else "land"
                    events.append(_mk(ts, t_rel, cat, cat, "telemetry"))

            # flight_mode changes only as a fallback when MAVLink is absent.
            if has_mode and not mavlink_present:
                mode = row.get("flight_mode")
                if last_mode is None:
                    last_mode = mode
                elif mode != last_mode:
                    last_mode = mode
                    events.append(
                        _mk(ts, t_rel, "mode_change", str(mode), "telemetry")
                    )

            # Telemetry gap proxy for client connect/disconnect.
            if t_rel is not None:
                if last_t_rel is not None:
                    gap = t_rel - last_t_rel
                    if gap > GAP_THRESHOLD_S:
                        events.append(
                            _mk(ts, t_rel, "telemetry_gap",
                                f"gap={round(gap, 3)}s", "telemetry")
                        )
                last_t_rel = t_rel
    return events


def _sort_key(event: dict):
    t_rel = event.get("t_rel_s")
    # Nulls last; then by t_rel_s, then ts (string), then category for stability.
    return (
        t_rel is None,
        t_rel if t_rel is not None else 0.0,
        event.get("ts") or "",
        event.get("category") or "",
    )


def derive_events(out_dir: pathlib.Path) -> pathlib.Path:
    """Derive the appendix event narrative for a single trial in ``out_dir``.

    Reads whatever of ``audit_slice.csv``, ``mavlink.jsonl`` and
    ``telemetry.csv`` exist in ``out_dir`` (missing inputs are skipped) and
    writes ``out_dir/events.jsonl`` sorted by ``t_rel_s`` (nulls last) then
    ``ts``. Returns the path to ``events.jsonl``.
    """
    out_dir = pathlib.Path(out_dir)
    events: list = []

    audit_path = out_dir / "audit_slice.csv"
    mavlink_path = out_dir / "mavlink.jsonl"
    telemetry_path = out_dir / "telemetry.csv"

    if audit_path.exists():
        events.extend(_read_audit(audit_path))

    mavlink_present = mavlink_path.exists()
    if mavlink_present:
        events.extend(_read_mavlink(mavlink_path))

    if telemetry_path.exists():
        events.extend(_read_telemetry(telemetry_path, mavlink_present))

    events.sort(key=_sort_key)

    events_path = out_dir / "events.jsonl"
    with open(events_path, "w", encoding="utf-8") as fh:
        for event in events:
            fh.write(json.dumps(event) + "\n")
    return events_path
