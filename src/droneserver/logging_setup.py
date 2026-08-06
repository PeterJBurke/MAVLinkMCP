"""Logging configuration for the droneserver package.

Single-line format tuned for systemd/journalctl output (v1 behavior,
formerly at the top of ``src/server/droneserver.py``).
"""

import logging
import sys

logger = logging.getLogger("droneserver")

_configured = False


def configure_logging() -> None:
    """Configure the ``droneserver`` logger (idempotent)."""
    global _configured
    if _configured:
        return
    _configured = True

    logger.setLevel(logging.INFO)

    # Remove any existing handlers to avoid duplicates
    logger.handlers.clear()

    # Compact format: timestamp | level | message (no logger name, no multi-line)
    console_handler = logging.StreamHandler()
    console_formatter = logging.Formatter("%(asctime)s | %(levelname)-7s | %(message)s", datefmt="%H:%M:%S")
    console_handler.setFormatter(console_formatter)
    logger.addHandler(console_handler)

    # Prevent propagation to avoid duplicate logs from parent loggers
    logger.propagate = False

    # Ensure output is unbuffered for systemd journalctl
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(line_buffering=True)
