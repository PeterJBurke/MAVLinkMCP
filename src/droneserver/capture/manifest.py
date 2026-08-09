"""Per-trial provenance manifest + dataflash-log retention (Plan 19 §3a, §6).

Every benchmark trial produces an ``out_dir`` full of artifacts (CSVs, logs,
the audit slice, screenshots, …). For the reproducibility package we need two
extra things per trial:

1. The vehicle's own **dataflash log** - ArduPilot ``.BIN`` (SITL or a real
   Cube) or PX4 ``.ulg`` - retained alongside the trial's other outputs, so
   the exact flight can be replayed in a ground station.
2. A **manifest.json** that pins down provenance: which model/firmware/sim
   produced this trial, when, on which host, plus a content hash + size of
   every artifact in the directory. The manifest is what lets a third party
   verify the archive was not altered and reproduce the conditions.

This module is deliberately dumb: it **serializes what the orchestrator gives
it and hashes what it finds on disk**. It never autodetects provenance (that
would let a wrong-but-plausible value slip into the permanent record) and it
never fabricates a git commit or a firmware version. The one convenience it
offers - ``gather_versions()`` - only reads installed package metadata, which
is a fact about the running environment, not a claim about the aircraft.

Manifest schema (``droneserver.manifest/1``)::

    {
      "schema": "droneserver.manifest/1",
      "generated_ts": "2026-08-08T21:34:59.123456+00:00",  # UTC ISO-8601
      "trial": { ... },        # the caller's `meta`, verbatim (see below)
      "artifacts": [           # every file in out_dir except manifest.json
        {"name": "missions.csv", "sha256": "…", "bytes": 1234},
        {"name": "T3.BIN",       "sha256": "…", "bytes": 987654},
        ...
      ]
    }

Provenance fields the orchestrator is expected to supply in ``meta`` (Plan 19
§6). This module does not enforce their presence - it records exactly what it
is handed - but the reproducibility package expects all of them:

    run_id            unique id for the whole benchmark run
    mission_id        mission/task identifier (e.g. "T3")
    trial_idx         1-based trial number within the mission
    model             LLM model name the client reported (e.g. "claude-opus-4-8")
    model_version     specific model version/snapshot string
    provider          LLM provider (e.g. "anthropic", "openai")
    decoding          dict: sampling params (temperature, top_p, max_tokens, seed, …)
    prompt_set_hash   hash of the exact prompt/system-prompt set in force
    firmware          autopilot firmware family (e.g. "ArduCopter", "PX4")
    firmware_version  autopilot firmware version string
    mavsdk_version    MAVSDK / MAVLink stack version in use
    droneserver_commit git commit of the droneserver code (orchestrator supplies)
    sim               simulator identifier (e.g. "ArduPilot SITL", "gazebo")
    sim_params        dict: home lat/lon/alt, wind, frame, speedup, …
    host              hostname where the orchestrator/client ran
    sitl_host         hostname/address where the sim/vehicle ran
    clock_offset_ms   measured client<->sim clock offset, milliseconds
    started_ts        trial start, UTC ISO-8601
    ended_ts          trial end, UTC ISO-8601
"""

import hashlib
import json
import shutil
from datetime import datetime, timezone
from pathlib import Path

SCHEMA = "droneserver.manifest/1"

#: Dataflash-log suffixes we recognise, in no particular order. ArduPilot logs
#: are ``.BIN`` (upper-case on the SD card, sometimes lower after copying);
#: PX4 logs are ``.ulg``. Matching is case-insensitive on the suffix.
_ARDUPILOT_SUFFIXES = {".bin"}
_PX4_SUFFIXES = {".ulg"}
_LOG_SUFFIXES = _ARDUPILOT_SUFFIXES | _PX4_SUFFIXES

_CHUNK = 1 << 20  # 1 MiB streaming read; dataflash logs can be large.


def sha256_file(path: Path) -> str:
    """Return the hex SHA-256 of ``path``, read in streaming chunks."""
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(_CHUNK), b""):
            h.update(chunk)
    return h.hexdigest()


