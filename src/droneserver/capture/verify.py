"""Did this trial actually capture anything? Checked against the files.

**Who this is for:** anyone who has to trust that a benchmark run left usable
evidence behind, and anyone who has just changed the capture topology.

**Why it exists.** Every capture defect found on this project so far was
*silent*. The harness exited 0, the per-trial directory looked full, and the
artifacts were wrong: a ``mavlink.tlog`` that held only the vehicle's half of
the conversation and not one command; a ``.BIN`` that was some other flight's
log; an ``events.jsonl`` derived from the ground station's heartbeats. The
recorders are deliberately fail-soft - a capture problem must never destroy a
flight - and the price of that is that **a run which skipped every recorder
still exits 0**. This module is the counterweight: after a trial, look at what
is on disk and say plainly whether it is a record or a directory of stubs.

**What "complete" means here** is the Plan 19 §8 bundle: the required files are
present, and each one is non-trivial in the specific way that its own failure
mode would betray.

===========================  ==================================================
``manifest.json``            parses, **lists every other file in the directory**
                             at its true size, lists nothing that is *not* there,
                             and every recorded ``sha256`` matches the bytes on
                             disk. A file written after the manifest is a file
                             nobody can verify; a hash nobody re-computes is a
                             promise, not a check.
``mavlink.tlog``             non-empty.
``mavlink.jsonl``            carries **both directions**: at least one message
                             from the vehicle's sysid and at least one from any
                             other (the ground-station/server side). This is the
                             check that catches a tap wired to a telemetry-only
                             path - the exact shape of blocker B-6, where a
                             plain MAVProxy ``--out`` forward yielded a
                             perfectly valid-looking tlog containing no commands
                             at all. A ground-station side consisting only of
                             HEARTBEATs is *not* evidence that commands were
                             captured, so when the vehicle is seen to arm - the
                             arm command demonstrably crossed this wire - the
                             ground-station side must carry something other than
                             heartbeats.
``telemetry.csv`` *schema*   every column the Plan 19 §3b data dictionary
                             promises is present in the header AND carries a
                             value in at least one row. ``hdop``, ``vdop``,
                             ``ekf_ok`` and ``geofence_ok`` were empty in every
                             row of every bundle this project ever captured,
                             and the bundles were reported complete: the
                             dictionary described columns the data never had.
``telemetry.csv``            enough rows *for a trial of this length*, rows that
                             actually carry vehicle state, rows that were fed by
                             a live link (``sample_age_s``) rather than held
                             from an old sample, no long gap between consecutive
                             rows, and recording all the way to the end of the
                             trial. Catches a MavSDK recorder that
                             never connected (which still emits perfectly
                             evenly-spaced rows - every cell empty), one whose
                             sampler stalled (ten rows spread over twelve
                             minutes used to pass), and one that connected and
                             then died mid-flight. T7-T9 last seconds and
                             legitimately produce single-figure row counts.
``telemetry.csv``            **is every row the same aircraft?** Any foreign
*single-vehicle*             system ID in a ``sysid`` column, or any two
                             consecutive rows more than 200 m apart, means the
                             recorder was fed by two vehicles at once. This is
                             the check whose absence let the 2026-08 shared-port
                             contamination pass 472 trials green: the recorder
                             was bound to ``udp://:14540`` with no host and no
                             system-ID filter, a second SITL was publishing to
                             it, and being sample-and-hold it assembled single
                             rows out of two aircraft's fields. Nothing else in
                             this module could see it - the schema has no column
                             in which the file could even say which vehicle a
                             row describes.
``audit_slice.csv``          present and non-empty (it is absent entirely when
                             the harness was run without ``--audit-log``, which
                             is worth being told about rather than discovering
                             at analysis time).
``events.jsonl``             present, and every line parses as JSON.
``transcript.jsonl``         when required (LLM-in-the-loop trials): present,
                             every line parses, and the role counts are reported.
dataflash ``.BIN`` / ``.ulg``  required **only if the aircraft is seen to have
                             armed** - in the telemetry, in the vehicle's own
                             HEARTBEATs, or in the derived events. A mission that
                             never arms writes no autopilot log, and demanding
                             one there would mean copying somebody else's
                             flight; but a trial that *did* fly and kept no log
                             is degraded. Reading the arm evidence from three
                             places matters because the one that used to be
                             consulted - the telemetry - is exactly the artifact
                             that is empty when the recorder never connected,
                             and an empty telemetry file was therefore able to
                             excuse a missing dataflash log on a trial that flew.
===========================  ==================================================

The manifest's ``sha256`` values are re-computed here from the bytes on disk.
``write_manifest`` hashed the same files moments earlier, so in-run this only
ever catches an artifact that changed *after* the manifest was sealed - but this
verifier is also what a third party runs on the downloaded archive, where
re-hashing is the whole point. Size agreement alone cannot see a same-length
substitution or a log that was corrupted in transit.

This module is pure stdlib and reads only. It imports neither pymavlink nor
mavsdk, so it can be used (and tested) anywhere.
"""

import csv
import hashlib
import json
import math
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

