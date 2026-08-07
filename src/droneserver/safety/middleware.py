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
9. **preconditions**- vehicle-state rules incl. the takeoff settling window

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
    | {"takeoff", "disarm_drone", "arm_drone", "clear_geofence", "land", "reposition", "do_orbit", "flight_altitudes"}
)


class SafetyLayer:
    """Holds the layer's mutable state (limiter, tokens, vehicle state, log)."""

    def __init__(self) -> None:
        self.rate_limiter = RateLimiter()
        self.confirmations = ConfirmationStore()
        self.state_tracker = StateTracker()
        self._audit: AuditLog | None = None
        self._audit_path: str | None = None

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

    state: dict = {"unknown": True}
    if tool in _STATE_DEPENDENT:
        state = await LAYER.state_tracker.refresh(_drone_from_ctx(ctx), s.state_cache_ttl_s)
    else:
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
        s = get_safety_settings()
        started = time.perf_counter()
        if not s.enabled:
            return await fn(*fn_args, **kwargs)

        ctx = kwargs.get("ctx") or next((a for a in fn_args if hasattr(a, "request_context")), None)
        # Arguments as the caller supplied them (ctx is transport plumbing).
        call_args = {k: v for k, v in kwargs.items() if k != "ctx"}

        safety_started = time.perf_counter()
        try:
            rejection, tier, client, _state = await _evaluate(tool_name, call_args, ctx, s)
        except Exception as e:  # a broken guard must not brick the server
            logger.exception(f"safety layer error on {tool_name}: {e}")
            rejection, tier, client = None, Tier.NORMAL, auth_mod.ANONYMOUS
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


def _write_audit(record: AuditRecord, s: SafetySettings) -> None:
    if not s.audit_enabled:
        return
    try:
        LAYER.audit_log(s).write(record)
    except Exception:
        logger.exception("failed to write audit record")
