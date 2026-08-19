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

    #: What to command once every mission item has been flown. ArduPilot
    #: missions self-terminate with a land+disarm; PX4 loiters (HOLD) armed at
    #: the final waypoint, so the server actively brings it down to reach the
    #: "landed and disarmed" completion signal. "rtl" returns to launch first
    #: (safest - lands at home); "land" descends in place.
    mission_complete_action: str = "rtl"  # rtl | land

    #: What to do if the MAVLink link to the vehicle drops while a mission is
    #: running. "none" leaves it to the autopilot's own failsafe (which is
    #: usually the right answer - the autopilot is closer to the problem).
    link_loss_action: str = "none"  # rtl | land | hold | none
    link_loss_grace_s: float = 15.0

    #: Arm retry budget when starting a mission (prearm checks settle slowly).
    arm_timeout_s: float = 90.0

    # ---- start confirmation / progress evidence ----
    #: How long to wait for positive evidence that the autopilot really entered
    #: mission execution after ``start_mission`` returned. ``start_mission``
    #: cannot be trusted on its own: PX4 ACKs the mode change as ACCEPTED and
    #: then refuses it ("Switching to Mission is currently not available"), so
    #: MavSDK reports success for a mission that never runs. If no evidence
    #: arrives within this window the mission FAILS - it is never reported as
    #: running, and therefore can never be reported as finished.
    start_confirm_timeout_s: float = 30.0

    #: How far the vehicle must get from the point where the mission started
    #: before that movement counts as evidence the mission is really flying.
    #: Comfortably above GPS/position drift while loitering (measured: 0.6 m
    #: over a whole PX4 trial that never left the launch point).
    progress_distance_m: float = 15.0

    #: Bring a RUNNING mission down (and FAIL it) if it has been airborne this
    #: long without EVER progressing - no item reached past the one it started
    #: on and no movement. Only catches "never started"; a mission that has
    #: progressed once is never stalled out by this, however long its legs or
    #: waypoint holds are. 0 disables. Fail-closed needs a bound, or "we are
    #: not sure it finished" just means "loiter armed forever".
    no_progress_timeout_s: float = 240.0


def get_mission_settings() -> MissionSettings:
    return MissionSettings()
