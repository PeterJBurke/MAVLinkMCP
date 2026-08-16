"""The two failsafe-policy decisions the project owner took on 2026-08-16.

Both follow the independent safety review's recommendations
(``docs/safety_review.md``; the review's "Recommendation on the fail-open /
fail-closed decision" section).

1. **Energy direction.** When the safety layer cannot read vehicle state, the
   deciding question is which way the command moves energy - not a single
   global timidity switch. Commands that REDUCE energy or recover the vehicle
   stay allowed (fail open); commands that ADD energy or commit to new motion
   are refused (fail closed, rule ``failsafe.energy_direction``).
2. **Unconfigured auth.** With no API keys configured, a client gets
   ``telemetry`` (read-only) scope, not ``control``.
"""

import asyncio

import pytest

import droneserver.tools  # noqa: F401  - registers all tools
from droneserver.app import mcp
from droneserver.safety import middleware as M
from droneserver.safety.auth import authenticate
from droneserver.safety.config import SafetySettings, reset_safety_settings
from droneserver.safety.tiers import Tier
from droneserver.safety.validation import (
    ENERGY_ADDING_TOOLS,
    ENERGY_DIRECTION,
    ENERGY_DIRECTION_BY_ARGS,
    EnergyDirection,
    check_preconditions,
    energy_direction,
)

UNKNOWN = {"unknown": True}
AIRBORNE = {"armed": True, "in_air": True, "unknown": False, "seconds_since_takeoff": 60.0}
GROUNDED = {"armed": False, "in_air": False, "unknown": False, "seconds_since_takeoff": None}


@pytest.fixture(params=[False, True], ids=["fail_open_default", "fail_closed_configured"])
def s(request):
    """Both policy settings: the energy-direction split must hold under each."""
    return SafetySettings(_env_file=None, preconditions_fail_closed=request.param)


# --------------------------------------------------------------- change 1

#: Recovery / energy-reducing calls. Must ALWAYS be allowed on unknown state.
FAIL_OPEN_CALLS = [
    ("land", {}),
    ("return_to_launch", {}),
    ("hold_position", {}),
    ("hold_mission_position", {}),
    ("pause_mission", {}),
    ("monitor_flight", {}),
    ("emergency_stop", {"mode": "land"}),
    ("emergency_stop", {"mode": "rtl"}),
    ("emergency_stop", {"mode": "kill"}),
    ("kill_motors", {}),
    ("disarm_drone", {}),
    ("offboard_control", {"action": "stop"}),
    ("offboard_control", {"action": "status"}),
    ("set_flight_mode", {"mode": "LAND"}),
    ("set_flight_mode", {"mode": "RTL"}),
    ("set_flight_mode", {"mode": "LOITER"}),
    ("set_flight_mode", {"mode": "HOLD"}),
    ("control_managed_mission", {"action": "abort"}),
    ("raw_mission_control", {"action": "pause"}),
    ("follow_me", {"action": "stop"}),
]

#: Energy-adding / new-motion calls. Must ALWAYS be refused on unknown state.
FAIL_CLOSED_CALLS = [
    ("arm_drone", {}),
    ("takeoff", {"takeoff_altitude": 10.0}),
    ("go_to_location", {"latitude_deg": 1.0, "longitude_deg": 2.0}),
    ("move_to_relative", {"north_m": 20.0}),
    ("reposition", {"latitude_deg": 1.0, "longitude_deg": 2.0}),
    ("set_yaw", {"yaw_deg": 90.0}),
    ("do_orbit", {"radius_m": 20.0}),
    ("offboard_set_position_ned", {"north_m": 10.0}),
    ("offboard_set_position_global", {"latitude_deg": 1.0}),
    ("offboard_set_velocity_ned", {"north_m_s": 2.0}),
    ("offboard_set_velocity_body", {"forward_m_s": 2.0}),
    ("offboard_set_attitude", {"pitch_deg": 5.0}),
    ("offboard_set_acceleration_ned", {"north_m_s2": 1.0}),
    ("offboard_set_actuator_control", {"group": 0}),
    ("offboard_control", {"action": "start"}),
    ("initiate_mission", {}),
    ("resume_mission", {}),
    ("start_managed_mission", {"takeoff_altitude_m": 10.0}),
    ("raw_mission_control", {"action": "start"}),
    ("control_managed_mission", {"action": "resume"}),
    ("set_max_speed", {"speed_m_s": 10.0}),
    ("set_actuator", {"index": 1, "value": 0.5}),
    ("manual_control", {"action": "start"}),
    ("vtol_transition", {"to": "fixedwing"}),
    ("follow_me", {"action": "start"}),
    ("set_flight_mode", {"mode": "AUTO"}),
    ("set_flight_mode", {"mode": "GUIDED"}),
]


