"""Unit tests for the stale-setpoint watchdog (no SITL - fake brake callback)."""

import asyncio

from droneserver.safety.offboard_watchdog import OffboardWatchdog


async def test_expiry_invokes_brake():
    braked = asyncio.Event()

    async def brake():
        braked.set()

    wd = OffboardWatchdog()
    wd.note_setpoint("velocity_ned", 0.05, brake)
    await asyncio.wait_for(braked.wait(), timeout=2)
    assert wd.auto_braked is True
    assert wd.status()["last_setpoint"] == "velocity_ned"


async def test_new_setpoint_resets_timer():
    calls = []

    async def brake():
        calls.append(1)

    wd = OffboardWatchdog()
    wd.note_setpoint("velocity_ned", 0.2, brake)
    await asyncio.sleep(0.1)
    wd.note_setpoint("velocity_ned", 0.2, brake)  # refresh before expiry
    await asyncio.sleep(0.15)
    assert calls == []  # original timer must not have fired
    await asyncio.sleep(0.15)
    assert calls == [1]  # refreshed timer fired once


async def test_position_setpoint_clears_timer():
    calls = []

    async def brake():
        calls.append(1)

    wd = OffboardWatchdog()
    wd.note_setpoint("velocity_ned", 0.05, brake)
    wd.note_setpoint("position_ned", None, None)  # self-terminating setpoint
    await asyncio.sleep(0.15)
    assert calls == []
    assert wd.status()["stale_timeout_s"] is None
    assert wd.status()["auto_braked"] is False


async def test_cancel_stops_timer():
    calls = []

    async def brake():
        calls.append(1)

    wd = OffboardWatchdog()
    wd.note_setpoint("attitude_angle", 0.05, brake)
    wd.cancel()
    await asyncio.sleep(0.15)
    assert calls == []


async def test_brake_exception_is_contained():
    async def brake():
        raise RuntimeError("boom")

    wd = OffboardWatchdog()
    wd.note_setpoint("velocity_ned", 0.05, brake)
    await asyncio.sleep(0.15)  # must not blow up the event loop
    assert wd.auto_braked is True
