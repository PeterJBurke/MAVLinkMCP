"""Unit tests for MavSDK value-object -> JSON conversion."""

import enum

from droneserver.convert import to_jsonable


class Color(enum.Enum):
    RED = 1


class Inner:
    def __init__(self):
        self.pitch_deg = -45.0
        self.bad = float("nan")


class Outer:
    def __init__(self):
        self.gimbal_id = 1
        self.attitude = Inner()
        self.tags = ["a", Color.RED]
        self._private = "hidden"


def test_nested_objects_enums_and_nan():
    result = to_jsonable(Outer())
    assert result == {
        "gimbal_id": 1,
        "attitude": {"pitch_deg": -45.0, "bad": None},
        "tags": ["a", "RED"],
    }


def test_primitives_pass_through():
    assert to_jsonable(5) == 5
    assert to_jsonable("x") == "x"
    assert to_jsonable(None) is None
    assert to_jsonable([1, 2]) == [1, 2]