class TestEnergyDirectionUnknownState:
    """Peter 2026-08-16 decision #1 - the fail-open/fail-closed split."""

    @pytest.mark.parametrize("tool,args", FAIL_OPEN_CALLS)
    def test_recovery_commands_stay_allowed(self, tool, args, s):
        assert energy_direction(tool, args) is EnergyDirection.REDUCES
        assert check_preconditions(tool, args, UNKNOWN, s) is None, (
            f"{tool}{args} reduces energy and must never be blocked by unreadable telemetry"
        )

    @pytest.mark.parametrize("tool,args", FAIL_CLOSED_CALLS)
    def test_energy_adding_commands_are_refused(self, tool, args, s):
        assert energy_direction(tool, args) is EnergyDirection.ADDS
        rejection = check_preconditions(tool, args, UNKNOWN, s)
        assert rejection is not None, f"{tool}{args} adds energy and must be refused on unknown state"
        assert rejection.rule == "failsafe.energy_direction"
        assert "telemetry" in rejection.remedy

    def test_the_rejection_tells_the_model_what_it_may_still_do(self, s):
        rejection = check_preconditions("takeoff", {}, UNKNOWN, s)
        for recovery in ("land", "return_to_launch", "hold_position", "emergency_stop"):
            assert recovery in rejection.remedy

    def test_ground_only_rule_still_wins_over_the_energy_split(self, s):
        """Calibration in flight is a crash; it keeps its more specific rule."""
        rejection = check_preconditions("calibrate", {"sensor": "gyro"}, UNKNOWN, s)
        assert rejection is not None and rejection.rule == "precondition.ground_only"

    def test_unclassified_tool_keeps_the_configured_policy(self):
        """NEUTRAL tools are untouched: the old switch still decides them."""
        assert energy_direction("send_status_text", {}) is EnergyDirection.NEUTRAL
        for fail_closed in (False, True):
            s = SafetySettings(_env_file=None, preconditions_fail_closed=fail_closed)
            assert check_preconditions("send_status_text", {"text": "hi"}, UNKNOWN, s) is None


class TestKnownStatePathsUnchanged:
    """The split only changes what happens when state is UNKNOWN."""

    def test_navigation_airborne_still_allowed(self, s):
        assert check_preconditions("go_to_location", {}, AIRBORNE, s) is None

    def test_navigation_on_the_ground_keeps_its_own_rule(self, s):
        rejection = check_preconditions("go_to_location", {}, GROUNDED, s)
        assert rejection is not None and rejection.rule == "precondition.navigation_requires_airborne"

    def test_takeoff_disarmed_keeps_its_own_rule(self, s):
        rejection = check_preconditions("takeoff", {}, GROUNDED, s)
        assert rejection is not None and rejection.rule == "precondition.takeoff_requires_armed"

    def test_takeoff_armed_still_allowed(self, s):
        assert check_preconditions("takeoff", {}, {**GROUNDED, "armed": True}, s) is None

    def test_takeoff_settling_window_survives(self, s):
        state = {**AIRBORNE, "seconds_since_takeoff": 0.5}
        rejection = check_preconditions("go_to_location", {}, state, s)
        assert rejection is not None and rejection.rule == "precondition.takeoff_settling"


