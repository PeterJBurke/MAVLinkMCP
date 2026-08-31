#!/usr/bin/env python3
"""Derive a system-ID-filtered companion telemetry CSV for a capture bundle.

Why this exists
---------------
``capture/telemetry_recorder.TelemetryRecorder`` was, for the PX4 SITL campaign
(2026-08-12 .. 2026-08-13) and the 2026-08-18 ArduPilot T6 campaign, connected
to ``udp://:14540`` -- a bind-to-any address with no host filter -- and
constructed ``System()`` with no ``sysid`` argument. A second SITL left running
from the previous campaign published to the same port, so its telemetry entered
the same MavSDK subscriptions. Because the recorder is a *sample-and-hold*
writer (ten independent subscriber tasks, one 10 Hz snapshot timer), the columns
of a single ``telemetry.csv`` row can come from two different aircraft, and the
row schema has no system-ID column in which to say so.

The raw MAVLink tap (``capture/mavlink_tap.py``) is unaffected: it reads a
dedicated per-firmware mirror port and stamps ``msg.get_srcSystem()`` into every
record as ``sysid``. ``mavlink.jsonl`` is therefore clean AND self-identifying,
and is the authoritative stream for the affected trials.

What this script produces
-------------------------
``telemetry_sysid<N>.csv`` next to the original ``telemetry.csv``, with:

* **the same row clock** -- every row's ``t_iso`` / ``t_rel_s`` is copied
  verbatim from the original file, so the companion has exactly as many rows as
  the original and can be diffed against it row for row;
* **the same column schema** (``telemetry_recorder.COLUMNS``) in the same order,
  plus one appended ``sysid`` column that the original schema lacked;
* **every state column re-derived** from ``mavlink.jsonl``, restricted to
  ``direction == "recv"`` and to the trial's own vehicle system ID, using the
  same sample-and-hold rule the recorder used: each column carries the newest
  wire message of its source type at or before that row's timestamp.

The original ``telemetry.csv`` is never read for anything but its row clock, and
is never modified. See ``llm_runs/CHANGELOG-TELEMETRY-CLEAN.md``.

Honest limits of the reconstruction (also stated in each sidecar README):

* ``flight_mode`` comes from ``HEARTBEAT.custom_mode`` at ~1 Hz, decoded per
  firmware into the same MavSDK ``FlightMode`` vocabulary the original file
  used. The ArduCopter half of that decode was validated against 67,176 rows of
  the *uncontaminated* ArduPilot campaign at 100 % agreement.
* ``armed`` (1 Hz) and ``in_air`` (from ``EXTENDED_SYS_STATE.landed_state``,
  ~1 Hz) update more slowly than MavSDK's own change-driven callbacks did.
* ``battery_pct`` is ``BATTERY_STATUS.battery_remaining`` as an integer
  percentage, i.e. the wire value, not MavSDK's ``remaining_percent``.
* ``sample_age_s`` is redefined: in the original it is the age of the newest
  item on any MavSDK subscription; here it is the age of the newest own-vehicle
  MAVLink message of any source type. Same meaning ("how stale is this row"),
  different clock.

Usage::

    derive_clean_telemetry.py TRIAL_DIR [TRIAL_DIR ...]
    derive_clean_telemetry.py --list trials.txt [--jobs 8] [--force]

Prints one JSON object per line on stdout (one per trial) with the stats each
trial's sidecar README records, so a caller can aggregate them.
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import math
import os
import sys
from bisect import bisect_right
from datetime import datetime
from pathlib import Path

# The column order is the recorder's, imported when droneserver is importable
# and restated otherwise so this script also runs standalone against an archive.
try:
    from droneserver.capture.telemetry_recorder import COLUMNS
except Exception:  # pragma: no cover - standalone use
    COLUMNS = [
        "t_iso", "t_rel_s", "lat_deg", "lon_deg", "abs_alt_m", "rel_alt_m",
        "flight_mode", "armed", "roll_deg", "pitch_deg", "yaw_deg", "vn_ms",
        "ve_ms", "vd_ms", "groundspeed_ms", "airspeed_ms", "gps_fix_type",
        "num_satellites", "hdop", "vdop", "battery_v", "battery_pct",
        "throttle_pct", "in_air", "home_lat", "home_lon", "home_alt",
        "ekf_ok", "geofence_ok", "sample_age_s",
    ]

OUT_COLUMNS = list(COLUMNS) + ["sysid"]

DERIVED_ON = "2026-08-18"

#: The wire message types each output column is read from. Nothing else is
#: decoded, so a cheap substring pre-filter can skip most of the JSONL.
WANTED = (
    "GLOBAL_POSITION_INT",   # lat/lon/alt/rel_alt/vn/ve/vd
    "ATTITUDE",              # roll/pitch/yaw
    "GPS_RAW_INT",           # fix type, satellites, hdop, vdop
    "BATTERY_STATUS",        # battery_v, battery_pct
    "HOME_POSITION",         # home_lat/lon/alt
    "HEARTBEAT",             # flight_mode, armed
    "EXTENDED_SYS_STATE",    # in_air
    "SYS_STATUS",            # ekf_ok, geofence_ok
    "VFR_HUD",               # throttle_pct, airspeed_ms
)
_WANTED_SET = frozenset(WANTED)

_DOP_UNKNOWN = 65535
_SYS_STATUS_GEOFENCE = 0x00100000
_SYS_STATUS_AHRS = 0x00200000
_MAV_MODE_FLAG_SAFETY_ARMED = 0x80

#: MAV_GPS_FIX_TYPE -> the MavSDK ``FixType`` enum name the original CSV used.
_FIX_TYPE = {
    0: "NO_GPS", 1: "NO_FIX", 2: "FIX_2D", 3: "FIX_3D",
    4: "FIX_DGPS", 5: "RTK_FLOAT", 6: "RTK_FIXED",
}

#: ArduCopter ``HEARTBEAT.custom_mode`` -> MavSDK ``FlightMode`` name.
#: The five modes the corpus actually flies (3,4,5,6,9) were validated against
#: the uncontaminated ArduPilot campaign at 100 % agreement over 67,176 rows;
#: the rest follow MAVSDK's ArduPilot mode table.
_ARDUCOPTER_FLIGHT_MODE = {
    0: "STABILIZED", 1: "ACRO", 2: "ALTCTL", 3: "MISSION", 4: "OFFBOARD",
    5: "HOLD", 6: "RETURN_TO_LAUNCH", 7: "UNKNOWN", 9: "LAND",
    11: "UNKNOWN", 13: "UNKNOWN", 14: "UNKNOWN", 15: "UNKNOWN",
    16: "POSCTL", 17: "UNKNOWN", 18: "LAND", 20: "OFFBOARD",
    21: "UNKNOWN", 22: "UNKNOWN", 23: "UNKNOWN", 24: "UNKNOWN",
    25: "UNKNOWN", 26: "UNKNOWN", 27: "UNKNOWN",
}

#: PX4 ``HEARTBEAT.custom_mode`` is a packed (main_mode, sub_mode) pair:
#: bits 16-23 are the main mode, bits 24-31 the AUTO sub-mode.
_PX4_MAIN = {
    1: "MANUAL", 2: "ALTCTL", 3: "POSCTL", 5: "ACRO",
    6: "OFFBOARD", 7: "STABILIZED", 8: "RATTITUDE",
}
_PX4_AUTO_SUB = {
    1: "READY", 2: "TAKEOFF", 3: "HOLD", 4: "MISSION",
    5: "RETURN_TO_LAUNCH", 6: "LAND", 7: "RETURN_TO_LAUNCH",
    8: "FOLLOW_ME", 9: "LAND",
}


class UnmappedMode(Exception):
    """A HEARTBEAT custom_mode this script has no vocabulary for."""


def _round(value, ndigits):
    if value is None:
        return ""
    try:
        f = float(value)
    except (TypeError, ValueError):
        return ""
    if math.isnan(f) or math.isinf(f):
        return ""
    return round(f, ndigits)


def _parse_ts(s: str) -> float:
    return datetime.fromisoformat(s.replace("Z", "+00:00")).timestamp()


def _flight_mode(custom_mode, firmware: str, unmapped: set) -> str:
    try:
        cm = int(custom_mode)
    except (TypeError, ValueError):
        return ""
    if firmware == "PX4":
        main = (cm >> 16) & 0xFF
        sub = (cm >> 24) & 0xFF
        if main == 4:
            name = _PX4_AUTO_SUB.get(sub)
        else:
            name = _PX4_MAIN.get(main)
        if name is None:
            unmapped.add(cm)
            return "UNKNOWN"
        return name
    name = _ARDUCOPTER_FLIGHT_MODE.get(cm)
    if name is None:
        unmapped.add(cm)
        return "UNKNOWN"
    return name


def _dop(gps_raw, field):
    if not gps_raw:
        return ""
    try:
        value = int(gps_raw.get(field))
    except (TypeError, ValueError):
        return ""
    if value < 0 or value >= _DOP_UNKNOWN:
        return ""
    return round(value / 100.0, 2)


def _sensor_health(sys_status, bit):
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


def _battery_voltage(batt):
    """First cell-block voltage in volts. UINT16_MAX means 'not reported'."""
    if not batt:
        return None
    volts = batt.get("voltages")
    if not isinstance(volts, (list, tuple)) or not volts:
        return None
    try:
        mv = int(volts[0])
    except (TypeError, ValueError):
        return None
    if mv <= 0 or mv >= 65535:
        return None
    return mv / 1000.0


def _battery_percent(batt):
    if not batt:
        return None
    try:
        pct = float(batt.get("battery_remaining"))
    except (TypeError, ValueError):
        return None
    if pct < 0:  # -1 == autopilot does not estimate it
        return None
    return pct


def _landed_to_in_air(ext):
    """MAV_LANDED_STATE -> the boolean MavSDK's ``in_air`` reported.

    UNDEFINED (0) is left unknown rather than guessed: a blank cell means the
    autopilot did not say, exactly as in the original file.
    """
    if not ext:
        return ""
    try:
        state = int(ext.get("landed_state"))
    except (TypeError, ValueError):
        return ""
    if state == 1:
        return False
    if state in (2, 3, 4):  # IN_AIR, TAKEOFF, LANDING
        return True
    return ""


def _scan_mavlink(path: Path):
    """Return ({msg_type: (times, fields)}, sysid_census, vehicle_sysid).

    Only ``direction == "recv"`` records are kept -- a ground station on the
    same wire heartbeats too, and mixing the two toggles the tracked state on
    every other message (the same rule ``capture/events.py`` follows).
    """
    per_type: dict[str, list] = {m: [] for m in WANTED}
    census: dict[int, int] = {}
    with open(path, "r", encoding="utf-8", errors="replace") as f:
        for line in f:
            i = line.find('"msg_type": "')
            if i < 0:
                continue
            j = line.find('"', i + 13)
            if j < 0:
                continue
            mtype = line[i + 13:j]
            if mtype not in _WANTED_SET:
                continue
            try:
                rec = json.loads(line)
            except ValueError:
                continue
            sysid = rec.get("sysid")
            census[sysid] = census.get(sysid, 0) + 1
            if rec.get("direction") != "recv":
                continue
            per_type[mtype].append((rec["ts"], rec.get("compid"), sysid, rec.get("fields") or {}))
    # The vehicle is whichever system publishes the position stream. Anything
    # else on the wire (the GCS/offboard commander, unrecognised broadcasts) is
    # excluded by construction.
    pos_sys: dict[int, int] = {}
    for _ts, _cid, sysid, _f in per_type["GLOBAL_POSITION_INT"]:
        pos_sys[sysid] = pos_sys.get(sysid, 0) + 1
    vehicle = max(pos_sys, key=pos_sys.get) if pos_sys else 1
    return per_type, census, vehicle


def _series(per_type, mtype, vehicle, compid=None):
    """Time-ordered (epoch_seconds, fields) for one own-vehicle message type."""
    out = []
    for ts, cid, sysid, fields in per_type[mtype]:
        if sysid != vehicle:
            continue
        if compid is not None and cid != compid:
            continue
        out.append((_parse_ts(ts), fields))
    out.sort(key=lambda r: r[0])
    return [r[0] for r in out], [r[1] for r in out]


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


README_TEMPLATE = """\
{out_name} -- system-ID-filtered companion to telemetry.csv
{underline}