#: Files Plan 19 §8 requires in every trial bundle, whatever the harness.
REQUIRED_ARTIFACTS = (
    "manifest.json",
    "mavlink.tlog",
    "mavlink.jsonl",
    "telemetry.csv",
    "audit_slice.csv",
    "events.jsonl",
)

#: Required in addition for an LLM-in-the-loop trial (Plan 19 §1c).
TRANSCRIPT_ARTIFACT = "transcript.jsonl"

#: Dataflash-log suffixes, matched case-insensitively.
DATAFLASH_SUFFIXES = (".bin", ".ulg")

#: Ceiling on the ``telemetry.csv`` row floor. The floor actually applied is
#: this or one row per second of the trial, whichever is *smaller*: a 2-second
#: parameter-read mission cannot produce ten rows however healthy the recorder
#: is, and reporting it degraded would train everyone to ignore the warning.
#: One row per second is a deliberately conservative reading of a 10 Hz stream.
DEFAULT_MIN_TELEMETRY_ROWS = 10

#: Fraction of the trial the telemetry must span before the recording counts as
#: covering it. This is the check that catches a recorder which connected and
#: then died: the row count stays plausible, the coverage does not.
MIN_TELEMETRY_COVERAGE = 0.8

#: Trials shorter than this are exempt from the coverage check - a few seconds
#: of recorder start-up and shut-down is a large fraction of a short trial and
#: says nothing about the recorder's health.
COVERAGE_MIN_TRIAL_S = 30.0

#: Longest acceptable interval between two consecutive ``telemetry.csv`` rows.
#: The recorder emits at 10 Hz; every trial ever flown on this project has a
#: worst-case inter-row gap under 0.2 s, so five seconds is two orders of
#: magnitude of headroom and still catches the failure the row *count* cannot:
#: a sampler that stalled or a recorder restarted mid-trial. Without it, ten
#: rows spread evenly over a twelve-minute trial satisfied both the row floor
#: (which is capped at :data:`DEFAULT_MIN_TELEMETRY_ROWS`) and the coverage
#: check, and the bundle was reported complete.
MAX_TELEMETRY_GAP_S = 5.0

#: Furthest two consecutive ``telemetry.csv`` rows may be apart before the file
#: is describing more than one aircraft.
#:
#: This is the check whose absence let a capture defect run for 472 trials. The
#: MavSDK recorder was bound to ``udp://:14540`` - no host, no system-ID filter
#: - so a second SITL left running from the previous campaign fed the same
#: subscriptions, and because the recorder is sample-and-hold the two aircraft's
#: fields landed in single rows. 351,312 of 526,059 PX4 rows belonged to the
#: wrong vehicle and every one of those trials passed verification with a green
#: ``telemetry.csv`` check, because nothing here asked whether the rows were all
#: the same aircraft. (Research/PX4-TELEMETRY-CONTAMINATION-VERIFICATION_
#: 2026-08-18.md; the fix is in telemetry_recorder.is_shared_bind.)
#:
#: 200 m has no false-positive mechanism at this row rate: one vehicle at 10 Hz
#: would need 2 km/s, and the fastest leg this project flies moves about 2 m
#: between rows. The measured separation when it fires is 780-820 m.
MAX_TELEMETRY_POSITION_JUMP_M = 200.0

#: Columns whose emptiness in *every* row means the recorder produced a shape
#: without a recording. A MavSDK recorder that never connects still runs its
#: timer and writes perfectly evenly-spaced rows with every cell blank, so the
#: row count, the coverage and the gap all look healthy.
TELEMETRY_STATE_COLUMNS = (
    "lat_deg",
    "lon_deg",
    "abs_alt_m",
    "rel_alt_m",
    "flight_mode",
    "armed",
    "in_air",
)

