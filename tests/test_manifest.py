"""Unit tests for droneserver.capture.manifest (Plan 19 §3a, §6)."""

import json
import os
from pathlib import Path

from droneserver.capture.manifest import (
    SCHEMA,
    RemoteDataflashError,
    gather_versions,
    parse_remote_spec,
    retain_dataflash,
    retain_remote_dataflash,
    sha256_file,
    write_manifest,
)


def _touch(path, data=b"x", mtime=None):
    path.write_bytes(data)
    if mtime is not None:
        os.utime(path, (mtime, mtime))
    return path


def test_retain_dataflash_picks_newest_bin(tmp_path):
    src = tmp_path / "logs"
    src.mkdir()
    out = tmp_path / "trial"
    old = _touch(src / "1.BIN", b"old-flight", mtime=1000)
    new = _touch(src / "2.BIN", b"new-flight", mtime=2000)

    dest = retain_dataflash(src, out, "T3")

    assert dest == out / "T3.BIN"
    assert dest.exists()
    # The NEWER file's contents were copied.
    assert dest.read_bytes() == new.read_bytes()
    # Source is untouched (not moved/deleted).
    assert old.exists() and new.exists()


def test_retain_dataflash_accepts_lowercase_bin(tmp_path):
    src = tmp_path / "logs"
    src.mkdir()
    _touch(src / "flight.bin", b"lower", mtime=5000)
    dest = retain_dataflash(src, tmp_path / "out", "T1")
    assert dest == tmp_path / "out" / "T1.BIN"
    assert dest.read_bytes() == b"lower"


def test_retain_dataflash_picks_up_ulg(tmp_path):
    src = tmp_path / "logs"
    src.mkdir()
    _touch(src / "log001.ulg", b"px4-log", mtime=3000)
    dest = retain_dataflash(src, tmp_path / "out", "T7")
    assert dest == tmp_path / "out" / "T7.ulg"
    assert dest.read_bytes() == b"px4-log"


def test_retain_dataflash_prefers_newest_across_types(tmp_path):
    src = tmp_path / "logs"
    src.mkdir()
    _touch(src / "old.BIN", b"bin", mtime=1000)
    _touch(src / "new.ulg", b"ulg", mtime=9000)
    dest = retain_dataflash(src, tmp_path / "out", "T2")
    assert dest == tmp_path / "out" / "T2.ulg"


def test_retain_dataflash_empty_dir_returns_none(tmp_path):
    src = tmp_path / "logs"
    src.mkdir()
    assert retain_dataflash(src, tmp_path / "out", "T1") is None


def test_retain_dataflash_missing_dir_returns_none(tmp_path):
    assert retain_dataflash(tmp_path / "nope", tmp_path / "out", "T1") is None


def test_retain_dataflash_ignores_unrelated_files(tmp_path):
    src = tmp_path / "logs"
    src.mkdir()
    _touch(src / "notes.txt", b"nope", mtime=9999)
    _touch(src / "params.parm", b"nope", mtime=9998)
    assert retain_dataflash(src, tmp_path / "out", "T1") is None


def _sample_meta():
    return {
        "run_id": "run-2026-08-08-abc",
        "mission_id": "T3",
        "trial_idx": 1,
        "model": "claude-opus-4-8",
        "model_version": "claude-opus-4-8-20260101",
        "provider": "anthropic",
        "decoding": {"temperature": 0.0, "top_p": 1.0, "max_tokens": 1024, "seed": 7},
        "prompt_set_hash": "deadbeef",
        "firmware": "ArduCopter",
        "firmware_version": "4.5.0",
        "mavsdk_version": "2.0.0",
        "droneserver_commit": "4113f9e",
        "sim": "ArduPilot SITL",
        "sim_params": {"home": [-35.36, 149.16, 584.0], "wind": 0.0,
                       "frame": "quad", "speedup": 1.0},
        "host": "llmuavdev",
        "sitl_host": "llmuavsitl",
        "clock_offset_ms": 12.5,
        "started_ts": "2026-08-08T21:00:00+00:00",
        "ended_ts": "2026-08-08T21:03:00+00:00",
    }


def test_write_manifest_structure_and_hashes(tmp_path):
    out = tmp_path / "trial"
    out.mkdir()
    f1 = out / "missions.csv"
    f1.write_text("a,b,c\n1,2,3\n", encoding="utf-8")
    f2 = out / "T3.BIN"
    f2.write_bytes(b"\x00\x01\x02flight-bytes")

    meta = _sample_meta()
    path = write_manifest(out, meta)

    assert path == out / "manifest.json"
    doc = json.loads(path.read_text(encoding="utf-8"))

    assert doc["schema"] == SCHEMA == "droneserver.manifest/1"
    assert doc["trial"] == meta
    assert "generated_ts" in doc and doc["generated_ts"].endswith("+00:00")

    by_name = {a["name"]: a for a in doc["artifacts"]}
    # manifest.json itself is never listed.
    assert "manifest.json" not in by_name
    assert set(by_name) == {"missions.csv", "T3.BIN"}

    for name, f in (("missions.csv", f1), ("T3.BIN", f2)):
        assert by_name[name]["sha256"] == sha256_file(f)
        assert by_name[name]["bytes"] == f.stat().st_size


def test_write_manifest_excludes_itself_on_rewrite(tmp_path):
    out = tmp_path / "trial"
    out.mkdir()
    (out / "data.csv").write_text("x\n", encoding="utf-8")

    write_manifest(out, _sample_meta())
    # A second pass must still not list manifest.json among the artifacts.
    path = write_manifest(out, _sample_meta())
    doc = json.loads(path.read_text(encoding="utf-8"))
    names = {a["name"] for a in doc["artifacts"]}
    assert names == {"data.csv"}


