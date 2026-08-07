"""Unit tests for parameter bounds, state preconditions and rate limiting."""

import pytest

from droneserver.safety.config import SafetySettings
from droneserver.safety.geofence import Geofence
from droneserver.safety.validation import (
    RateLimiter,
    check_geofence,
    check_parameter_bounds,
    check_preconditions,
)

HOME = (-35.363262, 149.165237)


@pytest.fixture
def s():
    return SafetySettings(
        _env_file=None,
        max_altitude_m=120.0,
        max_speed_m_s=20.0,
        max_mission_items=200,
        takeoff_settle_s=3.0,
    )


AIRBORNE = {"armed": True, "in_air": True, "unknown": False, "seconds_since_takeoff": 60.0}
GROUNDED = {"armed": False, "in_air": False, "unknown": False, "seconds_since_takeoff": None}


class TestParameterBounds:
    def test_takeoff_altitude_capped(self, s):
        r = check_parameter_bounds("takeoff", {"takeoff_altitude": 500.0}, s)
        assert r is not None and r.rule == "bounds.max_altitude"
        assert "120" in r.remedy

    def test_takeoff_altitude_ok(self, s):
        assert check_parameter_bounds("takeoff", {"takeoff_altitude": 30.0}, s) is None

    def test_offboard_down_is_altitude_up(self, s):
        # down_m = -500 means 500 m ABOVE the origin
        r = check_parameter_bounds("offboard_set_position_ned", {"down_m": -500.0}, s)
        assert r is not None and r.rule == "bounds.max_altitude"
        assert check_parameter_bounds("offboard_set_position_ned", {"down_m": -50.0}, s) is None

    def test_speed_capped(self, s):
        r = check_parameter_bounds("set_max_speed", {"speed_m_s": 99.0}, s)
        assert r is not None and r.rule == "bounds.max_speed"

    def test_negative_speed_magnitude_capped(self, s):
        r = check_parameter_bounds("offboard_set_velocity_ned", {"north_m_s": -99.0}, s)
        assert r is not None and r.rule == "bounds.max_speed"

    def test_coordinate_sanity(self, s):
        r = check_parameter_bounds("go_to_location", {"latitude_deg": 999.0, "longitude_deg": 0.0}, s)
        assert r is not None and r.rule == "bounds.latitude"
        r = check_parameter_bounds("go_to_location", {"latitude_deg": 0.0, "longitude_deg": 999.0}, s)
        assert r is not None and r.rule == "bounds.longitude"

    def test_mission_size(self, s):
        r = check_parameter_bounds("upload_mission", {"waypoints": [{}] * 500}, s)
        assert r is not None and r.rule == "bounds.mission_size"

    def test_unrelated_tool_untouched(self, s):
        assert check_parameter_bounds("get_position", {}, s) is None


class TestAltitudeFrames:
    """Tools disagree on altitude frame; the bound is 'metres above home'."""

    HOME_STATE = {"home_altitude_m": 584.0}

    def test_amsl_converted_using_home_altitude(self, s):
        # 604 m AMSL over a 584 m home = 20 m AGL -> allowed
        args = {"latitude_deg": 0.0, "longitude_deg": 0.0, "absolute_altitude_m": 604.0}
        assert check_parameter_bounds("go_to_location", args, s, self.HOME_STATE) is None
        # 800 m AMSL = 216 m AGL -> rejected
        args["absolute_altitude_m"] = 800.0
        r = check_parameter_bounds("go_to_location", args, s, self.HOME_STATE)
        assert r is not None and r.rule == "bounds.max_altitude"

    def test_amsl_not_range_checked_without_home_altitude(self, s):
        """Checking AMSL against the wrong datum would reject valid commands."""
        args = {"latitude_deg": 0.0, "longitude_deg": 0.0, "absolute_altitude_m": 604.0}
        assert check_parameter_bounds("go_to_location", args, s, {}) is None

    def test_relative_frame_unaffected_by_home(self, s):
        r = check_parameter_bounds("takeoff", {"takeoff_altitude": 500.0}, s, self.HOME_STATE)
        assert r is not None and r.rule == "bounds.max_altitude"

    def test_offboard_global_respects_altitude_type(self, s):
        rel = {"latitude_deg": 0.0, "longitude_deg": 0.0, "altitude_m": 30.0, "altitude_type": "rel_home"}
        assert check_parameter_bounds("offboard_set_position_global", rel, s, self.HOME_STATE) is None
        amsl = {**rel, "altitude_m": 604.0, "altitude_type": "amsl"}
        assert check_parameter_bounds("offboard_set_position_global", amsl, s, self.HOME_STATE) is None
        too_high = {**rel, "altitude_m": 800.0, "altitude_type": "amsl"}
        r = check_parameter_bounds("offboard_set_position_global", too_high, s, self.HOME_STATE)
        assert r is not None and r.rule == "bounds.max_altitude"