def retain_dataflash(src_log_dir: Path, out_dir: Path, trial_name: str) -> Path | None:
    """Copy the newest dataflash log from ``src_log_dir`` into ``out_dir``.

    Finds the most recently modified ArduPilot ``.BIN``/``.bin`` (or PX4
    ``.ulg``) in ``src_log_dir`` and copies it - preserving metadata, never
    moving or deleting the source - to ``out_dir/{trial_name}<suffix>``, where
    ``<suffix>`` is ``.BIN`` for ArduPilot logs and ``.ulg`` for PX4 logs.

    Returns the destination path, or ``None`` if ``src_log_dir`` is missing,
    is not a directory, or contains no recognised log.
    """
    src_log_dir = Path(src_log_dir)
    if not src_log_dir.is_dir():
        return None

    candidates = [
        p for p in src_log_dir.iterdir()
        if p.is_file() and p.suffix.lower() in _LOG_SUFFIXES
    ]
    if not candidates:
        return None

    # Newest by mtime. Tie-break on name for determinism if mtimes collide.
    newest = max(candidates, key=lambda p: (p.stat().st_mtime, p.name))

    # ArduPilot logs are canonicalised to .BIN; PX4 keep .ulg.
    dest_suffix = ".ulg" if newest.suffix.lower() in _PX4_SUFFIXES else ".BIN"

    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    dest = out_dir / f"{trial_name}{dest_suffix}"
    shutil.copy2(newest, dest)
    return dest


class RemoteDataflashError(RuntimeError):
    """The remote log directory could not be listed or the copy failed."""


def parse_remote_spec(spec: str) -> tuple[str, str]:
    """Split ``user@host:/path/to/logs`` into ``(ssh_target, directory)``.

    Raises ``ValueError`` when the spec has no ``:`` separator, because a bare
    path would silently be treated as local and the trial would end up with the
    wrong machine's logs (or none, reported as success).
    """
    target, sep, directory = spec.partition(":")
    if not sep or not target or not directory:
        raise ValueError(f"remote dataflash spec must be host:/path, got {spec!r}")
    return target, directory


def retain_remote_dataflash(
    spec: str,
    out_dir: Path,
    trial_name: str,
    *,
    min_mtime: float | None = None,
    max_bytes: int | None = None,
    timeout_s: float = 300.0,
    ssh: str = "ssh",
) -> Path | None:
    """Copy the newest dataflash log from a *remote* log directory.

    The simulator writes its dataflash log on the machine running it, which for
    the SITL campaign is not the machine running the harness - so the local
    :func:`retain_dataflash` has nothing to find. This fetches it over SSH
    instead. It is deliberately conservative, because the failure mode that
    matters is not "no log" but "the wrong log, silently":

    - ``min_mtime`` (normally the trial start time) rejects a log that does not
      belong to this trial. Where the filesystem records a birth time the log
      must have been *created* during the trial; otherwise it must at least
      have been *written* during it. The stricter birth-time test matters
      because the autopilot keeps its log file open past disarm: a mission that
      never armed would otherwise be handed the previous flight's still-growing
      ``.BIN``, with the manifest swearing to it. **A mission that does not arm
      correctly retains nothing.**
    - ``max_bytes`` rejects an implausibly large log rather than spending
      minutes copying, e.g., a continuously-logging session's multi-gigabyte
      file.

    Returns the destination path, or ``None`` when no log qualifies. Raises
    :class:`RemoteDataflashError` if the remote cannot be reached or the copy
    fails - that is a broken capture setup, not an empty result, and the caller
    should see the difference.
    """
    import subprocess

    target, directory = parse_remote_spec(spec)
    # One round trip: the newest matching file as "birth mtime size path".
    # stat rather than find -printf, because find's %B@ is unsupported on many
    # builds (it returns -1) while stat's %W works wherever the filesystem
    # records a birth time, and reports 0 where it does not.
    listing_cmd = (
        f"find {directory} -maxdepth 1 -type f "
        r"\( -iname '*.BIN' -o -iname '*.ulg' \) -print0 "
        r"| xargs -0 -r stat -c '%W %Y %s %n' | sort -k2 -rn | head -1"
    )
    try:
        proc = subprocess.run(
            [ssh, "-o", "BatchMode=yes", target, listing_cmd],
            capture_output=True, text=True, timeout=timeout_s, check=False,
        )
    except (OSError, subprocess.SubprocessError) as e:
        raise RemoteDataflashError(f"listing {spec} failed: {type(e).__name__}: {e}") from e
    if proc.returncode != 0:
        raise RemoteDataflashError(f"listing {spec} failed (rc={proc.returncode}): {proc.stderr.strip()}")

    line = proc.stdout.strip()
    if not line:
        return None
    try:
        birth_s, mtime_s, size_s, remote_path = line.split(None, 3)
        birth, mtime, size = float(birth_s), float(mtime_s), int(size_s)
    except ValueError as e:
        raise RemoteDataflashError(f"unparsable listing from {spec}: {line!r}") from e

    if min_mtime is not None:
        # birth <= 0 means the filesystem does not record one; fall back to the
        # weaker "written during the trial" test rather than refusing outright.
        stamp = birth if birth > 0 else mtime
        if stamp < min_mtime:
            return None
    if max_bytes is not None and size > max_bytes:
        return None

    suffix = ".ulg" if remote_path.lower().endswith(".ulg") else ".BIN"
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    dest = out_dir / f"{trial_name}{suffix}"
    try:
        copy = subprocess.run(
            ["scp", "-q", "-o", "BatchMode=yes", f"{target}:{remote_path}", str(dest)],
            capture_output=True, text=True, timeout=timeout_s, check=False,
        )
    except (OSError, subprocess.SubprocessError) as e:
        raise RemoteDataflashError(f"copying {remote_path} failed: {type(e).__name__}: {e}") from e
    if copy.returncode != 0:
        raise RemoteDataflashError(f"copying {remote_path} failed (rc={copy.returncode}): {copy.stderr.strip()}")
    return dest


