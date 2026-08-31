"""The declared Python floor must be one the code can actually run on (FIX 17).

``pyproject.toml`` advertised ``requires-python = ">=3.10"`` from the start of
the v2 branch, and it was never true. Two load-bearing mechanisms need 3.11:

* :mod:`droneserver.llm.mcp_session` tells a transport's own cancel scope apart
  from a real cancellation by counting ``asyncio.Task.cancelling()`` across the
  teardown - the whole of FIX 16. ``Task.cancelling()`` and ``Task.uncancel()``
  were added in 3.11; on 3.10 the very first line of ``_connect`` raises
  ``AttributeError``, so no LLM session opens at all.
* The ~23 ``except TimeoutError:`` handlers in the tool modules catch the
  timeouts raised by ``asyncio.wait_for``. ``asyncio.TimeoutError`` only became
  the builtin ``TimeoutError`` in 3.11; on 3.10 they are unrelated classes
  (the builtin is an ``OSError``), so those handlers would catch nothing and a
  timed-out telemetry read would propagate instead of returning the friendly
  "no telemetry received" result the tools promise.

Nothing in CI runs 3.10, so neither would ever have been caught by the suite -
the metadata was the only thing making the false claim, and mypy (pinned to the
declared floor) was the only thing reporting it. These tests pin the floor so a
future edit cannot quietly lower it back under the APIs the code uses.
"""

from __future__ import annotations

import asyncio
import pathlib
import tomllib

MINIMUM = (3, 11)

_PYPROJECT = pathlib.Path(__file__).resolve().parent.parent / "pyproject.toml"


def _declared_floor() -> tuple[int, ...]:
    """The ``>=`` bound in ``requires-python``, as a version tuple."""
    spec = tomllib.loads(_PYPROJECT.read_text())["project"]["requires-python"]
    assert spec.startswith(">="), f"expected a >= floor, got {spec!r}"
    return tuple(int(part) for part in spec.removeprefix(">=").strip().split("."))


def test_declared_floor_is_at_least_the_apis_we_use():
    assert _declared_floor() >= MINIMUM, (
        "requires-python promises a version older than the asyncio APIs this package calls"
    )


def test_the_classifiers_do_not_advertise_a_version_below_the_floor():
    classifiers = tomllib.loads(_PYPROJECT.read_text())["project"]["classifiers"]
    advertised = [
        tuple(int(part) for part in c.rsplit(" :: ", 1)[1].split("."))
        for c in classifiers
        if c.startswith("Programming Language :: Python :: ") and "." in c.rsplit(" :: ", 1)[1]
    ]
    assert advertised, "the Python version classifiers went missing"
    assert min(advertised) >= MINIMUM, f"a classifier advertises a version below the floor: {sorted(advertised)}"


def test_mypy_checks_against_the_declared_floor():
    """mypy pinned below requires-python is what surfaced this in the first place."""
    config = tomllib.loads(_PYPROJECT.read_text())["tool"]["mypy"]["python_version"]
    checked = tuple(int(part) for part in config.split("."))
    assert checked == _declared_floor(), (
        f"mypy checks {checked} but the package promises {_declared_floor()} - "
        "the two must agree or the oldest supported version goes untyped-checked"
    )


def test_the_cancellation_apis_fix_16_depends_on_are_present():
    assert hasattr(asyncio.Task, "cancelling"), "FIX 16 counts task.cancelling() across a transport teardown"
    assert hasattr(asyncio.Task, "uncancel"), "anyio unwinds its own cancel scopes through task.uncancel()"


def test_an_asyncio_timeout_is_catchable_as_the_builtin_timeouterror():
    """What the ~23 ``except TimeoutError:`` handlers in the tool modules assume."""
    assert asyncio.TimeoutError is TimeoutError

    async def never():
        await asyncio.Event().wait()

    async def race():
        try:
            await asyncio.wait_for(never(), timeout=0.01)
        except TimeoutError:
            return "caught"
        return "escaped"

    assert asyncio.run(race()) == "caught"
