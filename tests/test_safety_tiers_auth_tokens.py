"""Unit tests for the tier table, authN/authZ, confirmation tokens, audit log."""

import json

import pytest

from droneserver.safety.audit import AuditLog, AuditRecord, new_call_id, redact_args
from droneserver.safety.auth import Client, authenticate, parse_api_keys
from droneserver.safety.config import SafetySettings
from droneserver.safety.tiers import TOOL_TIERS, Tier, effective_tier
from droneserver.safety.tokens import ConfirmationStore, fingerprint


class TestTierTable:
    def test_every_registered_tool_is_classified(self):
        """The tier table is the review artifact - it must cover the registry."""
        import asyncio

        import droneserver.tools  # noqa: F401
        from droneserver.app import mcp

        registered = {t.name for t in asyncio.run(mcp.list_tools())}
        unclassified = registered - set(TOOL_TIERS)
        assert not unclassified, f"tools missing a criticality tier: {sorted(unclassified)}"

    def test_no_stale_entries(self):
        import asyncio

        import droneserver.tools  # noqa: F401
        from droneserver.app import mcp

        registered = {t.name for t in asyncio.run(mcp.list_tools())}
        stale = set(TOOL_TIERS) - registered
        assert not stale, f"tier table lists tools that no longer exist: {sorted(stale)}"

    def test_unknown_tool_is_critical(self):
        tier, why = effective_tier("some_new_tool", {}, {})
        assert tier is Tier.CRITICAL
        assert "no criticality classification" in why

    def test_kill_is_always_critical(self):
        assert effective_tier("kill_motors", {}, {})[0] is Tier.CRITICAL

    def test_emergency_stop_is_emergency_tier(self):
        assert effective_tier("emergency_stop", {}, {})[0] is Tier.EMERGENCY

    def test_disarm_escalates_in_air_only(self):
        assert effective_tier("disarm_drone", {}, {"in_air": False})[0] is Tier.NORMAL
        tier, why = effective_tier("disarm_drone", {}, {"in_air": True})
        assert tier is Tier.CRITICAL and "CRASH" in why

    def test_force_arm_escalates(self):
        assert effective_tier("arm_drone", {"force": False}, {})[0] is Tier.NORMAL
        assert effective_tier("arm_drone", {"force": True}, {})[0] is Tier.CRITICAL

    def test_erase_logs_escalates(self):
        assert effective_tier("flight_logs", {"action": "list"}, {})[0] is Tier.NORMAL
        assert effective_tier("flight_logs", {"action": "erase_all"}, {})[0] is Tier.CRITICAL

    def test_safety_parameter_escalates(self):
        assert effective_tier("set_parameter", {"name": "WPNAV_ACCEL"}, {})[0] is Tier.NORMAL
        tier, why = effective_tier("set_parameter", {"name": "FENCE_ENABLE"}, {})
        assert tier is Tier.CRITICAL and "FENCE_ENABLE" in why

    def test_read_only_tools_are_read_only(self):
        for tool in ("get_position", "get_battery", "list_cameras", "system_info"):
            assert TOOL_TIERS[tool] is Tier.READ_ONLY


class TestAuth:
    def test_parse_keys(self):
        registry = parse_api_keys("alice:KEY1:control,bob:KEY2:telemetry")
        assert registry["KEY1"] == ("alice", "control")
        assert registry["KEY2"] == ("bob", "telemetry")

    def test_bad_specs_rejected(self):
        with pytest.raises(ValueError, match="client_id:key:scope"):
            parse_api_keys("alice:KEY1")
        with pytest.raises(ValueError, match="unknown scope"):
            parse_api_keys("alice:KEY1:superuser")

    def test_authenticates_known_key(self):
        s = SafetySettings(_env_file=None, api_keys="alice:KEY1:control")
        client = authenticate("KEY1", s)
        assert client.authenticated and client.client_id == "alice" and client.scope == "control"

    def test_unknown_key_falls_back_to_unauthenticated_scope(self):
        s = SafetySettings(_env_file=None, api_keys="alice:KEY1:control")
        client = authenticate("WRONG", s)
        assert not client.authenticated and client.scope == "telemetry"

    def test_reject_policy(self):
        s = SafetySettings(_env_file=None, api_keys="alice:KEY1:control", unauthenticated_scope="reject")
        client = authenticate(None, s)
        assert client.scope == "none"
        assert not client.can(Tier.READ_ONLY)

    def test_scope_gates_tiers(self):
        telemetry = Client("t", "telemetry", True)
        control = Client("c", "control", True)
        assert telemetry.can(Tier.READ_ONLY)
        assert not telemetry.can(Tier.NORMAL)
        assert not telemetry.can(Tier.CRITICAL)
        assert control.can(Tier.CRITICAL) and control.can(Tier.EMERGENCY)

    def test_no_keys_configured_grants_control_with_warning(self):
        """A default install must be usable; auth binds once it is configured."""
        s = SafetySettings(_env_file=None, api_keys="")
        client = authenticate(None, s)
        assert client.scope == "control" and client.can(Tier.CRITICAL)
        assert not client.authenticated  # still recorded as unauthenticated
        assert client.client_id == "unconfigured"

    def test_explicit_policy_honored_even_without_keys(self):
        s = SafetySettings(_env_file=None, api_keys="", unauthenticated_scope="reject")
        client = authenticate(None, s)
        assert client.scope == "none" and not client.can(Tier.READ_ONLY)

    def test_configured_keys_restore_enforcement(self):
        s = SafetySettings(_env_file=None, api_keys="alice:KEY1:control")
        assert not authenticate(None, s).can(Tier.NORMAL)  # anonymous -> telemetry
        assert authenticate("KEY1", s).can(Tier.CRITICAL)

    def test_key_never_in_repr(self):
        s = SafetySettings(_env_file=None, api_keys="alice:SUPERSECRET:control")
        assert "SUPERSECRET" not in repr(s)


