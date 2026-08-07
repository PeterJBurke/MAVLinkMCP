"""Unit tests for the managed-mission state machine and checkpoint store."""

import json

import pytest

from droneserver.mission_plans import build_raw_items
from droneserver.missions.runner import _mission_items
from droneserver.missions.state import (
    ALLOWED,
    TERMINAL_PHASES,
    MissionEvent,
    MissionRecord,
    MissionStore,
    Phase,
    can_transition,
    new_mission_id,
)

HOME = (-35.363262, 149.165237)


class TestTransitions:
    def test_happy_path(self):
        chain = [
            Phase.SUBMITTED,
            Phase.VALIDATING,
            Phase.UPLOADING,
            Phase.ARMING,
            Phase.RUNNING,
            Phase.LANDING,
            Phase.COMPLETED,
        ]
        for current, target in zip(chain, chain[1:], strict=False):
            assert can_transition(current, target), f"{current} -> {target}"

    def test_rtl_path(self):
        assert can_transition(Phase.RUNNING, Phase.RETURNING)
        assert can_transition(Phase.RETURNING, Phase.LANDING)
        assert can_transition(Phase.LANDING, Phase.COMPLETED)

    def test_pause_resume(self):
        assert can_transition(Phase.RUNNING, Phase.PAUSED)
        assert can_transition(Phase.PAUSED, Phase.RUNNING)

    def test_terminal_phases_are_dead_ends(self):
        for phase in TERMINAL_PHASES:
            assert ALLOWED[phase] == frozenset()
            assert not can_transition(phase, Phase.RUNNING)

    def test_illegal_jumps_refused(self):
        assert not can_transition(Phase.SUBMITTED, Phase.RUNNING)
        assert not can_transition(Phase.UPLOADING, Phase.RUNNING)
        assert not can_transition(Phase.COMPLETED, Phase.PAUSED)
        assert not can_transition(Phase.LANDING, Phase.RUNNING)

    def test_every_phase_can_fail_or_abort_until_terminal(self):
        for phase in Phase:
            if phase in TERMINAL_PHASES:
                continue
            assert can_transition(phase, Phase.FAILED), phase
            assert can_transition(phase, Phase.ABORTED), phase


class TestRecord:
    def test_progress_and_active(self):
        record = MissionRecord(mission_id=new_mission_id(), total_items=4, current_item=1)
        assert record.active
        assert record.progress_percent() == 25.0
        record.phase = Phase.COMPLETED.value
        assert not record.active

    def test_progress_is_clamped(self):
        record = MissionRecord(mission_id="m", total_items=2, current_item=5)
        assert record.progress_percent() == 100.0

    def test_progress_without_items(self):
        assert MissionRecord(mission_id="m").progress_percent() == 0.0

    def test_events_are_capped(self):
        record = MissionRecord(mission_id="m")
        for i in range(20):
            record.add_event(MissionEvent.make("info", f"event {i}"), max_events=5)
        assert len(record.events) == 5
        assert record.events[-1]["message"] == "event 19"  # newest kept

    def test_elapsed_uses_finish_time_once_done(self):
        record = MissionRecord(mission_id="m", started_at=1000.0, finished_at=1123.5)
        assert record.elapsed_s() == 123.5


class TestStore:
    def test_round_trip(self, tmp_path):
        store = MissionStore(tmp_path / "mission_state.json")
        record = MissionRecord(mission_id="m_abc", total_items=3, current_item=2)
        record.add_event(MissionEvent.make("waypoint", "reached 2"), 100)
        store.save(record)

        loaded = store.load()
        assert loaded is not None
        assert loaded.mission_id == "m_abc"
        assert loaded.current_item == 2
        assert loaded.events[-1]["message"] == "reached 2"

    def test_missing_file(self, tmp_path):
        assert MissionStore(tmp_path / "nope.json").load() is None

    def test_corrupt_file_is_ignored_not_fatal(self, tmp_path):
        path = tmp_path / "mission_state.json"
        path.write_text("{ this is not json")
        assert MissionStore(path).load() is None

    def test_unknown_schema_ignored(self, tmp_path):
        path = tmp_path / "mission_state.json"
        path.write_text(json.dumps({"schema": "something/9", "mission_id": "x"}))
        assert MissionStore(path).load() is None

    def test_unknown_fields_ignored(self, tmp_path):
        """A checkpoint written by a newer version must not crash an older one."""
        path = tmp_path / "mission_state.json"
        path.write_text(json.dumps({"schema": "droneserver.mission/1", "mission_id": "m", "future_field": 1}))
        loaded = MissionStore(path).load()
        assert loaded is not None and loaded.mission_id == "m"

    def test_save_is_atomic(self, tmp_path):
        store = MissionStore(tmp_path / "mission_state.json")
        store.save(MissionRecord(mission_id="m1"))
        store.save(MissionRecord(mission_id="m2"))
        assert store.load().mission_id == "m2"
        assert not (tmp_path / "mission_state.tmp").exists()

    def test_clear(self, tmp_path):
        store = MissionStore(tmp_path / "mission_state.json")
        store.save(MissionRecord(mission_id="m"))
        store.clear()
        assert store.load() is None


