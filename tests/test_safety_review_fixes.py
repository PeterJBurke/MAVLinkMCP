"""Regression tests for the defects found by the independent safety review.

Each test names the finding it pins down.
"""

import pytest

from droneserver.safety.auth import (
    _PARSED_KEYS,
    authenticate,
    parse_api_keys,
    validate_api_keys_at_startup,
)
from droneserver.safety.config import SafetySettings, get_safety_settings, reset_safety_settings
from droneserver.safety.geofence import Geofence
from droneserver.safety.validation import (
    check_geofence,
    check_parameter_bounds,
    check_preconditions,
    resolve_target,
)

HOME = (-35.363262, 149.165237)
FENCE = Geofence(max_altitude_m=120.0, max_radius_m=300.0, home=HOME)
AIRBORNE = {
    "armed": True,
    "in_air": True,
    "unknown": False,
    "seconds_since_takeoff": 60.0,
    "position": {"latitude_deg": HOME[0], "longitude_deg": HOME[1], "relative_altitude_m": 30.0},
}


@pytest.fixture
def s():
    return SafetySettings(_env_file=None, max_altitude_m=120.0, max_speed_m_s=20.0, max_distance_from_home_m=2000.0)


class TestB1ConfigAndKeyCaching:
    """B1: SAFETY_API_KEYS was re-parsed on every call and raised inside the guard."""

    def test_keys_parsed_once_per_spec(self):
        _PARSED_KEYS.clear()
        spec = "alice:KEY1:control"
        first = parse_api_keys(spec)
        second = parse_api_keys(spec)
        assert first is second, "the parsed registry must be cached, not rebuilt per call"

    def test_startup_validation_rejects_malformed_spec(self):
        _PARSED_KEYS.clear()
        with pytest.raises(SystemExit) as excinfo:
            validate_api_keys_at_startup("alice:KEY1")  # missing scope
        assert "SAFETY_API_KEYS is malformed" in str(excinfo.value)

    def test_startup_validation_accepts_good_spec(self):
        _PARSED_KEYS.clear()
        validate_api_keys_at_startup("alice:KEY1:control,bob:KEY2:telemetry")
        assert authenticate("KEY1", SafetySettings(_env_file=None, api_keys="alice:KEY1:control")).scope == "control"

    def test_settings_are_cached(self):
        reset_safety_settings()
        first = get_safety_settings()
        assert get_safety_settings() is first, "settings must not be re-read from disk per call"
        reset_safety_settings()
        assert get_safety_settings() is not first


class TestB3RelativeMoveBounds:
    """B3: move_to_relative had no bounds and no fence despite moving the drone."""

    def test_absurd_offset_rejected(self, s):
        r = check_parameter_bounds("move_to_relative", {"north_m": 50000, "east_m": 0}, s, AIRBORNE)
        assert r is not None and r.rule == "bounds.max_offset"

    def test_reasonable_offset_allowed(self, s):
        assert check_parameter_bounds("move_to_relative", {"north_m": 50, "east_m": 20}, s, AIRBORNE) is None

    def test_offset_altitude_is_bounded(self, s):
        # down_m = -500 from 30 m up is 530 m
        r = check_parameter_bounds("move_to_relative", {"north_m": 0, "east_m": 0, "down_m": -500}, s, AIRBORNE)
        assert r is not None and r.rule == "bounds.max_altitude"

    def test_offset_is_fenced(self, s):
        """1 km north of a home-centred 300 m fence must be refused."""
        r = check_geofence("move_to_relative", {"north_m": 1000, "east_m": 0}, FENCE, s, AIRBORNE)
        assert r is not None and r.rule == "geofence.radius"

    def test_offset_inside_fence_allowed(self, s):
        assert check_geofence("move_to_relative", {"north_m": 50, "east_m": 0}, FENCE, s, AIRBORNE) is None

    def test_offset_refused_when_position_unknown(self, s):
        state = {**AIRBORNE, "position": None}
        r = check_geofence("move_to_relative", {"north_m": 50, "east_m": 0}, FENCE, s, state)
        assert r is not None and r.rule == "geofence.target_unresolvable"


