"""Unit tests for raw mission item builders and converters (no SITL)."""

import math

import pytest

from droneserver import mission_plans as mp

HOME = {"latitude_deg": -35.363262, "longitude_deg": 149.165237}


class TestRallyItems:
    def test_builds_items_with_first_current_1(self):
        items = mp.build_rally_items(
            [
                {**HOME, "altitude_m": 20.0},
                {"latitude_deg": HOME["latitude_deg"] + 0.001, "longitude_deg": HOME["longitude_deg"]},
            ]
        )
        assert len(items) == 2
        # The CURRENT_INVALID gotcha: MavSDK requires first item current=1
        assert items[0].current == 1
        assert items[1].current == 0
        assert items[0].command == mp.MAV_CMD_NAV_RALLY_POINT
        assert items[0].mission_type == mp.MISSION_TYPE_RALLY
        assert items[0].x == int(round(HOME["latitude_deg"] * 1e7))
        assert items[0].z == 20.0
        assert items[1].z == 0.0  # altitude defaults to 0

    def test_empty_rejected(self):
        with pytest.raises(ValueError, match="at least one rally point"):
            mp.build_rally_items([])

    def test_bad_latitude_rejected(self):
        with pytest.raises(ValueError, match=r"points\[0\]"):
            mp.build_rally_items([{"latitude_deg": 91.0, "longitude_deg": 0.0}])


class TestRawItems:
    def test_builds_and_forces_type_and_current(self):
        items = mp.build_raw_items(
            [
                {
                    "frame": 3,
                    "command": 5004,
                    "x": -353632620,
                    "y": 1491652370,
                    "z": 0.0,
                    "param1": 100.0,
                    "current": 0,
                },
            ],
            mp.MISSION_TYPE_FENCE,
        )
        assert items[0].mission_type == mp.MISSION_TYPE_FENCE
        assert items[0].current == 1  # forced for first item
        assert items[0].param1 == 100.0

    def test_missing_fields_rejected(self):
        with pytest.raises(ValueError, match="missing required fields"):
            mp.build_raw_items([{"frame": 3, "command": 16}], mp.MISSION_TYPE_FENCE)

    def test_none_param_becomes_nan(self):
        items = mp.build_raw_items(
            [{"frame": 3, "command": 16, "x": 0, "y": 0, "z": 1.0, "param4": None}],
            mp.MISSION_TYPE_MISSION,
        )
        assert math.isnan(items[0].param4)


class TestItemsToDicts:
    def test_roundtrip_with_lat_lon_and_nan(self):
        items = mp.build_raw_items(
            [{"frame": 3, "command": 16, "x": -353632620, "y": 1491652370, "z": 25.0, "param4": None}],
            mp.MISSION_TYPE_MISSION,
        )
        d = mp.items_to_dicts(items)[0]
        assert d["latitude_deg"] == pytest.approx(HOME["latitude_deg"])
        assert d["longitude_deg"] == pytest.approx(HOME["longitude_deg"])
        assert d["param4"] is None  # NaN -> None for JSON
        assert d["command"] == 16
