"""Mission completion must be earned by evidence, on both firmwares.

The regression this file now pins (PX4 N=5 scored campaign, 2026-08-12/13;
diagnosed 2026-08-19 against llmuavpx4 and the captured run bundles): in 33 of
PX4's 44 T4 failures the managed-mission executor announced "mission items
complete" about seven seconds after the start, with progress 0%, current_item
0 of 6, and the aircraft still hovering over its launch point
(``max_distance_from_home_m: 0.6``). It then commanded RTL, the vehicle landed
and disarmed, and the mission was reported COMPLETED. 28 of the 38 models
handed that report closed with "MISSION COMPLETE".

Two independent defects produced it, and both are covered here:

1. ``mission_raw.start_mission()`` returned success for a start PX4 refused.
   PX4 ACKs the DO_SET_MODE as ACCEPTED and *then* denies the transition with
   ``STATUSTEXT`` severity CRITICAL "Switching to Mission is currently not
   available"; MavSDK reads the first ACK. The runner now requires positive
   evidence - MISSION flight mode, or real progress - before a mission counts
   as running at all.
2. ``_mission_items_done`` read ``flight_mode == HOLD`` as "PX4 finished its
   mission". HOLD is also where PX4 sits after an ordinary takeoff, i.e. it is
   true at item 0. Completion is now gated on evidence that the mission
   actually ran and actually progressed.

The old third signal, ``mission_raw.is_mission_finished()``, is gone: it does
not exist on the MissionRaw plugin in MavSDK 3.0.1 (verified - it is only on
the ``mission`` plugin), so every call raised AttributeError inside a
``suppress()`` and it was never a signal on either firmware.
"""

import types

import pytest

from droneserver.missions.config import MissionSettings
from droneserver.missions.runner import MissionRunner, _mission_items
from droneserver.missions.state import MissionRecord

S = MissionSettings()


def record(
    *,
    confirmed=True,
    baseline=0,
    reached=0,
    total=6,
    mode="MISSION",
    distance=0.0,
) -> MissionRecord:
    r = MissionRecord(mission_id="m_test", total_items=total)
    r.mission_mode_confirmed = confirmed
    r.baseline_item = baseline
    r.items_reached = reached
    r.last_flight_mode = mode
    r.max_distance_from_start_m = distance
    return r


# --------------------------------------------------------------- the regression


def test_px4_hold_at_item_zero_is_NOT_complete():
    """The exact captured failure: HOLD, no progress, aircraft over launch.

    PX4 v1.16.2 after a takeoff sits in HOLD, and it sits in HOLD after a
    refused mission start too. current=1 is not progress either: PX4 reports
    current=1 (the takeoff item) from the moment the mission is uploaded.
    """
    r = record(confirmed=False, baseline=1, reached=1, mode="HOLD", distance=0.6)
    assert MissionRunner()._mission_items_done(r, S) is False


def test_hold_without_mission_mode_is_never_complete():
    """Even with distance flown: if the autopilot never took the mission, no."""
    r = record(confirmed=False, baseline=0, reached=0, mode="HOLD", distance=500.0)
    assert MissionRunner()._mission_items_done(r, S) is False


def test_hold_after_real_progress_IS_complete():
    """PX4's genuine "Mission finished, loitering" -> HOLD, after it flew."""
    r = record(confirmed=True, baseline=0, reached=2, total=6, mode="HOLD", distance=61.0)
    assert MissionRunner()._mission_items_done(r, S) is True


def test_all_items_reached_is_complete():
    """ArduCopter's signal: measured "reached item 6/6" on a healthy T4."""
    r = record(confirmed=True, baseline=0, reached=6, total=6, mode="MISSION")
    assert MissionRunner()._mission_items_done(r, S) is True


def test_still_flying_the_mission_is_not_complete():
    r = record(confirmed=True, baseline=0, reached=3, total=6, mode="MISSION", distance=40.0)
    assert MissionRunner()._mission_items_done(r, S) is False


def test_distance_alone_counts_as_progress_when_the_stream_is_silent():
    """PX4's progress stream only speaks on waypoint transitions; a vehicle
    that flew 60 m has demonstrably progressed whether or not it said so."""
    r = record(confirmed=True, baseline=1, reached=1, total=6, mode="HOLD", distance=S.progress_distance_m + 1)
    assert MissionRunner()._mission_items_done(r, S) is True


def test_drift_is_not_progress():
    """0.6 m is what the failed trials actually moved. It must not count."""
    r = record(confirmed=True, baseline=1, reached=1, total=6, mode="HOLD", distance=0.6)
    assert MissionRunner()._mission_items_done(r, S) is False


# ------------------------------------------------------------- progress helper


@pytest.mark.parametrize(
    "baseline,reached,distance,expected",
    [
        (0, 0, 0.0, False),  # nothing happened
        (1, 1, 0.6, False),  # the captured failure
        (1, 2, 0.0, True),  # one real item past the start
        (0, 0, 60.0, True),  # flew a leg, silent progress stream
        (0, 0, 14.9, False),  # below the movement threshold
    ],
)
def test_progressed(baseline, reached, distance, expected):
    r = record(baseline=baseline, reached=reached, distance=distance)
    assert r.progressed(S.progress_distance_m) is expected


