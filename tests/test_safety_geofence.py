"""Unit tests for the server-side geofence geometry (no SITL)."""

import pytest

from droneserver.safety.geofence import (
    Geofence,
    check_mission,
    check_position,
    clip_altitude,
    haversine_m,
    parse_polygon,
    point_in_polygon,
)

HOME = (-35.363262, 149.165237)
D = 0.002  # ~200 m
SQUARE = (
    (HOME[0] - D, HOME[1] - D),
    (HOME[0] - D, HOME[1] + D),
    (HOME[0] + D, HOME[1] + D),
    (HOME[0] + D, HOME[1] - D),
)
FENCE = Geofence(polygon=SQUARE, max_altitude_m=120.0, max_radius_m=1000.0, home=HOME)


class TestParsePolygon:
    def test_parses_vertices(self):
        assert parse_polygon("1,2;3,4;5,6") == ((1.0, 2.0), (3.0, 4.0), (5.0, 6.0))

    def test_empty_is_no_polygon(self):
        assert parse_polygon("") == ()
        assert parse_polygon("   ") == ()

    def test_too_few_vertices_rejected(self):
        with pytest.raises(ValueError, match="at least 3"):
            parse_polygon("1,2;3,4")

    def test_malformed_rejected(self):
        with pytest.raises(ValueError, match="lat,lon"):
            parse_polygon("1,2,3;4,5;6,7")
        with pytest.raises(ValueError, match="not numeric"):
            parse_polygon("a,b;3,4;5,6")

    def test_out_of_range_rejected(self):
        with pytest.raises(ValueError, match="out of range"):
            parse_polygon("91,2;3,4;5,6")


class TestPointInPolygon:
    def test_inside_and_outside(self):
        assert point_in_polygon(HOME[0], HOME[1], SQUARE)
        assert not point_in_polygon(HOME[0] + 10 * D, HOME[1], SQUARE)

    def test_far_outside(self):
        assert not point_in_polygon(0.0, 0.0, SQUARE)


class TestCheckPosition:
    def test_inside_allowed(self):
        assert check_position(FENCE, HOME[0], HOME[1], 50.0) is None

    def test_altitude_ceiling(self):
        v = check_position(FENCE, HOME[0], HOME[1], 500.0)
        assert v is not None and v.rule == "geofence.altitude_ceiling"
        assert "500" in v.detail

    def test_outside_polygon(self):
        v = check_position(FENCE, HOME[0] + 10 * D, HOME[1], 50.0)
        assert v is not None and v.rule == "geofence.polygon"

    def test_radius_without_polygon(self):
        fence = Geofence(max_altitude_m=120.0, max_radius_m=100.0, home=HOME)
        v = check_position(fence, HOME[0] + 0.01, HOME[1], 50.0)
        assert v is not None and v.rule == "geofence.radius"

    def test_altitude_checked_even_without_coordinates(self):
        v = check_position(FENCE, None, None, 500.0)
        assert v is not None and v.rule == "geofence.altitude_ceiling"

    def test_inactive_fence_allows_everything(self):
        inactive = Geofence(max_altitude_m=0.0, max_radius_m=0.0)
        assert check_position(inactive, 0.0, 0.0, 99999.0) is None


class TestClipAltitude:
    def test_clips_to_ceiling(self):
        assert clip_altitude(FENCE, 500.0) == 120.0
        assert clip_altitude(FENCE, 50.0) == 50.0


class TestCheckMission:
    def test_all_inside_passes(self):
        wps = [{"latitude_deg": HOME[0], "longitude_deg": HOME[1], "altitude_m": 30}]
        assert check_mission(FENCE, wps) == []

    def test_reports_every_offending_item(self):
        wps = [
            {"latitude_deg": HOME[0], "longitude_deg": HOME[1], "altitude_m": 30},
            {"latitude_deg": HOME[0] + 10 * D, "longitude_deg": HOME[1], "altitude_m": 30},
            {"latitude_deg": HOME[0], "longitude_deg": HOME[1], "altitude_m": 900},
        ]
        violations = check_mission(FENCE, wps)
        assert [i for i, _ in violations] == [1, 2]

    def test_accepts_alternate_key_names(self):
        wps = [{"lat": HOME[0] + 10 * D, "lon": HOME[1], "alt": 30}]
        assert len(check_mission(FENCE, wps)) == 1

    def test_malformed_items_ignored_here(self):
        assert check_mission(FENCE, [{"latitude_deg": "abc"}, "not a dict"]) == []


def test_haversine_sanity():
    # one degree of latitude is ~111 km
    assert 110_000 < haversine_m(0, 0, 1, 0) < 112_000
