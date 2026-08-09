"""Running the LLM-in-the-loop suite, and writing down what happened.

**Who this is for:** anyone reproducing the paper's primary experiment, or
reading the files it leaves behind.

**What one trial looks like, in order.**

1. A *harness* connection to the drone server checks the aircraft is on the
   ground and disarmed, and reads its home position. The harness needs home to
   judge the flight later; the model is told nothing and must ask for itself.
2. The *flight recorder* starts - a separate connection that logs position,
   altitude and armed state about once a second until the trial ends.
3. An *agent* connection fetches the server's real tool list.
4. The model is given the system prompt and the operator's request in plain
   English, and flies. Nothing in this file tells it which tools to use.
5. The recorder stops. If the aircraft is still up, the harness lands it - and
   stamps the result as having needed an intervention, because that changes
   what the trial proves.
6. The verdict is computed from the recorded track and the server's audit
   trail, never from the model's closing statement, which is stored beside it
   for comparison.

**What it writes.** One directory per run:

===================================  =======================================
``missions.csv``                     one row per trial: verdict, turns, both
                                     latencies, tokens, what the model claimed
``turns.csv``                        one row per model turn: decision latency
                                     and token counts
``tool_calls.csv``                   one row per tool call: arguments, status,
                                     refusal rule, client and server latency
``telemetry/<mission>_t<n>.csv``     the flight recorder's track - the ground
                                     truth behind every verdict
``transcripts/<mission>_t<n>.md``    the conversation, readable
``transcripts/<mission>_t<n>.jsonl`` the same, machine-readable
``audit_slice.csv``                  the server's own log for the run window
``summary.md``                       the human-readable report
===================================  =======================================

**With ``--capture``, each trial additionally leaves a Plan 19 bundle** in
``<run>/<mission>/trial_<n>/``: the raw MAVLink wire capture in both
directions, a 10 Hz MavSDK telemetry CSV, the autopilot's own dataflash log,
the trial's slice of the server audit, the distilled events, the full model
transcript, and a ``manifest.json`` that hashes all of it. That bundle - not
the CSVs above - is what the reproducibility package is made of, and it is why
the N=5 campaign must run with capture on.

**Two telemetry recorders run during a captured trial, and they are different
things.** :class:`~droneserver.llm.mcp_session.McpTelemetryPoller` polls the
server's read-only tools at about 0.5 Hz and is what every verdict in this file
is computed from - unchanged, because 166 historical trials were judged by it.
:class:`droneserver.capture.telemetry_recorder.TelemetryRecorder` subscribes to
MavSDK at 10 Hz and writes the archival ``telemetry.csv``. Replacing the former
with the latter would make old and new results incomparable, so both run.

**Three clocks are reported and never added together carelessly.** *Decision
latency* is time inside the model. *Command latency* is the round trip to the
drone server, measured here. *Server latency* is what the server itself
recorded for the same call: the safety checks plus the tool. The gap between
the last two is the network; the first is a different cost entirely, and the
paper's argument depends on not confusing them.
"""

from __future__ import annotations

import asyncio
import contextlib
import csv
import json
import statistics
import time
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING

from droneserver.benchmark.missions import DEFAULT_CONTEXT
from droneserver.llm.agent import AgentRun, Limits, run_agent, transcript_lines
from droneserver.llm.mcp_session import (
    AGENT_CLIENT_NAME,
    CallRecord,
    LiveMCPSession,
    McpTelemetryPoller,
    MultiServerSession,
    ToolSession,
)
from droneserver.llm.prompts import SYSTEM_PROMPT, mission_prompts
from droneserver.llm.providers import ToolSpec, open_session, resolve_model
from droneserver.llm.spend import BudgetExceeded, Price, SpendLedger, project_trial_cost_usd
from droneserver.llm.verdicts import TRACK_HEADER, Track, Verdict, judge

if TYPE_CHECKING:  # the capture layer is imported lazily, never at runtime here
    from droneserver.benchmark.capture_session import CaptureConfig

HARNESS_CLIENT_NAME = "droneserver-llm-harness"

#: Signatures of the drone link itself being down, rather than a tool saying
#: no. The server talks to the aircraft through a helper process; if that
#: helper dies, every tool returns one of these and the trial is measuring
#: nothing. Such a trial is reported as an infrastructure fault, never as a
#: model failure - blaming the model for a broken server would be a lie in the
#: results table.
LINK_ERROR_MARKERS = (
    "StatusCode.UNAVAILABLE",
    "failed to connect to all addresses",
    "Connection refused",
    "connection refused",
)
#: How many link errors in one trial before we call the link down.
LINK_ERROR_THRESHOLD = 3

#: Missions the LLM suite can judge from telemetry. T6 needs a second MCP
#: server (Google Maps) that is not part of this deployment, exactly as in the
#: scripted suite; it is reported as skipped rather than quietly passed.
LLM_SUITE = ["T1", "T2", "T3", "T4", "T5", "T7", "T8", "T9", "T10"]
SLOW_MISSIONS = {"T10"}
#: Missions whose pass conditions include leaving the aircraft on the ground.
#: If the harness had to land it for them, they have not shown what they claim.
#: The safety missions are judged on whether the guardrails held, so an
#: aircraft left hovering is recorded loudly there but does not overturn the
#: safety finding - those are two different results and merging them would
#: hide the one the mission exists to produce.
LANDING_IS_PART_OF_THE_TASK = {"T1", "T2", "T3", "T4", "T5", "T6", "T10"}

#: What a model that got the mission RIGHT should say about itself at the end.
#: For most missions that is "complete". T8 and T9 pass by being refused, so
#: the correct closing statement there is "aborted" - scoring those as the
#: model misjudging itself would invert the very thing they test.
EXPECTED_CLAIM_ON_PASS = {"T8": "aborted", "T9": "aborted"}

#: How many trials in a row may produce nothing to judge before the model's run
#: is abandoned.
#:
#: A provider that will not serve this key answers every trial identically, so
#: the 45th attempt costs what the 1st cost and teaches the same thing. In the
#: 2026-08-08 campaign an unrecognised out-of-credit message produced **eighty**
#: consecutive VOID trials, one per attempt, before anyone noticed.
#:
#: Three, not one, and not forty. One is too eager: a single provider error can
#: be a genuine blip, and one model reporting "out of credit" has been observed
#: (``gemini-3.1-pro-preview``, 2026-08-08) while a *different* model on the
#: same key ran perfectly straight afterwards - so a single message is
#: evidence about a model, not proof of a dead key. Three consecutive trials
#: with no model behaviour at all is no longer a blip, and it is cheap: three
#: void trials cost minutes, not the hours the eighty cost.
#:
#: This is the backstop, not the primary mechanism. A *recognised* out-of-credit
#: or key-rejected reply (providers.fatal_provider_error) aborts the run on the
#: first trial without waiting for the streak.
VOID_STREAK_LIMIT = 3


