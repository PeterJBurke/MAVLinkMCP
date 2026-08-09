"""One capture command line, shared by both harnesses.

**Why this is a module and not two copies.** The Plan 19 capture layer was
wired into the scripted mission suite first, and the flags lived in
``scripts/run_mission_suite.py``. The LLM-in-the-loop harness -
``scripts/run_llm_missions.py``, which is what the N=5 campaign actually runs -
therefore had no ``--capture`` at all, and would have flown hundreds of trials
that left no MAVLink tlog, no dataflash log, no manifest and no events. The
flags now live here and both scripts call :func:`add_capture_arguments`, so the
two harnesses cannot drift apart again: a flag added for one exists for both.

Everything here is opt-in. Nothing has any effect, and **no capture code
(pymavlink / mavsdk) is imported**, unless ``--capture`` is passed:
:func:`build_capture_config` returns ``None`` and imports nothing otherwise.
"""

import json
import subprocess
from pathlib import Path

from droneserver.capture.verify import DEFAULT_MIN_TELEMETRY_ROWS

#: The repository this code was run from, for :func:`git_commit`.
_REPO_ROOT = Path(__file__).resolve().parents[3]


def git_commit(repo: Path | None = None) -> str:
    """The droneserver commit that flew this run, or ``""`` if unreadable.

    Recorded in every manifest: without it the archive says what happened but
    not which code made it happen. Reported as ``<sha>-dirty`` when the working
    tree has uncommitted changes, because a clean sha would be a lie.
    """
    repo = Path(repo or _REPO_ROOT)
    try:
        sha = subprocess.run(["git", "-C", str(repo), "rev-parse", "HEAD"],
                             capture_output=True, text=True, timeout=10, check=False)
        if sha.returncode != 0:
            return ""
        commit = sha.stdout.strip()
        dirty = subprocess.run(["git", "-C", str(repo), "status", "--porcelain"],
                               capture_output=True, text=True, timeout=10, check=False)
        return f"{commit}-dirty" if dirty.stdout.strip() else commit
    except (OSError, subprocess.SubprocessError):
        return ""


def add_capture_arguments(parser, *, model_provenance: bool = True) -> None:
    """Add the ``capture (Plan 19)`` argument group to ``parser``.

    ``model_provenance=False`` omits ``--model`` / ``--provider`` /
    ``--decoding``: the LLM harness already owns those names and knows the real
    values from the resolved route, which is better provenance than anything a
    human would retype on the command line.
    """
    cap = parser.add_argument_group(
        "capture (Plan 19)",
        "Opt-in per-trial artifact capture. Nothing below has any effect, and no "
        "capture code (pymavlink/mavsdk) is imported, unless --capture is passed.")
    cap.add_argument("--capture", action="store_true",
                     help="enable the per-trial capture layer (MAVLink tap + telemetry "
                          "recorder + dataflash retention + manifest + events)")
    cap.add_argument("--mavlink-endpoint", default="udpin:127.0.0.1:14650",
                     help="passive pymavlink listener for the MAVLink wire tap; SITL / "
                          "mavlink-router must forward a COPY of the stream here. It has to "
                          "be fed from INSIDE the link (see scripts/mavlink_relay.py): a "
                          "plain MAVProxy --out forward carries the vehicle's telemetry and "
                          "none of the server's commands (default: %(default)s)")
    cap.add_argument("--telemetry-address", default="udp://:14540",
                     help="MavSDK system address for the telemetry recorder. Defaults to "
                          "the same MAVLink address/port the server uses (MAVLINK_PORT "
                          "14540); in practice point it at its OWN forwarded endpoint "
                          "(mavlink-router --out) so it does not contend with the server "
                          "for the socket (default: %(default)s)")
    cap.add_argument("--dataflash-dir", default="",
                     help="directory where SITL writes its .BIN/.ulg logs; the newest is "
                          "retained per trial (empty: skip dataflash retention)")
    cap.add_argument("--dataflash-remote", default="",
                     help="host:/path of the log directory when the simulator runs on "
                          "ANOTHER machine (the usual SITL case), fetched per trial over "
                          "ssh/scp; only a log written during the trial is kept. Takes "
                          "precedence over --dataflash-dir")
    cap.add_argument("--vehicle-sysid", type=int, default=1,
                     help="MAVLink sysid of the autopilot, for the tap's direction "
                          "heuristic (default: %(default)s)")
    cap.add_argument("--telemetry-rate", type=float, default=10.0,
                     help="telemetry.csv sample rate in Hz (default: %(default)s)")
    cap.add_argument("--min-telemetry-rows", type=int, default=DEFAULT_MIN_TELEMETRY_ROWS,
                     help="a trial whose telemetry.csv has fewer data rows than this is "
                          "reported as degraded - it is how a recorder that never connected "
                          "is told apart from a short flight (default: %(default)s)")
    cap.add_argument("--require-complete-capture", action="store_true",
                     help="exit non-zero if ANY trial's bundle is degraded. What the N=5 "
                          "campaign runs with: a green exit code should mean the evidence "
                          "exists, not merely that the flights happened")

    # Manifest provenance (§6). Free-form; may be empty. JSON where noted.
    if model_provenance:
        cap.add_argument("--model", default="", help="LLM model id for the manifest provenance")
        cap.add_argument("--provider", default="", help="LLM provider (e.g. anthropic, openai)")
        cap.add_argument("--decoding", default="",
                         help="decoding settings as JSON, e.g. '{\"temperature\":0,\"seed\":1}'")
    cap.add_argument("--firmware", default="", help="autopilot firmware family (e.g. ArduCopter, PX4)")
    cap.add_argument("--firmware-version", default="", help="autopilot firmware version string")
    cap.add_argument("--sitl-host", default="",
                     help="hostname/address of the machine running the simulator, for the "
                          "manifest. Set it whenever the link goes through a local relay or "
                          "forward, where the endpoints no longer name the sim's machine")
    cap.add_argument("--sim-params", default="",
                     help="simulator params as JSON, e.g. '{\"frame\":\"quad\",\"wind\":0}'")


