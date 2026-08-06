"""Unit tests for the pure setpoint/geofence builders (no SITL, no I/O)."""

import pytest
from mavsdk.geofence import FenceType
from mavsdk.offboard import Attitude, AttitudeRate

from droneserver import setpoints as sp

HOME = {"latitude_deg": -35.363262, "longitude_deg": 149.165237}


def _square(d=0.001):
    lat, lon = HOME["latitude_deg"], HOME["longitude_deg"]
    return [
        {"latitude_deg": lat - d, "longitude_deg": lon - d},
        {"latitude_deg": lat - d, "longitude_deg": lon + d},
        {"latitude_deg": lat + d, "longitude_deg": lon + d},
        {"latitude_deg": lat + d, "longitude_deg": lon - d},
    ]


class TestGeofence:
    def test_polygon_and_circle(self):
        data = sp.build_geofence_data(
            [{"points": _square(), "fence_type": "inclusion"}],
            [{**HOME, "radius_m": 250.0, "fence_type": "exclusion"}],
        )
        assert len(data.polygons) == 1
        assert len(data.polygons[0].points) == 4
        assert data.polygons[0].fence_type == FenceType.INCLUSION
        assert data.circles[0].fence_type == FenceType.EXCLUSION
        assert data.circles[0].radius == 250.0

    def test_empty_rejected(self):
        with pytest.raises(ValueError, match="at least one polygon or circle"):
            sp.build_geofence_data([], [])

    def test_two_point_polygon_rejected(self):
        with pytest.raises(ValueError, match="at least 3 points"):
            sp.build_geofence_data([{"points": _square()[:2], "fence_type": "inclusion"}], [])

    def test_bad_fence_type_rejected(self):
        with pytest.raises(ValueError, match="fence_type"):
            sp.build_geofence_data([{"points": _square(), "fence_type": "keep-out"}], [])

    def test_out_of_range_latitude_rejected(self):
        bad = _square()
        bad[0]["latitude_deg"] = 123.0
        with pytest.raises(ValueError, match="latitude_deg"):
            sp.build_geofence_data([{"points": bad, "fence_type": "inclusion"}], [])


class TestPositionNed:
    def test_plain_position(self):
        pos, vel, acc = sp.build_position_ned(10, -5, -15, 90)
        assert (pos.north_m, pos.east_m, pos.down_m, pos.yaw_deg) == (10, -5, -15, 90)
        assert vel is None and acc is None

    def test_with_feed_forward(self):
        pos, vel, acc = sp.build_position_ned(
            0,
            0,
            -15,
            0,
            velocity={"north_m_s": 1.0},
            acceleration={"north_m_s2": 0.1},
        )
        assert vel.north_m_s == 1.0
        assert acc.north_m_s2 == 0.1

    def test_acceleration_without_velocity_rejected(self):
        with pytest.raises(ValueError, match="requires velocity"):
            sp.build_position_ned(0, 0, -15, 0, acceleration={"north_m_s2": 0.1})


class TestPositionGlobal:
    def test_altitude_types(self):
        for name in ("amsl", "rel_home", "agl"):
            target = sp.build_position_global(**HOME, altitude_m=30, yaw_deg=0, altitude_type=name)
            assert target.altitude_type == sp.ALTITUDE_TYPES[name]

    def test_bad_altitude_type_rejected(self):
        with pytest.raises(ValueError, match="altitude_type"):
            sp.build_position_global(**HOME, altitude_m=30, yaw_deg=0, altitude_type="msl")


class TestVelocity:
    def test_ned(self):
        v = sp.build_velocity_ned(2, 0, -1, 45)
        assert (v.north_m_s, v.down_m_s, v.yaw_deg) == (2, -1, 45)

    def test_body(self):
        v = sp.build_velocity_body(1.5, 0, 0, 10)
        assert (v.forward_m_s, v.yawspeed_deg_s) == (1.5, 10)

    def test_overspeed_rejected(self):
        with pytest.raises(ValueError, match="north_m_s"):
            sp.build_velocity_ned(sp.MAX_SPEED_M_S + 1, 0, 0, 0)

    def test_non_numeric_rejected(self):
        with pytest.raises(ValueError, match="must be a number"):
            sp.build_velocity_ned("fast", 0, 0, 0)


class TestAttitude:
    def test_angle_mode(self):
        a = sp.build_attitude("angle", 0, -5, 0, 0.55)
        assert isinstance(a, Attitude)
        assert a.pitch_deg == -5
        assert a.thrust_value == 0.55

    def test_rate_mode(self):
        a = sp.build_attitude("rate", 0, 0, 5, 0.5)
        assert isinstance(a, AttitudeRate)
        assert a.yaw_deg_s == 5

    def test_bad_mode_rejected(self):
        with pytest.raises(ValueError, match="mode"):
            sp.build_attitude("euler", 0, 0, 0, 0.5)

    def test_thrust_out_of_range_rejected(self):
        with pytest.raises(ValueError, match="thrust"):
            sp.build_attitude("angle", 0, 0, 0, 1.5)


class TestActuatorControl:
    def test_single_group(self):
        ac = sp.build_actuator_control([[0.0, 0.5, -0.5]])
        assert len(ac.groups) == 1
        assert ac.groups[0].controls == [0.0, 0.5, -0.5]

    def test_too_many_groups_rejected(self):
        with pytest.raises(ValueError, match="groups"):
            sp.build_actuator_control([[0.0]] * 3)

    def test_out_of_range_control_rejected(self):
        with pytest.raises(ValueError, match=r"groups\[0\]\[1\]"):
            sp.build_actuator_control([[0.0, 2.0]])


class TestStaleTimeout:
    def test_bounds(self):
        assert sp.validate_stale_timeout(15) == 15.0
        for bad in (0.5, 300, "soon"):
            with pytest.raises(ValueError):
                sp.validate_stale_timeout(bad)