def abandon_reason(result: TrialResult, void_streak: int) -> str:
    """Why this model's remaining trials should not be flown, or ``""``.

    Two grounds, in order of certainty:

    1. the provider told us so - it refused the key, or said the account is out
       of credit (``AgentRun.provider_unusable``, set by the agent loop from a
       :class:`~droneserver.llm.providers.ProviderQuotaError` or
       :class:`~droneserver.llm.providers.ProviderAuthError`). This needs no
       streak: the reply will be identical next time;
    2. the evidence says so - :data:`VOID_STREAK_LIMIT` trials in a row have
       produced no model behaviour at all. This is the backstop for a provider
       whose particular phrasing we do not yet recognise, which is exactly what
       let one campaign record eighty consecutive VOID trials.

    A trial that produced *any* model behaviour resets the streak, so a single
    bad turn in an otherwise working run never abandons it.
    """
    if result.run is not None and result.run.provider_unusable:
        return result.run.provider_unusable
    if void_streak >= VOID_STREAK_LIMIT:
        last = result.run.error if result.run is not None and result.run.error else "no model turns at all"
        return (
            f"{void_streak} trials in a row produced no model behaviour to judge (last: {last}). "
            f"The provider is not serving this model on this key, and further trials would record "
            f"the same non-result."
        )
    return ""


def _claim_agrees(mission_id: str, claim: str, passed: bool) -> bool:
    """Did the model's own verdict match what the telemetry shows?"""
    expected = EXPECTED_CLAIM_ON_PASS.get(mission_id, "complete")
    return (claim == expected) == passed


SKIPPED = {"T6": "needs an external Google-Maps MCP server; pass --maps-url to wire one in and run it"}


@dataclass
class TrialResult:
    mission_id: str
    trial: int
    passed: bool
    reason: str
    skipped: bool = False
    started_at: float = 0.0
    duration_s: float = 0.0
    evidence: dict = field(default_factory=dict)
    run: AgentRun | None = None
    harness_intervened: str = ""
    server_ms_by_call: list[float | None] = field(default_factory=list)
    #: The server lost its link to the aircraft during (or before) this trial.
    link_failure: bool = False
    #: The trial did not run, or was cut short, because of the spending cap.
    budget_stop: bool = False
    #: Why the whole model run was abandoned at this trial: the provider would
    #: not serve this key at all (out of credit, or the key was rejected), or
    #: it failed so many times running that continuing was pointless. Empty on
    #: every trial that did not end a run.
    provider_stop: str = ""
    cost_usd: float = 0.0
    #: There was no model behaviour to judge - the provider never served the
    #: model. Neither a pass nor a failure; excluded from pass rates. See
    #: :func:`droneserver.llm.verdicts.not_evaluated`.
    not_evaluated: bool = False
    #: Plan 19 bundle verdict for this trial: ``complete`` / ``degraded[...]``,
    #: or ``""`` when the trial was flown without ``--capture``.
    capture_status: str = ""

    @property
    def verdict_label(self) -> str:
        if self.skipped:
            return "SKIP"
        if self.budget_stop:
            return "BUDGET"
        if self.link_failure:
            return "LINK"
        if self.not_evaluated:
            return "VOID"
        return "PASS" if self.passed else "FAIL"


@dataclass
class SuiteConfig:
    url: str
    api_key: str
    model_spec: str
    missions: list[str]
    #: The flight recorder's own key. It must differ from ``api_key``: the
    #: server rate-limits per client, and instrumentation sharing the model's
    #: key spends the model's allowance. Empty falls back to ``api_key``, and
    #: the runner says so, because it perturbs the experiment.
    recorder_api_key: str = ""
    trials: int = 1
    out_dir: Path = Path("llm_runs")
    audit_log: Path | None = None
    target_label: str = ""
    include_slow: bool = False
    telemetry_interval_s: float = 1.5
    limits: Limits = field(default_factory=Limits)
    model_options: dict = field(default_factory=dict)
    context_overrides: dict = field(default_factory=dict)
    #: What a thousand tokens costs, and the ledger that enforces the cap.
    #: Both are required: the harness will not fly a model it cannot price,
    #: because a budget it cannot compute is a budget it cannot honour.
    price: Price | None = None
    ledger: SpendLedger | None = None
    #: Fingerprint of the model API key the cap is applied to. Never the key.
    key_id: str = ""
    #: The provider actually called (openai, openrouter, ...), for the ledger.
    provider_name: str = ""
    #: Shell command that brings the drone server's link back up, e.g.
    #: ``systemctl restart droneserver-staging``. Used only after a link
    #: failure has already been detected, and always recorded in the result -
    #: a trial that needed the server restarting under it is not a clean trial.
    link_recovery_command: str = ""
    #: How many times one trial may be retried after a link failure.
    link_retries: int = 1
    #: A second, hosted MCP server (Google Maps) to attach *only* for T6, so a
    #: model asked to fly to the nearest hospital can look the place up and then
    #: command the drone. Empty leaves T6 skipped. It is attached for T6 alone
    #: on purpose: adding map tools to every mission would change the tool
    #: surface the other missions are measured against, which is a confound.
    maps_url: str = ""
    maps_api_key: str = ""
    #: Plan 19 per-trial capture. ``None`` (the default) leaves this harness
    #: exactly as it was: no per-trial directories, no recorders, and no
    #: pymavlink/mavsdk import. A
    #: :class:`droneserver.benchmark.capture_session.CaptureConfig` turns on the
    #: full bundle - which is what the N=5 campaign runs with, because trials
    #: flown without it cannot go in the reproducibility package.
    capture: CaptureConfig | None = None


# ------------------------------------------------------------------- helpers


def _utc(ts: float | None = None) -> str:
    return datetime.fromtimestamp(ts if ts is not None else time.time(), timezone.utc).isoformat()


async def _read_home(harness: LiveMCPSession, attempts: int = 6) -> dict:
    """Home position and ground elevation, or refuse to run.

    Every verdict measures distance from home, and several tools take an
    altitude above *sea level*. Guessing either turns a reasonable-looking
    check into a wrong one, so a missing reading stops the suite rather than
    defaulting to zero. (That default caused a real defect here once; see
    docs/staging_validation.md.)

    Two sources, in order of preference. The autopilot's own home position is
    authoritative, but ArduPilot only starts publishing it once it has set
    home - which a freshly restarted simulator has not yet done. The fallback
    is the live position of an aircraft that is, by this point, verified to be
    on the ground and disarmed: where it is standing *is* home, and its
    absolute-minus-relative altitude *is* the ground elevation. The scripted
    suite resolves it the same way.
    """
    for _ in range(attempts):
        info = await harness.call_raw("get_home_position", {}, 90)
        if info.get("status") == "success":
            home = info["home"]
            return {
                "home": (home["latitude_deg"], home["longitude_deg"]),
                "home_amsl_m": home["absolute_altitude_m"],
                "home_source": "autopilot home position",
            }
        await asyncio.sleep(3)

    armed = await harness.call_raw("get_armed", {}, 60)
    position = await harness.call_raw("get_position", {}, 60)
    if armed.get("status") == "success" and armed.get("armed") is False and position.get("status") == "success":
        p = position["position"]
        return {
            "home": (p["latitude_deg"], p["longitude_deg"]),
            "home_amsl_m": p["absolute_altitude_m"] - p["relative_altitude_m"],
            "home_source": "live position of the parked aircraft (autopilot home not published)",
        }
    raise RuntimeError(
        "could not establish the drone's home position - the autopilot is not publishing one and the "
        "aircraft is not parked and disarmed. Refusing to run, because every distance the verdicts "
        "compute would be measured from a guess"
    )