def build_capture_config(args, *, error, model=None, provider=None, decoding=None):
    """Build a ``CaptureConfig`` from parsed args, or ``None`` without ``--capture``.

    ``error`` is the failure callback (normally ``parser.error``) used for
    malformed JSON arguments. ``model`` / ``provider`` / ``decoding`` override
    the corresponding flags; the LLM harness passes the resolved route and the
    protocol options it is actually going to send, which is the truth, whereas
    the flags are only what someone typed.
    """
    if not getattr(args, "capture", False):
        if getattr(args, "require_complete_capture", False):
            # Otherwise the two flags together are a trap: a run that captured
            # nothing at all would satisfy "no bundle is degraded" and exit 0.
            error("--require-complete-capture has no meaning without --capture: "
                  "a run with no capture at all has no bundle to be complete")
        return None

    from droneserver.benchmark.capture_session import CaptureConfig

    def parse_json(value: str, flag: str) -> dict:
        if not value:
            return {}
        try:
            parsed = json.loads(value)
        except json.JSONDecodeError as e:
            error(f"{flag} must be valid JSON: {e}")
            return {}
        if not isinstance(parsed, dict):
            error(f"{flag} must be a JSON object")
            return {}
        return parsed

    if model is None:
        model = getattr(args, "model", "") or ""
    if provider is None:
        provider = getattr(args, "provider", "") or ""
    if decoding is None:
        decoding = parse_json(getattr(args, "decoding", "") or "", "--decoding")

    return CaptureConfig(
        mavlink_endpoint=args.mavlink_endpoint,
        telemetry_address=args.telemetry_address,
        dataflash_dir=Path(args.dataflash_dir) if args.dataflash_dir else None,
        dataflash_remote=args.dataflash_remote,
        vehicle_sysid=args.vehicle_sysid,
        rate_hz=args.telemetry_rate,
        min_telemetry_rows=args.min_telemetry_rows,
        model=model,
        provider=provider,
        decoding=decoding,
        firmware=args.firmware,
        firmware_version=args.firmware_version,
        sim_params=parse_json(args.sim_params, "--sim-params"),
        sitl_host=args.sitl_host,
        droneserver_commit=git_commit(),
    )


def report_capture(statuses, *, require_complete: bool, out=print) -> bool:
    """Print the run-end capture line. ``True`` when the run should fail.

    ``statuses`` is one ``capture_status`` string per trial that had capture on
    (``""`` for trials that did not). The line is printed whenever capture ran
    at all, including when everything is fine - "0 of 9 degraded" is the
    sentence that makes the absence of a warning mean something.
    """
    captured = [s for s in statuses if s]
    if not captured:
        return False
    degraded = [s for s in captured if not s.startswith("complete")]
    out(f"capture: {len(degraded)}/{len(captured)} trial(s) degraded"
        f"{' - every bundle is complete' if not degraded else ''}")
    for status in degraded:
        out(f"  {status}")
    if degraded and require_complete:
        out("ERROR: --require-complete-capture was set and at least one bundle is degraded")
        return True
    return False
