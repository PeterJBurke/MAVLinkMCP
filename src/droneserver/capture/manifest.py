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
