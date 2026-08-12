"""Mission completion must work on PX4, not only ArduPilot.

The regression (PX4 N=5 scripted validation, 2026-08-12): T4 failed 5/5 with
"mission ended in phase 'running'". ArduPilot missions self-terminate with a
land+disarm, so completion was detected purely as "was airborne, now disarmed
on the ground". PX4 instead loiters (HOLD) armed at the final waypoint forever,
so that signal never arrived and every T4 trial ran to the 908 s timeout - and
the still-running mission then rejected the next trial's upload.

Two-part fix, both covered here:
  1. ``_mission_items_done`` recognises "all items flown" from mission_progress
     (current >= total) as well as ``is_mission_finished()``, so it works even
     when the firmware's is_mission_finished stream is unavailable/raises.
  2. On items-done the runner commands the descent itself (see runner.monitor);
     that is exercised end-to-end by the SITL integration suite.
"""

import types

from droneserver.missions.runner import MissionRunner


class _MissionRaw:
    def __init__(self, finished=None):
        # finished: True | False | "raises" (firmware without the stream)
        self._finished = finished

    async def is_mission_finished(self):
        if self._finished == "raises":
            raise RuntimeError("is_mission_finished not available on this firmware")
        return bool(self._finished)


def _drone(finished):
    return types.SimpleNamespace(mission_raw=_MissionRaw(finished))


def _record(current, total, flight_mode=""):
    return types.SimpleNamespace(current_item=current, total_items=total, last_flight_mode=flight_mode)


async def test_is_mission_finished_true_is_enough():
    r = MissionRunner()
    assert await r._mission_items_done(_drone(True), _record(0, 0)) is True


async def test_progress_reaching_total_is_enough_when_stream_raises():
    """ArduPilot case: is_mission_finished unavailable, but every item was flown."""
    r = MissionRunner()
    assert await r._mission_items_done(_drone("raises"), _record(4, 4)) is True


async def test_px4_hold_mode_is_the_completion_signal():
    """PX4: progress stream silent, is_mission_finished unavailable - HOLD fires."""
    r = MissionRunner()
    assert await r._mission_items_done(_drone("raises"), _record(0, 0, flight_mode="HOLD")) is True


async def test_not_done_when_items_remain():
    r = MissionRunner()
    assert await r._mission_items_done(_drone(False), _record(2, 4, flight_mode="MISSION")) is False


async def test_unknown_progress_and_active_mode_is_not_done():
    """total == 0 and still flying the mission - never declare done from it."""
    r = MissionRunner()
    assert await r._mission_items_done(_drone(False), _record(0, 0, flight_mode="MISSION")) is False


def test_mission_complete_action_default_is_a_descent():
    from droneserver.missions.config import MissionSettings

    assert MissionSettings().mission_complete_action in ("rtl", "land")