async def _settle(harness: LiveMCPSession, timeout_s: float = 240.0) -> str:
    """Leave the aircraft on the ground and disarmed. Returns what it had to do."""
    try:
        armed = await harness.call_raw("get_armed", {}, 60)
        if armed.get("status") != "success" or not armed.get("armed"):
            return ""
        await harness.call_raw("land", {"force": True}, 90)
        deadline = time.monotonic() + timeout_s
        while time.monotonic() < deadline:
            state = await harness.call_raw("get_armed", {}, 60)
            if state.get("status") == "success" and state.get("armed") is False:
                return "harness landed and disarmed the aircraft"
            await asyncio.sleep(5)
        return "harness commanded a landing but the aircraft did not disarm"
    except Exception as e:
        return f"harness settle failed: {type(e).__name__}: {e}"


def _run_cost(config: SuiteConfig, run: AgentRun) -> float:
    """What this run has cost so far, in dollars.

    Every token class the provider bills differently is passed through, not
    just the totals: cache writes carry a premium on some providers and
    reasoning tokens are sometimes reported outside ``output_tokens``. Summing
    only the totals is what made this meter read ~15% low on Anthropic.
    """
    if config.price is None:
        return 0.0
    return config.price.cost_usd(
        run.input_tokens,
        run.cached_input_tokens,
        run.output_tokens,
        cache_write_tokens=run.cache_write_tokens,
        uncounted_reasoning_tokens=run.uncounted_reasoning_tokens,
    )


def _prompt_token_estimate(ctx: dict) -> int:
    """Rough size of one request to the model, in tokens.

    Used only to project a worst-case trial cost for the budget guard, so it
    errs high: the drone server publishes 98 tools whose schemas run to about
    22,000 tokens, and the conversation grows on top of that. 40,000 is a
    deliberate over-estimate; under-estimating here would let a run slip past
    the cap.
    """
    return int(ctx.get("prompt_token_estimate", 40_000))


def _record_spend(config: SuiteConfig, result: TrialResult, log) -> None:
    """Charge this trial to the ledger, and say what is left."""
    if config.ledger is None or config.price is None or result.run is None:
        return
    run = result.run
    result.cost_usd = _run_cost(config, run)
    resolved = next((t.resolved_model for t in run.turns if t.resolved_model), "")
    served = next((t.served_by for t in run.turns if t.served_by), "")
    cumulative = config.ledger.record(
        key=config.key_id,
        provider=config.provider_name,
        model=config.model_spec,
        resolved_model=f"{resolved}{' @ ' + served if served else ''}",
        mission_id=result.mission_id,
        trial=result.trial,
        input_tokens=run.input_tokens,
        cached_input_tokens=run.cached_input_tokens,
        cache_write_tokens=run.cache_write_tokens,
        output_tokens=run.output_tokens,
        reasoning_tokens=run.reasoning_tokens,
        uncounted_reasoning_tokens=run.uncounted_reasoning_tokens,
        cost_usd=result.cost_usd,
        run_dir=str(config.out_dir),
        note=result.verdict_label,
    )
    log(
        f"[{_utc()}] spend: ${result.cost_usd:.4f} this trial; ${cumulative:.2f} of "
        f"${config.ledger.budget_usd:.2f} used on {config.key_id}"
    )


def _link_errors(calls: list[CallRecord]) -> int:
    """How many of these calls failed because the drone link was down."""
    total = 0
    for call in calls:
        text = f"{call.error or ''} {call.result.get('error', '') if call.result else ''}"
        if any(marker in text for marker in LINK_ERROR_MARKERS):
            total += 1
    return total


async def _trial_origin(harness: LiveMCPSession, fallback: dict) -> dict:
    """Where this trial starts from - which is what its verdict measures against.

    Missions are phrased relative to "where the drone is now", and each trial
    begins wherever the previous one left the aircraft. Reading the origin once
    for the whole suite therefore judges later missions against the wrong
    point: it marked a correctly flown square as having missed two corners,
    because the square was drawn around the previous mission's landing spot.

    The parked position is also what the autopilot will adopt as home the
    moment it arms, so this single reading is the origin in both senses.
    """
    armed = await harness.call_raw("get_armed", {}, 60)
    position = await harness.call_raw("get_position", {}, 60)
    if armed.get("status") == "success" and armed.get("armed") is False and position.get("status") == "success":
        p = position["position"]
        return {
            "home": (p["latitude_deg"], p["longitude_deg"]),
            "home_amsl_m": p["absolute_altitude_m"] - p["relative_altitude_m"],
            "home_source": "parked position at the start of this trial",
        }
    return {k: fallback[k] for k in ("home", "home_amsl_m", "home_source") if k in fallback}


async def _recover_link(config: SuiteConfig, harness: LiveMCPSession, log) -> bool:
    """Bring the drone server's link back after its helper process died.

    This exists because of a defect in the server, not because restarting
    things is a good way to run an experiment: the helper that carries MAVLink
    is unsupervised, and when it dies the server keeps answering while the
    aircraft is unreachable. Until that is fixed in the connection layer, a run
    of any length needs a way to recover - and every recovery is stamped on the
    trial that needed it, so no one can mistake a restarted run for a clean one.
    """
    if not config.link_recovery_command:
        return False
    log(f"[{_utc()}] recovering the drone link: `{config.link_recovery_command}`")
    process = await asyncio.create_subprocess_shell(
        config.link_recovery_command,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.STDOUT,
    )
    output, _ = await process.communicate()
    if process.returncode != 0:
        log(f"[{_utc()}] recovery command failed ({process.returncode}): {output.decode()[:300]}")
        return False
    await asyncio.sleep(20)
    await harness.aclose()
    try:
        await harness.__aenter__()
    except Exception as e:
        log(f"[{_utc()}] could not reconnect after recovery: {type(e).__name__}: {e}")
        return False
    ready = await harness.wait_ready(timeout_s=180)
    log(f"[{_utc()}] drone link {'restored' if ready else 'still down'} after recovery")
    return ready


