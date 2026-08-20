"""The guard pipeline: every tool call passes through here before MavSDK.

Check order (fixed, and the order the audit ``rule`` fields will show):

1. **authenticate** - resolve the presented API key to a client + scope
2. **state**        - refresh the vehicle snapshot (only when a rule needs it)
3. **tier**         - base tier + conditional escalation for these arguments
4. **authorize**    - client scope vs. tier
5. **rate limit**   - per client, separate budget for critical calls
6. **confirmation** - critical tools need a valid single-use token
7. **bounds**       - parameter bounds (altitude, speed, coordinates, size)
8. **geofence**     - server-side fence: single targets and whole missions
9. **preconditions**- vehicle-state rules incl. the takeoff settling window and,
   when state cannot be read, the energy-direction failsafe (recovery commands
   stay allowed, energy-adding ones are refused)

Checks 7-8 test the arguments themselves and run before 9, which tests vehicle
state: an out-of-fence waypoint is illegal however long you wait, so it is the
more useful thing to tell the caller when both would fire.
10. **execute**     - the tool itself
11. **record**      - command history + append-only audit line

Failing any check returns a normal tool result with ``status="rejected"`` (or
``"confirmation_required"``), never an exception: the LLM sees a readable
explanation with a ``remedy`` and can correct itself.
"""

import functools
import inspect
import time
from collections.abc import Callable

from droneserver.logging_setup import logger
from droneserver.safety import auth as auth_mod
from droneserver.safety.audit import AuditLog, AuditRecord, new_call_id
from droneserver.safety.config import SafetySettings, get_safety_settings
from droneserver.safety.geofence import Geofence, parse_polygon
from droneserver.safety.state import StateTracker
from droneserver.safety.tiers import ESCALATIONS, TOOL_TIERS, Tier, effective_tier
from droneserver.safety.tokens import (
    CONFIRM_ARG,
    ConfirmationStore,
    confirmation_failed_result,
    confirmation_required_result,
)
from droneserver.safety.validation import (
    ENERGY_ADDING_TOOLS,
    MISSION_START_TOOLS,
    MISSION_UPLOAD_TOOLS,
    NAVIGATION_TOOLS,
    RateLimiter,
    check_geofence,
    check_parameter_bounds,
    check_preconditions,
)

#: Tools whose rules consult vehicle state (others skip the telemetry refresh).
_STATE_DEPENDENT = (
    NAVIGATION_TOOLS
    | MISSION_START_TOOLS
    | MISSION_UPLOAD_TOOLS
    # Every tool the energy-direction failsafe can REFUSE must have its state
    # refreshed for the same reason the escalating tools below do: a stale
    # snapshot reads "unknown", which would refuse them permanently. Nothing is
    # added here for the energy-REDUCING tools - they are allowed under unknown
    # state by design, so a telemetry round-trip on the abort path would buy
    # nothing. (``land`` was already in the explicit set below and stays.)
    | ENERGY_ADDING_TOOLS
    | {
        "takeoff",
        "disarm_drone",
        "arm_drone",
        "land",
        "reposition",
        "do_orbit",
        "flight_altitudes",
        # The RTL honesty rule (precondition.rtl_requires_airborne) can only
        # fire on state that was actually read: a stale snapshot reads
        # "unknown", and unknown state leaves these energy-REDUCING tools
        # available, which would put the T6 phantom-return defect straight
        # back. Refreshing them costs one cached telemetry round-trip and
        # cannot block the abort path - an unreadable link still allows both.
        "return_to_launch",
        "set_flight_mode",
        # Tools whose TIER depends on whether we are flying must have their
        # state REFRESHED, not read from a stale snapshot. An unrefreshed
        # snapshot reads "unknown", which the fail-safe escalation (B4) treats
        # as airborne - so every one of these demanded a confirmation token
        # even sitting on the ground. Caught by the SITL sweep: 7 previously
        # passing tests failed on it.
        "clear_geofence",
        "upload_geofence",
        "raw_geofence_transfer",
        "import_qgc_mission",
        "calibrate",
        "cancel_calibration",
    }
)