class TestS1OffboardHorizontalFencing:
    """S1: offboard NED position and velocity were horizontally unfenced."""

    def test_position_ned_horizontal_fenced(self, s):
        r = check_geofence(
            "offboard_set_position_ned", {"north_m": 2000, "east_m": 0, "down_m": -20}, FENCE, s, AIRBORNE
        )
        assert r is not None and r.rule == "geofence.radius"

    def test_velocity_projected_forward_and_fenced(self, s):
        """20 m/s north for a 60 s stale window leaves a 300 m fence."""
        r = check_geofence(
            "offboard_set_velocity_ned", {"north_m_s": 20, "east_m_s": 0, "stale_timeout_s": 60}, FENCE, s, AIRBORNE
        )
        assert r is not None and r.rule == "geofence.radius"

    def test_slow_velocity_within_horizon_allowed(self, s):
        assert (
            check_geofence(
                "offboard_set_velocity_ned", {"north_m_s": 1, "east_m_s": 0, "stale_timeout_s": 5}, FENCE, s, AIRBORNE
            )
            is None
        )

    def test_body_velocity_uses_worst_case_direction(self, s):
        r = check_geofence(
            "offboard_set_velocity_body", {"forward_m_s": 20, "right_m_s": 0, "stale_timeout_s": 60}, FENCE, s, AIRBORNE
        )
        assert r is not None

    def test_follow_me_target_is_fenced(self, s):
        far = {"action": "target", "latitude_deg": HOME[0] + 0.5, "longitude_deg": HOME[1]}
        r = check_geofence("follow_me", far, FENCE, s, AIRBORNE)
        assert r is not None and r.rule == "geofence.radius"

    def test_follow_me_non_target_actions_ignored(self, s):
        assert check_geofence("follow_me", {"action": "status"}, FENCE, s, AIRBORNE) is None


class TestS2GimbalNotFenced:
    """S2: a configured polygon rejected every gimbal command, because lat/lon
    default to 0.0 for action='set_angles'."""

    def test_set_angles_not_rejected_by_fence(self, s):
        polygon_fence = Geofence(
            polygon=(
                (HOME[0] - 0.002, HOME[1] - 0.002),
                (HOME[0] - 0.002, HOME[1] + 0.002),
                (HOME[0] + 0.002, HOME[1] + 0.002),
                (HOME[0] + 0.002, HOME[1] - 0.002),
            ),
            max_altitude_m=120.0,
            home=HOME,
        )
        args = {"gimbal_id": 1, "action": "set_angles", "pitch": -30.0}
        assert check_geofence("gimbal_point", args, polygon_fence, s, AIRBORNE) is None

    def test_roi_outside_fence_still_allowed(self, s):
        """Looking at something outside the fence is not a containment breach."""
        args = {"gimbal_id": 1, "action": "roi_location", "latitude_deg": HOME[0] + 0.5, "longitude_deg": HOME[1]}
        assert check_geofence("gimbal_point", args, FENCE, s, AIRBORNE) is None


class TestS6CalibrationGroundOnly:
    def test_calibrate_refused_in_air(self, s):
        r = check_preconditions("calibrate", {"sensor": "gyro"}, AIRBORNE, s)
        assert r is not None and r.rule == "precondition.ground_only"

    def test_calibrate_refused_when_state_unknown(self, s):
        r = check_preconditions("calibrate", {"sensor": "gyro"}, {"unknown": True}, s)
        assert r is not None and r.rule == "precondition.ground_only"

    def test_calibrate_allowed_on_ground(self, s):
        grounded = {"armed": False, "in_air": False, "unknown": False}
        assert check_preconditions("calibrate", {"sensor": "gyro"}, grounded, s) is None


class TestS7ManagedMissionAltitude:
    def test_takeoff_altitude_bounded(self, s):
        r = check_parameter_bounds("start_managed_mission", {"takeoff_altitude_m": 900}, s, AIRBORNE)
        assert r is not None and r.rule == "bounds.max_altitude"

    def test_reasonable_takeoff_altitude_allowed(self, s):
        assert check_parameter_bounds("start_managed_mission", {"takeoff_altitude_m": 30}, s, AIRBORNE) is None