class TestConfirmationTokens:
    def test_round_trip(self):
        store = ConfirmationStore()
        pending = store.issue("alice", "kill_motors", {}, "boom", 60.0, 16)
        ok, reason = store.redeem(pending.token, "alice", "kill_motors", {})
        assert ok and reason == ""

    def test_single_use(self):
        store = ConfirmationStore()
        pending = store.issue("alice", "kill_motors", {}, "boom", 60.0, 16)
        assert store.redeem(pending.token, "alice", "kill_motors", {})[0]
        ok, reason = store.redeem(pending.token, "alice", "kill_motors", {})
        assert not ok and reason == "unknown_or_used"

    def test_forged_token_rejected(self):
        store = ConfirmationStore()
        ok, reason = store.redeem("not-a-real-token", "alice", "kill_motors", {})
        assert not ok and reason == "unknown_or_used"

    def test_expired_token_rejected(self):
        store = ConfirmationStore()
        pending = store.issue("alice", "kill_motors", {}, "boom", ttl_s=-1.0, max_outstanding=16)
        ok, reason = store.redeem(pending.token, "alice", "kill_motors", {})
        assert not ok and reason == "expired"

    def test_bound_to_client(self):
        store = ConfirmationStore()
        pending = store.issue("alice", "kill_motors", {}, "boom", 60.0, 16)
        ok, reason = store.redeem(pending.token, "mallory", "kill_motors", {})
        assert not ok and reason == "wrong_client"

    def test_bound_to_tool(self):
        store = ConfirmationStore()
        pending = store.issue("alice", "kill_motors", {}, "boom", 60.0, 16)
        ok, reason = store.redeem(pending.token, "alice", "disarm_drone", {})
        assert not ok and reason == "wrong_tool"

    def test_bound_to_arguments(self):
        store = ConfirmationStore()
        pending = store.issue("alice", "set_parameter", {"name": "FENCE_ENABLE", "value": 1}, "x", 60.0, 16)
        ok, reason = store.redeem(pending.token, "alice", "set_parameter", {"name": "ARMING_CHECK", "value": 0})
        assert not ok and reason == "arguments_changed"

    def test_fingerprint_ignores_the_token_argument(self):
        assert fingerprint("t", {"a": 1}) == fingerprint("t", {"a": 1, "confirm_token": "xyz"})

    def test_outstanding_is_bounded(self):
        store = ConfirmationStore()
        for i in range(20):
            store.issue("alice", "kill_motors", {"i": i}, "boom", 60.0, max_outstanding=5)
        assert store.outstanding <= 5


class TestAudit:
    def test_writes_jsonl_and_reads_back(self, tmp_path):
        log = AuditLog(tmp_path / "audit.jsonl")
        log.write(
            AuditRecord(
                call_id=new_call_id(),
                client_id="alice",
                authenticated=True,
                key_fp="abc",
                model="test/1",
                tool="takeoff",
                tier="normal",
                args={"takeoff_altitude": 10},
                verdict="allowed",
                outcome_status="success",
                latency_ms=12.3,
                safety_ms=1.2,
            )
        )
        log.write(
            AuditRecord(
                call_id=new_call_id(),
                client_id="alice",
                authenticated=True,
                key_fp="abc",
                model=None,
                tool="kill_motors",
                tier="critical",
                args={},
                verdict="rejected",
                rule="confirmation.unknown_or_used",
            )
        )
        records = log.read_all()
        assert len(records) == 2
        assert records[0]["schema"] == "droneserver.audit/1"
        assert records[0]["tool"] == "takeoff" and records[0]["latency_ms"] == 12.3
        assert records[1]["rule"] == "confirmation.unknown_or_used"

    def test_append_only(self, tmp_path):
        path = tmp_path / "audit.jsonl"
        log = AuditLog(path)
        for i in range(3):
            log.write(AuditRecord(new_call_id(), "c", True, "fp", None, f"t{i}", "normal", {}, "allowed"))
        assert len(path.read_text().strip().splitlines()) == 3

    def test_secrets_redacted(self):
        args = redact_args({"confirm_token": "SECRET", "name": "RTL_ALT", "api_key": "K"})
        assert args["confirm_token"] == "<redacted>"
        assert args["api_key"] == "<redacted>"
        assert args["name"] == "RTL_ALT"

    def test_large_args_truncated(self):
        args = redact_args({"waypoints": [{"lat": 1.0, "lon": 2.0}] * 500})
        assert isinstance(args["waypoints"], str) and "truncated" in args["waypoints"]

    def test_record_is_valid_json(self):
        record = AuditRecord(new_call_id(), "c", False, "", None, "takeoff", "normal", {"a": object()}, "allowed")
        assert json.loads(record.to_json())["tool"] == "takeoff"