def test_progress_evidence_is_reported():
    """The client is given what the server SAW, not just a verdict."""
    ev = record(confirmed=False, baseline=1, reached=1, distance=0.63).progress_evidence()
    assert ev == {
        "mission_mode_confirmed": False,
        "baseline_item": 1,
        "items_reached": 1,
        "total_items": 6,
        "max_distance_from_start_m": 0.6,
    }


# --------------------------------------------------------- start confirmation


class _Telemetry:
    def __init__(self, modes, lat=0.0, lon=0.0):
        self._modes = list(modes)
        self.lat, self.lon = lat, lon

    async def flight_mode(self):
        yield self._modes.pop(0) if len(self._modes) > 1 else self._modes[0]

    async def position(self):
        yield types.SimpleNamespace(
            latitude_deg=self.lat,
            longitude_deg=self.lon,
            relative_altitude_m=20.0,
            absolute_altitude_m=120.0,
        )

    async def battery(self):
        yield types.SimpleNamespace(voltage_v=12.4, remaining_percent=90.0)

    async def armed(self):
        yield True


class _MissionRaw:
    def __init__(self, current=1, total=6):
        self.current, self.total = current, total

    async def mission_progress(self):
        yield types.SimpleNamespace(current=self.current, total=self.total)


def _drone(modes, lat=0.0, lon=0.0, current=1):
    return types.SimpleNamespace(telemetry=_Telemetry(modes, lat, lon), mission_raw=_MissionRaw(current))


async def test_start_is_not_confirmed_when_px4_stays_in_hold():
    """The refused start: MavSDK said success, PX4 stayed in HOLD."""
    runner = MissionRunner()
    runner.record = record(confirmed=False, baseline=1, reached=1, mode="")
    s = MissionSettings(start_confirm_timeout_s=1.0, poll_interval_s=0.05)
    assert await runner._confirm_running(_drone(["HOLD"]), s) is False
    assert runner.record.mission_mode_confirmed is False


async def test_start_is_confirmed_by_mission_mode():
    """Both firmwares: MavSDK maps ArduCopter AUTO and PX4 AUTO.MISSION here."""
    runner = MissionRunner()
    runner.record = record(confirmed=False, baseline=0, reached=0, mode="")
    s = MissionSettings(start_confirm_timeout_s=2.0, poll_interval_s=0.05)
    assert await runner._confirm_running(_drone(["MISSION"]), s) is True
    assert runner.record.mission_mode_confirmed is True


async def test_start_is_confirmed_by_progress_when_the_mode_name_is_unknown():
    runner = MissionRunner()
    runner.record = record(confirmed=False, baseline=1, reached=1, mode="")
    s = MissionSettings(start_confirm_timeout_s=2.0, poll_interval_s=0.05)
    assert await runner._confirm_running(_drone(["SOMETHING_ELSE"], current=3), s) is True


# ------------------------------------------------------------- item layouts


def _cmds(items):
    return [(i.seq, i.command, i.frame) for i in items]


def test_ardupilot_layout_keeps_the_seq_zero_home_placeholder():
    from droneserver.mission_plans import build_raw_items

    items = _mission_items(build_raw_items, [{"latitude_deg": 1.0, "longitude_deg": 2.0}], 20.0, True)
    assert _cmds(items)[0] == (0, 16, 5), "seq 0 must stay the HOME placeholder for ArduPilot"
    assert _cmds(items)[1] == (1, 22, 3), "the takeoff is the first real item"
    assert items[0].current == 0 and items[1].current == 1
    assert items[-1].command == 20  # RTL


def test_px4_fallback_layout_drops_the_placeholder_and_keeps_current_valid():
    """PX4 refuses the placeholder layout in flight (measured, llmuavpx4).

    MavSDK's transfer validation also rejects a mission with no current=1 item
    ("CURRENT_INVALID"), so dropping seq 0 has to move the flag, not delete it.
    """
    from droneserver.mission_plans import build_raw_items

    items = _mission_items(
        build_raw_items, [{"latitude_deg": 1.0, "longitude_deg": 2.0}], 20.0, True, home_placeholder=False
    )
    assert _cmds(items)[0] == (0, 22, 3), "the takeoff becomes seq 0"
    assert items[0].current == 1
    assert sum(i.current for i in items) == 1
    assert [i.seq for i in items] == list(range(len(items)))


def test_the_two_layouts_differ_by_exactly_the_placeholder():
    from droneserver.mission_plans import build_raw_items

    wps = [{"latitude_deg": 1.0, "longitude_deg": 2.0}, {"latitude_deg": 1.1, "longitude_deg": 2.1}]
    ap = _mission_items(build_raw_items, wps, 20.0, True)
    px4 = _mission_items(build_raw_items, wps, 20.0, True, home_placeholder=False)
    assert len(ap) - len(px4) == 1
    assert [i.command for i in ap][1:] == [i.command for i in px4]


def test_mission_complete_action_default_is_a_descent():
    assert MissionSettings().mission_complete_action in ("rtl", "land")


def test_fail_closed_is_bounded():
    """A mission that will never progress must come down, not loiter armed."""
    assert MissionSettings().no_progress_timeout_s > 0
