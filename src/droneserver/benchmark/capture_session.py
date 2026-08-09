"""Per-trial capture orchestration for the mission suite (Plan 19 wiring).

This module glues the six passive capture recorders in
:mod:`droneserver.capture` onto the mission-suite harness so that, when the
``--capture`` flag is set, every trial leaves a self-contained artifact
directory behind (the Plan 19 §8 bundle layout: one dir per mission/trial).

**It is lazy-imported.** :func:`droneserver.benchmark.runner.run_suite` only
imports this module when a :class:`CaptureConfig` is supplied, so the default
(no-capture) benchmark path never pulls in pymavlink / mavsdk and behaves
exactly as before. Importing this module is itself safe without those packages:
:mod:`droneserver.capture` imports them tolerantly (pymavlink inside
``MavlinkTap.start``; mavsdk under a guarded ``try/except`` in the recorder).

Orchestration per trial (:class:`TrialCapture`)
-----------------------------------------------
1. ``start()`` - create ``<run>/<mission>/trial_<n>/``, open a
   :class:`~droneserver.capture.TranscriptWriter` (and write the system + user
   prompt turns we have), start the :class:`~droneserver.capture.MavlinkTap`,
   and start the async :class:`~droneserver.capture.TelemetryRecorder`.
2. the mission runs (synchronously, in the caller's thread).
3. ``stop()`` - stop the tap and the telemetry recorder (always, via the
   caller's ``try/finally``, so a mission crash still flushes both).
4. ``finalize(...)`` - retain the dataflash ``.BIN``, write the per-trial
   ``audit_slice.csv``, record the tool-call turns onto the transcript and close
   it, ``write_manifest`` (§6 provenance + sha256 of every artifact), then
   ``derive_events`` -> ``events.jsonl``.

The async telemetry recorder
----------------------------
:class:`~droneserver.capture.TelemetryRecorder` is async and must keep
servicing its per-topic subscriber tasks *while* the mission runs. The mission
itself is synchronous (``client.call`` blocks on an SSE round-trip). Rather than
block the mission thread, :class:`_AsyncLoopThread` runs one asyncio event loop
in a dedicated background thread; ``TrialCapture`` drives the recorder's async
``start()``/``stop()`` onto that loop with
``asyncio.run_coroutine_threadsafe(...).result()``. The subscriber + sampler
tasks the recorder spawns then live on that loop and tick independently of the
(synchronous) mission code. This is the simplest correct approach: one owned
loop per trial, no interaction with whatever loop ``client.call`` uses
internally (``BenchmarkClient`` spins up its own throwaway loop per call via
``asyncio.run``).
"""

import asyncio
import socket
import threading
from datetime import datetime, timezone
from pathlib import Path

from droneserver.capture import (
    MavlinkTap,
    TelemetryRecorder,
    TranscriptWriter,
    derive_events,
    gather_versions,
    retain_dataflash,
    write_manifest,
)

#: audit_slice.csv columns written per trial. Superset of what the run-level
#: slice writes, plus ``call_id``/``outcome_error`` so events.derive_events can
#: join tool calls and surface error details.
AUDIT_SLICE_FIELDS = (
    "ts",
    "call_id",
    "tool",
    "tier",
    "verdict",
    "rule",
    "latency_ms",
    "safety_ms",
    "audit_write_ms",
    "client_id",
    "outcome_status",
    "outcome_error",
)


def _iso(ts: float) -> str:
    return datetime.fromtimestamp(ts, timezone.utc).isoformat()


def _host_of(address: str) -> str | None:
    """Best-effort host component of a MAVLink address for the manifest.

    Handles the shapes the recorders accept: ``udp://:14540``,
    ``tcp://127.0.0.1:5760``, ``udpin:127.0.0.1:14650``. Returns ``None`` when
    the address binds any interface (no explicit host) or cannot be parsed.
    """
    if not address:
        return None
    rest = address.split("://", 1)[1] if "://" in address else address
    if ":" in rest and rest.split(":", 1)[0] in ("udpin", "udpout", "tcpin"):
        rest = rest.split(":", 1)[1]
    host = rest.rsplit(":", 1)[0] if ":" in rest else rest
    host = host.strip("/")
    return host or None


