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

from droneserver.benchmark.missions import DEFAULT_CONTEXT
from droneserver.llm.agent import AgentRun, Limits, run_agent, transcript_lines
from droneserver.llm.mcp_session import AGENT_CLIENT_NAME, CallRecord, LiveMCPSession, TelemetryRecorder
from droneserver.llm.prompts import SYSTEM_PROMPT, mission_prompts
from droneserver.llm.providers import ToolSpec, open_session, resolve_model
from droneserver.llm.spend import BudgetExceeded, Price, SpendLedger, project_trial_cost_usd
from droneserver.llm.verdicts import TRACK_HEADER, Track, Verdict, judge

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
LANDING_IS_PART_OF_THE_TASK = {"T1", "T2", "T3", "T4", "T5", "T10"}
SKIPPED = {"T6": "needs an external Google-Maps MCP server; not configured for this deployment"}


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
    cost_usd: float = 0.0

    @property
    def verdict_label(self) -> str:
        if self.skipped:
            return "SKIP"
        if self.budget_stop:
            return "BUDGET"
        if self.link_failure:
            return "LINK"
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
    """What this run has cost so far, in dollars."""
    if config.price is None:
        return 0.0
    return config.price.cost_usd(run.input_tokens, run.cached_input_tokens, run.output_tokens)


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
        output_tokens=run.output_tokens,
        reasoning_tokens=run.reasoning_tokens,
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


async def _read_parameter(harness: LiveMCPSession, name: str) -> float | None:
    result = await harness.call_raw("get_parameter", {"name": name}, 90)
    value = result.get("value") if result.get("status") == "success" else None
    try:
        return float(value) if value is not None else None
    except (TypeError, ValueError):
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
            if mission_id in SKIPPED:
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
                if result.run is not None and result.run.out_of_credit:
                    log(f"[{_utc()}] BUDGET stop: the account is out of credit. Stopping cleanly; rerun to resume.")
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

    recorder = TelemetryRecorder(config.url, config.recorder_api_key or config.api_key, config.telemetry_interval_s)
    await recorder.start()

    agent_mcp = LiveMCPSession(config.url, config.api_key, AGENT_CLIENT_NAME, agent_version)
    model = None
    started = time.time()
    clock = time.perf_counter()
    try:
        await agent_mcp.__aenter__()
        tools: list[ToolSpec] = await agent_mcp.list_tools()
        model = open_session(config.model_spec, **config.model_options)
        run = await run_agent(
            model=model,
            mcp=agent_mcp,
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
        await agent_mcp.aclose()
        with contextlib.suppress(Exception):
            await recorder.sample_once(full=True)
        await recorder.stop()

    duration = time.perf_counter() - clock
    intervened = await _settle(harness)
    if mission_id == "T7":
        extra["param_after"] = await _read_parameter(harness, ctx["param_name"])
        extra["param_observed_values"] = _parameter_values_seen(run.calls)
    extra["model_claim"] = run.model_claim

    track = Track(recorder.samples, ctx["home"])
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
        )
    verdict: Verdict = judge(mission_id, track, run.calls, ctx, extra)
    if not verdict.passed and not run.stop_reason.startswith("model declared"):  # noqa: SIM102
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
    )
    _write_trial_files(config, result, track, messages)
    return result


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
        if call.tool == "get_parameter" and call.status == "success":
            with contextlib.suppress(TypeError, ValueError):
                values.append(float(call.result.get("value")))
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


def _read_audit(audit_log: Path | None, window_start: float) -> list[dict]:
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
        if ts >= window_start:
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
                "output_tokens",
                "reasoning_tokens",
                "cost_usd",
                "model_claim",
                "claim_matches_telemetry",
                "stop_reason",
                "harness_intervened",
                "started_utc",
                "evidence",
            ]
        )
        for r in results:
            run = r.run
            claim = run.model_claim if run else ""
            matches = "" if r.skipped or r.link_failure or not run else str((claim == "complete") == r.passed)
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
                    run.output_tokens if run else "",
                    run.reasoning_tokens if run else "",
                    f"{cost:.4f}" if cost is not None else "",
                    claim,
                    matches,
                    run.stop_reason if run else "",
                    r.harness_intervened,
                    _utc(r.started_at) if r.started_at else "",
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
                "output_tokens",
                "reasoning_tokens",
                "tool_calls",
                "finish_reason",
                "text_chars",
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
                        turn.output_tokens,
                        turn.reasoning_tokens,
                        " ".join(turn.tool_calls),
                        turn.finish_reason,
                        len(turn.text),
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
    ran = [r for r in results if not r.skipped and not r.link_failure]
    broken = [r for r in results if r.link_failure]
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
        f"({sum(1 for r in results if r.skipped)} skipped, {len(broken)} lost to a broken drone link)",
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
        f"| ... of which served from cache | {sum(run.cached_input_tokens for run in runs):,} |",
        f"| Output tokens | {sum(run.output_tokens for run in runs):,} |",
        f"| ... of which reasoning | {sum(run.reasoning_tokens for run in runs):,} |",
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

    disagreements = [r for r in ran if r.run and (r.run.model_claim == "complete") != r.passed]
    lines += [
        "## Did the model know how it did?",
        "",
        f"The model's closing claim disagreed with the telemetry on **{len(disagreements)} of {len(ran)}** trials.",
        "",
    ]
    for r in disagreements:
        lines.append(
            f"- **{r.mission_id}.{r.trial}**: model said *{r.run.model_claim}*, telemetry says {r.verdict_label} - {r.reason}"
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