#: Columns the Plan 19 §3b data dictionary promises will carry data, checked
#: here so a promise cannot survive unkept. Every one of these must be
#: non-empty in **at least one row** of a trial whose recorder had a live link.
#:
#: This check exists because ``hdop``, ``vdop``, ``ekf_ok`` and ``geofence_ok``
#: were empty in every row of every mission of every bundle ever captured -
#: they are not in the MavSDK telemetry plugin - and ``verify_bundle`` called
#: those bundles complete. A Zenodo data dictionary describing columns that are
#: always blank is precisely the reproducibility criticism this package exists
#: to answer, so the schema is now enforced rather than documented. ``hdop`` and
#: ``vdop`` come off the wire (``GPS_RAW_INT``) on every firmware, so requiring
#: them is what proves the tap's ``raw_source`` is actually wired to the
#: recorder - the exact defect above - regardless of which autopilot flew.
#:
#: Deliberately NOT required, because a blank is honest rather than a gap:
#:
#: - ``ekf_ok`` / ``geofence_ok`` - the autopilot's OWN health bits, read from
#:   ``SYS_STATUS`` only when the autopilot declares the subsystem *present*
#:   (see :func:`droneserver.capture.telemetry_recorder._sensor_health`). Whether
#:   they carry a value is therefore a property of the *firmware*, not of the
#:   capture: ArduPilot sets the AHRS (0x00200000) and GEOFENCE (0x00100000)
#:   present bits and fills both columns; PX4 (measured on v1.16.2 SITL,
#:   present=0x0200402f) sets neither, so both are honestly blank on every PX4
#:   row. Requiring them would degrade every PX4 bundle for telling the truth.
#:   They live in :data:`TELEMETRY_FIRMWARE_HEALTH_COLUMNS` and are reported in
#:   the schema detail without failing the bundle. The raw_source wiring they
#:   used to co-witness is now guarded by ``hdop``/``vdop`` above.
#: - ``flight_mode`` / ``armed`` / ``in_air`` - MavSDK delivers these one to two
#:   seconds after subscribing, which a two-second trial (T7) can end before.
#:   That the recorder connected at all is covered by
#:   :data:`TELEMETRY_STATE_COLUMNS`.
#: - ``airspeed_ms`` - a vehicle with no airspeed sensor publishes no honest
#:   value (see the recorder's module docstring).
#: - ``sample_age_s`` - empty until the first sample of anything arrives, which
#:   is the correct reading of "nothing has gone stale yet".
TELEMETRY_REQUIRED_COLUMNS = (
    "lat_deg",
    "lon_deg",
    "abs_alt_m",
    "rel_alt_m",
    "roll_deg",
    "pitch_deg",
    "yaw_deg",
    "vn_ms",
    "ve_ms",
    "vd_ms",
    "groundspeed_ms",
    "gps_fix_type",
    "num_satellites",
    "hdop",
    "vdop",
    "battery_v",
    "battery_pct",
    "throttle_pct",
    "home_lat",
    "home_lon",
    "home_alt",
)

#: The autopilot's own health bits, whose presence is firmware-dependent (see
#: the note on :data:`TELEMETRY_REQUIRED_COLUMNS`). Reported in the schema
#: detail so a reader can see whether this firmware carried them, but never a
#: reason to degrade a bundle: ArduPilot fills them, PX4 leaves them blank, and
#: both are honest.
TELEMETRY_FIRMWARE_HEALTH_COLUMNS = (
    "ekf_ok",
    "geofence_ok",
)

#: MAV_MODE_FLAG_SAFETY_ARMED, the bit in HEARTBEAT.base_mode that says the
#: aircraft is armed. Duplicated from :mod:`droneserver.capture.events` to keep
#: this module pure stdlib and importable anywhere.
_MAV_MODE_FLAG_SAFETY_ARMED = 0x80

#: Event categories that mean the aircraft left the ground under power.
_FLEW_EVENT_CATEGORIES = ("arm", "takeoff")


@dataclass
class Check:
    """One named verification and its outcome."""

    name: str
    ok: bool
    detail: str = ""

    def as_dict(self) -> dict:
        return {"check": self.name, "ok": self.ok, "detail": self.detail}


@dataclass
class BundleCheck:
    """The verdict on one trial's artifact bundle."""

    trial_dir: Path
    checks: list[Check] = field(default_factory=list)

    @property
    def problems(self) -> list[str]:
        return [f"{c.name}: {c.detail}" for c in self.checks if not c.ok]

    @property
    def complete(self) -> bool:
        return not self.problems

    @property
    def status(self) -> str:
        """``"complete"`` or ``"degraded[...]"`` - the value recorded in the
        manifest and counted in the run-end summary."""
        return "complete" if self.complete else f"degraded[{'; '.join(self.problems)}]"

    def as_dict(self) -> dict:
        return {
            "capture_status": self.status,
            "capture_checks": [c.as_dict() for c in self.checks],
        }


def _count_lines(path: Path) -> int:
    with path.open("rb") as fh:
        return sum(1 for _ in fh)


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _check_manifest(trial_dir: Path, *, verify_hashes: bool = True) -> Check:
    path = trial_dir / "manifest.json"
    if not path.is_file():
        return Check("manifest.json", False, "missing")
    try:
        document = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as e:
        return Check("manifest.json", False, f"unreadable ({type(e).__name__})")

    artifacts = document.get("artifacts")
    if not isinstance(artifacts, list):
        return Check("manifest.json", False, "no artifacts list")
    # A nameless entry is not an artifact anyone can look up; str() keeps the
    # key space comparable with the paths found on disk.
    listed = {str(a.get("name")): a.get("bytes") for a in artifacts if isinstance(a, dict)}
    hashes = {str(a.get("name")): a.get("sha256") for a in artifacts if isinstance(a, dict)}

    on_disk = {
        p.relative_to(trial_dir).as_posix(): p.stat().st_size
        for p in sorted(trial_dir.rglob("*"))
        if p.is_file() and p.name != "manifest.json"
    }
    unlisted = sorted(set(on_disk) - set(listed))
    if unlisted:
        return Check("manifest.json", False, f"does not list {', '.join(unlisted)}")
    # The other direction: an artifact the manifest swears to that is not in
    # the bundle. Nothing else notices - the per-file checks above only look at
    # the files they know by name - so an archive can lose a screenshot, a
    # transcript or a dataflash log between hashing and shipping and still be
    # reported complete.
    vanished = sorted(set(listed) - set(on_disk))
    if vanished:
        return Check("manifest.json", False, f"lists {', '.join(vanished)}, which is not in the bundle")
    stale = sorted(n for n, size in on_disk.items() if listed.get(n) != size)
    if stale:
        return Check("manifest.json", False, f"recorded size differs on {', '.join(stale)}")

    detail = f"lists {len(listed)} artifact(s)"
    if not verify_hashes:
        return Check("manifest.json", True, detail + " (sha256 not re-computed)")
    mismatched, unhashable = [], []
    for name in sorted(on_disk):
        recorded = hashes.get(name)
        if not isinstance(recorded, str) or not recorded:
            mismatched.append(f"{name} (no sha256 recorded)")
            continue
        try:
            if _sha256(trial_dir / name) != recorded:
                mismatched.append(name)
        except OSError as e:
            unhashable.append(f"{name} ({type(e).__name__})")
    if mismatched:
        return Check("manifest.json", False, f"sha256 does not match on {', '.join(mismatched)}")
    if unhashable:
        return Check("manifest.json", False, f"could not re-hash {', '.join(unhashable)}")
    return Check("manifest.json", True, detail + ", all sha256 verified")


