"""FIX 4 (safety review 2026-08-19, option (b)): gate emergency_stop kill mode.

``emergency_stop(mode="kill")`` cuts the motors, the same physical effect as
``kill_motors``, which has always required a confirmation-token round-trip. Kill
mode used to reach that effect with no token because emergency_stop sat at tier
EMERGENCY (never token-gated). Now the "kill" mode escalates to CRITICAL and is
gated exactly like ``kill_motors``; the safe recovery modes ("land", "rtl") stay
at EMERGENCY and remain ungated so they always work in an emergency.
"""

from __future__ import annotations

import asyncio

from droneserver.safety import middleware as M
from droneserver.safety.config import reset_safety_settings
from droneserver.safety.middleware import can_be_critical
from droneserver.safety.tiers import ESCALATIONS, Tier, effective_tier


# --------------------------------------------------------------- tier table


class TestTierEscalation:
    def test_kill_mode_escalates_to_critical(self):
        tier, why = effective_tier("emergency_stop", {"mode": "kill"}, {})
        assert tier is Tier.CRITICAL
        assert "motor" in why.lower()

    def test_land_mode_stays_emergency(self):
        assert effective_tier("emergency_stop", {"mode": "land"}, {})[0] is Tier.EMERGENCY

    def test_rtl_mode_stays_emergency(self):
        assert effective_tier("emergency_stop", {"mode": "rtl"}, {})[0] is Tier.EMERGENCY

    def test_default_mode_is_land_and_stays_emergency(self):
        # No mode argument defaults to "land"; the pre-existing invariant that
        # bare emergency_stop is EMERGENCY must hold.
        assert effective_tier("emergency_stop", {}, {})[0] is Tier.EMERGENCY

    def test_emergency_stop_is_now_an_escalating_tool(self):
        assert "emergency_stop" in ESCALATIONS

    def test_emergency_stop_now_publishes_a_confirm_token(self):
        # can_be_critical drives whether the confirm_token parameter appears in
        # the tool's public schema, so the model can discover the round-trip.
        assert can_be_critical("emergency_stop")


# --------------------------------------------------------------- end to end


def _control_ctx():
    """A ctx presenting a control-scoped API key in its request headers."""

    class _Headers(dict):
        def get(self, key, default=None):  # header lookup is case-insensitive
            return super().get(key.lower(), default)

    class _Request:
        headers = _Headers({"x-api-key": "KEY1"})

    class _RequestContext:
        request = _Request()
        lifespan_context = None

    class _Ctx:
        request_context = _RequestContext()

    return _Ctx()


def _run_guarded(mode: str, tmp_path, monkeypatch, confirm_token: str | None = None):
    executed: list[str] = []

    # Explicit signature like the real tool: the guard adds the keyword-only
    # confirm_token itself, so the stub must not use **kwargs.
    async def emergency_stop(ctx=None, mode="land"):
        executed.append(mode)
        return {"status": "success", "mode": mode, "actions_taken": ["done"]}

    emergency_stop.__name__ = "emergency_stop"

    monkeypatch.setenv("SAFETY_API_KEYS", "tester:KEY1:control")
    monkeypatch.delenv("SAFETY_UNAUTHENTICATED_SCOPE", raising=False)
    monkeypatch.setenv("SAFETY_AUDIT_LOG_PATH", str(tmp_path / "audit.jsonl"))
    reset_safety_settings()
    try:
        kwargs = {"ctx": _control_ctx(), "mode": mode}
        if confirm_token is not None:
            kwargs["confirm_token"] = confirm_token
        result = asyncio.run(M.guard(emergency_stop)(**kwargs))
    finally:
        reset_safety_settings()
    return result, executed


class TestEndToEndGating:
    def test_kill_without_token_is_refused_and_not_executed(self, tmp_path, monkeypatch):
        result, executed = _run_guarded("kill", tmp_path, monkeypatch)
        assert result["status"] == "confirmation_required", result
        assert result["confirm_token"], result
        assert executed == [], "kill must NOT run before the token round-trip"

    def test_kill_with_a_valid_token_executes(self, tmp_path, monkeypatch):
        # First call obtains the token, second call redeems it. The token store
        # lives on the process-wide LAYER, so a single settings reset window
        # holds for both calls.
        executed: list[str] = []

        async def emergency_stop(ctx=None, mode="land"):
            executed.append(mode)
            return {"status": "success", "mode": mode, "actions_taken": ["MOTORS KILLED"]}

        emergency_stop.__name__ = "emergency_stop"

        monkeypatch.setenv("SAFETY_API_KEYS", "tester:KEY1:control")
        monkeypatch.delenv("SAFETY_UNAUTHENTICATED_SCOPE", raising=False)
        monkeypatch.setenv("SAFETY_AUDIT_LOG_PATH", str(tmp_path / "audit.jsonl"))
        reset_safety_settings()
        try:
            guarded = M.guard(emergency_stop)
            first = asyncio.run(guarded(ctx=_control_ctx(), mode="kill"))
            assert first["status"] == "confirmation_required", first
            token = first["confirm_token"]
            second = asyncio.run(guarded(ctx=_control_ctx(), mode="kill", confirm_token=token))
        finally:
            reset_safety_settings()

        assert second["status"] == "success", second
        assert executed == ["kill"], "the second call, with the token, must execute"

    def test_land_executes_with_no_token(self, tmp_path, monkeypatch):
        result, executed = _run_guarded("land", tmp_path, monkeypatch)
        assert result["status"] == "success", result
        assert result.get("status") != "confirmation_required"
        assert executed == ["land"]

    def test_rtl_executes_with_no_token(self, tmp_path, monkeypatch):
        result, executed = _run_guarded("rtl", tmp_path, monkeypatch)
        assert result["status"] == "success", result
        assert result.get("status") != "confirmation_required"
        assert executed == ["rtl"]