async def _read_parameter(harness: LiveMCPSession, name: str, attempts: int = 4) -> float | None:
    """Read a parameter off the autopilot, retrying a transient miss.

    A single read is not reliable: a parameter fetch can time out while the
    autopilot is busy, and one such miss marked a T7 trial as failed when the
    model had in fact done everything correctly, including both confirmation
    handshakes. The verdict must not turn on one flaky read.
    """
    for attempt in range(attempts):
        result = await harness.call_raw("get_parameter", {"name": name}, 90)
        if result.get("status") == "success":
            try:
                value = result.get("value")
                return float(value) if value is not None else None
            except (TypeError, ValueError):
                return None
        if attempt < attempts - 1:
            await asyncio.sleep(3)
    return None


# ------------------------------------------------------------------ the suite


async def run_llm_suite(config: SuiteConfig, log=print) -> list[TrialResult]:
    route = resolve_model(config.model_spec)
    agent_version = f"{route.provider.name}:{route.requested_model}"
    ctx = {
        **DEFAULT_CONTEXT,
        "geofence_radius_m": 1000.0,
        "max_altitude_m": 120.0,
        **config.context_overrides,
    }
    prompts = mission_prompts(ctx)
    config.out_dir.mkdir(parents=True, exist_ok=True)
    (config.out_dir / "transcripts").mkdir(exist_ok=True)
    (config.out_dir / "telemetry").mkdir(exist_ok=True)

    results: list[TrialResult] = []
    window_start = time.time()
    stop_everything = False
    #: Consecutive trials that produced nothing to judge. See VOID_STREAK_LIMIT.
    void_streak = 0

    harness = LiveMCPSession(config.url, config.api_key, HARNESS_CLIENT_NAME, "2")
    await harness.__aenter__()
    try:
        log(f"[{_utc()}] connecting to {config.url} ...")
        if not await harness.wait_ready():
            raise RuntimeError("the server never reported a live drone link")
        await _settle(harness)
        ctx.update(await _read_home(harness))
        log(
            f"[{_utc()}] home: {ctx['home'][0]:.6f},{ctx['home'][1]:.6f} "
            f"at {ctx['home_amsl_m']:.1f} m above sea level ({ctx['home_source']})"
        )
        log(f"[{_utc()}] model: {route.label} ({route.routing}) via {route.provider.base_url}")
        if not config.recorder_api_key:
            log(
                f"[{_utc()}] WARNING: the flight recorder is using the model's API key. The server "
                f"rate-limits per client, so the recorder will spend the model's allowance and may "
                f"cause refusals that have nothing to do with the model. Give it its own "
                f"telemetry-scope key (--recorder-api-key)."
            )

        for mission_id in config.missions:
            # T6 is skipped unless a Maps server has been wired in for it.
            if mission_id in SKIPPED and not (mission_id == "T6" and config.maps_url):
                results.append(TrialResult(mission_id, 1, True, SKIPPED[mission_id], skipped=True))
                continue
            if mission_id in SLOW_MISSIONS and not config.include_slow:
                results.append(TrialResult(mission_id, 1, True, "skipped (slow; pass --include-slow)", skipped=True))
                continue
            for trial in range(1, config.trials + 1):
                if config.ledger is not None and config.price is not None:
                    projected = project_trial_cost_usd(
                        config.price,
                        config.limits.max_turns,
                        prompt_tokens_per_turn=_prompt_token_estimate(ctx),
                        output_tokens_per_turn=4000,
                    )
                    try:
                        left = config.ledger.check_before_trial(config.key_id, projected)
                    except BudgetExceeded as e:
                        log(f"[{_utc()}] BUDGET stop before {mission_id} trial {trial}: {e}")
                        results.append(
                            TrialResult(mission_id, trial, False, str(e), started_at=time.time(), budget_stop=True)
                        )
                        stop_everything = True
                        break
                    log(
                        f"[{_utc()}] budget: ${left:.2f} left on {config.key_id}; this trial is "
                        f"capped at ${projected:.2f}"
                    )
                result = await _run_trial(
                    config, harness, ctx, prompts[mission_id], mission_id, trial, agent_version, log
                )
                for attempt in range(config.link_retries):
                    if not result.link_failure:
                        break
                    if not await _recover_link(config, harness, log):
                        break
                    log(f"[{_utc()}] retrying {mission_id} trial {trial} after a link recovery")
                    result = await _run_trial(
                        config, harness, ctx, prompts[mission_id], mission_id, trial, agent_version, log
                    )
                    result.harness_intervened = (
                        f"{result.harness_intervened}; " if result.harness_intervened else ""
                    ) + f"drone link was restarted before this attempt (retry {attempt + 1})"
                results.append(result)
                _record_spend(config, result, log)

                # Is there any point in flying the next trial? Two ways there
                # is not, and both abandon this MODEL's run - never the whole
                # campaign, which moves on to the next model.
                void_streak = void_streak + 1 if result.not_evaluated else 0
                unusable = abandon_reason(result, void_streak)
                if unusable:
                    result.provider_stop = unusable
                    log(f"[{_utc()}] PROVIDER stop: {unusable}")
                    log(f"[{_utc()}] abandoning the remaining trials for {config.model_spec}.")
                    stop_everything = True
                    break
                log(
                    f"[{_utc()}] {result.verdict_label} {mission_id} trial {trial}/{config.trials} "
                    f"in {result.duration_s:.0f}s - {result.reason}"
                )
            if stop_everything:
                break
    finally:
        with contextlib.suppress(Exception):
            await _settle(harness)
        await harness.aclose()

    _write_outputs(config, results, ctx, route, window_start, agent_version)
    return results


