"""droneserver - MAVLink MCP server: LLM-agnostic drone control via the Model Context Protocol."""

from droneserver.logging_setup import configure_logging

__version__ = "2.0.2"

# v1 behavior: logging is configured as a side effect of importing the server
# module. Preserved here so every entry point (console script, systemd unit,
# `python -m droneserver.server`, tests) gets the same log format.
configure_logging()