def _check_tlog(trial_dir: Path) -> Check:
    path = trial_dir / "mavlink.tlog"
    if not path.is_file():
        return Check("mavlink.tlog", False, "missing")
    size = path.stat().st_size
    if size == 0:
        return Check("mavlink.tlog", False, "empty - the wire tap heard nothing")
    return Check("mavlink.tlog", True, f"{size} bytes")


@dataclass
class _MavlinkEvidence:
    """What one read of ``mavlink.jsonl`` establishes about the trial."""

    present: bool = False
    unreadable: str = ""
    recv: int = 0
    sent: int = 0
    sysids: dict[int, int] = field(default_factory=dict)
    bad_lines: int = 0
    #: Ground-station-side messages that are not HEARTBEAT - i.e. plausible
    #: commands. A heartbeat is not evidence that a command was captured.
    gcs_non_heartbeat: int = 0
    #: The vehicle's own HEARTBEAT reported MAV_MODE_FLAG_SAFETY_ARMED.
    vehicle_armed: bool = False


def _read_mavlink(trial_dir: Path) -> _MavlinkEvidence:
    path = trial_dir / "mavlink.jsonl"
    evidence = _MavlinkEvidence()
    if not path.is_file():
        return evidence
    evidence.present = True
    try:
        with path.open(encoding="utf-8") as fh:
            for line in fh:
                if not line.strip():
                    continue
                try:
                    record = json.loads(line)
                except json.JSONDecodeError:
                    evidence.bad_lines += 1
                    continue
                direction = record.get("direction", "?")
                msg_type = str(record.get("msg_type") or "").upper()
                if direction == "recv":
                    evidence.recv += 1
                    base_mode = (record.get("fields") or {}).get("base_mode")
                    if (
                        msg_type == "HEARTBEAT"
                        and isinstance(base_mode, (int, float))
                        and int(base_mode) & _MAV_MODE_FLAG_SAFETY_ARMED
                    ):
                        evidence.vehicle_armed = True
                elif direction == "sent":
                    evidence.sent += 1
                    if msg_type != "HEARTBEAT":
                        evidence.gcs_non_heartbeat += 1
                sysid = record.get("sysid")
                if isinstance(sysid, int):
                    evidence.sysids[sysid] = evidence.sysids.get(sysid, 0) + 1
    except OSError as e:
        evidence.unreadable = f"{type(e).__name__}: {e}"
    return evidence


def _check_mavlink_directions(evidence: _MavlinkEvidence, vehicle_sysid: int, *, flew: bool) -> Check:
    """Both halves of the link, or the capture is a telemetry recording.

    ``direction`` is the tap's own label (vehicle sysid -> ``recv``, anything
    else -> ``sent``); the sysids are counted alongside so the detail line says
    *who* was heard, not merely that two labels appeared.

    ``sent > 0`` on its own proves only that *something* other than the vehicle
    was on the wire, and the ground station heartbeats once a second whether or
    not any command is being forwarded. So when the aircraft is known to have
    armed, the ground-station side must carry at least one non-HEARTBEAT
    message: the arm command crossed this wire, and a capture that does not
    contain it is not a record of what the model commanded.
    """
    if not evidence.present:
        return Check("mavlink.jsonl", False, "missing")
    if evidence.unreadable:
        return Check("mavlink.jsonl", False, f"unreadable ({evidence.unreadable})")

    recv, sent = evidence.recv, evidence.sent
    detail = (
        f"recv={recv} sent={sent} commands={evidence.gcs_non_heartbeat} sysids={dict(sorted(evidence.sysids.items()))}"
    )
    if evidence.bad_lines:
        detail += f" ({evidence.bad_lines} unparsable line(s))"
    if recv == 0 and sent == 0:
        return Check("mavlink.jsonl", False, "no messages recorded")
    if sent == 0:
        return Check(
            "mavlink.jsonl",
            False,
            f"one direction only - nothing from any sysid other than the vehicle ({vehicle_sysid}); "
            f"the tap is on a telemetry-only path and recorded no commands [{detail}]",
        )
    if recv == 0:
        return Check("mavlink.jsonl", False, f"one direction only - nothing from the vehicle [{detail}]")
    if flew and evidence.gcs_non_heartbeat == 0:
        return Check(
            "mavlink.jsonl",
            False,
            "the ground-station side is HEARTBEATs and nothing else, yet the aircraft armed - the command "
            f"that armed it crossed this wire and was not captured [{detail}]",
        )
    return Check("mavlink.jsonl", True, detail)