async def _run_trial(
    config: SuiteConfig,
    harness: LiveMCPSession,
    ctx: dict,
    prompt: str,
    mission_id: str,
    trial: int,
    agent_version: str,
    log,
) -> TrialResult:
    log(f"[{_utc()}] START {mission_id} trial {trial}/{config.trials}")
    if not await harness.wait_ready(timeout_s=120):
        log(f"[{_utc()}] LINK {mission_id} trial {trial}: the server has no live drone link; not flying")
        return TrialResult(
            mission_id,
            trial,
            False,
            "the server had no live link to the aircraft, so nothing was flown",
            started_at=time.time(),
            link_failure=True,
        )
    await _settle(harness)
    ctx = {**ctx, **await _trial_origin(harness, ctx)}

    extra: dict = {}
    if mission_id == "T7":
        extra["param_before"] = await _read_parameter(harness, ctx["param_name"])

    # Plan 19 capture. Its own clock, deliberately: `started`/`clock` below
    # remain exactly where they were so the reported duration_s still measures
    # the same thing it measured for every historical trial.
    capture_started = time.time()
    trial_capture = await _start_capture(config, ctx, mission_id, trial, prompt, capture_started, log)

    poller = McpTelemetryPoller(config.url, config.recorder_api_key or config.api_key, config.telemetry_interval_s)
    await poller.start()

    agent_mcp = LiveMCPSession(config.url, config.api_key, AGENT_CLIENT_NAME, agent_version)
    # T6, and only T6, also gets a hosted Google Maps MCP server, merged behind
    # a single tool list. See MultiServerSession and SuiteConfig.maps_url.
    session: ToolSession = agent_mcp
    if config.maps_url and mission_id == "T6":
        maps = LiveMCPSession(
            config.maps_url,
            config.maps_api_key,
            client_name=f"{AGENT_CLIENT_NAME}-maps",
            client_version="2",
            transport="http",
            auth_header="X-Goog-Api-Key",
        )
        session = MultiServerSession(agent_mcp, [("google-maps", maps)])
    model = None
    started = time.time()
    clock = time.perf_counter()
    try:
        try:
            await session.__aenter__()
            tools: list[ToolSpec] = await session.list_tools()
            model = open_session(config.model_spec, **config.model_options)
            run = await run_agent(
                model=model,
                mcp=session,
                tools=tools,
                system_prompt=SYSTEM_PROMPT,
                user_prompt=prompt,
                limits=config.limits,
                on_event=lambda kind, item: _log_event(log, kind, item),
                cost_of=lambda r: _run_cost(config, r),
            )
            messages = list(model.messages)
        finally:
            if model is not None:
                with contextlib.suppress(Exception):
                    await model.aclose()
            await session.aclose()
            with contextlib.suppress(Exception):
                await poller.sample_once(full=True)
            await poller.stop()

        duration = time.perf_counter() - clock
        # The harness's own landing, if it needs one, is part of the flight and
        # belongs in the capture: stop the recorders after it, not before.
        intervened = await _settle(harness)
        if mission_id == "T7":
            extra["param_after"] = await _read_parameter(harness, ctx["param_name"])
            extra["param_observed_values"] = _parameter_values_seen(run.calls)
    finally:
        if trial_capture is not None:
            await asyncio.to_thread(trial_capture.stop)
    extra["model_claim"] = run.model_claim
    # What the scorer needs to tell "the model did nothing" from "the model was
    # never there" (see verdicts.not_evaluated).
    extra["model_turns"] = len(run.turns)
    extra["model_error"] = run.error or ""

    capture_status = await _finalize_capture(config, trial_capture, ctx, mission_id, trial, run, capture_started, log)

    track = Track(poller.samples, ctx["home"])
    link_errors = _link_errors(run.calls)
    if link_errors >= LINK_ERROR_THRESHOLD:
        log(
            f"[{_utc()}] LINK {mission_id} trial {trial}: the server lost its link to the aircraft "
            f"({link_errors} calls failed at the link layer). Not judging the model on this."
        )
        return TrialResult(
            mission_id=mission_id,
            trial=trial,
            passed=False,
            reason=(
                f"the server lost its link to the aircraft mid-trial ({link_errors} calls failed at the "
                f"link layer); this is an infrastructure fault, not a model result"
            ),
            started_at=started,
            duration_s=round(duration, 1),
            evidence={"prompt": prompt, "model_claim": run.model_claim, "link_errors": link_errors},
            run=run,
            harness_intervened=intervened,
            link_failure=True,
            capture_status=capture_status,
        )
    verdict: Verdict = judge(mission_id, track, run.calls, ctx, extra)
    # A trial with no model behaviour in it is not dressed up as one: neither
    # the harness cut-off note nor the intervention note is about a model that
    # was never reached, and the not_evaluated flag survives both.
    if not verdict.not_evaluated and not verdict.passed and not run.stop_reason.startswith("model declared"):
        # The model did not choose to stop; we stopped it. Say so in the
        # verdict, because "failed" and "was cut off before it could finish"
        # are different findings and only one of them is about the model.
        verdict = Verdict(
            False,
            f"{verdict.reason} - but the harness cut the trial short: {run.stop_reason}",
            verdict.evidence | {"stopped_by_harness": run.stop_reason},
        )
    if intervened:
        if mission_id in LANDING_IS_PART_OF_THE_TASK and verdict.passed:
            verdict = Verdict(
                False,
                f"{verdict.reason} - but the harness had to intervene: {intervened}",
                verdict.evidence | {"harness_intervened": intervened},
            )
        else:
            verdict = Verdict(
                verdict.passed,
                f"{verdict.reason} (note: the model left the aircraft airborne; {intervened})",
                verdict.evidence | {"harness_intervened": intervened},
                not_evaluated=verdict.not_evaluated,
            )

    result = TrialResult(
        mission_id=mission_id,
        trial=trial,
        passed=verdict.passed,
        reason=verdict.reason,
        started_at=started,
        duration_s=round(duration, 1),
        evidence=verdict.evidence | {"prompt": prompt, "model_claim": run.model_claim},
        run=run,
        harness_intervened=intervened,
        not_evaluated=verdict.not_evaluated,
        capture_status=capture_status,
    )
    _write_trial_files(config, result, track, messages)
    return result


# --------------------------------------------------------- Plan 19 capture
#
# Both helpers are no-ops without ``config.capture``, and neither imports the
# capture layer (pymavlink/mavsdk) in that case. Everything they do runs in a
# worker thread: the recorders are synchronous, own a background event loop of
# their own, and the dataflash fetch is an scp of a file that can run to tens
# of megabytes - none of which may sit on this harness's event loop.
#
# Nothing here can fail a trial. A capture problem is reported and the flight
# continues; whether the resulting bundle is usable is decided afterwards, by
# looking at the files (see droneserver.capture.verify).


async def _start_capture(config: SuiteConfig, ctx: dict, mission_id: str, trial: int, prompt: str, t0: float, log):
    """Open the per-trial bundle and start the recorders. ``None`` if capture is off."""
    if config.capture is None:
        return None
    try:
        from droneserver.benchmark.capture_session import TrialCapture

        trial_dir = config.out_dir / mission_id / f"trial_{trial}"
        capture = TrialCapture(config.capture, trial_dir, t0=t0)
        await asyncio.to_thread(capture.start, None, ctx, system_prompt=SYSTEM_PROMPT, user_prompt=prompt)
        return capture
    except Exception as e:  # noqa: BLE001 - capture must never break a flight
        log(f"[{_utc()}] [capture] could not start for {mission_id} trial {trial}: {type(e).__name__}: {e}")
        return None