class CaptureConfig:
    """Immutable capture settings gathered from the CLI (all optional/free-form).

    Kept as a plain object (not a frozen dataclass) so importing it never costs
    anything and so :mod:`scripts.run_mission_suite` can build it lazily only
    when ``--capture`` is passed.
    """

    def __init__(
        self,
        *,
        mavlink_endpoint: str = "udpin:127.0.0.1:14650",
        telemetry_address: str = "udp://:14540",
        dataflash_dir: Path | None = None,
        vehicle_sysid: int = 1,
        rate_hz: float = 10.0,
        model: str = "",
        provider: str = "",
        decoding: dict | None = None,
        firmware: str = "",
        firmware_version: str = "",
        sim_params: dict | None = None,
    ):
        self.mavlink_endpoint = mavlink_endpoint
        self.telemetry_address = telemetry_address
        self.dataflash_dir = Path(dataflash_dir) if dataflash_dir else None
        self.vehicle_sysid = vehicle_sysid
        self.rate_hz = rate_hz
        self.model = model
        self.provider = provider
        self.decoding = decoding or {}
        self.firmware = firmware
        self.firmware_version = firmware_version
        self.sim_params = sim_params or {}


class _AsyncLoopThread:
    """A private asyncio event loop running in a daemon thread.

    See the module docstring: this is how the async TelemetryRecorder keeps
    ticking while the synchronous mission runs. ``run()`` submits a coroutine
    and blocks for its result; ``close()`` stops the loop and joins the thread.
    """

    def __init__(self) -> None:
        self._loop = asyncio.new_event_loop()
        self._thread = threading.Thread(target=self._run, name="capture-loop", daemon=True)
        self._thread.start()

    def _run(self) -> None:
        asyncio.set_event_loop(self._loop)
        self._loop.run_forever()

    def run(self, coro, timeout: float = 90.0):
        """Run ``coro`` on the background loop and block for its result."""
        return asyncio.run_coroutine_threadsafe(coro, self._loop).result(timeout)

    def close(self) -> None:
        try:
            self._loop.call_soon_threadsafe(self._loop.stop)
        except RuntimeError:
            pass
        self._thread.join(timeout=5.0)
        try:
            self._loop.close()
        except Exception:
            pass