class TestEnergyTableIsWellFormed:
    def test_no_tool_is_classified_twice(self):
        both = sorted(set(ENERGY_DIRECTION) & set(ENERGY_DIRECTION_BY_ARGS))
        assert not both, f"a tool must be in exactly one energy table, not both: {both}"

    def test_no_stale_entries(self):
        registered = {t.name for t in asyncio.run(mcp.list_tools())}
        stale = sorted((set(ENERGY_DIRECTION) | set(ENERGY_DIRECTION_BY_ARGS)) - registered)
        assert not stale, f"the energy-direction tables list tools that no longer exist: {stale}"

    def test_every_refusable_tool_has_its_state_refreshed(self):
        """The trap this table could fall into.

        A tool the failsafe can REFUSE must be in the middleware's state-refresh
        set. Otherwise it reads a stale snapshot, which says "unknown", and the
        tool is refused forever - the same way the S3 fence escalation once
        demanded a confirmation token for a fence upload on the ground.
        """
        missing = sorted(ENERGY_ADDING_TOOLS - M._STATE_DEPENDENT)
        assert not missing, f"these tools can be refused on unknown state but are never refreshed: {missing}"


# --------------------------------------------------------------- change 2


class TestUnconfiguredAuthIsTelemetryOnly:
    """Peter 2026-08-16 decision #2 - the unconfigured fallback tightens."""

    def test_scope_is_telemetry_not_control(self):
        s = SafetySettings(_env_file=None, api_keys="")
        client = authenticate(None, s)
        assert client.client_id == "unconfigured"
        assert client.scope == "telemetry"
        assert client.can(Tier.READ_ONLY)
        for tier in (Tier.NORMAL, Tier.CRITICAL, Tier.EMERGENCY):
            assert not client.can(tier), f"unconfigured clients must not reach {tier.value}"

    def test_still_marked_unauthenticated_for_the_audit_log(self):
        s = SafetySettings(_env_file=None, api_keys="")
        assert authenticate(None, s).authenticated is False

    def test_the_warning_still_fires_and_names_the_fix(self, monkeypatch):
        from droneserver.safety import auth as auth_mod

        warnings: list[str] = []
        monkeypatch.setattr(auth_mod.logger, "warning", lambda message, *a, **kw: warnings.append(str(message)))
        monkeypatch.setattr(auth_mod, "_warned_unconfigured", False)

        auth_mod.authenticate(None, SafetySettings(_env_file=None, api_keys=""))

        message = " ".join(warnings)
        assert "SAFETY_API_KEYS" in message
        assert "telemetry" in message and "COMMAND AND CONTROL REQUIRES" in message

    def test_explicit_setting_still_wins(self):
        """An operator who really wants the old behaviour can still ask for it."""
        s = SafetySettings(_env_file=None, api_keys="", unauthenticated_scope="control")
        assert authenticate(None, s).scope == "control"
        s = SafetySettings(_env_file=None, api_keys="", unauthenticated_scope="reject")
        assert authenticate(None, s).scope == "none"

    def test_a_control_call_is_refused_end_to_end(self, monkeypatch, tmp_path):
        """Through the real guard: no keys configured -> takeoff is rejected."""
        executed: list[bool] = []

        async def takeoff(ctx=None, **kwargs):
            executed.append(True)
            return {"status": "success"}

        takeoff.__name__ = "takeoff"

        monkeypatch.setenv("SAFETY_API_KEYS", "")
        monkeypatch.delenv("SAFETY_UNAUTHENTICATED_SCOPE", raising=False)
        monkeypatch.setenv("SAFETY_AUDIT_LOG_PATH", str(tmp_path / "audit.jsonl"))
        reset_safety_settings()
        try:
            result = asyncio.run(M.guard(takeoff)(ctx=None))
        finally:
            reset_safety_settings()

        assert result["status"] == "rejected", result
        assert result["rule"] == "authz.insufficient_scope"
        assert "control" in result["error"] and "telemetry" in result["error"]
        assert not executed, "the tool must not run for a telemetry-scope client"

    def test_a_read_only_call_still_works_end_to_end(self, monkeypatch, tmp_path):
        """The server is not bricked out of the box - it is read-only."""
        executed: list[bool] = []

        async def get_position(ctx=None, **kwargs):
            executed.append(True)
            return {"status": "success"}

        get_position.__name__ = "get_position"

        monkeypatch.setenv("SAFETY_API_KEYS", "")
        monkeypatch.delenv("SAFETY_UNAUTHENTICATED_SCOPE", raising=False)
        monkeypatch.setenv("SAFETY_AUDIT_LOG_PATH", str(tmp_path / "audit.jsonl"))
        reset_safety_settings()
        try:
            result = asyncio.run(M.guard(get_position)(ctx=None))
        finally:
            reset_safety_settings()

        assert result["status"] == "success" and executed
