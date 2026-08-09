"""Per-trial capture orchestration for both benchmark harnesses (Plan 19 wiring).

This module glues the passive capture recorders in :mod:`droneserver.capture`
onto a benchmark harness so that, when the ``--capture`` flag is set, every
trial leaves a self-contained artifact directory behind (the Plan 19 §8 bundle
layout: one dir per mission/trial).

**Both harnesses use it.** :func:`droneserver.benchmark.runner.run_suite` (the
scripted suite) and :func:`droneserver.llm.runner.run_llm_suite` (the
LLM-in-the-loop suite the N=5 campaign runs) drive the same
:class:`TrialCapture`, so a trial flown by a model and a trial flown by a
script leave the same evidence in the same shape. The only difference is the
transcript: the scripted harness has no model conversation to record and says
so, while the LLM harness writes the real turns (Plan 19 §1c).

**It is lazy-imported.** Both runners only import this module when a
:class:`CaptureConfig` is supplied, so the default (no-capture) path never
pulls in pymavlink / mavsdk and behaves exactly as before. Importing this
module is itself safe without those packages: :mod:`droneserver.capture`
imports them tolerantly (pymavlink inside ``MavlinkTap.start``; mavsdk under a
guarded ``try/except`` in the recorder).

Orchestration per trial (:class:`TrialCapture`)
-----------------------------------------------
1. ``start()`` - create ``<run>/<mission>/trial_<n>/``, open a
   :class:`~droneserver.capture.TranscriptWriter` (and write the system + user
   prompt turns we have), start the :class:`~droneserver.capture.MavlinkTap`,
   and start the async :class:`~droneserver.capture.TelemetryRecorder`.
2. the mission runs.
3. ``stop()`` - stop the tap and the telemetry recorder (always, via the
   caller's ``try/finally``, so a mission crash still flushes both).
4. ``finalize(...)`` - retain the dataflash ``.BIN``, write the per-trial
   ``audit_slice.csv``, record the conversation onto the transcript and close
   it, ``derive_events`` -> ``events.jsonl``, ``write_manifest`` (§6 provenance
   + sha256 of every artifact), then :func:`~droneserver.capture.verify_bundle`
   and stamp its ``capture_status`` back into the manifest.

**Why the events are derived before the manifest is written:** the manifest
hashes what it finds on disk, so anything written afterwards is a file nobody
can verify. ``events.jsonl`` used to be written after it and was therefore
absent from every manifest's artifact list.

**Why the bundle is verified at all.** The recorders are deliberately
fail-soft - a capture problem must never destroy a flight - which means a run
that skipped every recorder still exits 0. Every capture defect found on this
project was silent in exactly that way. :func:`~droneserver.capture.verify_bundle`
looks at the files instead of the exit code and records the answer in the
manifest, and the runners count the degraded trials at the end of the run.

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
    DEFAULT_MIN_TELEMETRY_ROWS,
    BundleCheck,
    MavlinkTap,
    TelemetryRecorder,
    TranscriptWriter,
    annotate_manifest,
    derive_events,
    gather_versions,
    retain_dataflash,
    retain_remote_dataflash,
    verify_bundle,
    write_manifest,
)

#: Refuse to copy a dataflash log bigger than this (bytes). A SITL left in
#: continuous-logging mode produces multi-gigabyte files that would dominate
#: the trial directory and the copy time; better to skip and say so.
MAX_DATAFLASH_BYTES = 1 << 30  # 1 GiB

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
        dataflash_remote: str = "",
        vehicle_sysid: int = 1,
        rate_hz: float = 10.0,
        min_telemetry_rows: int = DEFAULT_MIN_TELEMETRY_ROWS,
        model: str = "",
        provider: str = "",
        decoding: dict | None = None,
        firmware: str = "",
        firmware_version: str = "",
        sim_params: dict | None = None,
        droneserver_commit: str = "",
        sitl_host: str = "",
    ):
        self.mavlink_endpoint = mavlink_endpoint
        self.telemetry_address = telemetry_address
        self.dataflash_dir = Path(dataflash_dir) if dataflash_dir else None
        #: ``host:/path`` of the log directory when the simulator runs on
        #: another machine (the usual SITL case - see finalize()).
        self.dataflash_remote = dataflash_remote
        self.vehicle_sysid = vehicle_sysid
        self.rate_hz = rate_hz
        #: Floor for ``telemetry.csv``: fewer data rows than this and the trial
        #: is reported degraded (see droneserver.capture.verify).
        self.min_telemetry_rows = min_telemetry_rows
        self.model = model
        self.provider = provider
        self.decoding = decoding or {}
        self.firmware = firmware
        self.firmware_version = firmware_version
        self.sim_params = sim_params or {}
        #: Plan 19 §6 provenance the harness must supply, because this layer
        #: refuses to guess it: which code flew, and which machine the sim ran
        #: on (not derivable once the link goes through a local relay).
        self.droneserver_commit = droneserver_commit
        self.sitl_host = sitl_host


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

    def start(
        self,
        mission=None,
        context: dict | None = None,
        *,
        system_prompt: str | None = None,
        user_prompt: str | None = None,
    ) -> None:
        """Create the trial dir and start all recorders before the mission runs.

        ``system_prompt`` / ``user_prompt`` are the LLM harness's real prompts;
        when they are given they are written verbatim as the opening transcript
        turns. Without them (the scripted harness) the transcript opens with a
        note saying there was no model, which is the truth and is preferable to
        an empty file that looks like a lost recording.
        """
        self.trial_dir.mkdir(parents=True, exist_ok=True)

        # Transcript: pure stdlib, cannot hard-fail.
        self.transcript = TranscriptWriter(self.trial_dir, t0=self.t0)
        self._write_prompt_turns(mission, context or {}, system_prompt, user_prompt)

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
        client=None,
        context: dict,
        audit_rows: list[dict],
        started_ts: float,
        ended_ts: float,
        llm_run=None,
        require_transcript: bool = False,
    ) -> BundleCheck:
        """Post-trial: dataflash, audit slice, transcript, events, manifest, verify.

        Order matters. ``derive_events`` reads the audit slice and the two
        MAVLink files, so it runs after them; ``write_manifest`` hashes whatever
        it finds, so it runs after *every* artifact including the events; and
        the verification runs last, because one of the things it checks is that
        the manifest lists everything. The verdict is stamped back onto the
        manifest as ``capture_status`` and returned to the caller, which counts
        the degraded trials for the run-end summary.

        ``llm_run`` is an :class:`~droneserver.llm.agent.AgentRun`: when given,
        the model's own turns and tool calls are written to the transcript
        instead of the scripted harness's client-side call list.
        """
        # 1. Retain the newest dataflash .BIN/.ulg for this trial, from the local
        #    disk or - the usual SITL case - from the machine running the sim.
        #    Only a log actually written during this trial is taken (started_ts
        #    guard), so a mission that never armed retains nothing rather than
        #    inheriting the previous flight's log.
        trial_name = f"{mission_id}_t{trial_idx}"
        if self.config.dataflash_remote:
            try:
                kept = retain_remote_dataflash(
                    self.config.dataflash_remote, self.trial_dir, trial_name,
                    min_mtime=started_ts, max_bytes=MAX_DATAFLASH_BYTES,
                )
                if kept is None:
                    print(f"[capture] no dataflash log written during {trial_name} "
                          f"(nothing newer than the trial start, or over the size cap)", flush=True)
                else:
                    print(f"[capture] retained {kept.name} ({kept.stat().st_size} bytes)", flush=True)
            except Exception as e:  # noqa: BLE001 - never let capture break the trial
                print(f"[capture] retain_remote_dataflash error: {type(e).__name__}: {e}", flush=True)
        elif self.config.dataflash_dir is not None:
            try:
                retain_dataflash(self.config.dataflash_dir, self.trial_dir, trial_name)
            except Exception as e:  # noqa: BLE001
                print(f"[capture] retain_dataflash error: {type(e).__name__}: {e}", flush=True)

        # 2. Per-trial audit slice (windowed rows the caller already read).
        self._write_audit_slice(audit_rows)

        # 3. Transcript: the model's conversation (LLM harness) or the tool
        #    calls the deterministic client made (scripted harness), then close.
        if llm_run is not None:
            self._write_model_turns(llm_run)
        else:
            self._write_tool_turns(client, started_ts, ended_ts)
        if self.transcript is not None:
            self.transcript.close()

        # 4. Derive the distilled event narrative (reads audit_slice + mavlink +
        #    telemetry from the trial dir). BEFORE the manifest, so events.jsonl
        #    is hashed like every other artifact.
        try:
            derive_events(self.trial_dir)
        except Exception as e:  # noqa: BLE001
            print(f"[capture] derive_events error: {type(e).__name__}: {e}", flush=True)

        # 5. Manifest: provenance (§6) + sha256 of every artifact present.
        meta = self._manifest_meta(run_id, mission_id, trial_idx, context,
                                   started_ts, ended_ts)
        try:
            write_manifest(self.trial_dir, meta)
        except Exception as e:  # noqa: BLE001
            print(f"[capture] write_manifest error: {type(e).__name__}: {e}", flush=True)

        # 6. Verify what is actually on disk, and record the answer where a
        #    reader of the archive will find it.
        check = verify_bundle(
            self.trial_dir,
            require_transcript=require_transcript,
            min_telemetry_rows=self.config.min_telemetry_rows,
            vehicle_sysid=self.config.vehicle_sysid,
        )
        try:
            annotate_manifest(self.trial_dir, check.as_dict())
        except Exception as e:  # noqa: BLE001
            print(f"[capture] annotate_manifest error: {type(e).__name__}: {e}", flush=True)
        label = f"{mission_id} trial {trial_idx}"
        if check.complete:
            print(f"[capture] {label}: bundle complete", flush=True)
        else:
            print(f"[capture] {label}: DEGRADED - {'; '.join(check.problems)}", flush=True)
        return check

    # -- helpers -----------------------------------------------------------

    def _write_prompt_turns(self, mission, context: dict,
                            system_prompt: str | None = None,
                            user_prompt: str | None = None) -> None:
        """Open the transcript with the prompts this trial genuinely used.

        The LLM harness passes the real system and mission prompts, which is
        the Plan 19 §1c ground truth of *what the model was told*. The scripted
        mission suite has no model: it drives the MCP server directly through
        the deterministic ``BenchmarkClient``, so there are no assistant turns
        and none are invented - the opening turn says so instead, and the file
        still exists and is clock-aligned with the other recorders.
        """
        if self.transcript is None:
            return
        if system_prompt is not None or user_prompt is not None:
            self.transcript.turn(
                "system",
                content=system_prompt,
                model=self.config.model or None,
                params=self.config.decoding or None,
            )
            self.transcript.turn(
                "user",
                content=user_prompt,
                model=self.config.model or None,
            )
            return

        label = getattr(mission, "mission_id", "?")
        name = getattr(mission, "name", "")
        self.transcript.turn(
            "system",
            content=(
                "droneserver mission-suite benchmark harness. Tool calls are issued "
                "by the deterministic BenchmarkClient against the MCP server, not by "
                "an LLM; no model conversation exists for this trial. Recorded for "
                "clock-aligned provenance."
            ),
            model=self.config.model or None,
            params=self.config.decoding or None,
        )
        self.transcript.turn(
            "user",
            content=f"Mission {label}: {name}",
            model=self.config.model or None,
        )

    def _write_model_turns(self, run) -> None:
        """Write the model's own conversation to the transcript (Plan 19 §1c).

        One ``assistant`` turn per model turn - its text, the tool calls it
        requested with their arguments, its token usage and decision latency -
        followed by one ``tool`` turn per call carrying what the server sent
        back. Arguments and results pass through the transcript writer's
        redactor, so confirmation tokens and keys never reach the file.

        Turn indices and ``call_id`` (``<turn>.<seq>``) are the harness's own,
        so a line here joins to the same call in ``tool_calls.csv`` and
        ``audit_slice.csv``.
        """
        if self.transcript is None or run is None:
            return
        calls_by_turn: dict[int, list] = {}
        for call in getattr(run, "calls", []):
            calls_by_turn.setdefault(call.turn, []).append(call)

        for turn in getattr(run, "turns", []):
            calls = calls_by_turn.get(turn.index, [])
            self.transcript.turn(
                "assistant",
                content=turn.text or None,
                tool_calls=[
                    {"call_id": f"{c.turn}.{c.seq}", "tool": c.tool, "args": c.arguments}
                    for c in calls
                ] or None,
                model=turn.resolved_model or self.config.model or None,
                params=self.config.decoding or None,
                usage={
                    "prompt_tokens": turn.input_tokens,
                    "cached_prompt_tokens": turn.cached_input_tokens,
                    "completion_tokens": turn.output_tokens,
                    "reasoning_tokens": turn.reasoning_tokens,
                    "decision_latency_ms": round(turn.decision_latency_ms, 1),
                    "finish_reason": turn.finish_reason,
                    "served_by": turn.served_by,
                    "quantization": turn.quantization,
                },
            )
            for call in calls:
                self.transcript.turn(
                    "tool",
                    tool_calls=[{"call_id": f"{call.turn}.{call.seq}", "tool": call.tool}],
                    tool_result={
                        "status": call.status,
                        "rule": call.rule,
                        "error": call.error,
                        "confirmation_required": call.confirmation_required,
                        "client_side_rejection": call.client_side_rejection,
                        "wall_ms": round(call.wall_ms, 2),
                        "result": call.result,
                    },
                )

        stop_reason = getattr(run, "stop_reason", "")
        if stop_reason:
            self.transcript.turn("system", content=f"trial ended: {stop_reason}")

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
            "sitl_host": cfg.sitl_host or _host_of(cfg.telemetry_address) or _host_of(cfg.mavlink_endpoint),
            "droneserver_commit": cfg.droneserver_commit,
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
