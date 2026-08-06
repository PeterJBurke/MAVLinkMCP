"""SITL integration tests for the v2 geofence tools (ArduCopter docker SITL)."""

import pytest

from tests.integration.conftest import SITL_HOME

pytestmark = pytest.mark.sitl

LAT, LON = SITL_HOME["lat"], SITL_HOME["lon"]
D = 0.002  # ~200 m


def _square():
    return [
        {"latitude_deg": LAT - D, "longitude_deg": LON - D},
        {"latitude_deg": LAT - D, "longitude_deg": LON + D},
        {"latitude_deg": LAT + D, "longitude_deg": LON + D},
        {"latitude_deg": LAT + D, "longitude_deg": LON - D},
    ]


def test_upload_polygon_and_circle(drone_tools):
    result = drone_tools.call(
        "upload_geofence",
        polygons=[{"points": _square(), "fence_type": "inclusion"}],
        circles=[{"latitude_deg": LAT, "longitude_deg": LON, "radius_m": 250.0, "fence_type": "inclusion"}],
    )
    assert result["status"] == "success", result
    assert "1 polygon(s), 1 circle(s)" in result["message"]


def test_clear_geofence(drone_tools):
    result = drone_tools.call("clear_geofence")
    assert result["status"] == "success", result


def test_invalid_polygon_rejected(drone_tools):
    result = drone_tools.call(
        "upload_geofence",
        polygons=[{"points": _square()[:2], "fence_type": "inclusion"}],
    )
    assert result["status"] == "failed"
    assert "at least 3 points" in result["error"]


def test_empty_geofence_rejected(drone_tools):
    result = drone_tools.call("upload_geofence", polygons=[], circles=[])
    assert result["status"] == "failed"
    assert "at least one polygon or circle" in result["error"]