class TestS8RadiusFenceInertUntilHomeKnown:
    def test_refuses_rather_than_silently_passing(self, s):
        fence = Geofence(max_altitude_m=120.0, max_radius_m=300.0, home=None)
        args = {"latitude_deg": HOME[0], "longitude_deg": HOME[1], "absolute_altitude_m": 600.0}
        r = check_geofence("go_to_location", args, fence, s, AIRBORNE)
        assert r is not None and r.rule == "geofence.home_unknown"

    def test_no_radius_configured_is_fine(self, s):
        fence = Geofence(max_altitude_m=120.0, max_radius_m=0.0, home=None)
        args = {"latitude_deg": HOME[0], "longitude_deg": HOME[1], "absolute_altitude_m": 600.0}
        assert check_geofence("go_to_location", args, fence, s, AIRBORNE) is None


class TestS9FailClosedCoversAllStateRules:
    def test_fail_closed_covers_takeoff_and_mission_start(self):
        s = SafetySettings(_env_file=None, preconditions_fail_closed=True)
        for tool in ("go_to_location", "takeoff", "initiate_mission"):
            r = check_preconditions(tool, {}, {"unknown": True}, s)
            assert r is not None, f"{tool} must fail closed when state is unknown"
            assert r.rule == "precondition.state_unknown"

    def test_calibrate_blocked_regardless_of_policy(self):
        """Calibration gets the more specific ground-only rule, and is blocked
        on unknown state even in the default fail-OPEN configuration."""
        for fail_closed in (False, True):
            s = SafetySettings(_env_file=None, preconditions_fail_closed=fail_closed)
            r = check_preconditions("calibrate", {}, {"unknown": True}, s)
            assert r is not None and r.rule == "precondition.ground_only"

    def test_fail_open_default_unchanged(self, s):
        assert check_preconditions("go_to_location", {}, {"unknown": True}, s) is None


class TestResolveTarget:
    def test_unrelated_tool_resolves_to_nothing(self, s):
        assert resolve_target("get_position", {}, AIRBORNE, s) == (None, None, None, None)

    def test_offset_resolution_is_roughly_right(self, s):
        lat, lon, _alt, err = resolve_target("move_to_relative", {"north_m": 111.32, "east_m": 0}, AIRBORNE, s)
        assert err is None
        assert lat == pytest.approx(HOME[0] + 0.001, abs=1e-5)


class TestB1GuardFailsClosed:
    """B1: the guard used to execute the tool when it crashed. It must refuse."""

    def _make_guarded_tool(self, monkeypatch, boom: bool):
        from droneserver.safety import middleware as M

        executed: list[bool] = []

        async def tool(ctx=None, **kwargs):
            executed.append(True)
            return {"status": "success"}

        tool.__name__ = "takeoff"

        if boom:

            async def exploding(*a, **kw):
                raise RuntimeError("simulated guard fault")

            monkeypatch.setattr(M, "_evaluate", exploding)
        return M.guard(tool), executed

    def test_guard_crash_refuses_and_does_not_execute(self, monkeypatch, tmp_path):
        import asyncio

        monkeypatch.setenv("SAFETY_AUDIT_LOG_PATH", str(tmp_path / "audit.jsonl"))
        reset_safety_settings()
        guarded, executed = self._make_guarded_tool(monkeypatch, boom=True)

        result = asyncio.run(guarded(ctx=None))

        assert result["status"] == "rejected", result
        assert result["rule"] == "guard.internal_error"
        assert not executed, "the tool must NOT run when the guard itself failed"
        reset_safety_settings()

    def test_guard_crash_is_distinguishable_in_the_audit_log(self, monkeypatch, tmp_path):
        """S11: a crashed guard used to look exactly like an allowed call."""
        import asyncio
        import json

        audit = tmp_path / "audit.jsonl"
        monkeypatch.setenv("SAFETY_AUDIT_LOG_PATH", str(audit))
        reset_safety_settings()
        guarded, _ = self._make_guarded_tool(monkeypatch, boom=True)
        asyncio.run(guarded(ctx=None))

        rows = [json.loads(line) for line in audit.read_text().splitlines() if line.strip()]
        assert rows, "the failed call must still be audited"
        assert rows[-1]["verdict"] == "rejected"
        assert rows[-1]["rule"] == "guard.internal_error"
        assert "simulated guard fault" in (rows[-1]["guard_error"] or "")
        reset_safety_settings()
