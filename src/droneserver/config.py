"""Typed configuration via pydantic-settings.

All settings come from environment variables (or the repo-root ``.env`` file,
matching v1 behavior). The former hardcoded values are now defaults:

- ``MAVLINK_ADDRESS``   (required to connect; no default)
- ``MAVLINK_PORT``      (default ``14540``)
- ``MAVLINK_PROTOCOL``  (``udp``/``tcp``/``serial``, default ``udp``)
- ``MCP_HOST``          (default ``0.0.0.0``)
- ``MCP_PORT``          (default ``8080``)
- ``MCP_MOUNT_PATH``    (default ``/mcp``)
- ``MAVLINK_VERBOSE``   (default off; ``1`` shows HTTP/framework logs)
- ``FLIGHT_LOG_DIR``    (default ``<repo root>/flight_logs``)
"""

from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

# Repo root when running from a checkout: src/droneserver/config.py -> ../../..
REPO_ROOT = Path(__file__).resolve().parents[2]


class Settings(BaseSettings):
    """droneserver runtime settings (env vars / .env file)."""

    model_config = SettingsConfigDict(
        env_file=(REPO_ROOT / ".env", ".env"),
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # MAVLink connection (server -> drone/SITL)
    mavlink_address: str = ""
    mavlink_port: str = "14540"
    mavlink_protocol: str = "udp"
    mavlink_verbose: bool = False

    # MCP HTTP/SSE endpoint
    mcp_host: str = "0.0.0.0"  # noqa: S104 - tailnet-only exposure is enforced at deploy time
    mcp_port: int = 8080
    mcp_mount_path: str = "/mcp"

    # Flight logging
    flight_log_dir: Path = REPO_ROOT / "flight_logs"


def get_settings() -> Settings:
    """Read settings fresh from the environment.

    Deliberately not cached: v1 read ``os.environ`` at connect time, so tests
    and wrappers may set variables after import but before use.
    """
    return Settings()
