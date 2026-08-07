"""Managed-mission configuration (pydantic-settings, env prefix ``MISSION_``).

Defaults are deliberately conservative: auto-actions that end the flight early
are preferred over auto-actions that keep flying.
"""

from pydantic_settings import BaseSettings, SettingsConfigDict

from droneserver.config import REPO_ROOT


class MissionSettings(BaseSettings):
    """Server-side mission runner settings (env vars ``MISSION_*``)."""

    model_config = SettingsConfigDict(
        env_prefix="MISSION_",
        env_file=(REPO_ROOT / ".env", ".env"),
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # ---- monitoring ----
    poll_interval_s: float = 2.0
    #: Give up on a mission that has not progressed for this long (0 = never).
    stall_timeout_s: float = 0.0
    #: Where the mission state is checkpointed (JSON).
    state_path: str = ""  # default: <flight_log_dir>/mission_state.json
    #: Keep at most this many events in memory/state (the audit log keeps all).
    max_events: int = 500

    # ---- auto-actions (server-side, no LLM in the loop) ----
    auto_actions_enabled: bool = True

    #: Battery fraction (0-1) below which the low-battery action fires.
    low_battery_action: str = "rtl"  # rtl | land | hold | none
    low_battery_threshold: float = 0.25
    #: Second, harder threshold - always lands regardless of the action above.
    critical_battery_threshold: float = 0.10

    #: What to do if the vehicle leaves the server-side geofence mid-mission.
    geofence_breach_action: str = "rtl"  # rtl | land | hold | none

    #: What to do if the MAVLink link to the vehicle drops while a mission is
    #: running. "none" leaves it to the autopilot's own failsafe (which is
    #: usually the right answer - the autopilot is closer to the problem).
    link_loss_action: str = "none"  # rtl | land | hold | none
    link_loss_grace_s: float = 15.0

    #: Arm retry budget when starting a mission (prearm checks settle slowly).
    arm_timeout_s: float = 90.0


def get_mission_settings() -> MissionSettings:
    return MissionSettings()