def test_write_manifest_recurses_subdirs(tmp_path):
    out = tmp_path / "trial"
    (out / "sub").mkdir(parents=True)
    (out / "sub" / "nested.log").write_text("deep\n", encoding="utf-8")

    doc = json.loads(write_manifest(out, _sample_meta()).read_text(encoding="utf-8"))
    names = {a["name"] for a in doc["artifacts"]}
    assert "sub/nested.log" in names


def test_sha256_file_matches_hashlib(tmp_path):
    import hashlib
    p = tmp_path / "blob"
    data = b"the quick brown fox" * 100
    p.write_bytes(data)
    assert sha256_file(p) == hashlib.sha256(data).hexdigest()


def test_gather_versions_returns_dict():
    result = gather_versions()
    assert isinstance(result, dict)
    # Never fabricates: values, if present, are strings from package metadata.
    for v in result.values():
        assert isinstance(v, str)


# -- remote dataflash retention (the SITL-on-another-machine case) -----------


class _FakeCompleted:
    def __init__(self, returncode=0, stdout="", stderr=""):
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr


def _fake_subprocess(monkeypatch, listing, *, scp_rc=0, calls=None):
    """Patch subprocess.run so ssh returns ``listing`` and scp writes a file."""
    import subprocess

    def run(cmd, **kwargs):
        if calls is not None:
            calls.append(cmd)
        if cmd[0] == "scp":
            if scp_rc == 0:
                Path(cmd[-1]).write_bytes(b"BIN-CONTENT")
            return _FakeCompleted(returncode=scp_rc, stderr="permission denied")
        return _FakeCompleted(stdout=listing)

    monkeypatch.setattr(subprocess, "run", run)


def test_remote_spec_must_name_a_host():
    import pytest

    with pytest.raises(ValueError):
        parse_remote_spec("/just/a/local/path")
    assert parse_remote_spec("llmuavsitl:/logs") == ("llmuavsitl", "/logs")


def test_retain_remote_copies_the_newest_log(tmp_path, monkeypatch):
    _fake_subprocess(monkeypatch, "1786000000.0 1786000000.5 4096 /logs/00000007.BIN\n")
    dest = retain_remote_dataflash("sitl:/logs", tmp_path, "T1_t1", min_mtime=1785999999.0)
    assert dest is not None and dest.name == "T1_t1.BIN"
    assert dest.read_bytes() == b"BIN-CONTENT"


def test_retain_remote_refuses_a_log_older_than_the_trial(tmp_path, monkeypatch):
    """The dangerous failure: a mission that never armed inheriting the
    previous flight's log, with the manifest vouching for it."""
    calls = []
    _fake_subprocess(monkeypatch, "1786000000.0 1786000000.0 4096 /logs/00000007.BIN\n", calls=calls)
    dest = retain_remote_dataflash("sitl:/logs", tmp_path, "T1_t1", min_mtime=1786000060.0)
    assert dest is None
    assert not any(c[0] == "scp" for c in calls), "must not copy a pre-trial log"


def test_retain_remote_skips_an_oversized_log(tmp_path, monkeypatch):
    _fake_subprocess(monkeypatch, "1786000000.0 1786000000.0 999999999 /logs/00000007.BIN\n")
    assert retain_remote_dataflash("sitl:/logs", tmp_path, "T1_t1", max_bytes=1024) is None


def test_retain_remote_returns_none_when_no_logs_exist(tmp_path, monkeypatch):
    _fake_subprocess(monkeypatch, "")
    assert retain_remote_dataflash("sitl:/logs", tmp_path, "T1_t1") is None


def test_retain_remote_raises_when_the_copy_fails(tmp_path, monkeypatch):
    """A broken capture setup must be loud, not silently log-less."""
    import pytest

    _fake_subprocess(monkeypatch, "1786000000.0 1786000000.0 4096 /logs/7.BIN\n", scp_rc=1)
    with pytest.raises(RemoteDataflashError):
        retain_remote_dataflash("sitl:/logs", tmp_path, "T1_t1")


def test_retain_remote_keeps_the_px4_suffix(tmp_path, monkeypatch):
    _fake_subprocess(monkeypatch, "1786000000.0 1786000000.0 4096 /logs/log001.ulg\n")
    dest = retain_remote_dataflash("sitl:/logs", tmp_path, "T2_t1")
    assert dest is not None and dest.name == "T2_t1.ulg"


def test_retain_remote_refuses_a_log_that_only_grew_during_the_trial(tmp_path, monkeypatch):
    """The autopilot keeps its log open past disarm, so a still-growing file
    from an earlier flight has a fresh mtime. Birth time is what settles it."""
    calls = []
    _fake_subprocess(monkeypatch, "1786000000.0 1786000300.0 4096 /logs/00000007.BIN\n", calls=calls)
    dest = retain_remote_dataflash("sitl:/logs", tmp_path, "T8_t1", min_mtime=1786000200.0)
    assert dest is None, "a log born before the trial is not this trial's log"
    assert not any(c[0] == "scp" for c in calls)


def test_retain_remote_falls_back_to_mtime_without_a_birth_time(tmp_path, monkeypatch):
    """Filesystems that report birth 0 still get the weaker mtime test."""
    _fake_subprocess(monkeypatch, "0 1786000300.0 4096 /logs/00000007.BIN\n")
    dest = retain_remote_dataflash("sitl:/logs", tmp_path, "T1_t1", min_mtime=1786000200.0)
    assert dest is not None and dest.name == "T1_t1.BIN"
