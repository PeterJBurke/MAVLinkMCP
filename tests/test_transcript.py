"""Unit tests for the LLM transcript logger (Plan 19 capture spec §1c)."""

import json

from droneserver.capture.transcript import (
    MAX_VALUE_CHARS,
    TranscriptWriter,
    redact,
)


def _read_lines(path):
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def test_small_conversation_roundtrips(tmp_path):
    w = TranscriptWriter(tmp_path, t0=1000.0)
    assert w.t0 == 1000.0

    w.turn("system", "You are a drone-control assistant.", model="test-model")
    w.turn("user", "Take off to 10 meters.")
    w.turn(
        "assistant",
        "I'll take off now.",
        tool_calls=[
            {
                "call_id": "c1",
                "tool": "takeoff",
                "args": {"altitude": 10, "api_key": "sk-SECRET", "confirm_token": "tok-XYZ"},
            }
        ],
        model="test-model",
        params={"temperature": 0.0, "top_p": 1.0, "seed": 42, "max_tokens": 256},
        usage={"prompt_tokens": 30, "completion_tokens": 5},
    )
    w.turn("tool", tool_result={"status": "success", "token": "leak-me"})
    w.close()

    rows = _read_lines(tmp_path / "transcript.jsonl")
    assert len(rows) == 4

    # turn_idx increments 0..n
    assert [r["turn_idx"] for r in rows] == [0, 1, 2, 3]

    # every row is valid JSON (already parsed) with the required keys
    for r in rows:
        assert "ts" in r and isinstance(r["ts"], str)
        assert "t_rel_s" in r and isinstance(r["t_rel_s"], (int, float))

    # roles/content preserved
    assert rows[0]["role"] == "system"
    assert rows[0]["content"] == "You are a drone-control assistant."
    assert rows[1]["role"] == "user"
    assert rows[1]["content"] == "Take off to 10 meters."
    assert rows[2]["role"] == "assistant"
    assert rows[2]["content"] == "I'll take off now."

    # tool_calls round-trip; non-secret args preserved
    tc = rows[2]["tool_calls"]
    assert tc[0]["call_id"] == "c1"
    assert tc[0]["tool"] == "takeoff"
    assert tc[0]["args"]["altitude"] == 10

    # model/params/usage carried through
    assert rows[2]["model"] == "test-model"
    assert rows[2]["params"]["seed"] == 42
    assert rows[2]["usage"] == {"prompt_tokens": 30, "completion_tokens": 5}

    # tool_result round-trips
    assert rows[3]["role"] == "tool"
    assert rows[3]["tool_result"]["status"] == "success"

    # secrets are redacted wherever they appear
    assert tc[0]["args"]["api_key"] == "<redacted>"
    assert tc[0]["args"]["confirm_token"] == "<redacted>"
    assert rows[3]["tool_result"]["token"] == "<redacted>"
    assert "sk-SECRET" not in (tmp_path / "transcript.jsonl").read_text()
    assert "tok-XYZ" not in (tmp_path / "transcript.jsonl").read_text()
    assert "leak-me" not in (tmp_path / "transcript.jsonl").read_text()

    # null fields where nothing supplied
    assert rows[0]["tool_calls"] is None
    assert rows[0]["tool_result"] is None
    assert rows[1]["usage"] is None


def test_nested_secret_is_redacted():
    out = redact({"outer": {"nested": {"OPENAI_API_KEY": "sk-abc"}, "keep": 1}})
    assert out["outer"]["nested"]["OPENAI_API_KEY"] == "<redacted>"
    assert out["outer"]["keep"] == 1


def test_long_value_truncated():
    long = "x" * (MAX_VALUE_CHARS + 10)
    assert redact(long) == f"<truncated {len(long)} chars>"
    short = "y" * 10
    assert redact(short) == short


def test_default_t0_is_set_and_close_is_idempotent(tmp_path):
    w = TranscriptWriter(tmp_path)
    assert isinstance(w.t0, float)
    w.turn("user", "hi")
    w.close()
    w.close()  # idempotent
    # turns after close are dropped, not raised
    w.turn("user", "should be ignored")
    rows = _read_lines(tmp_path / "transcript.jsonl")
    assert len(rows) == 1