@dataclass
class _TelemetryEvidence:
    """What one read of ``telemetry.csv`` establishes about the trial."""

    rows: int = 0
    armed: bool = False
    last_t: float = 0.0
    #: Longest interval between consecutive rows, and when it ended.
    max_gap: float = 0.0
    max_gap_at: float = 0.0
    #: Any row carried a value in any of :data:`TELEMETRY_STATE_COLUMNS`.
    has_state: bool = False
    #: Worst ``sample_age_s`` seen, when the recorder wrote that column: how
    #: stale the held values ever got. A recorder whose link died keeps writing
    #: rows at full rate, so nothing else in the file betrays it.
    max_sample_age: float | None = None
    #: The file has at least one of those columns, so ``has_state`` means
    #: something. A CSV without them cannot be judged this way and says so
    #: rather than passing silently.
    state_columns_present: bool = False
    #: Required columns (:data:`TELEMETRY_REQUIRED_COLUMNS`) that the header
    #: does not even declare, and those that it declares but no row fills.
    absent_columns: list[str] = field(default_factory=list)
    unpopulated_columns: list[str] = field(default_factory=list)
    #: Which firmware-dependent health columns
    #: (:data:`TELEMETRY_FIRMWARE_HEALTH_COLUMNS`) this file actually filled.
    #: Reported, never required (ArduPilot fills them, PX4 does not).
    firmware_health_populated: list[str] = field(default_factory=list)
    #: Consecutive-row position jumps beyond
    #: :data:`MAX_TELEMETRY_POSITION_JUMP_M`, and the worst one seen. Every
    #: count above zero is a second aircraft in the file.
    position_flips: int = 0
    worst_position_jump_m: float = 0.0
    worst_position_jump_at: float = 0.0
    #: System IDs found in a ``sysid`` column, when the file has one. The
    #: recorder's own schema does not, but the system-ID-filtered companions
    #: (``telemetry_sysid<N>.csv``) do, and so should any future recorder: a
    #: file that can name its vehicle should be checked on the name, not only
    #: on the geometry.
    sysids: set = field(default_factory=set)


def _haversine_m(first: tuple[float, float], second: tuple[float, float]) -> float:
    """Great-circle metres between two (lat, lon) degree pairs."""
    radius = 6371000.0
    lat1, lat2 = math.radians(first[0]), math.radians(second[0])
    dlat = math.radians(second[0] - first[0])
    dlon = math.radians(second[1] - first[1])
    h = math.sin(dlat / 2) ** 2 + math.cos(lat1) * math.cos(lat2) * math.sin(dlon / 2) ** 2
    return 2 * radius * math.asin(min(1.0, math.sqrt(h)))


def _fix_of(record: dict) -> tuple[float, float] | None:
    try:
        return float(record["lat_deg"]), float(record["lon_deg"])
    except (KeyError, TypeError, ValueError):
        return None


def _read_telemetry(trial_dir: Path, name: str = "telemetry.csv") -> _TelemetryEvidence:
    path = trial_dir / name
    evidence = _TelemetryEvidence()
    previous_t: float | None = None
    previous_fix: tuple[float, float] | None = None
    populated: set[str] = set()
    with path.open(newline="", encoding="utf-8") as fh:
        reader = csv.DictReader(fh)
        fields = reader.fieldnames or ()
        evidence.state_columns_present = any(c in fields for c in TELEMETRY_STATE_COLUMNS)
        evidence.absent_columns = [c for c in TELEMETRY_REQUIRED_COLUMNS if c not in fields]
        declared = [c for c in TELEMETRY_REQUIRED_COLUMNS if c in fields]
        # Firmware-health columns are reported, not required (see the note on
        # TELEMETRY_REQUIRED_COLUMNS): scan them for the detail line alongside.
        health = [c for c in TELEMETRY_FIRMWARE_HEALTH_COLUMNS if c in fields]
        for record in reader:
            evidence.rows += 1
            # Which promised columns this file ever fills. Cheap: a column drops
            # out of the search as soon as one row carries it.
            for column in declared + health:
                if column not in populated and str(record.get(column, "") or "").strip():
                    populated.add(column)
            if str(record.get("armed", "")).strip().lower() in ("true", "1"):
                evidence.armed = True
            if not evidence.has_state:
                evidence.has_state = any(str(record.get(c, "") or "").strip() for c in TELEMETRY_STATE_COLUMNS)
            age_cell = str(record.get("sample_age_s", "") or "").strip()
            if age_cell:
                try:
                    age = float(age_cell)
                except ValueError:
                    age = 0.0
                evidence.max_sample_age = max(evidence.max_sample_age or 0.0, age)
            sysid_cell = str(record.get("sysid", "") or "").strip()
            if sysid_cell:
                evidence.sysids.add(sysid_cell)
            try:
                t = float(record.get("t_rel_s") or 0.0)
            except (TypeError, ValueError):
                continue
            evidence.last_t = max(evidence.last_t, t)
            if previous_t is not None and t - previous_t > evidence.max_gap:
                evidence.max_gap, evidence.max_gap_at = t - previous_t, t
            previous_t = t
            # Interleave detector: consecutive rows too far apart to be one
            # aircraft (see MAX_TELEMETRY_POSITION_JUMP_M).
            fix = _fix_of(record)
            if fix is not None:
                if previous_fix is not None:
                    metres = _haversine_m(previous_fix, fix)
                    if metres > evidence.worst_position_jump_m:
                        evidence.worst_position_jump_m, evidence.worst_position_jump_at = metres, t
                    if metres > MAX_TELEMETRY_POSITION_JUMP_M:
                        evidence.position_flips += 1
                previous_fix = fix
    evidence.unpopulated_columns = [c for c in declared if c not in populated]
    evidence.firmware_health_populated = [c for c in health if c in populated]
    return evidence


