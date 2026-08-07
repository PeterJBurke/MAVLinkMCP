"""Mission phases, events, and the checkpointed mission record.

The state machine is deliberately small and explicit so it can be reasoned
about (and reviewed) in one screen::

    SUBMITTED -> VALIDATING -> UPLOADING -> ARMING -> RUNNING -> COMPLETED
                     |            |           |         |  \\-> RETURNING -> LANDING -> COMPLETED
                     |            |           |         \\---> PAUSED -> RUNNING
                     \\------------+-----------+----------\\--> FAILED | ABORTED

Terminal phases are COMPLETED, FAILED and ABORTED. Everything else is
"active" and is resumed by the runner after a server restart.
"""

import json
import os
import time
import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path

SCHEMA = "droneserver.mission/1"


class Phase(str, Enum):
    SUBMITTED = "submitted"
    VALIDATING = "validating"
    UPLOADING = "uploading"
    ARMING = "arming"
    RUNNING = "running"
    PAUSED = "paused"
    RETURNING = "returning"
    LANDING = "landing"
    COMPLETED = "completed"
    FAILED = "failed"
    ABORTED = "aborted"


TERMINAL_PHASES = frozenset({Phase.COMPLETED, Phase.FAILED, Phase.ABORTED})

#: Allowed transitions. Anything not listed is a bug and is refused.
ALLOWED: dict[Phase, frozenset] = {
    Phase.SUBMITTED: frozenset({Phase.VALIDATING, Phase.FAILED, Phase.ABORTED}),
    Phase.VALIDATING: frozenset({Phase.UPLOADING, Phase.FAILED, Phase.ABORTED}),
    Phase.UPLOADING: frozenset({Phase.ARMING, Phase.FAILED, Phase.ABORTED}),
    Phase.ARMING: frozenset({Phase.RUNNING, Phase.FAILED, Phase.ABORTED}),
    Phase.RUNNING: frozenset(
        {Phase.PAUSED, Phase.RETURNING, Phase.LANDING, Phase.COMPLETED, Phase.FAILED, Phase.ABORTED}
    ),
    Phase.PAUSED: frozenset({Phase.RUNNING, Phase.RETURNING, Phase.LANDING, Phase.ABORTED, Phase.FAILED}),
    Phase.RETURNING: frozenset({Phase.LANDING, Phase.COMPLETED, Phase.FAILED, Phase.ABORTED}),
    Phase.LANDING: frozenset({Phase.COMPLETED, Phase.FAILED, Phase.ABORTED}),
    Phase.COMPLETED: frozenset(),
    Phase.FAILED: frozenset(),
    Phase.ABORTED: frozenset(),
}


def can_transition(current: Phase, target: Phase) -> bool:
    return target in ALLOWED.get(current, frozenset())


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass
class MissionEvent:
    """One thing that happened during the mission."""

    ts: str
    kind: str  # phase_change | waypoint | battery | mode | auto_action | error | info
    message: str
    data: dict = field(default_factory=dict)

    @staticmethod
    def make(kind: str, message: str, **data) -> "MissionEvent":
        return MissionEvent(ts=utc_now(), kind=kind, message=message, data=data)


@dataclass
class MissionRecord:
    """The checkpointed mission. Everything here survives a server restart."""

    mission_id: str
    phase: str = Phase.SUBMITTED.value
    created_at: str = field(default_factory=utc_now)
    updated_at: str = field(default_factory=utc_now)
    started_at: float | None = None  # monotonic-independent wall clock (time.time)
    finished_at: float | None = None

    waypoint_count: int = 0
    current_item: int = 0
    total_items: int = 0
    takeoff_altitude_m: float = 20.0
    return_to_launch: bool = True
    source: str = "waypoints"  # waypoints | qgc_plan

    last_position: dict | None = None
    last_battery: dict | None = None
    last_flight_mode: str | None = None
    last_armed: bool = True

    error: str | None = None
    auto_actions_fired: list = field(default_factory=list)
    events: list = field(default_factory=list)

    # Not persisted-meaningful, but useful for the client:
    resumed_after_restart: bool = False

    @property
    def phase_enum(self) -> Phase:
        return Phase(self.phase)

    @property
    def active(self) -> bool:
        return self.phase_enum not in TERMINAL_PHASES

    def elapsed_s(self) -> float | None:
        if self.started_at is None:
            return None
        end = self.finished_at if self.finished_at is not None else time.time()
        return round(end - self.started_at, 1)

    def progress_percent(self) -> float:
        if not self.total_items:
            return 0.0
        return round(min(self.current_item / self.total_items, 1.0) * 100, 1)

    def add_event(self, event: MissionEvent, max_events: int) -> None:
        self.events.append(asdict(event))
        if len(self.events) > max_events:
            del self.events[: len(self.events) - max_events]
        self.updated_at = utc_now()

    def to_dict(self) -> dict:
        d = asdict(self)
        d["schema"] = SCHEMA
        return d

    @classmethod
    def from_dict(cls, data: dict) -> "MissionRecord":
        known = {f for f in cls.__dataclass_fields__}
        return cls(**{k: v for k, v in data.items() if k in known})


def new_mission_id() -> str:
    return f"m_{uuid.uuid4().hex[:12]}"


class MissionStore:
    """Atomic JSON checkpoint of the current mission.

    Only ONE mission is tracked at a time (one server, one drone). Writes are
    atomic (temp file + ``os.replace``) so a crash mid-write cannot leave a
    truncated checkpoint.
    """

    def __init__(self, path: Path | str):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def save(self, record: MissionRecord) -> None:
        record.updated_at = utc_now()
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(record.to_dict(), indent=2, default=str), encoding="utf-8")
        os.replace(tmp, self.path)

    def load(self) -> MissionRecord | None:
        if not self.path.exists():
            return None
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return None
        if data.get("schema") not in (SCHEMA, None):
            return None
        try:
            return MissionRecord.from_dict(data)
        except TypeError:
            return None

    def clear(self) -> None:
        self.path.unlink(missing_ok=True)