class SafetyLayer:
    """Holds the layer's mutable state (limiter, tokens, vehicle state, log)."""

    def __init__(self) -> None:
        self.rate_limiter = RateLimiter()
        self.confirmations = ConfirmationStore()
        self.state_tracker = StateTracker()
        self._audit: AuditLog | None = None
        self._audit_path: str | None = None
        #: Measured duration of the previous durable audit write (ms).
        self.last_audit_write_ms: float = 0.0

    def audit_log(self, s: SafetySettings) -> AuditLog:
        from droneserver.config import get_settings

        path = s.audit_log_path or str(get_settings().flight_log_dir / "audit.jsonl")
        if self._audit is None or self._audit_path != path:
            self._audit = AuditLog(path)
            self._audit_path = path
        return self._audit

    def fence(self, s: SafetySettings) -> Geofence:
        try:
            polygon = parse_polygon(s.geofence_polygon)
        except ValueError as e:
            logger.error(f"Invalid SAFETY_GEOFENCE_POLYGON, fence polygon disabled: {e}")
            polygon = ()
        return Geofence(
            polygon=polygon,
            max_altitude_m=s.geofence_max_altitude_m,
            max_radius_m=s.geofence_max_radius_m,
            home=self.state_tracker.state.home,
        )

    def reset(self) -> None:
        self.rate_limiter.reset()
        self.confirmations.clear()
        self.state_tracker.reset()


#: Process-wide layer (one server, one drone), mirroring the global connector.
LAYER = SafetyLayer()


# --------------------------------------------------------------- identity


def _client_key_from_ctx(ctx) -> str | None:
    """Best-effort extraction of the API key from the transport request.

    Looked for in ``X-API-Key``, then ``Authorization: Bearer …``. Returns
    None when the transport exposes no headers (e.g. stdio), in which case the
    unauthenticated policy applies.
    """
    request = None
    try:
        request = getattr(ctx.request_context, "request", None)
    except Exception:
        return None
    headers = getattr(request, "headers", None)
    if not headers:
        return None
    try:
        key = headers.get("x-api-key")
        if key:
            return key
        authorization = headers.get("authorization") or ""
        if authorization.lower().startswith("bearer "):
            return authorization[7:].strip()
    except Exception:
        return None
    return None


def _reported_model(ctx) -> str | None:
    """The model/client the caller reported at MCP initialize, if any."""
    try:
        params = ctx.session.client_params
        info = getattr(params, "clientInfo", None)
        if info is not None:
            version = getattr(info, "version", "")
            return f"{info.name}{'/' + version if version else ''}"
    except Exception:
        return None
    return None


def _drone_from_ctx(ctx):
    try:
        return ctx.request_context.lifespan_context.drone
    except Exception:
        return None


#: Transport header a TRIAL-LAYER client sets to say "this session's launch
#: point is wherever the aircraft is parked right now" (FIX 13). It is a header
#: and not a tool argument on purpose: the model never reaches it, because a
#: launch point the aircraft's own arming can move is the defect FIX 8a/10/11/12
#: exist to escape. The harness sets it on its own MCP session
#: (``droneserver.llm.runner``), where every call it makes is either mid-ferry
#: (the vehicle is armed, and the re-anchor declines) or with the aircraft
#: parked on the point the next trial will fly from.
ANCHOR_HEADER = "x-session-launch-anchor"

#: Smallest gap between two re-anchor attempts. The harness polls, and each
#: attempt costs a few telemetry reads; the aircraft does not move while parked.
ANCHOR_MIN_INTERVAL_S = 2.0

_last_anchor_attempt = 0.0


def _headers_from_ctx(ctx):
    try:
        request = getattr(ctx.request_context, "request", None)
    except Exception:
        return None
    return getattr(request, "headers", None)


def _anchor_requested(ctx) -> bool:
    """Did this call arrive on a session that re-anchors the launch point?"""
    headers = _headers_from_ctx(ctx)
    if not headers:
        return False
    try:
        value = headers.get(ANCHOR_HEADER)
    except Exception:
        return False
    return str(value or "").strip().lower() in ("1", "true", "yes", "on")