def _trial_duration_s(trial_dir: Path) -> float | None:
    """How long the trial lasted, from the manifest's own ``started``/``ended``."""
    try:
        trial = json.loads((trial_dir / "manifest.json").read_text(encoding="utf-8")).get("trial", {})
        started = datetime.fromisoformat(trial["started_ts"])
        ended = datetime.fromisoformat(trial["ended_ts"])
    except (OSError, json.JSONDecodeError, KeyError, TypeError, ValueError):
        return None
    seconds = (ended - started).total_seconds()
    return seconds if seconds > 0 else None


def _check_telemetry(trial_dir: Path, min_rows: int) -> tuple[Check, _TelemetryEvidence]:
    """Enough rows for a trial of this length, carrying state, with no long gap,
    recorded all the way to the end.

    A fixed row count cannot serve both a twelve-minute survey and a two-second
    parameter read, so the floor is the caller's ceiling or one row per second
    of trial, whichever is smaller. That floor is a weak instrument on its own -
    it is capped at ten rows, which a twelve-minute trial clears with one sample
    a minute - so three other things are asked of the file: that its rows carry
    vehicle state at all (a recorder that never connected writes beautifully
    regular empty rows), that no two consecutive rows are far apart (a sampler
    that stalled), and that the recording reaches the end of the trial (a
    recorder that connected and then died).
    """
    path = trial_dir / "telemetry.csv"
    if not path.is_file():
        return Check("telemetry.csv", False, "missing"), _TelemetryEvidence()
    try:
        evidence = _read_telemetry(trial_dir)
    except (OSError, csv.Error) as e:
        return Check("telemetry.csv", False, f"unreadable ({type(e).__name__}: {e})"), _TelemetryEvidence()

    rows, armed, last_t = evidence.rows, evidence.armed, evidence.last_t
    duration = _trial_duration_s(trial_dir)
    floor = min_rows if duration is None else max(1, min(min_rows, int(duration)))
    detail = f"{rows} rows, armed={armed}"
    if duration is not None:
        detail += f", spanning {last_t:.0f}s of a {duration:.0f}s trial"
    detail += f", worst gap {evidence.max_gap:.1f}s"
    if evidence.max_sample_age is not None:
        detail += f", worst sample age {evidence.max_sample_age:.1f}s"
    if not evidence.state_columns_present:
        detail += " (no state columns to check)"

    if rows < floor:
        return Check(
            "telemetry.csv",
            False,
            f"{rows} row(s), below the floor of {floor} for a {duration:.0f}s trial"
            if duration is not None
            else f"{rows} row(s), below the floor of {floor}",
        ), evidence
    if evidence.state_columns_present and not evidence.has_state:
        return Check(
            "telemetry.csv",
            False,
            f"{rows} row(s) and not one carries any vehicle state (position, mode, armed all empty) - "
            "the recorder ran but never connected to the drone",
        ), evidence
    if evidence.max_sample_age is not None and evidence.max_sample_age > MAX_TELEMETRY_GAP_S:
        return Check(
            "telemetry.csv",
            False,
            f"the recorder went {evidence.max_sample_age:.0f}s without a single fresh sample while still "
            f"writing rows ({detail}) - those rows repeat a held value, they are not observations of the "
            "aircraft",
        ), evidence
    if evidence.max_gap > MAX_TELEMETRY_GAP_S:
        return Check(
            "telemetry.csv",
            False,
            f"a {evidence.max_gap:.0f}s hole in the recording ending at t={evidence.max_gap_at:.0f}s "
            f"({detail}) - the recorder stopped sampling mid-trial",
        ), evidence
    if duration is not None and duration >= COVERAGE_MIN_TRIAL_S and last_t < MIN_TELEMETRY_COVERAGE * duration:
        return Check(
            "telemetry.csv",
            False,
            f"the recording stops {duration - last_t:.0f}s before the trial ends "
            f"({detail}) - the recorder died mid-trial",
        ), evidence
    return Check("telemetry.csv", True, detail), evidence


