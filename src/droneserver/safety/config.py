"""Safety-layer configuration (pydantic-settings, env prefix ``SAFETY_``).

Every component can be switched off independently for benchmarking
(guardrails-on vs guardrails-off comparisons, Plan 04). ALL DEFAULT ON.

Turning a component off is a deliberate, logged act: the audit record for
every call carries the component flags in effect.
"""

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

from droneserver.config import REPO_ROOT


class SafetySettings(BaseSettings):
    """Server-side safety limits and switches (env vars ``SAFETY_*``)."""

    model_config = SettingsConfigDict(
        env_prefix="SAFETY_",
        env_file=(REPO_ROOT / ".env", ".env"),
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # ---- master + per-component switches (all default ON) ----
    enabled: bool = True  # master switch; False disables the whole layer
    validation_enabled: bool = True  # parameter bounds + state preconditions
    geofence_enabled: bool = True  # server-side geofence
    tiers_enabled: bool = True  # confirmation tokens for critical tools
    auth_enabled: bool = True  # API keys + scopes
    audit_enabled: bool = True  # JSONL audit log
    rate_limit_enabled: bool = True

    # ---- parameter bounds ----
    max_altitude_m: float = 120.0  # 120 m ~ the common legal VLOS ceiling
    min_altitude_m: float = 0.0
    max_speed_m_s: float = 20.0
    max_distance_from_home_m: float = 2000.0
    max_mission_items: int = 200

    # ---- state preconditions ----
    require_armed_for_takeoff: bool = True
    require_in_air_for_navigation: bool = True
    # The takeoff-then-crash timing fix: after a takeoff command, navigation
    # is refused until the vehicle is actually airborne AND this settling
    # window has elapsed.
    takeoff_settle_s: float = 3.0
    # If drone telemetry cannot be read, should preconditions fail closed?
    # Default False (fail open with a logged warning) so a telemetry hiccup
    # cannot strand an airborne vehicle mid-command.
    preconditions_fail_closed: bool = False
    state_cache_ttl_s: float = 2.0

    # ---- geofence (server-side, independent of the firmware fence) ----
    # Polygon as "lat,lon;lat,lon;..." (>=3 vertices). Empty = no polygon
    # constraint (the altitude ceiling and radius still apply).
    geofence_polygon: str = ""
    geofence_max_altitude_m: float = 120.0
    # Radius fence around home; 0 disables.
    geofence_max_radius_m: float = 1000.0
    # reject | clip - clip only applies to altitude, never to horizontal
    # position (clipping a horizontal target silently flies somewhere the
    # operator did not ask for).
    geofence_violation_action: str = "reject"

    # ---- criticality tiers / confirmation tokens ----
    confirmation_ttl_s: float = 60.0
    confirmation_max_outstanding: int = 16

    # ---- authN/authZ ----
    # "CLIENT_ID:KEY:SCOPE,CLIENT_ID:KEY:SCOPE" - scope in {telemetry, control, admin}
    api_keys: str = Field(default="", repr=False)
    # What an unauthenticated client may do: "telemetry" | "reject" | "control"
    unauthenticated_scope: str = "telemetry"

    # ---- rate limiting (per client) ----
    rate_limit_calls: int = 60
    rate_limit_window_s: float = 60.0
    rate_limit_critical_calls: int = 6
    rate_limit_critical_window_s: float = 60.0

    # ---- audit ----
    audit_log_path: str = ""  # default: <flight_log_dir>/audit.jsonl

    def __repr__(self) -> str:  # never leak keys in logs/tracebacks
        return f"SafetySettings(enabled={self.enabled}, api_keys=<{len(self.api_keys.split(',')) if self.api_keys else 0} configured>)"


_CACHED: SafetySettings | None = None


def get_safety_settings() -> SafetySettings:
    """Return the safety settings, parsed ONCE and cached.

    Caching is load-bearing, not an optimisation: the uncached version re-read
    the ``.env`` file from disk on every single tool call, which (a) dominated
    the measured guard cost and (b) meant a mid-flight edit to ``.env`` could
    silently change the safety envelope between two calls. Call
    :func:`reset_safety_settings` to re-read (tests, or a deliberate reload).
    """
    global _CACHED
    if _CACHED is None:
        _CACHED = SafetySettings()
    return _CACHED


def reset_safety_settings() -> None:
    """Drop the cached settings so the next call re-reads the environment."""
    global _CACHED
    _CACHED = None
