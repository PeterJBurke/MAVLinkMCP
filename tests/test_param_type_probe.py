"""set_parameter must write to a parameter's REAL type, not one guessed from
whether the new value happens to be a whole number.

The regression (PX4 N=5 scripted validation, 2026-08-12): T7 wrote 15.0 to the
float parameter ``MPC_XY_CRUISE`` and read back the old value every trial. The
old ``auto`` logic keyed on ``value == int(value)`` - 15.0 is whole, so it wrote
via ``set_param_int`` to a float parameter, which PX4 silently drops. The fix
probes the live type (float first, as get_parameter does) and writes to that.
"""

import types

import pytest

from droneserver.tools import param as param_mod


class _Param:
    """A fake MavSDK param plugin with typed get/set and WRONG_TYPE errors."""

    def __init__(self, floats=None, ints=None):
        self._floats = dict(floats or {})
        self._ints = dict(ints or {})
        self.calls: list = []

    async def get_param_float(self, name):
        self.calls.append(("get_float", name))
        if name not in self._floats:
            raise RuntimeError(f"PARAM_WRONG_TYPE: {name} is not a float")
        return self._floats[name]

    async def get_param_int(self, name):
        self.calls.append(("get_int", name))
        if name not in self._ints:
            raise RuntimeError(f"PARAM_WRONG_TYPE: {name} is not an int")
        return self._ints[name]

    async def set_param_float(self, name, value):
        self.calls.append(("set_float", name, value))
        if name in self._ints:
            raise RuntimeError(f"PARAM_WRONG_TYPE: {name} is an int")
        self._floats[name] = value

    async def set_param_int(self, name, value):
        self.calls.append(("set_int", name, value))
        if name in self._floats:
            raise RuntimeError(f"PARAM_WRONG_TYPE: {name} is a float")
        self._ints[name] = value


def _ctx(param):
    connector = types.SimpleNamespace(drone=types.SimpleNamespace(param=param))
    return types.SimpleNamespace(request_context=types.SimpleNamespace(lifespan_context=connector))


@pytest.fixture(autouse=True)
def _always_connected(monkeypatch):
    async def _ok(_connector):
        return True

    monkeypatch.setattr(param_mod, "ensure_connection", _ok)


async def test_whole_number_float_param_is_written_as_float():
    """The regression: 15.0 into a float param must go via set_param_float."""
    p = _Param(floats={"MPC_XY_CRUISE": 12.0})
    r = await param_mod.set_parameter.__wrapped__(_ctx(p), name="MPC_XY_CRUISE", value=15.0, param_type="auto")

    assert r["status"] == "success"
    assert r["type"] == "float"
    assert ("set_float", "MPC_XY_CRUISE", 15.0) in p.calls
    assert all(c[0] != "set_int" for c in p.calls), "must not write a float param via set_param_int"
    assert p._floats["MPC_XY_CRUISE"] == 15.0  # readback would now match


async def test_genuine_int_param_still_written_as_int():
    """A real int param (float probe raises WRONG_TYPE) falls back to int."""
    p = _Param(ints={"BATT_CAPACITY": 5000})
    r = await param_mod.set_parameter.__wrapped__(_ctx(p), name="BATT_CAPACITY", value=5200, param_type="auto")

    assert r["status"] == "success"
    assert r["type"] == "int"
    assert ("set_int", "BATT_CAPACITY", 5200) in p.calls
    assert p._ints["BATT_CAPACITY"] == 5200


async def test_explicit_float_type_is_honoured():
    p = _Param(floats={"RTL_ALT": 10.0})
    r = await param_mod.set_parameter.__wrapped__(_ctx(p), name="RTL_ALT", value=15.0, param_type="float")
    assert r["status"] == "success"
    assert ("set_float", "RTL_ALT", 15.0) in p.calls