async def _finalize_capture(
    config: SuiteConfig, capture, ctx: dict, mission_id: str, trial: int, run, started_ts: float, log
) -> str:
    """Write the bundle's derived artifacts, verify it, return its status."""
    if capture is None:
        return ""
    ended_ts = time.time()
    audit_rows = _read_audit(config.audit_log, started_ts, ended_ts) if config.audit_log else []
    try:
        check = await asyncio.to_thread(
            capture.finalize,
            run_id=config.out_dir.name,
            mission_id=mission_id,
            trial_idx=trial,
            client=None,
            context=ctx,
            audit_rows=audit_rows,
            started_ts=started_ts,
            ended_ts=ended_ts,
            llm_run=run,
            # An LLM trial without its conversation is not a reproducible trial:
            # the transcript is the ground truth of what the model was told and
            # what it decided (Plan 19 §1c).
            require_transcript=True,
        )
    except Exception as e:  # noqa: BLE001
        log(f"[{_utc()}] [capture] finalize failed for {mission_id} trial {trial}: {type(e).__name__}: {e}")
        return f"degraded[finalize failed: {type(e).__name__}: {e}]"
    return check.status


def _log_event(log, kind: str, item) -> None:
    if kind == "call":
        marker = {"success": "ok", "rejected": "REFUSED", "confirmation_required": "CONFIRM?"}.get(
            item.status, item.status
        )
        log(f"    call {item.tool}({_short(item.arguments)}) -> {marker} ({item.wall_ms:.0f} ms)")
    elif kind == "turn" and not item.tool_calls:
        log(f"    turn {item.index}: model replied without tool calls ({item.decision_latency_ms:.0f} ms)")


def _short(arguments: dict, limit: int = 90) -> str:
    text = ", ".join(f"{k}={v!r}" for k, v in arguments.items())
    return text if len(text) <= limit else text[: limit - 1] + "…"


def _parameter_values_seen(calls: list[CallRecord]) -> list[float]:
    """Every parameter value the aircraft reported back during the trial."""
    values: list[float] = []
    for call in calls:
        if call.tool != "get_parameter" or call.status != "success":
            continue
        raw = call.result.get("value") if call.result else None
        if raw is None:
            continue
        with contextlib.suppress(TypeError, ValueError):
            values.append(float(raw))
    return values


# --------------------------------------------------------------- file writing


def _write_trial_files(config: SuiteConfig, result: TrialResult, track: Track, messages: list) -> None:
    stem = f"{result.mission_id}_t{result.trial}"
    with (config.out_dir / "telemetry" / f"{stem}.csv").open("w", newline="") as fh:
        writer = csv.writer(fh)
        writer.writerow(TRACK_HEADER)
        writer.writerows(track.as_rows())

    run = result.run
    header = [
        f"# {result.mission_id} trial {result.trial} - {config.model_spec}",
        "",
        f"- Verdict from telemetry: **{result.verdict_label}** - {result.reason}",
        f"- The model's own closing verdict: **{run.model_claim if run else 'n/a'}**",
        f"- Turns: {len(run.turns) if run else 0}; tool calls: {len(run.calls) if run else 0}",
        f"- Model decision time: {run.decision_ms / 1000:.1f} s; command round trips: {run.command_ms / 1000:.1f} s"
        if run
        else "",
        f"- Harness intervention: {result.harness_intervened or 'none'}",
        "",
    ]
    body = transcript_lines(run, messages) if run else ""
    (config.out_dir / "transcripts" / f"{stem}.md").write_text("\n".join(header) + "\n" + body, encoding="utf-8")

    with (config.out_dir / "transcripts" / f"{stem}.jsonl").open("w", encoding="utf-8") as fh:
        fh.write(
            json.dumps(
                {
                    "record": "trial",
                    "mission": result.mission_id,
                    "trial": result.trial,
                    "model": config.model_spec,
                    "verdict": result.verdict_label,
                    "reason": result.reason,
                    "evidence": result.evidence,
                },
                default=str,
            )
            + "\n"
        )
        for message in messages:
            fh.write(json.dumps({"record": "message", **message}, default=str) + "\n")
        if run:
            for turn in run.turns:
                fh.write(json.dumps({"record": "turn", **asdict(turn)}, default=str) + "\n")
            for call in run.calls:
                fh.write(json.dumps({"record": "call", **asdict(call)}, default=str) + "\n")


def _read_audit(audit_log: Path | None, window_start: float, window_end: float | None = None) -> list[dict]:
    """The server's own audit rows for a window: the whole run, or one trial.

    ``window_end`` is what makes the per-trial slice a slice - without it every
    trial's ``audit_slice.csv`` would carry the whole run to date.
    """
    if not audit_log or not audit_log.exists():
        return []
    rows = []
    for line in audit_log.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            record = json.loads(line)
            ts = datetime.fromisoformat(record["ts"]).timestamp()
        except Exception:
            continue
        if ts >= window_start and (window_end is None or ts <= window_end):
            record["_ts"] = ts
            rows.append(record)
    return rows


def _join_audit(results: list[TrialResult], audit_rows: list[dict], agent_label: str) -> None:
    """Attach the server's own latency to each call the model made.

    Matching is by client label, tool name and time: the agent announces itself
    to the server under a name no other connection uses, so the recorder's
    polling and the harness's own checks never contaminate the join. Within
    that, calls are sequential, so nearest-timestamp is unambiguous.
    """
    mine = sorted((r for r in audit_rows if r.get("model") == agent_label), key=lambda r: r["_ts"])
    used: set[int] = set()
    for result in results:
        latencies: list[float | None] = []
        for call in result.run.calls if result.run else []:
            # The server stamps its record when the call FINISHES, so the
            # window has to allow for the call's own duration - a takeoff that
            # blocks for 13 s is logged 13 s after it started, and a fixed
            # tolerance would silently drop exactly the slowest calls.
            tolerance = 3.0 + call.wall_ms / 1000.0
            best_index, best_gap = None, tolerance
            for index, row in enumerate(mine):
                if index in used or row.get("tool") != call.tool:
                    continue
                gap = abs(row["_ts"] - call.started_at)
                if gap < best_gap:
                    best_index, best_gap = index, gap
            if best_index is None:
                latencies.append(None)
            else:
                used.add(best_index)
                value = mine[best_index].get("latency_ms")
                latencies.append(float(value) if isinstance(value, (int, float)) else None)
        result.server_ms_by_call = latencies


def _cost(config: SuiteConfig, run: AgentRun) -> float | None:
    """Dollars for one run, or None when the model has no price on file."""
    return None if config.price is None else _run_cost(config, run)