async def maybe_anchor_launch_point(ctx) -> dict:
    """Re-anchor the session launch point for a trial-layer call. Never raises.

    Runs before the state refresh so the snapshot this call is judged against
    already measures height from the new point. Declines - loudly in the log,
    silently to the caller - whenever the autopilot does not say the vehicle is
    disarmed and on the ground.
    """
    global _last_anchor_attempt

    now = time.monotonic()
    if (now - _last_anchor_attempt) < ANCHOR_MIN_INTERVAL_S:
        return {"anchored": False, "reason": "an attempt was made moments ago"}
    _last_anchor_attempt = now
    try:
        connector = ctx.request_context.lifespan_context
        drone = getattr(connector, "drone", None)
        if drone is None:
            return {"anchored": False, "reason": "no drone link"}
        from droneserver.mavlink.connection import anchor_launch_point_here

        outcome = await anchor_launch_point_here(
            drone, connector, "parked position when the trial layer re-anchored the session"
        )
        if outcome.get("moved"):
            LAYER.state_tracker.reanchor_session_launch(getattr(connector, "session_launch", None))
        return outcome
    except Exception as e:  # noqa: BLE001 - an anchor fault must not refuse the call it rode in on
        logger.warning("session-launch re-anchor failed: %s: %s", type(e).__name__, e)
        return {"anchored": False, "reason": f"{type(e).__name__}: {e}"}


def _session_launch_from_ctx(ctx):
    """The connector's launch record, or None.

    Where this session started is the only ground elevation that does not move
    with the aircraft (the autopilot's home follows every arm), so the state
    tracker measures heights against it. Best effort: without it the layer
    falls back to the autopilot's home exactly as before.
    """
    try:
        return getattr(ctx.request_context.lifespan_context, "session_launch", None)
    except Exception:
        return None


# --------------------------------------------------------------- pipeline


def _guards_in_force(s: SafetySettings) -> dict:
    return {
        "enabled": s.enabled,
        "validation": s.validation_enabled,
        "geofence": s.geofence_enabled,
        "tiers": s.tiers_enabled,
        "auth": s.auth_enabled,
        "rate_limit": s.rate_limit_enabled,
    }


async def _evaluate(tool: str, args: dict, ctx, s: SafetySettings) -> tuple[dict | None, Tier, object, dict]:
    """Run checks 1-9. Returns (rejection_result_or_None, tier, client, state)."""
    client = auth_mod.authenticate(_client_key_from_ctx(ctx), s)

    # 2a. the trial layer's launch-point re-anchor (FIX 13). Gated on control
    # scope for the same reason every state-changing call is: a telemetry-scope
    # client may read where the aircraft is, not redefine where it started.
    if _anchor_requested(ctx) and (not s.auth_enabled or client.can(Tier.NORMAL)):
        await maybe_anchor_launch_point(ctx)

    state: dict = {"unknown": True}
    if tool in _STATE_DEPENDENT:
        state = await LAYER.state_tracker.refresh(
            _drone_from_ctx(ctx), s.state_cache_ttl_s, session_launch=_session_launch_from_ctx(ctx)
        )
    else:
        LAYER.state_tracker.note_session_launch(_session_launch_from_ctx(ctx))
        state = LAYER.state_tracker.state.snapshot()

    tier, consequence = effective_tier(tool, args, state)

    # 4. authorize
    if s.auth_enabled and not client.can(tier):
        return auth_mod.authorization_failed_result(client, tool, tier), tier, client, state

    # 5. rate limit (emergency stop is deliberately exempt)
    if s.rate_limit_enabled and tier is not Tier.EMERGENCY:
        rejection = LAYER.rate_limiter.check(client.client_id, tier is Tier.CRITICAL, s)
        if rejection is not None:
            return rejection.as_result(), tier, client, state

    # 6. confirmation token for critical tools
    if s.tiers_enabled and tier is Tier.CRITICAL:
        token = args.get(CONFIRM_ARG)
        if not token:
            pending = LAYER.confirmations.issue(
                client.client_id, tool, args, consequence, s.confirmation_ttl_s, s.confirmation_max_outstanding
            )
            return confirmation_required_result(pending, tool), tier, client, state
        ok, reason = LAYER.confirmations.redeem(str(token), client.client_id, tool, args)
        if not ok:
            return confirmation_failed_result(tool, reason), tier, client, state

    # 7. parameter bounds, then 8. geofence, then 9. state preconditions.
    # Argument-intrinsic violations are reported BEFORE state-dependent ones:
    # a target outside the fence stays illegal no matter how long you wait,
    # so "that waypoint is outside the geofence" is a more useful message than
    # "you are on the ground" when both are true.
    if s.validation_enabled:
        rejection = check_parameter_bounds(tool, args, s, state)
        if rejection is not None:
            return rejection.as_result(), tier, client, state

    if s.geofence_enabled:
        rejection = check_geofence(tool, args, LAYER.fence(s), s, state)
        if rejection is not None:
            return rejection.as_result(), tier, client, state

    if s.validation_enabled:
        rejection = check_preconditions(tool, args, state, s)
        if rejection is not None:
            return rejection.as_result(), tier, client, state

    return None, tier, client, state


