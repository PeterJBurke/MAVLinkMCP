"""Append-only JSONL audit log - also the paper's latency instrumentation.

One JSON object per tool call, one line, append-only, never rewritten. The
schema is STABLE (documented in docs/safety_review.md and depended on by the
analysis scripts); fields may be added, never removed or repurposed.

Schema (v1)::

    {
      "schema": "droneserver.audit/1",
      "ts": "2026-08-06T21:34:59.123456+00:00",  # UTC ISO-8601
      "call_id": "…",            # unique per call
      "client_id": "…",          # from the API key, or "anonymous"
      "authenticated": true,
      "key_fp": "…",             # short key fingerprint; NEVER the key
      "model": "…"|null,         # model the client reported, if any
      "tool": "takeoff",
      "tier": "normal",          # effective tier for this call
      "args": {...},             # redacted (see REDACTED_ARGS)
      "verdict": "allowed"|"rejected"|"confirmation_required"|"error",
      "rule": "bounds.max_altitude"|null,   # set when rejected
      "outcome_status": "success"|"failed"|…|null,  # tool's own status field
      "outcome_error": "…"|null,
      "latency_ms": 12.3,        # entry -> result ready (see note below)
      "safety_ms": 1.4,          # time spent in the safety layer alone
      "audit_write_ms": 0.9,     # measured cost of the PREVIOUS durable write
      "guard_error": null,       # set when the guard itself failed (fail-closed)
      "guards": {"validation": true, "geofence": true, …}  # config in force
    }

Timing semantics (important for anything quoting these numbers):

- ``latency_ms`` starts at the very first statement of the guard - before the
  settings are loaded - and ends when the tool's result is ready. It therefore
  includes every check and the tool's own work.
- It excludes this record's own fsync'd write, because that write cannot be
  timed before it happens. That cost is measured and reported on the NEXT
  record as ``audit_write_ms``. Over a run, mean(latency_ms) +
  mean(audit_write_ms) is the true end-to-end cost; the one-record lag
  cancels.
- ``verdict`` is ``allowed_safety_disabled`` when the layer was switched off,
  so a guardrails-off experiment is still labelled in its own data.
"""

import json
import os
import threading
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

SCHEMA = "droneserver.audit/1"

#: Argument names never written verbatim to the log.
REDACTED_ARGS = {"confirm_token", "api_key", "key", "token", "rtcm_base64", "plan_json"}
#: Arguments longer than this are truncated (mission lists, plan files).
MAX_ARG_CHARS = 2000


def redact_args(args: dict) -> dict:
    out: dict[str, Any] = {}
    for k, v in (args or {}).items():
        if k in REDACTED_ARGS:
            out[k] = "<redacted>"
            continue
        try:
            text = json.dumps(v, default=str)
        except (TypeError, ValueError):
            text = str(v)
        out[k] = json.loads(text) if len(text) <= MAX_ARG_CHARS else f"<truncated {len(text)} chars>"
    return out


@dataclass
class AuditRecord:
    call_id: str
    client_id: str
    authenticated: bool
    key_fp: str
    model: str | None
    tool: str
    tier: str
    args: dict
    verdict: str
    rule: str | None = None
    outcome_status: str | None = None
    outcome_error: str | None = None
    latency_ms: float = 0.0
    safety_ms: float = 0.0
    audit_write_ms: float = 0.0
    guard_error: str | None = None
    guards: dict = field(default_factory=dict)

    def to_json(self) -> str:
        return json.dumps(
            {
                "schema": SCHEMA,
                "ts": datetime.now(timezone.utc).isoformat(),
                "call_id": self.call_id,
                "client_id": self.client_id,
                "authenticated": self.authenticated,
                "key_fp": self.key_fp,
                "model": self.model,
                "tool": self.tool,
                "tier": self.tier,
                "args": redact_args(self.args),
                "verdict": self.verdict,
                "rule": self.rule,
                "outcome_status": self.outcome_status,
                "outcome_error": self.outcome_error,
                "latency_ms": round(self.latency_ms, 3),
                "safety_ms": round(self.safety_ms, 3),
                "audit_write_ms": round(self.audit_write_ms, 3),
                "guard_error": self.guard_error,
                "guards": self.guards,
            },
            default=str,
        )


class AuditLog:
    """Append-only JSONL writer. Thread-safe; opened in append mode only."""

    def __init__(self, path: Path | str):
        self.path = Path(path)
        self._lock = threading.Lock()
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def write(self, record: AuditRecord) -> None:
        line = record.to_json()
        with self._lock:
            # O_APPEND: concurrent writers cannot interleave whole lines.
            with open(self.path, "a", encoding="utf-8") as fh:
                fh.write(line + "\n")
                fh.flush()
                os.fsync(fh.fileno())

    def read_all(self) -> list[dict]:
        """Read back every record (tests + analysis scripts)."""
        if not self.path.exists():
            return []
        records = []
        for line in self.path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line:
                records.append(json.loads(line))
        return records


def new_call_id() -> str:
    return uuid.uuid4().hex[:16]
