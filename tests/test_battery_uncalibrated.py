"""FIX 3: an uncalibrated battery must not trigger a false low-battery alarm.

When the autopilot reports no calibrated remaining percentage, get_battery used
to invent a percentage from 4S LiPo voltage thresholds. On a healthy 3S pack a
FULL 12.6 V read as roughly 10%, which raised "LOW BATTERY - Land soon!" and
made models responsibly abort good flights. A missing calibration is not
evidence of a low battery, so no low-battery warning may come from it; the
status is reported as UNKNOWN with an explicitly uncertain voltage estimate.
Genuine low-battery warnings from a valid calibrated reading are preserved.
"""

from __future__ import annotations

import asyncio

from droneserver.mavlink.connection import MAVLinkConnector
from droneserver.tools.telemetry import get_battery


class _FakeBattery:
    def __init__(self, voltage_v: float, remaining_percent: float) -> None:
        self.voltage_v = voltage_v
        self.remaining_percent = remaining_percent


class _FakeTelemetry:
    def __init__(self, battery: _FakeBattery) -> None:
        self._battery = battery

    async def battery(self):
        yield self._battery


class _FakeDrone:
    def __init__(self, battery: _FakeBattery) -> None:
        self.telemetry = _FakeTelemetry(battery)


class _FakeRequestContext:
    def __init__(self, connector: MAVLinkConnector) -> None:
        self.lifespan_context = connector


class _FakeCtx:
    def __init__(self, connector: MAVLinkConnector) -> None:
        self.request_context = _FakeRequestContext(connector)


def _battery_reading(voltage_v: float, remaining_percent: float) -> dict:
    connector = MAVLinkConnector(drone=_FakeDrone(_FakeBattery(voltage_v, remaining_percent)))
    connector.connection_ready.set()
    result = asyncio.run(get_battery.__wrapped__(ctx=_FakeCtx(connector)))
    assert result["status"] == "success", result
    return result["battery"]


def test_uncalibrated_full_3s_pack_raises_no_low_battery_warning():
    # 12.6 V on a 3S pack is FULL; remaining_percent 0.0 means uncalibrated.
    battery = _battery_reading(voltage_v=12.6, remaining_percent=0.0)
    assert "warning" not in battery, battery
    warning_text = str(battery).lower()
    assert "low battery" not in warning_text
    assert "land soon" not in warning_text


def test_uncalibrated_reading_reports_status_as_unknown_not_a_number():
    battery = _battery_reading(voltage_v=12.6, remaining_percent=0.0)
    assert battery["remaining_percent"] is None
    assert "unknown" in battery["calibration_status"].lower()
    # a rough voltage state is offered, explicitly labelled uncertain
    assert "UNCERTAIN" in battery["voltage_state_estimate"]
    assert "full" in battery["voltage_state_estimate"].lower()


def test_genuine_low_calibrated_reading_still_warns():
    # 15% remaining, calibrated: the real alarm must still fire.
    battery = _battery_reading(voltage_v=14.0, remaining_percent=0.15)
    assert "LOW BATTERY" in battery["warning"]
    assert battery["remaining_percent"] == 15.0


def test_healthy_calibrated_reading_has_no_warning():
    battery = _battery_reading(voltage_v=16.4, remaining_percent=0.85)
    assert "warning" not in battery
    assert battery["remaining_percent"] == 85.0


def test_mildly_low_calibrated_reading_gets_the_soft_advisory():
    battery = _battery_reading(voltage_v=14.5, remaining_percent=0.25)
    assert battery["warning"] == "Battery getting low - consider landing"