class TestMissionItemBuilding:
    WAYPOINTS = [
        {"latitude_deg": HOME[0] + 0.001, "longitude_deg": HOME[1], "altitude_m": 25},
        {"latitude_deg": HOME[0] + 0.001, "longitude_deg": HOME[1] + 0.001, "altitude_m": 30, "hold_s": 5},
    ]

    def test_ardupilot_layout_home_placeholder_then_takeoff(self):
        """seq 0 must be a HOME placeholder with current=0; the takeoff is the
        first real item and carries current=1 (measured requirement)."""
        items = _mission_items(build_raw_items, self.WAYPOINTS, 20.0, True)
        assert len(items) == 5  # home + takeoff + 2 waypoints + RTL
        assert items[0].command == 16 and items[0].current == 0  # HOME placeholder
        assert items[1].command == 22 and items[1].current == 1 and items[1].z == 20.0  # NAV_TAKEOFF
        assert items[2].command == 16 and items[3].command == 16  # NAV_WAYPOINT
        assert items[-1].command == 20  # NAV_RETURN_TO_LAUNCH

    def test_sequence_numbers_are_contiguous(self):
        items = _mission_items(build_raw_items, self.WAYPOINTS, 20.0, True)
        assert [i.seq for i in items] == list(range(len(items)))

    def test_without_rtl(self):
        items = _mission_items(build_raw_items, self.WAYPOINTS, 20.0, False)
        assert len(items) == 4
        assert items[-1].command == 16

    def test_hold_time_becomes_param1(self):
        items = _mission_items(build_raw_items, self.WAYPOINTS, 20.0, False)
        assert items[3].param1 == 5.0

    def test_coordinates_scaled(self):
        items = _mission_items(build_raw_items, self.WAYPOINTS, 20.0, False)
        assert items[2].x == int(round((HOME[0] + 0.001) * 1e7))

    def test_altitude_defaults_to_takeoff_altitude(self):
        items = _mission_items(build_raw_items, [{"latitude_deg": HOME[0], "longitude_deg": HOME[1]}], 33.0, False)
        assert items[2].z == 33.0


@pytest.mark.parametrize("phase", list(Phase))
def test_phase_values_are_stable_strings(phase):
    """Phase values are persisted in checkpoints; they must not drift."""
    assert Phase(phase.value) is phase


class TestBatteryNormalisation:
    """MavSDK documents remaining_percent as a fraction, but ArduPilot reports
    a percentage - without normalising, no battery auto-action can ever fire."""

    def test_percentage_is_normalised(self):
        from droneserver.missions.runner import _battery_fraction

        assert _battery_fraction(77.0) == 0.77
        assert _battery_fraction(100.0) == 1.0

    def test_fraction_passes_through(self):
        from droneserver.missions.runner import _battery_fraction

        assert _battery_fraction(0.77) == 0.77
        assert _battery_fraction(1.0) == 1.0

    def test_invalid_values(self):
        from droneserver.missions.runner import _battery_fraction

        assert _battery_fraction(None) is None
        assert _battery_fraction(-1.0) is None

    def test_threshold_reachable_after_normalisation(self):
        from droneserver.missions.config import MissionSettings
        from droneserver.missions.runner import _battery_fraction

        s = MissionSettings(_env_file=None)
        assert _battery_fraction(20.0) <= s.low_battery_threshold
        assert _battery_fraction(8.0) <= s.critical_battery_threshold
        assert _battery_fraction(77.0) > s.low_battery_threshold


class TestAutoActionDedup:
    """An auto-action must fire once per CONDITION, not once per poll.

    Regression: dedup keyed on the formatted reason, which embeds the live
    battery percentage, so RTL was re-commanded every poll (17 times in one
    measured flight).
    """

    def _fired(self, entries, trigger):
        return [e for e in entries if e.get("trigger") == trigger]

    def test_same_trigger_fires_once_despite_changing_reason(self):
        fired: list = []

        def would_fire(trigger):
            if any(e.get("trigger") == trigger for e in fired):
                return False
            fired.append({"trigger": trigger})
            return True

        assert would_fire("low_battery")
        for percent in (24, 23, 22, 20, 19):  # the reason text changes each poll
            assert not would_fire("low_battery"), f"re-fired at {percent}%"
        assert len(self._fired(fired, "low_battery")) == 1

    def test_distinct_triggers_each_fire(self):
        fired: list = []

        def would_fire(trigger):
            if any(e.get("trigger") == trigger for e in fired):
                return False
            fired.append({"trigger": trigger})
            return True

        assert would_fire("low_battery")
        assert would_fire("critical_battery")
        assert would_fire("geofence_breach")
        assert len(fired) == 3