class TestPreconditions:
    def test_takeoff_requires_armed(self, s):
        r = check_preconditions("takeoff", {}, GROUNDED, s)
        assert r is not None and r.rule == "precondition.takeoff_requires_armed"
        assert "arm_drone" in r.remedy

    def test_takeoff_allowed_when_armed(self, s):
        r = check_preconditions("takeoff", {}, {**GROUNDED, "armed": True}, s)
        assert r is None

    def test_navigation_requires_airborne(self, s):
        r = check_preconditions("go_to_location", {}, GROUNDED, s)
        assert r is not None and r.rule == "precondition.navigation_requires_airborne"

    def test_takeoff_settling_window_blocks_navigation(self, s):
        """The takeoff-then-crash timing fix."""
        state = {**AIRBORNE, "seconds_since_takeoff": 0.5}
        r = check_preconditions("go_to_location", {}, state, s)
        assert r is not None and r.rule == "precondition.takeoff_settling"

    def test_navigation_allowed_after_settling(self, s):
        state = {**AIRBORNE, "seconds_since_takeoff": 10.0}
        assert check_preconditions("go_to_location", {}, state, s) is None

    def test_mission_start_requires_upload(self, s):
        state = {**AIRBORNE, "mission_uploaded": False}
        r = check_preconditions("initiate_mission", {}, state, s)
        assert r is not None and r.rule == "precondition.mission_required"

    def test_unknown_state_fails_open_by_default(self, s):
        assert check_preconditions("go_to_location", {}, {"unknown": True}, s) is None

    def test_unknown_state_fails_closed_when_configured(self):
        s = SafetySettings(_env_file=None, preconditions_fail_closed=True)
        r = check_preconditions("go_to_location", {}, {"unknown": True}, s)
        assert r is not None and r.rule == "precondition.state_unknown"


class TestGeofenceRule:
    fence = Geofence(max_altitude_m=120.0, max_radius_m=500.0, home=HOME)

    def test_goto_outside_radius_rejected(self, s):
        r = check_geofence("go_to_location", {"latitude_deg": HOME[0] + 0.5, "longitude_deg": HOME[1]}, self.fence, s)
        assert r is not None and r.rule == "geofence.radius"

    def test_goto_inside_allowed(self, s):
        assert (
            check_geofence("go_to_location", {"latitude_deg": HOME[0], "longitude_deg": HOME[1]}, self.fence, s) is None
        )

    def test_whole_mission_rejected_before_upload(self, s):
        wps = [
            {"latitude_deg": HOME[0], "longitude_deg": HOME[1], "altitude_m": 20},
            {"latitude_deg": HOME[0] + 0.5, "longitude_deg": HOME[1], "altitude_m": 20},
        ]
        r = check_geofence("upload_mission", {"waypoints": wps}, self.fence, s)
        assert r is not None and "mission item 1" in r.reason
        assert "nothing was sent to the drone" in r.remedy

    def test_inactive_fence_allows(self, s):
        inactive = Geofence(max_altitude_m=0.0, max_radius_m=0.0)
        assert check_geofence("go_to_location", {"latitude_deg": 0, "longitude_deg": 0}, inactive, s) is None


class TestRateLimiter:
    def test_allows_under_limit_then_blocks(self):
        s = SafetySettings(_env_file=None, rate_limit_calls=3, rate_limit_window_s=60)
        limiter = RateLimiter()
        for _ in range(3):
            assert limiter.check("c1", False, s, now=100.0) is None
        r = limiter.check("c1", False, s, now=100.0)
        assert r is not None and r.rule == "rate_limit.normal"

    def test_window_slides(self):
        s = SafetySettings(_env_file=None, rate_limit_calls=2, rate_limit_window_s=10)
        limiter = RateLimiter()
        limiter.check("c1", False, s, now=0.0)
        limiter.check("c1", False, s, now=1.0)
        assert limiter.check("c1", False, s, now=2.0) is not None
        assert limiter.check("c1", False, s, now=20.0) is None  # old calls expired

    def test_critical_budget_is_separate(self):
        s = SafetySettings(_env_file=None, rate_limit_calls=10, rate_limit_critical_calls=1)
        limiter = RateLimiter()
        assert limiter.check("c1", True, s, now=0.0) is None
        assert limiter.check("c1", True, s, now=0.0) is not None  # critical exhausted
        assert limiter.check("c1", False, s, now=0.0) is None  # normal still fine

    def test_per_client(self):
        s = SafetySettings(_env_file=None, rate_limit_calls=1)
        limiter = RateLimiter()
        assert limiter.check("c1", False, s, now=0.0) is None
        assert limiter.check("c2", False, s, now=0.0) is None
        assert limiter.check("c1", False, s, now=0.0) is not None