def _pre_execute_effects(tool: str) -> None:
    """Command history that must be stamped BEFORE the tool runs.

    The takeoff settling window is measured from when takeoff was COMMANDED,
    not when it returned: ``takeoff(wait_for_altitude=True)`` blocks until the
    target altitude is reached, so stamping afterwards would impose a second,
    pointless wait on a vehicle that is already stable.
    """
    if tool == "takeoff":
        LAYER.state_tracker.note_takeoff()


def _record_effects(tool: str, args: dict, result) -> None:
    """Update the command history from a successful call (check 11)."""
    status = result.get("status") if isinstance(result, dict) else None
    if status not in ("success", None):
        return
    if tool in ("land", "return_to_launch", "kill_motors", "disarm_drone", "emergency_stop"):
        LAYER.state_tracker.note_landed()
    elif tool in MISSION_UPLOAD_TOOLS or tool in ("import_qgc_mission", "raw_mission_control"):
        if tool != "raw_mission_control" or str(args.get("action", "")).lower() != "clear":
            LAYER.state_tracker.note_mission_uploaded(True)
        else:
            LAYER.state_tracker.note_mission_uploaded(False)
    elif tool == "clear_mission":
        LAYER.state_tracker.note_mission_uploaded(False)
    elif tool in ("arm_drone", "set_flight_mode"):
        LAYER.state_tracker.invalidate()


def can_be_critical(tool: str) -> bool:
    """True if this tool is CRITICAL always, or can escalate to CRITICAL.

    Such tools get a ``confirm_token`` parameter added to their public schema
    so the model can discover the round-trip from the tool definition itself.
    """
    return TOOL_TIERS.get(tool) is Tier.CRITICAL or tool in ESCALATIONS


def _with_confirm_token_param(fn: Callable) -> inspect.Signature:
    """The tool's signature plus a keyword-only ``confirm_token`` parameter."""
    sig = inspect.signature(fn)
    params = list(sig.parameters.values())
    if any(p.name == CONFIRM_ARG for p in params):
        return sig
    # keyword-only so it never disturbs positional ordering
    params.append(inspect.Parameter(CONFIRM_ARG, inspect.Parameter.KEYWORD_ONLY, default="", annotation=str))
    return sig.replace(parameters=params)