Derived {derived_on} from mavlink.jsonl filtered to sysid {vehicle} (this trial's own
vehicle), by scripts/derive_clean_telemetry.py in the droneserver repository.

WHY. The MavSDK telemetry recorder that wrote telemetry.csv was bound to
udp://:14540 with no host and no system-ID filter, and a second SITL instance
left running from the previous campaign published to the same port. The
recorder is a sample-and-hold writer, so on this trial the columns of a single
telemetry.csv row can come from two different aircraft, and its schema has no
system-ID column in which to say which. The raw MAVLink tap is unaffected: it
reads a dedicated per-firmware mirror port and stamps every record with its
source system ID, so mavlink.jsonl is clean and self-identifying.

WHAT IS AND IS NOT CHANGED. telemetry.csv is preserved exactly as captured,
with its original sha256 in manifest.json unchanged; this file is an addition,
not a replacement. Every row here copies its t_iso / t_rel_s verbatim from
telemetry.csv, so the two files have the same number of rows on the same clock
and can be diffed row for row. Every state column is re-derived from this
trial's own mavlink.jsonl, restricted to direction == "recv" and sysid ==
{vehicle}, under the same sample-and-hold rule (each column carries the newest
wire message of its source type at or before that row's timestamp). One column,
`sysid`, is appended to the recorder's stable schema; it is {vehicle} on every row.

THE TWO FILES ARE DIFFERENTLY DERIVED ARTIFACTS and will not agree cell for
cell even where the original happens to be uncontaminated. telemetry.csv is a
MavSDK gRPC subscription record; this file is a decode of the MAVLink wire.
Specifically: flight_mode comes from HEARTBEAT.custom_mode at ~1 Hz decoded
into the same MavSDK FlightMode vocabulary (rather than MavSDK's own
change-driven callback); armed is HEARTBEAT.base_mode & 0x80 at ~1 Hz; in_air
is EXTENDED_SYS_STATE.landed_state at ~1 Hz; position is GLOBAL_POSITION_INT,
which on this firmware arrives at {pos_hz:.1f} Hz against the file's 10 Hz row rate;
battery_pct is the wire's integer BATTERY_STATUS.battery_remaining; and
sample_age_s is redefined as the age of the newest own-vehicle MAVLink message
of any source type, rather than of the newest MavSDK subscription item.

THIS TRIAL.
  run              {run_id}
  mission / trial  {mission} / {trial}
  firmware         {firmware}
  vehicle sysid    {vehicle}   (system IDs present among the message types read: {census})
  rows             {rows} (same as telemetry.csv)
  source           mavlink.jsonl  sha256 {jsonl_sha}
  original         telemetry.csv  sha256 {csv_sha}  (unmodified)
  this file        {out_name}  sha256 recorded in llm_runs/sha256-telemetry-clean.txt

See llm_runs/CHANGELOG-TELEMETRY-CLEAN.md for the full defect writeup, the
verification results, and the scope of the affected corpus.
"""


def derive(trial_dir: Path, force: bool = False) -> dict:
    csv_path = trial_dir / "telemetry.csv"
    jsonl_path = trial_dir / "mavlink.jsonl"
    if not csv_path.exists() or not jsonl_path.exists():
        return {"trial": str(trial_dir), "status": "skipped", "reason": "missing telemetry.csv or mavlink.jsonl"}

    manifest = {}
    mpath = trial_dir / "manifest.json"
    if mpath.exists():
        try:
            manifest = json.loads(mpath.read_text()).get("trial", {}) or {}
        except ValueError:
            manifest = {}
    firmware = "PX4" if str(manifest.get("firmware", "")).upper().startswith("PX4") else "ArduCopter"

    per_type, census, vehicle = _scan_mavlink(jsonl_path)
    out_path = trial_dir / f"telemetry_sysid{vehicle}.csv"
    readme_path = trial_dir / f"telemetry_sysid{vehicle}.README.txt"
    if out_path.exists() and not force:
        return {"trial": str(trial_dir), "status": "exists", "out": out_path.name}

    pos_t, pos_f = _series(per_type, "GLOBAL_POSITION_INT", vehicle)
    att_t, att_f = _series(per_type, "ATTITUDE", vehicle)
    gps_t, gps_f = _series(per_type, "GPS_RAW_INT", vehicle)
    bat_t, bat_f = _series(per_type, "BATTERY_STATUS", vehicle)
    hom_t, hom_f = _series(per_type, "HOME_POSITION", vehicle)
    hbt_t, hbt_f = _series(per_type, "HEARTBEAT", vehicle, compid=1)
    ext_t, ext_f = _series(per_type, "EXTENDED_SYS_STATE", vehicle)
    sst_t, sst_f = _series(per_type, "SYS_STATUS", vehicle)
    vfr_t, vfr_f = _series(per_type, "VFR_HUD", vehicle)

    all_t = sorted(pos_t + att_t + gps_t + bat_t + hom_t + hbt_t + ext_t + sst_t + vfr_t)

    def latest(times, fields, t):
        i = bisect_right(times, t) - 1
        return (fields[i], times[i]) if i >= 0 else (None, None)

    unmapped: set = set()
    rows_out = []
    n_rows = 0
    src_rows = list(csv.DictReader(open(csv_path, newline="")))
    for src in src_rows:
        t_iso = src.get("t_iso") or ""
        if not t_iso:
            continue
        n_rows += 1
        t = _parse_ts(t_iso)
        pos, _ = latest(pos_t, pos_f, t)
        att, _ = latest(att_t, att_f, t)
        gps, _ = latest(gps_t, gps_f, t)
        bat, _ = latest(bat_t, bat_f, t)
        hom, _ = latest(hom_t, hom_f, t)
        hbt, _ = latest(hbt_t, hbt_f, t)
        ext, _ = latest(ext_t, ext_f, t)
        sst, _ = latest(sst_t, sst_f, t)
        vfr, _ = latest(vfr_t, vfr_f, t)

        i_any = bisect_right(all_t, t) - 1
        sample_age = round(t - all_t[i_any], 3) if i_any >= 0 else ""

        vn = ve = vd = groundspeed = None
        lat = lon = abs_alt = rel_alt = None
        if pos:
            lat = pos.get("lat") / 1e7 if pos.get("lat") is not None else None
            lon = pos.get("lon") / 1e7 if pos.get("lon") is not None else None
            abs_alt = pos.get("alt") / 1000.0 if pos.get("alt") is not None else None
            rel_alt = pos.get("relative_alt") / 1000.0 if pos.get("relative_alt") is not None else None
            vn = pos.get("vx") / 100.0 if pos.get("vx") is not None else None
            ve = pos.get("vy") / 100.0 if pos.get("vy") is not None else None
            vd = pos.get("vz") / 100.0 if pos.get("vz") is not None else None
            if vn is not None and ve is not None:
                groundspeed = math.sqrt(vn * vn + ve * ve)

        armed = ""
        mode = ""
        if hbt:
            try:
                armed = bool(int(hbt.get("base_mode") or 0) & _MAV_MODE_FLAG_SAFETY_ARMED)
            except (TypeError, ValueError):
                armed = ""
            mode = _flight_mode(hbt.get("custom_mode"), firmware, unmapped)

        fix = ""
        sats = ""
        if gps:
            try:
                fix = _FIX_TYPE.get(int(gps.get("fix_type")), "")
            except (TypeError, ValueError):
                fix = ""
            try:
                sats = int(gps.get("satellites_visible"))
            except (TypeError, ValueError):
                sats = ""

        values = {
            "t_iso": t_iso,
            "t_rel_s": src.get("t_rel_s", ""),
            "lat_deg": _round(lat, 7),
            "lon_deg": _round(lon, 7),
            "abs_alt_m": _round(abs_alt, 3),
            "rel_alt_m": _round(rel_alt, 3),
            "flight_mode": mode,
            "armed": armed,
            "roll_deg": _round(math.degrees(att["roll"]), 3) if att and att.get("roll") is not None else "",
            "pitch_deg": _round(math.degrees(att["pitch"]), 3) if att and att.get("pitch") is not None else "",
            "yaw_deg": _round(math.degrees(att["yaw"]), 3) if att and att.get("yaw") is not None else "",
            "vn_ms": _round(vn, 3),
            "ve_ms": _round(ve, 3),
            "vd_ms": _round(vd, 3),
            "groundspeed_ms": _round(groundspeed, 3),
            "airspeed_ms": _round(vfr.get("airspeed"), 3) if vfr else "",
            "gps_fix_type": fix,
            "num_satellites": sats,
            "hdop": _dop(gps, "eph"),
            "vdop": _dop(gps, "epv"),
            "battery_v": _round(_battery_voltage(bat), 3),
            "battery_pct": _round(_battery_percent(bat), 1),
            "throttle_pct": _round(vfr.get("throttle"), 1) if vfr else "",
            "in_air": _landed_to_in_air(ext),
            "home_lat": _round(hom.get("latitude") / 1e7, 7) if hom and hom.get("latitude") is not None else "",
            "home_lon": _round(hom.get("longitude") / 1e7, 7) if hom and hom.get("longitude") is not None else "",
            "home_alt": _round(hom.get("altitude") / 1000.0, 3) if hom and hom.get("altitude") is not None else "",
            "ekf_ok": _sensor_health(sst, _SYS_STATUS_AHRS),
            "geofence_ok": _sensor_health(sst, _SYS_STATUS_GEOFENCE),
            "sample_age_s": sample_age,
            "sysid": vehicle,
        }
        rows_out.append([values[c] for c in OUT_COLUMNS])

    tmp = out_path.with_suffix(".csv.tmp")
    with open(tmp, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(OUT_COLUMNS)
        w.writerows(rows_out)
    os.replace(tmp, out_path)

    span = (pos_t[-1] - pos_t[0]) if len(pos_t) > 1 else 0.0
    pos_hz = (len(pos_t) - 1) / span if span > 0 else 0.0
    jsonl_sha = _sha256(jsonl_path)
    csv_sha = _sha256(csv_path)
    readme_path.write_text(README_TEMPLATE.format(
        out_name=out_path.name,
        underline="=" * (len(out_path.name) + 45),
        derived_on=DERIVED_ON,
        vehicle=vehicle,
        run_id=manifest.get("run_id", trial_dir.parents[1].name),
        mission=manifest.get("mission_id", trial_dir.parent.name),
        trial=manifest.get("trial_idx", trial_dir.name),
        firmware=manifest.get("firmware", firmware),
        census=", ".join(f"{k}({v})" for k, v in sorted(census.items(), key=lambda kv: -kv[1])),
        rows=len(rows_out),
        pos_hz=pos_hz,
        jsonl_sha=jsonl_sha,
        csv_sha=csv_sha,
    ))

    return {
        "trial": str(trial_dir),
        "status": "ok",
        "out": out_path.name,
        "vehicle_sysid": vehicle,
        "rows": len(rows_out),
        "src_rows": n_rows,
        "firmware": firmware,
        "census": {str(k): v for k, v in census.items()},
        "pos_hz": round(pos_hz, 2),
        "unmapped_custom_modes": sorted(unmapped),
        "out_sha256": _sha256(out_path),
    }


def _worker(args):
    trial, force = args
    try:
        return derive(Path(trial), force=force)
    except Exception as exc:  # noqa: BLE001 - one bad trial must not stop the batch
        return {"trial": str(trial), "status": "error", "reason": f"{type(exc).__name__}: {exc}"}


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("trials", nargs="*", help="trial directories")
    ap.add_argument("--list", help="file with one trial directory per line")
    ap.add_argument("--jobs", type=int, default=1)
    ap.add_argument("--force", action="store_true", help="rewrite an existing companion")
    args = ap.parse_args(argv)

    trials = list(args.trials)
    if args.list:
        trials += [ln.strip() for ln in open(args.list) if ln.strip() and not ln.startswith("#")]
    if not trials:
        ap.error("no trial directories given")

    work = [(t, args.force) for t in trials]
    if args.jobs > 1:
        from multiprocessing import Pool
        with Pool(args.jobs) as pool:
            for res in pool.imap_unordered(_worker, work, chunksize=1):
                print(json.dumps(res), flush=True)
    else:
        for w in work:
            print(json.dumps(_worker(w)), flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