def _check_telemetry_single_vehicle(evidence: _TelemetryEvidence, vehicle_sysid: int) -> Check:
    """Is every row of ``telemetry.csv`` the SAME aircraft?

    The one check that would have caught the 2026-08 contamination on its first
    trial instead of its 472nd. Two ways of asking, both cheap:

    1. **By name.** If the file carries a ``sysid`` column, any value other than
       the trial's own vehicle is a foreign aircraft, stated outright. The
       recorder's historical schema has no such column - which is precisely why
       the defect was inexpressible in the artifact - but the system-ID-filtered
       companions do, and so should any future recorder.
    2. **By geometry.** Otherwise, count consecutive rows more than
       :data:`MAX_TELEMETRY_POSITION_JUMP_M` apart. A single vehicle at this row
       rate cannot produce one; two vehicles taking turns in one sample-and-hold
       recorder produce thousands (measured: 6 to 4,384 per trial).

    A file with no positions to compare (a T7 parameter read, a recorder that
    never connected) passes here and fails, if it should, in
    :func:`_check_telemetry` for the real reason.
    """
    name = "telemetry.csv single-vehicle"
    foreign = {s for s in evidence.sysids if s not in ("", str(vehicle_sysid))}
    if foreign:
        return Check(
            name,
            False,
            f"rows carry system ID(s) {', '.join(sorted(foreign))} as well as this trial's vehicle "
            f"({vehicle_sysid}) - the file describes more than one aircraft. The recorder's telemetry "
            "address is accepting every source on its port (see "
            "telemetry_recorder.is_shared_bind)",
        )
    if evidence.position_flips:
        return Check(
            name,
            False,
            f"{evidence.position_flips} consecutive row pair(s) more than {MAX_TELEMETRY_POSITION_JUMP_M:.0f} m "
            f"apart (worst {evidence.worst_position_jump_m:.0f} m at t={evidence.worst_position_jump_at:.0f}s) - "
            "no single aircraft moves that far between rows, so this file interleaves two vehicles. The "
            "recorder's telemetry address is accepting every source on its port (see "
            "telemetry_recorder.is_shared_bind); the sysid-tagged mavlink.jsonl is the authoritative stream",
        )
    detail = f"worst consecutive-row move {evidence.worst_position_jump_m:.1f} m"
    if evidence.sysids:
        detail += f"; sysid column carries only {', '.join(sorted(evidence.sysids))}"
    if evidence.worst_position_jump_m == 0.0 and not evidence.sysids:
        detail = "no position pairs to compare"
    return Check(name, True, detail)


def _check_telemetry_schema(evidence: _TelemetryEvidence) -> Check:
    """Does the file carry every column the data dictionary promises?

    Separate from :func:`_check_telemetry` so the answer is reported in its own
    right: a recording can be the right length, live and gap-free and still be
    missing a documented quantity, which is exactly how four always-blank
    columns shipped in every bundle this project has ever produced.

    Only asked of a file whose recorder genuinely connected. When no row
    carries any vehicle state at all, ``_check_telemetry`` has already failed
    the trial for the real reason, and listing twenty-three empty columns
    underneath it would bury it.
    """
    name = "telemetry.csv schema"
    if evidence.rows == 0:
        return Check(name, True, "no rows to check")
    if evidence.absent_columns:
        return Check(
            name,
            False,
            f"the header is missing {', '.join(evidence.absent_columns)} - Plan 19 §3b requires "
            f"{len(TELEMETRY_REQUIRED_COLUMNS)} populated columns",
        )
    if not evidence.has_state:
        return Check(name, True, "not checked - the recorder never connected (see telemetry.csv)")
    if evidence.unpopulated_columns:
        return Check(
            name,
            False,
            f"{', '.join(evidence.unpopulated_columns)} empty in every one of {evidence.rows} row(s) - "
            "the data dictionary promises these and the data does not carry them",
        )
    # Firmware-health columns are reported, not required: which of them this
    # autopilot filled is genuine provenance (ArduPilot fills both, PX4 neither),
    # but never a reason to degrade the bundle.
    health_note = (
        f"; firmware-health carried: {', '.join(evidence.firmware_health_populated)}"
        if evidence.firmware_health_populated
        else "; firmware-health (ekf_ok/geofence_ok) blank - not reported by this firmware"
    )
    return Check(name, True, f"all {len(TELEMETRY_REQUIRED_COLUMNS)} required columns populated{health_note}")


def _check_jsonl(trial_dir: Path, name: str) -> Check:
    path = trial_dir / name
    if not path.is_file():
        return Check(name, False, "missing")
    roles: dict[str, int] = {}
    count, bad = 0, 0
    try:
        with path.open(encoding="utf-8") as fh:
            for line in fh:
                if not line.strip():
                    continue
                try:
                    record = json.loads(line)
                except json.JSONDecodeError:
                    bad += 1
                    continue
                count += 1
                if isinstance(record, dict) and record.get("role"):
                    roles[record["role"]] = roles.get(record["role"], 0) + 1
    except OSError as e:
        return Check(name, False, f"unreadable ({type(e).__name__}: {e})")
    if bad:
        return Check(name, False, f"{bad} line(s) do not parse as JSON")
    if count == 0:
        return Check(name, False, "no records")
    detail = f"{count} record(s)" + (f", roles={dict(sorted(roles.items()))}" if roles else "")
    return Check(name, True, detail)