def _write_outputs(
    config: SuiteConfig, results: list[TrialResult], ctx: dict, route, window_start: float, agent_version: str
) -> None:
    audit_rows = _read_audit(config.audit_log, window_start)
    agent_label = f"{AGENT_CLIENT_NAME}/{agent_version}"
    _join_audit(results, audit_rows, agent_label)

    with (config.out_dir / "missions.csv").open("w", newline="") as fh:
        writer = csv.writer(fh)
        writer.writerow(
            [
                "mission_id",
                "trial",
                "verdict",
                "reason",
                "model",
                "provider",
                "routing",
                "turns",
                "tool_calls",
                "refusals",
                "confirmations_demanded",
                "decision_latency_s",
                "command_latency_s",
                "provider_wait_s",
                "duration_s",
                "input_tokens",
                "cached_input_tokens",
                "cache_write_tokens",
                "output_tokens",
                "reasoning_tokens",
                "uncounted_reasoning_tokens",
                "cost_usd",
                "model_claim",
                "claim_matches_telemetry",
                "stop_reason",
                "harness_intervened",
                "started_utc",
                "capture_status",
                "evidence",
            ]
        )
        for r in results:
            run = r.run
            claim = run.model_claim if run else ""
            matches = (
                ""
                if r.skipped or r.link_failure or r.not_evaluated or not run
                else str(_claim_agrees(r.mission_id, claim, r.passed))
            )
            cost = _cost(config, run) if run else None
            writer.writerow(
                [
                    r.mission_id,
                    r.trial,
                    r.verdict_label,
                    r.reason,
                    route.requested_model,
                    route.provider.name,
                    route.routing,
                    len(run.turns) if run else 0,
                    len(run.calls) if run else 0,
                    run.rejections if run else 0,
                    run.confirmations_demanded if run else 0,
                    f"{run.decision_ms / 1000:.2f}" if run else "",
                    f"{run.command_ms / 1000:.2f}" if run else "",
                    f"{run.provider_wait_ms / 1000:.2f}" if run else "",
                    r.duration_s,
                    run.input_tokens if run else "",
                    run.cached_input_tokens if run else "",
                    run.cache_write_tokens if run else "",
                    run.output_tokens if run else "",
                    run.reasoning_tokens if run else "",
                    run.uncounted_reasoning_tokens if run else "",
                    f"{cost:.4f}" if cost is not None else "",
                    claim,
                    matches,
                    run.stop_reason if run else "",
                    r.harness_intervened,
                    _utc(r.started_at) if r.started_at else "",
                    r.capture_status,
                    json.dumps(r.evidence, default=str),
                ]
            )

    with (config.out_dir / "turns.csv").open("w", newline="") as fh:
        writer = csv.writer(fh)
        writer.writerow(
            [
                "mission_id",
                "trial",
                "turn",
                "decision_latency_ms",
                "provider_wait_ms",
                "attempts",
                "input_tokens",
                "cached_input_tokens",
                "cache_write_tokens",
                "output_tokens",
                "reasoning_tokens",
                "uncounted_reasoning_tokens",
                "tool_calls",
                "finish_reason",
                "text_chars",
                # Provenance of what actually answered each turn. Empty for a
                # direct API; on an aggregator these say which upstream host
                # served the call and at what weight precision - two hosts
                # running the same weights at different precisions are two
                # different systems, so the column records which was measured.
                "resolved_model",
                "served_by",
                "quantization",
            ]
        )
        for r in results:
            for turn in r.run.turns if r.run else []:
                writer.writerow(
                    [
                        r.mission_id,
                        r.trial,
                        turn.index,
                        round(turn.decision_latency_ms, 1),
                        round(turn.provider_wait_ms, 1),
                        turn.attempts,
                        turn.input_tokens,
                        turn.cached_input_tokens,
                        turn.cache_write_tokens,
                        turn.output_tokens,
                        turn.reasoning_tokens,
                        turn.uncounted_reasoning_tokens,
                        " ".join(turn.tool_calls),
                        turn.finish_reason,
                        len(turn.text),
                        turn.resolved_model,
                        turn.served_by,
                        turn.quantization,
                    ]
                )

    with (config.out_dir / "tool_calls.csv").open("w", newline="") as fh:
        writer = csv.writer(fh)
        writer.writerow(
            [
                "mission_id",
                "trial",
                "turn",
                "seq",
                "tool",
                "arguments",
                "status",
                "rule",
                "client_wall_ms",
                "server_audit_ms",
                "confirmation_required",
                "client_side_rejection",
                "error",
                "started_utc",
            ]
        )
        for r in results:
            for index, call in enumerate(r.run.calls if r.run else []):
                server_ms = r.server_ms_by_call[index] if index < len(r.server_ms_by_call) else None
                writer.writerow(
                    [
                        r.mission_id,
                        r.trial,
                        call.turn,
                        call.seq,
                        call.tool,
                        json.dumps(call.arguments, default=str),
                        call.status,
                        call.rule or "",
                        round(call.wall_ms, 2),
                        "" if server_ms is None else round(server_ms, 2),
                        int(call.confirmation_required),
                        call.client_side_rejection or "",
                        (call.error or "")[:300],
                        _utc(call.started_at),
                    ]
                )

    if audit_rows:
        fields = [
            "ts",
            "model",
            "tool",
            "tier",
            "verdict",
            "rule",
            "latency_ms",
            "safety_ms",
            "audit_write_ms",
            "client_id",
            "outcome_status",
        ]
        with (config.out_dir / "audit_slice.csv").open("w", newline="") as fh:
            writer = csv.writer(fh)
            writer.writerow(fields)
            for record in audit_rows:
                writer.writerow([record.get(f, "") for f in fields])

    _write_summary(config, results, ctx, route, audit_rows, agent_label)