def guard(fn: Callable) -> Callable:
    """Wrap a tool coroutine with the safety pipeline.

    Applied by :class:`droneserver.app.SafeFastMCP` at registration, so no tool
    can be registered without passing through it.
    """
    tool_name = fn.__name__

    @functools.wraps(fn)
    async def wrapper(*fn_args, **kwargs):
        # The timer starts HERE, before anything else the guard does, so the
        # reported latency includes the settings load and every check. (It
        # used to start after the settings load, which - when settings were
        # re-read from disk each call - excluded the dominant fixed cost.)
        started = time.perf_counter()
        s = get_safety_settings()
        ctx = kwargs.get("ctx") or next((a for a in fn_args if hasattr(a, "request_context")), None)

        if not s.enabled:
            # A guardrails-off run must still be self-documenting: audit the
            # call, flagged, so an experiment cannot silently produce
            # unlabelled data.
            return await _run_unguarded(fn, fn_args, kwargs, tool_name, ctx, s, started)

        # Arguments as the caller supplied them (ctx is transport plumbing).
        call_args = {k: v for k, v in kwargs.items() if k != "ctx"}

        safety_started = time.perf_counter()
        guard_error: str | None = None
        try:
            rejection, tier, client, _state = await _evaluate(tool_name, call_args, ctx, s)
        except Exception as e:
            # FAIL CLOSED. An exception inside the guard means we do not know
            # whether this call is safe, so we refuse it. (This previously
            # executed the tool unguarded - the single worst defect the
            # independent review found.)
            logger.exception(f"safety layer error on {tool_name}: {e}")
            guard_error = f"{type(e).__name__}: {e}"
            tier, client = Tier.CRITICAL, auth_mod.ANONYMOUS
            rejection = {
                "status": "rejected",
                "error": (
                    "The safety layer failed while evaluating this call, so the call was "
                    "refused. This is a server fault, not a problem with your request."
                ),
                "rule": "guard.internal_error",
                "remedy": (
                    "Retry once. If it fails again, stop commanding the drone and tell the "
                    "operator - the safety layer needs attention. Read-only telemetry tools "
                    "are unaffected by the failing check only if they also succeed."
                ),
                "safety_layer": "droneserver.safety",
            }
        safety_ms = (time.perf_counter() - safety_started) * 1000

        record = AuditRecord(
            call_id=new_call_id(),
            client_id=client.client_id,
            authenticated=client.authenticated,
            key_fp=client.key_fingerprint,
            model=_reported_model(ctx),
            tool=tool_name,
            tier=tier.value,
            args=call_args,
            verdict="allowed",
            guards=_guards_in_force(s),
            safety_ms=safety_ms,
            guard_error=guard_error,
        )

        if rejection is not None:
            record.verdict = rejection.get("status", "rejected")
            record.rule = rejection.get("rule")
            record.outcome_error = rejection.get("error")
            record.latency_ms = (time.perf_counter() - started) * 1000
            _write_audit(record, s)
            return rejection

        # 10. execute - the token argument is plumbing, not a tool parameter
        kwargs.pop(CONFIRM_ARG, None)
        _pre_execute_effects(tool_name)
        try:
            result = await fn(*fn_args, **kwargs)
        except Exception as e:
            record.verdict = "error"
            record.outcome_error = str(e)
            record.latency_ms = (time.perf_counter() - started) * 1000
            _write_audit(record, s)
            raise

        try:
            _record_effects(tool_name, call_args, result)
        except Exception:
            logger.exception("safety layer failed to record command effects")

        if isinstance(result, dict):
            record.outcome_status = result.get("status")
            record.outcome_error = result.get("error")
        record.latency_ms = (time.perf_counter() - started) * 1000
        _write_audit(record, s)
        return result

    if can_be_critical(tool_name):
        # Publish confirm_token in the tool's schema (FastMCP builds the schema
        # from the signature) so the model can see the round-trip exists.
        wrapper.__signature__ = _with_confirm_token_param(fn)  # type: ignore[attr-defined]
        wrapper.__doc__ = (fn.__doc__ or "") + (
            "\n\n    SAFETY: this is a CRITICAL action. Calling it without confirm_token returns a\n"
            "    single-use token plus a consequence statement; repeat the call with that exact\n"
            "    token and unchanged arguments to execute. Never invent or reuse a token.\n"
        )

    return wrapper


async def _run_unguarded(fn, fn_args, kwargs, tool_name: str, ctx, s: SafetySettings, started: float):
    """Execute a tool with the safety layer disabled - but still audit it."""
    call_args = {k: v for k, v in kwargs.items() if k != "ctx"}
    record = AuditRecord(
        call_id=new_call_id(),
        client_id="safety_disabled",
        authenticated=False,
        key_fp="",
        model=_reported_model(ctx),
        tool=tool_name,
        tier="unclassified",
        args=call_args,
        verdict="allowed_safety_disabled",
        guards=_guards_in_force(s),
        safety_ms=0.0,
    )
    try:
        result = await fn(*fn_args, **kwargs)
    except Exception as e:
        record.verdict = "error"
        record.outcome_error = str(e)
        record.latency_ms = (time.perf_counter() - started) * 1000
        _write_audit(record, s)
        raise
    if isinstance(result, dict):
        record.outcome_status = result.get("status")
        record.outcome_error = result.get("error")
    record.latency_ms = (time.perf_counter() - started) * 1000
    _write_audit(record, s)
    return result


def _write_audit(record: AuditRecord, s: SafetySettings) -> None:
    """Write the record and measure the durable-write cost.

    ``latency_ms`` covers entry -> result ready, i.e. everything except this
    record's own fsync'd write. That write is measured here and reported on
    the NEXT record as ``audit_write_ms``; over any run the mean of
    ``latency_ms + audit_write_ms`` is the true end-to-end cost (the one-record
    lag cancels). This is stated in the audit schema and in
    docs/safety_review.md so the paper's numbers are not quietly optimistic.
    """
    if not s.audit_enabled:
        return
    record.audit_write_ms = LAYER.last_audit_write_ms
    write_started = time.perf_counter()
    try:
        LAYER.audit_log(s).write(record)
    except Exception:
        logger.exception("failed to write audit record")
    finally:
        LAYER.last_audit_write_ms = round((time.perf_counter() - write_started) * 1000, 3)
