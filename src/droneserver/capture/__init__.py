"""Passive capture recorders for the flight-data reproducibility package.

These recorders implement the Plan 19 capture spec: they observe a copy of a
stream that SITL / mavlink-router forwards to them and never inject traffic.

- :class:`~droneserver.capture.mavlink_tap.MavlinkTap` - all MAVLink sent/received
- :class:`~droneserver.capture.telemetry_recorder.TelemetryRecorder` - drone state CSV
- :class:`~droneserver.capture.transcript.TranscriptWriter` - full LLM transcript
- :mod:`~droneserver.capture.manifest` - per-trial provenance + dataflash retention
- :func:`~droneserver.capture.events.derive_events` - distilled safety/flight events
- :func:`~droneserver.capture.verify.verify_bundle` - is the bundle real, or stubs?
"""

from droneserver.capture.events import derive_events
from droneserver.capture.manifest import (
    RemoteDataflashError,
    annotate_manifest,
    gather_versions,
    remote_clock_offset_s,
    retain_dataflash,
    retain_remote_dataflash,
    write_manifest,
)
from droneserver.capture.mavlink_tap import MavlinkTap
from droneserver.capture.telemetry_recorder import TelemetryRecorder, is_shared_bind
from droneserver.capture.transcript import TranscriptWriter
from droneserver.capture.verify import DEFAULT_MIN_TELEMETRY_ROWS, BundleCheck, verify_bundle

__all__ = [
    "MavlinkTap",
    "TelemetryRecorder",
    "is_shared_bind",
    "TranscriptWriter",
    "retain_dataflash",
    "retain_remote_dataflash",
    "remote_clock_offset_s",
    "RemoteDataflashError",
    "write_manifest",
    "annotate_manifest",
    "gather_versions",
    "derive_events",
    "verify_bundle",
    "BundleCheck",
    "DEFAULT_MIN_TELEMETRY_ROWS",
]