def _check_audit_slice(trial_dir: Path) -> Check:
    path = trial_dir / "audit_slice.csv"
    if not path.is_file():
        return Check("audit_slice.csv", False, "missing - was the harness given --audit-log?")
    try:
        rows = max(_count_lines(path) - 1, 0)
    except OSError as e:
        return Check("audit_slice.csv", False, f"unreadable ({type(e).__name__}: {e})")
    if rows == 0:
        return Check("audit_slice.csv", False, "header only - no server-side audit rows for this trial")
    return Check("audit_slice.csv", True, f"{rows} audit row(s)")


def _flew_per_events(trial_dir: Path) -> bool:
    """Did the derived event narrative record an arm or a takeoff?

    Third witness to the same fact, for a bundle whose ``mavlink.jsonl`` is
    absent and whose telemetry is empty. Never raises: unreadable events are
    simply no evidence.
    """
    path = trial_dir / "events.jsonl"
    if not path.is_file():
        return False
    try:
        with path.open(encoding="utf-8") as fh:
            for line in fh:
                if not line.strip():
                    continue
                try:
                    record = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if isinstance(record, dict) and record.get("category") in _FLEW_EVENT_CATEGORIES:
                    return True
    except OSError:
        return False
    return False


def _check_dataflash(trial_dir: Path, expected: bool) -> Check:
    logs = [p for p in trial_dir.iterdir() if p.is_file() and p.suffix.lower() in DATAFLASH_SUFFIXES]
    if logs:
        sizes = ", ".join(f"{p.name} ({p.stat().st_size} bytes)" for p in sorted(logs))
        return Check("dataflash", True, sizes)
    if expected:
        return Check(
            "dataflash",
            False,
            "the aircraft armed but no .BIN/.ulg was retained (check --dataflash-remote and "
            "LOG_FILE_DSRMROT=1 on the simulator)",
        )
    return Check("dataflash", True, "n/a - the aircraft never armed, so the autopilot wrote no log")


def verify_bundle(
    trial_dir: Path,
    *,
    require_transcript: bool = False,
    min_telemetry_rows: int = DEFAULT_MIN_TELEMETRY_ROWS,
    vehicle_sysid: int = 1,
    expect_dataflash: bool | None = None,
    verify_hashes: bool = True,
) -> BundleCheck:
    """Verify one trial's bundle on disk. See the module docstring.

    Args:
        trial_dir: the per-trial directory (``<run>/<mission>/trial_<n>/``).
        require_transcript: also require ``transcript.jsonl`` (LLM trials).
        min_telemetry_rows: ceiling on the ``telemetry.csv`` row floor; the
            floor applied is this or one row per second of trial, whichever is
            smaller (see :func:`_check_telemetry`).
        vehicle_sysid: the autopilot's MAVLink sysid, for the direction check.
        expect_dataflash: force the dataflash expectation. ``None`` (default)
            derives it from the evidence that the aircraft armed, in *any* of
            the telemetry, the vehicle's HEARTBEATs or the derived events.
        verify_hashes: re-compute each artifact's sha256 and compare it with the
            manifest. On by default; pass ``False`` only where the cost of
            re-reading very large logs matters more than the guarantee.

    Never raises. A verification that cannot run is itself reported as a failed
    check, because a verifier that throws would be one more silent failure.
    """
    trial_dir = Path(trial_dir)
    checks: list[Check] = []
    if not trial_dir.is_dir():
        return BundleCheck(trial_dir, [Check("trial directory", False, "missing")])

    telemetry_check, telemetry = _check_telemetry(trial_dir, min_telemetry_rows)
    mavlink = _read_mavlink(trial_dir)
    # Did this trial fly? Asked of every witness, because the failure being
    # guarded against is precisely one witness going silent: an empty telemetry
    # file used to mean "never armed", which waived the dataflash requirement on
    # a trial that flew and lost its autopilot log.
    flew = telemetry.armed or mavlink.vehicle_armed or _flew_per_events(trial_dir)

    checks.append(_check_tlog(trial_dir))
    checks.append(_check_mavlink_directions(mavlink, vehicle_sysid, flew=flew))
    checks.append(telemetry_check)
    checks.append(_check_telemetry_single_vehicle(telemetry, vehicle_sysid))
    checks.append(_check_telemetry_schema(telemetry))
    checks.append(_check_audit_slice(trial_dir))
    checks.append(_check_jsonl(trial_dir, "events.jsonl"))
    if require_transcript:
        checks.append(_check_jsonl(trial_dir, TRANSCRIPT_ARTIFACT))
    checks.append(_check_dataflash(trial_dir, flew if expect_dataflash is None else expect_dataflash))
    # Last: it asserts that every file above is listed, so it must run after
    # nothing else is going to be written.
    checks.append(_check_manifest(trial_dir, verify_hashes=verify_hashes))
    return BundleCheck(trial_dir, checks)