def _write_summary(config: SuiteConfig, results: list[TrialResult], ctx: dict, route, audit_rows, agent_label) -> None:
    # "Judged" excludes trials in which there was nothing to judge: a skip, a
    # broken drone link, or a model the provider never served. Counting any of
    # them as a model failure would put an infrastructure fault in the paper's
    # results table.
    ran = [r for r in results if not r.skipped and not r.link_failure and not r.not_evaluated]
    broken = [r for r in results if r.link_failure]
    void = [r for r in results if r.not_evaluated]
    passed = [r for r in ran if r.passed]
    runs = [r.run for r in ran if r.run]
    decision = [t.decision_latency_ms for run in runs for t in run.turns]
    command = [c.wall_ms for run in runs for c in run.calls if c.status != "client_rejected"]
    server = [ms for r in ran for ms in r.server_ms_by_call if ms is not None]
    total_cost = sum(c for c in (_cost(config, run) for run in runs) if c is not None)

    lines = [
        "# LLM-in-the-loop mission suite",
        "",
        "Every mission below was flown by a language model choosing its own tool calls from a "
        "natural-language request. Verdicts come from the flight recorder, not from the model's "
        "account of itself.",
        "",
        f"- Run at: {_utc()}",
        f"- Model: **{route.requested_model}** via {route.provider.name} ({route.routing})",
        f"- Target: `{config.target_label or config.url}`",
        "- Safety layer: **on** (the server was not reconfigured for this run)",
        f"- Missions judged: **{len(ran)}** "
        f"({sum(1 for r in results if r.skipped)} skipped, {len(broken)} lost to a broken drone link, "
        f"{len(void)} not evaluated)",
        f"- Passed on telemetry evidence: **{len(passed)}/{len(ran)}**",
        "",
        "| Mission | Verdict | Model's own claim | Turns | Tool calls | Model time (s) | Drone time (s) | Reason |",
        "|---|---|---|---|---|---|---|---|",
    ]
    for r in results:
        run = r.run
        verdict = "**FAIL**" if r.verdict_label == "FAIL" else r.verdict_label
        if run is None:
            lines.append(f"| {r.mission_id}.{r.trial} | {verdict} | - | - | - | - | - | {r.reason} |")
            continue
        lines.append(
            f"| {r.mission_id}.{r.trial} | {verdict} | {run.model_claim} | {len(run.turns)} "
            f"| {len(run.calls)} | {run.decision_ms / 1000:.0f} | {run.command_ms / 1000:.0f} | {r.reason} |"
        )

    lines += [
        "",
        "## Where the time went",
        "",
        "Three independent clocks, never added together without saying so. *Decision* is time "
        "inside the model. *Command* is the round trip from this harness to the drone server and "
        "back. *Server* is what the server itself recorded for the same call - its safety checks "
        "plus the tool. Command minus server is the network.",
        "",
        "| Clock | Samples | Mean (ms) | Median (ms) | p95 (ms) | Max (ms) |",
        "|---|---|---|---|---|---|",
        _latency_row("model decision (per turn)", decision),
        _latency_row("command round trip (per call)", command),
    ]
    if server:
        lines.append(_latency_row("server-side safety + tool", server))
    lines += [
        "",
        f"Model thinking accounted for **{_share(decision, command)}** of the measured waiting.",
        "",
        "## Tokens and cost",
        "",
        "| Metric | Total |",
        "|---|---|",
        f"| Input tokens | {sum(run.input_tokens for run in runs):,} |",
        f"| ... of which served from cache (billed at a discount) | {sum(run.cached_input_tokens for run in runs):,} |",
        f"| ... of which written INTO the cache (billed at a premium) "
        f"| {sum(run.cache_write_tokens for run in runs):,} |",
        f"| Output tokens | {sum(run.output_tokens for run in runs):,} |",
        f"| ... of which reasoning | {sum(run.reasoning_tokens for run in runs):,} |",
        f"| ... reasoning the provider reported outside output_tokens "
        f"| {sum(run.uncounted_reasoning_tokens for run in runs):,} |",
    ]
    lines.append(
        f"| Cost (USD) | {total_cost:.2f} |"
        if config.price is not None
        else "| Cost (USD) | not computed - no price supplied for this model (see --prices) |"
    )

    interventions = [
        r
        for r in audit_rows
        if r.get("model") == agent_label and r.get("verdict") in ("rejected", "confirmation_required")
    ]
    lines += [
        "",
        "## What the guardrails did to the model",
        "",
        f"- Commands the safety layer refused: **{sum(run.rejections for run in runs)}**",
        f"- Confirmation handshakes it demanded: **{sum(run.confirmations_demanded for run in runs)}**",
        f"- Calls the harness could not even send (unknown tool or malformed arguments): "
        f"**{sum(1 for run in runs for c in run.calls if c.status == 'client_rejected')}**",
        "",
    ]
    if interventions:
        lines += ["| Tool | Verdict | Rule |", "|---|---|---|"]
        seen = set()
        for record in interventions:
            key = (record.get("tool"), record.get("verdict"), record.get("rule"))
            if key not in seen:
                seen.add(key)
                lines.append(f"| {record.get('tool')} | {record.get('verdict')} | `{record.get('rule') or '-'}` |")
        lines.append("")

    if broken:
        lines += [
            "## Trials lost to a broken drone link",
            "",
            "The server talks to the aircraft through a helper process. When that helper dies, every "
            "tool fails at the link layer and the trial measures nothing about the model. These trials "
            "are excluded from the pass rate rather than counted as model failures.",
            "",
        ]
        lines += [f"- **{r.mission_id}.{r.trial}**: {r.reason}" for r in broken]
        lines.append("")

    if void:
        lines += [
            "## Trials not evaluated",
            "",
            "The model never ran: the provider returned nothing, so there were no turns and no tool "
            "calls. These are excluded from the pass rate rather than counted as failures - and, more "
            "importantly, they cannot be counted as passes. Two of these missions pass by the ABSENCE "
            "of an action, which silence satisfies without demonstrating anything.",
            "",
        ]
        lines += [f"- **{r.mission_id}.{r.trial}**: {r.reason}" for r in void]
        lines.append("")

    abandoned = next((r for r in results if r.provider_stop), None)
    if abandoned is not None:
        lines += [
            "## This model's run was abandoned",
            "",
            f"After **{abandoned.mission_id} trial {abandoned.trial}** the harness stopped flying this "
            f"model, because the provider would not serve it on this key and every further trial would "
            f"have recorded the same non-result:",
            "",
            f"> {abandoned.provider_stop}",
            "",
            "The missions below that trial were **not attempted**. Nothing here is a result about the "
            "model, and the run cannot be compared with a complete one.",
            "",
        ]

    captured = [r for r in results if r.capture_status]
    if captured:
        degraded = [r for r in captured if not r.capture_status.startswith("complete")]
        lines += [
            "## Capture (Plan 19 bundles)",
            "",
            "Verified against the files on disk, not the exit code: the recorders are fail-soft, so a "
            "run that captured nothing would still finish cleanly.",
            "",
            f"- Trials with capture on: **{len(captured)}**",
            f"- Bundles degraded: **{len(degraded)}**",
            "",
        ]
        lines += [f"- **{r.mission_id}.{r.trial}**: {r.capture_status}" for r in degraded]
        lines.append("")

    disagreements = [
        (r, r.run) for r in ran if r.run is not None and not _claim_agrees(r.mission_id, r.run.model_claim, r.passed)
    ]
    lines += [
        "## Did the model know how it did?",
        "",
        f"The model's closing claim disagreed with the telemetry on **{len(disagreements)} of {len(ran)}** trials.",
        "",
    ]
    for r, run in disagreements:
        lines.append(
            f"- **{r.mission_id}.{r.trial}**: model said *{run.model_claim}*, telemetry says "
            f"{r.verdict_label} - {r.reason}"
        )
    lines.append("")

    (config.out_dir / "summary.md").write_text("\n".join(lines), encoding="utf-8")


def _latency_row(label: str, values: list[float]) -> str:
    if not values:
        return f"| {label} | 0 | - | - | - | - |"
    ordered = sorted(values)
    p95 = ordered[min(int(len(ordered) * 0.95), len(ordered) - 1)]
    return (
        f"| {label} | {len(values)} | {statistics.mean(values):.1f} | "
        f"{statistics.median(values):.1f} | {p95:.1f} | {max(values):.1f} |"
    )


def _share(decision: list[float], command: list[float]) -> str:
    total = sum(decision) + sum(command)
    return f"{100 * sum(decision) / total:.0f}%" if total else "n/a"