class TrialCapture:
    """Owns every recorder for a single mission trial. See module docstring.

    Every method is defensive: a recorder that fails to start (e.g. no MAVLink
    forward is running) is logged and skipped rather than being allowed to break
    the trial. The transcript recorder is pure-stdlib and always available.
    """

    def __init__(self, config: CaptureConfig, trial_dir: Path, t0: float):
        self.config = config
        self.trial_dir = Path(trial_dir)
        self.t0 = t0
        self._tap: MavlinkTap | None = None
        self._recorder: TelemetryRecorder | None = None
        self._loop: _AsyncLoopThread | None = None
        self.transcript: TranscriptWriter | None = None

    # -- lifecycle ---------------------------------------------------------

    def start(self, mission, context: dict) -> None:
        """Create the trial dir and start all recorders before the mission runs."""
        self.trial_dir.mkdir(parents=True, exist_ok=True)

        # Transcript: pure stdlib, cannot hard-fail. Record the prompt turns we
        # genuinely have (see the class TODO below).
        self.transcript = TranscriptWriter(self.trial_dir, t0=self.t0)
        self._write_prompt_turns(mission, context)

        # MAVLink wire tap (needs a passive UDP forward from SITL/mavlink-router).
        try:
            self._tap = MavlinkTap(
                self.config.mavlink_endpoint,
                self.trial_dir,
                t0=self.t0,
                vehicle_sysid=self.config.vehicle_sysid,
            )
            self._tap.start()
        except Exception as e:  # noqa: BLE001 - never let capture break the trial
            print(f"[capture] MavlinkTap failed to start ({type(e).__name__}: {e}); "
                  f"continuing without mavlink.tlog", flush=True)
            self._tap = None

        # Async telemetry recorder on its own background event loop.
        try:
            self._loop = _AsyncLoopThread()
            self._recorder = TelemetryRecorder(
                self.config.telemetry_address,
                self.trial_dir,
                rate_hz=self.config.rate_hz,
                t0=self.t0,
            )
            self._loop.run(self._recorder.start(), timeout=90)
        except Exception as e:  # noqa: BLE001
            print(f"[capture] TelemetryRecorder failed to start ({type(e).__name__}: {e}); "
                  f"continuing without telemetry.csv", flush=True)
            self._recorder = None
            if self._loop is not None:
                self._loop.close()
                self._loop = None

    def stop(self) -> None:
        """Stop the streaming recorders. Called from the caller's finally, so a
        mission crash still flushes the tap and the telemetry CSV."""
        if self._tap is not None:
            try:
                self._tap.stop()
            except Exception as e:  # noqa: BLE001
                print(f"[capture] MavlinkTap.stop error: {type(e).__name__}: {e}", flush=True)
        if self._recorder is not None and self._loop is not None:
            try:
                self._loop.run(self._recorder.stop(), timeout=30)
            except Exception as e:  # noqa: BLE001
                print(f"[capture] TelemetryRecorder.stop error: {type(e).__name__}: {e}", flush=True)
        if self._loop is not None:
            self._loop.close()
            self._loop = None

    def finalize(
        self,
        *,
        run_id: str,
        mission_id: str,
        trial_idx: int,
        client,
        context: dict,
        audit_rows: list[dict],
        started_ts: float,
        ended_ts: float,
    ) -> None:
        """Post-trial: dataflash, audit slice, transcript tool turns, manifest,
        events. Order matters - ``write_manifest`` hashes whatever exists, so it
        runs after every other artifact is on disk, and ``derive_events`` last
        (Plan 19 §7 build order)."""
        # 1. Retain the newest dataflash .BIN/.ulg for this trial.
        if self.config.dataflash_dir is not None:
            try:
                retain_dataflash(self.config.dataflash_dir, self.trial_dir,
                                 f"{mission_id}_t{trial_idx}")
            except Exception as e:  # noqa: BLE001
                print(f"[capture] retain_dataflash error: {type(e).__name__}: {e}", flush=True)

        # 2. Per-trial audit slice (windowed rows the caller already read).
        self._write_audit_slice(audit_rows)

        # 3. Transcript tool-call turns (what the client actually saw), then close.
        self._write_tool_turns(client, started_ts, ended_ts)
        if self.transcript is not None:
            self.transcript.close()

        # 4. Manifest: provenance (§6) + sha256 of every artifact present.
        meta = self._manifest_meta(run_id, mission_id, trial_idx, context,
                                   started_ts, ended_ts)
        try:
            write_manifest(self.trial_dir, meta)
        except Exception as e:  # noqa: BLE001
            print(f"[capture] write_manifest error: {type(e).__name__}: {e}", flush=True)

        # 5. Derive the distilled event narrative (reads audit_slice + mavlink +
        #    telemetry from the trial dir).
        try:
            derive_events(self.trial_dir)
        except Exception as e:  # noqa: BLE001
            print(f"[capture] derive_events error: {type(e).__name__}: {e}", flush=True)

    # -- helpers -----------------------------------------------------------

    def _write_prompt_turns(self, mission, context: dict) -> None:
        """Record the prompt turns the harness DOES have.

        TODO(transcript): the mission suite drives the MCP server directly via
        the deterministic ``BenchmarkClient`` - there is no LLM in this loop, so
        there are no assistant-reasoning turns or model-decided tool_call args to
        record here. When a real LLM client is wired in front of the server, thread
        this same TranscriptWriter through it and emit the assistant/tool turns
        (with call_id, args, tool_result, usage) per Plan 19 §1c. Until then we
        record only the system + user prompt turns below (not fabricated) so the
        transcript.jsonl exists and is clock-aligned.
        """
        if self.transcript is None:
            return
        self.transcript.turn(
            "system",
            content=(
                "droneserver mission-suite benchmark harness. Tool calls are issued "
                "by the deterministic BenchmarkClient against the MCP server, not by "
                "an LLM; no model conversation exists for this trial (see transcript "
                "module TODO). Recorded for clock-aligned provenance."
            ),
            model=self.config.model or None,
            params=self.config.decoding or None,
        )
        self.transcript.turn(
            "user",
            content=f"Mission {mission.mission_id}: {mission.name}",
            model=self.config.model or None,
        )

    def _write_tool_turns(self, client, started_ts: float, ended_ts: float) -> None:
        """Record one turn per tool call the client made during this trial window.

        These are the calls the ``BenchmarkClient`` observed (tool name, status,
        rule, wall time, whether a confirmation was demanded). Args and call_id
        are not available client-side (see the module/transcript TODO), so only
        what the client genuinely saw is written - nothing is fabricated.
        """
        if self.transcript is None or client is None:
            return
        for call in getattr(client, "calls", []):
            started = getattr(call, "started_at", None)
            if started is None or not (started_ts <= started <= ended_ts):
                continue
            self.transcript.turn(
                "tool",
                tool_calls=[{"tool": call.tool}],
                tool_result={
                    "status": call.status,
                    "rule": call.rule,
                    "error": call.error,
                    "confirmation_required": call.confirmation_required,
                    "wall_ms": round(call.wall_ms, 2),
                },
            )

    def _write_audit_slice(self, audit_rows: list[dict]) -> None:
        import csv

        if not audit_rows:
            return
        try:
            with (self.trial_dir / "audit_slice.csv").open("w", newline="", encoding="utf-8") as fh:
                w = csv.writer(fh)
                w.writerow(AUDIT_SLICE_FIELDS)
                for record in audit_rows:
                    w.writerow([record.get(f, "") for f in AUDIT_SLICE_FIELDS])
        except OSError as e:
            print(f"[capture] audit_slice write error: {type(e).__name__}: {e}", flush=True)

    def _manifest_meta(self, run_id: str, mission_id: str, trial_idx: int,
                       context: dict, started_ts: float, ended_ts: float) -> dict:
        cfg = self.config
        meta = {
            "run_id": run_id,
            "mission_id": mission_id,
            "trial_idx": trial_idx,
            "model": cfg.model,
            "provider": cfg.provider,
            "decoding": cfg.decoding,
            "firmware": cfg.firmware,
            "firmware_version": cfg.firmware_version,
            "sim": "SITL",
            "sim_params": cfg.sim_params,
            "host": socket.gethostname(),
            "sitl_host": _host_of(cfg.telemetry_address) or _host_of(cfg.mavlink_endpoint),
            "clock_offset_ms": None,  # not measured by the harness
            "started_ts": _iso(started_ts),
            "ended_ts": _iso(ended_ts),
            # context the harness already resolved (home fix, what we flew).
            "target_label": context.get("target_label"),
            "home": context.get("home"),
            "home_amsl_m": context.get("home_amsl_m"),
            # capture endpoints, for reproducibility of the tap topology.
            "mavlink_endpoint": cfg.mavlink_endpoint,
            "telemetry_address": cfg.telemetry_address,
        }
        # Best-effort installed MAVLink-stack versions (facts about this env).
        meta.update(gather_versions())
        return meta
