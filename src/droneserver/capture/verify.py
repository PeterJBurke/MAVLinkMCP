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
``manifest.json``            parses, and **lists every other file in the
                             directory** at its true size. A file written after
                             the manifest is a file nobody can verify.
``mavlink.tlog``             non-empty.
``mavlink.jsonl``            carries **both directions**: at least one message
                             from the vehicle's sysid and at least one from any
                             other (the ground-station/server side). This is the
                             check that catches a tap wired to a telemetry-only
                             path - the exact shape of blocker B-6, where a
                             plain MAVProxy ``--out`` forward yielded a
                             perfectly valid-looking tlog containing no commands
                             at all.
``telemetry.csv``            more than ``min_rows`` data rows. Catches a MavSDK
                             recorder that failed to connect and wrote a header.
``audit_slice.csv``          present and non-empty (it is absent entirely when
                             the harness was run without ``--audit-log``, which
                             is worth being told about rather than discovering
                             at analysis time).
``events.jsonl``             present, and every line parses as JSON.
``transcript.jsonl``         when required (LLM-in-the-loop trials): present,
                             every line parses, and the role counts are reported.
dataflash ``.BIN`` / ``.ulg``  required **only if the telemetry shows the
                             aircraft armed**. A mission that never arms writes
                             no autopilot log, and demanding one there would
                             mean copying somebody else's flight; but a trial
                             that *did* fly and kept no log is degraded.
===========================  ==================================================

Nothing here re-hashes the artifacts: ``write_manifest`` hashed them moments
earlier from the same bytes. What is checked instead is *coverage* - that the
manifest knows about every file - and size agreement, which is what catches an
artifact appearing after the manifest was sealed.

This module is pure stdlib and reads only. It imports neither pymavlink nor
mavsdk, so it can be used (and tested) anywhere.
"""

import csv
import json
from dataclasses import dataclass, field
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

#: Default floor for ``telemetry.csv``. At the 10 Hz Plan 19 asks for this is
#: one second of flight - low enough that a legitimately short trial (T7-T9
#: never leave the ground) is not reported as degraded, high enough that a
#: recorder which never connected always is.
DEFAULT_MIN_TELEMETRY_ROWS = 10


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


def _check_manifest(trial_dir: Path) -> Check:
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
    listed = {a.get("name"): a.get("bytes") for a in artifacts if isinstance(a, dict)}

    on_disk = {
        p.relative_to(trial_dir).as_posix(): p.stat().st_size
        for p in sorted(trial_dir.rglob("*"))
        if p.is_file() and p.name != "manifest.json"
    }
    unlisted = sorted(set(on_disk) - set(listed))
    if unlisted:
        return Check("manifest.json", False, f"does not list {', '.join(unlisted)}")
    stale = sorted(n for n, size in on_disk.items() if listed.get(n) != size)
    if stale:
        return Check("manifest.json", False, f"recorded size differs on {', '.join(stale)}")
    return Check("manifest.json", True, f"lists {len(listed)} artifact(s)")


def _check_tlog(trial_dir: Path) -> Check:
    path = trial_dir / "mavlink.tlog"
    if not path.is_file():
        return Check("mavlink.tlog", False, "missing")
    size = path.stat().st_size
    if size == 0:
        return Check("mavlink.tlog", False, "empty - the wire tap heard nothing")
    return Check("mavlink.tlog", True, f"{size} bytes")


def _check_mavlink_directions(trial_dir: Path, vehicle_sysid: int) -> Check:
    """Both halves of the link, or the capture is a telemetry recording.

    ``direction`` is the tap's own label (vehicle sysid -> ``recv``, anything
    else -> ``sent``); the sysids are counted alongside so the detail line says
    *who* was heard, not merely that two labels appeared.
    """
    path = trial_dir / "mavlink.jsonl"
    if not path.is_file():
        return Check("mavlink.jsonl", False, "missing")
    directions: dict[str, int] = {}
    sysids: dict[int, int] = {}
    bad_lines = 0
    try:
        with path.open(encoding="utf-8") as fh:
            for line in fh:
                if not line.strip():
                    continue
                try:
                    record = json.loads(line)
                except json.JSONDecodeError:
                    bad_lines += 1
                    continue
                directions[record.get("direction", "?")] = directions.get(record.get("direction", "?"), 0) + 1
                sysid = record.get("sysid")
                if isinstance(sysid, int):
                    sysids[sysid] = sysids.get(sysid, 0) + 1
    except OSError as e:
        return Check("mavlink.jsonl", False, f"unreadable ({type(e).__name__}: {e})")

    recv, sent = directions.get("recv", 0), directions.get("sent", 0)
    detail = f"recv={recv} sent={sent} sysids={dict(sorted(sysids.items()))}"
    if bad_lines:
        detail += f" ({bad_lines} unparsable line(s))"
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
    return Check("mavlink.jsonl", True, detail)


def _read_telemetry(trial_dir: Path) -> tuple[int, bool]:
    """``(data row count, did any row report the aircraft armed)``."""
    path = trial_dir / "telemetry.csv"
    rows, armed = 0, False
    with path.open(newline="", encoding="utf-8") as fh:
        for record in csv.DictReader(fh):
            rows += 1
            if str(record.get("armed", "")).strip().lower() in ("true", "1"):
                armed = True
    return rows, armed


def _check_telemetry(trial_dir: Path, min_rows: int) -> tuple[Check, bool]:
    path = trial_dir / "telemetry.csv"
    if not path.is_file():
        return Check("telemetry.csv", False, "missing"), False
    try:
        rows, armed = _read_telemetry(trial_dir)
    except (OSError, csv.Error) as e:
        return Check("telemetry.csv", False, f"unreadable ({type(e).__name__}: {e})"), False
    if rows < min_rows:
        return Check("telemetry.csv", False, f"{rows} row(s), below the floor of {min_rows}"), armed
    return Check("telemetry.csv", True, f"{rows} rows, armed={armed}"), armed


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
) -> BundleCheck:
    """Verify one trial's bundle on disk. See the module docstring.

    Args:
        trial_dir: the per-trial directory (``<run>/<mission>/trial_<n>/``).
        require_transcript: also require ``transcript.jsonl`` (LLM trials).
        min_telemetry_rows: floor for ``telemetry.csv`` data rows.
        vehicle_sysid: the autopilot's MAVLink sysid, for the direction check.
        expect_dataflash: force the dataflash expectation. ``None`` (default)
            derives it from the telemetry: expected iff the aircraft armed.

    Never raises. A verification that cannot run is itself reported as a failed
    check, because a verifier that throws would be one more silent failure.
    """
    trial_dir = Path(trial_dir)
    checks: list[Check] = []
    if not trial_dir.is_dir():
        return BundleCheck(trial_dir, [Check("trial directory", False, "missing")])

    telemetry_check, armed = _check_telemetry(trial_dir, min_telemetry_rows)

    checks.append(_check_tlog(trial_dir))
    checks.append(_check_mavlink_directions(trial_dir, vehicle_sysid))
    checks.append(telemetry_check)
    checks.append(_check_audit_slice(trial_dir))
    checks.append(_check_jsonl(trial_dir, "events.jsonl"))
    if require_transcript:
        checks.append(_check_jsonl(trial_dir, TRANSCRIPT_ARTIFACT))
    checks.append(_check_dataflash(trial_dir, armed if expect_dataflash is None else expect_dataflash))
    # Last: it asserts that every file above is listed, so it must run after
    # nothing else is going to be written.
    checks.append(_check_manifest(trial_dir))
    return BundleCheck(trial_dir, checks)