def write_manifest(out_dir: Path, meta: dict) -> Path:
    """Write ``out_dir/manifest.json`` and return its path.

    Scans ``out_dir`` (recursively) for every file except ``manifest.json``
    itself and records each as ``{"name", "sha256", "bytes"}`` under
    ``artifacts``. The caller's ``meta`` is passed through verbatim under
    ``trial``; this function adds only ``schema`` and ``generated_ts``.
    """
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    manifest_path = out_dir / "manifest.json"

    artifacts = []
    for path in sorted(out_dir.rglob("*")):
        if not path.is_file():
            continue
        if path == manifest_path:
            continue
        rel = path.relative_to(out_dir).as_posix()
        artifacts.append({
            "name": rel,
            "sha256": sha256_file(path),
            "bytes": path.stat().st_size,
        })

    document = {
        "schema": SCHEMA,
        "generated_ts": datetime.now(timezone.utc).isoformat(),
        "trial": meta,
        "artifacts": artifacts,
    }
    manifest_path.write_text(
        json.dumps(document, indent=2, ensure_ascii=False, default=str) + "\n",
        encoding="utf-8",
    )
    return manifest_path


def annotate_manifest(out_dir: Path, extra: dict) -> Path | None:
    """Merge ``extra`` into an existing manifest's ``trial`` block.

    Used for facts that can only be established *after* the manifest exists -
    principally ``capture_status``, which is decided by verifying the bundle,
    and verification includes checking that the manifest lists every file. The
    manifest never hashes itself, so re-writing it cannot invalidate any hash
    it records; nothing else in the document is touched.

    Returns the manifest path, or ``None`` when there is no manifest to
    annotate or it cannot be read (never raises: an annotation failure must not
    take a trial's artifacts with it).
    """
    manifest_path = Path(out_dir) / "manifest.json"
    try:
        document = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    trial = document.get("trial")
    if not isinstance(trial, dict):
        trial = {}
    trial.update(extra)
    document["trial"] = trial
    try:
        manifest_path.write_text(
            json.dumps(document, indent=2, ensure_ascii=False, default=str) + "\n",
            encoding="utf-8",
        )
    except OSError:
        return None
    return manifest_path


def gather_versions() -> dict:
    """Best-effort installed versions of the MAVLink stack, for convenience.

    Returns ``{}`` on any failure. The orchestrator MAY merge the result into
    its ``meta`` - these are facts about the running Python environment, not
    claims about the aircraft. Never used to fabricate firmware or git values.
    """
    try:
        from importlib import metadata
    except Exception:
        return {}

    out: dict[str, str] = {}
    for dist, key in (("mavsdk", "mavsdk_version"), ("pymavlink", "pymavlink_version")):
        try:
            out[key] = metadata.version(dist)
        except Exception:
            continue
    return out
