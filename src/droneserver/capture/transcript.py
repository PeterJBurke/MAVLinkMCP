"""Full LLM conversation transcript logger for the reproducibility package.

One JSON object per turn, one line, append-only, never rewritten
(Plan 19 capture spec §1c). The file ``transcript.jsonl`` records the entire
model conversation for a trial: the system prompt, the user mission prompt,
each assistant turn (reasoning/answer + any tool calls it requested), and the
tool results returned to the model. Written next to the other per-trial
recorders and clock-aligned to a shared ``t0`` so the transcript, the audit
log, and the flight telemetry can be laid on one timeline.

This file is destined for a PUBLIC Zenodo deposit, so it must never carry
secrets. Every turn is passed through a recursive redactor that scrubs any
key named like ``api_key``/``key``/``token``/``confirm_token`` (see
``REDACTED_KEYS``) and truncates over-long values.

Schema (one object per line)::

    {
      "ts": "2026-08-06T21:34:59.123456+00:00",  # UTC ISO-8601, wall clock
      "t_rel_s": 12.345,          # seconds since t0 (shared recorder clock)
      "turn_idx": 0,              # auto-incrementing from 0, per writer
      "role": "assistant",        # "system"|"user"|"assistant"|"tool"
      "content": "…"|null,        # message text, redacted (null if none)
      "tool_calls": [             # calls the model requested this turn, or null
        {"call_id": "…", "tool": "takeoff", "args": {…}}   # args redacted
      ],
      "tool_result": {…}|null,    # result returned to the model, redacted
      "model": "…"|null,          # model id
      "params": {…}|null,         # decoding settings (temperature/top_p/seed/…)
      "usage": {                  # token accounting if available, else null
        "prompt_tokens": 1234, "completion_tokens": 56
      }
    }

The schema is STABLE: fields may be added, never removed or repurposed.
"""

import json
import os
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

SCHEMA = "droneserver.transcript/1"

#: Keys whose values are never written verbatim (matched case-insensitively as
#: a substring, so ``openai_api_key`` and ``confirm_token`` are both caught).
#: Mirrors safety/audit.py::REDACTED_ARGS in spirit.
REDACTED_KEYS = ("api_key", "confirm_token", "token", "key", "secret", "password")

#: Values whose JSON form is longer than this are replaced with a length note.
MAX_VALUE_CHARS = 4000


def _is_sensitive(key: str) -> bool:
    k = key.lower()
    return any(marker in k for marker in REDACTED_KEYS)


def redact(value: Any) -> Any:
    """Recursively redact secrets and truncate over-long values.

    - dict: any key that looks like a secret (``REDACTED_KEYS``) has its value
      replaced with ``"<redacted>"``; other values are recursed into.
    - list/tuple: each item recursed into.
    - anything else: kept as-is unless its JSON form exceeds
      ``MAX_VALUE_CHARS``, in which case it becomes ``"<truncated N chars>"``.
    """
    if isinstance(value, dict):
        out: dict[str, Any] = {}
        for k, v in value.items():
            if isinstance(k, str) and _is_sensitive(k):
                out[k] = "<redacted>"
            else:
                out[k] = redact(v)
        return out
    if isinstance(value, (list, tuple)):
        return [redact(v) for v in value]
    if isinstance(value, str):
        return value if len(value) <= MAX_VALUE_CHARS else f"<truncated {len(value)} chars>"
    # Non-string scalars: guard against pathologically large repr via JSON size.
    try:
        text = json.dumps(value, default=str)
    except (TypeError, ValueError):
        text = str(value)
    if len(text) > MAX_VALUE_CHARS:
        return f"<truncated {len(text)} chars>"
    return value


class TranscriptWriter:
    """Append-only JSONL writer for a single trial's LLM conversation.

    Thread-safe; the file is opened in append mode only and each line is
    flushed and fsync'd so a crash mid-run still leaves a valid prefix.
    Writes are fail-closed: an I/O or serialisation error is swallowed (it
    must never propagate into the benchmark client / model loop).
    """

    def __init__(self, out_dir: Path, t0: float | None = None):
        self.out_dir = Path(out_dir)
        self.path = self.out_dir / "transcript.jsonl"
        #: Shared wall-clock origin (epoch seconds, ``time.time()``) for clock
        #: alignment with the other recorders. Wall-clock so it is portable
        #: across independent recorders/processes and stays consistent with the
        #: wall-clock ``ts`` field; matches the benchmark client's
        #: ``started_at``. Pass the same t0 to every recorder in a trial.
        self.t0 = time.time() if t0 is None else t0
        self._lock = threading.Lock()
        self._turn_idx = 0
        self._closed = False
        try:
            self.out_dir.mkdir(parents=True, exist_ok=True)
        except OSError:
            # Fail-closed: never raise into the caller from construction either.
            pass

    def turn(
        self,
        role: str,
        content: str | None = None,
        *,
        tool_calls: list | None = None,
        tool_result: object | None = None,
        model: str | None = None,
        params: dict | None = None,
        usage: dict | None = None,
    ) -> None:
        """Append one conversation turn. Never raises into the caller."""
        with self._lock:
            if self._closed:
                return
            idx = self._turn_idx
            record = {
                "schema": SCHEMA,
                "ts": datetime.now(timezone.utc).isoformat(),
                "t_rel_s": round(time.time() - self.t0, 6),
                "turn_idx": idx,
                "role": role,
                "content": redact(content) if content is not None else None,
                "tool_calls": redact(tool_calls) if tool_calls is not None else None,
                "tool_result": redact(tool_result) if tool_result is not None else None,
                "model": model,
                "params": redact(params) if params is not None else None,
                "usage": usage,
            }
            try:
                line = json.dumps(record, default=str)
            except (TypeError, ValueError):
                # Last-resort: coerce everything through str() so we still log.
                line = json.dumps(
                    {**record, "content": str(content), "tool_calls": None, "tool_result": None, "params": None},
                    default=str,
                )
            try:
                with open(self.path, "a", encoding="utf-8") as fh:
                    fh.write(line + "\n")
                    fh.flush()
                    os.fsync(fh.fileno())
            except OSError:
                # Fail-closed: a logging failure must not break the model loop.
                # We still advance turn_idx so indices stay monotonic on retry.
                self._turn_idx = idx + 1
                return
            self._turn_idx = idx + 1

    def close(self) -> None:
        with self._lock:
            self._closed = True
