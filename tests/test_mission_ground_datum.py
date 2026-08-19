"""FIX 11: the managed mission must not read the ground off a moving datum.

Third and last consumer of the defect found on 2026-08-19 (after the scorer,
FIX 8b/33de5ec, and monitor_flight, FIX 10/680ee81). ArduPilot re-zeroes
``relative_altitude_m`` wherever the aircraft last ARMED, so a mission flown
after one that armed somewhere else carries a constant offset in every reading
- +4.1 m across eight independent fresh SITL lanes.

``MissionRunner.monitor`` read that number twice:

    altitude = (record.last_position or {}).get("relative_altitude_m") or 0.0
    if record.last_armed and altitude > 2.0:          # "we are airborne"
    if was_airborne and not record.last_armed and altitude <= 2.0:   # "done"

With +4.1 m the first fires on a parked aircraft and the second never fires at
all: the flight ends, the vehicle disarms on its pad, and the mission sits in
RUNNING until something else times it out.

``_descend_and_fail`` read it a third time, in the more dangerous direction:
``> 2.0`` meant "still up there, bring it down", so an offset of the opposite
sign made a genuinely airborne aircraft look landed and the descent that should
have followed a failed start was skipped - an aircraft left loitering armed
with the runner walking away.

All three now ask the autopilot (landed_state / in_air, with the vertical-rate
veto from droneserver.telemetry.ground) and fall back to a height measured
against the elevation this mission started from - never the moving one.
"""

from __future__ import annotations

import asyncio
import types

import pytest

from droneserver.missions.config import MissionSettings
from droneserver.missions.runner import GROUND_HEIGHT_M, MissionRunner
from droneserver.missions.state import MissionRecord, Phase

#: The launch pad, and what the aircraft standing on it reports after a
#: previous flight re-armed 4.1 m lower down.
LAUNCH_AMSL = 41.3
DATUM_OFFSET_M = 4.1


def _record(**kw) -> MissionRecord:
    r = MissionRecord(mission_id="m_test", total_items=6)
    r.launch_amsl_m = LAUNCH_AMSL
    for key, value in kw.items():
        setattr(r, key, value)
    return r


def _position(relative, absolute, lat=0.0, lon=0.0):
    return {
        "latitude_deg": lat,
        "longitude_deg": lon,
        "relative_altitude_m": relative,
        "absolute_altitude_m": absolute,
    }


#: Parked on the launch pad, but the autopilot's relative reading says +4.1 m.
PARKED_WITH_OFFSET = _position(DATUM_OFFSET_M, LAUNCH_AMSL)
#: Genuinely 40 m up over the pad.
FLYING = _position(40.0 + DATUM_OFFSET_M, LAUNCH_AMSL + 40.0)


# ------------------------------------------------------------------ height


def test_height_is_measured_against_the_missions_own_launch_elevation():
    runner = MissionRunner()
    assert runner._height_above_launch(_record(last_position=PARKED_WITH_OFFSET)) == pytest.approx(0.0)
    assert runner._height_above_launch(_record(last_position=FLYING)) == pytest.approx(40.0)


def test_height_falls_back_to_the_relative_reading_without_a_launch_elevation():
    """An older checkpoint, or a mission whose first position read failed."""
    runner = MissionRunner()
    r = _record(last_position=PARKED_WITH_OFFSET, launch_amsl_m=None)
    assert runner._height_above_launch(r) == DATUM_OFFSET_M


def test_height_is_none_when_there_is_no_position_at_all():
    assert MissionRunner()._height_above_launch(_record()) is None


# ------------------------------------------------------------ ground state


def test_the_autopilot_outranks_the_offset():
    """THE BUG, in one assertion: +4.1 m on the pad is still on the pad."""
    runner = MissionRunner()
    r = _record(last_position=PARKED_WITH_OFFSET, last_landed_state="ON_GROUND", last_in_air=False)
    assert runner._ground_state(r) is True


def test_a_flying_aircraft_reading_low_is_still_flying():
    """The inverse offset: 1.2 m on the dial, IN_AIR from the autopilot."""
    runner = MissionRunner()
    r = _record(last_position=_position(1.2, LAUNCH_AMSL + 30.0), last_landed_state="IN_AIR", last_in_air=True)
    assert runner._ground_state(r) is False


@pytest.mark.parametrize("state", ["IN_AIR", "TAKING_OFF", "LANDING"])
def test_every_airborne_landed_state_counts_as_airborne(state):
    runner = MissionRunner()
    assert runner._ground_state(_record(last_landed_state=state)) is False


def test_in_air_answers_when_landed_state_does_not():
    runner = MissionRunner()
    assert runner._ground_state(_record(last_landed_state=None, last_in_air=False)) is True
    assert runner._ground_state(_record(last_landed_state="UNKNOWN", last_in_air=True)) is False


def test_the_height_fallback_is_used_only_when_the_autopilot_is_silent():
    runner = MissionRunner()
    r = _record(last_position=PARKED_WITH_OFFSET)
    assert r.last_landed_state is None and r.last_in_air is None
    assert runner._ground_state(r) is True, "0.0 m above the launch elevation"
    assert runner._ground_state(_record(last_position=FLYING)) is False


def test_nothing_known_is_not_an_answer():
    """The third answer exists so neither caller may guess."""
    assert MissionRunner()._ground_state(_record()) is None


def test_on_ground_while_still_falling_is_not_believed():
    """A landing in progress, not a landing finished."""
    runner = MissionRunner()
    r = _record(last_landed_state="ON_GROUND", last_in_air=False, last_vertical_speed_m_s=-3.0)
    assert runner._ground_state(r) is False


def test_settling_noise_is_not_a_descent():
    runner = MissionRunner()
    r = _record(last_landed_state="ON_GROUND", last_in_air=False, last_vertical_speed_m_s=-0.2)
    assert runner._ground_state(r) is True


def test_the_ground_height_fallback_threshold_is_the_documented_one():
    assert GROUND_HEIGHT_M == 2.0


# ------------------------------------------------------- the monitor loop


class _LandedState:
    def __init__(self, name):
        self.name = name

    def __str__(self):
        return f"LandedState.{self.name}"


class _Telemetry:
    """A scripted flight: each poll advances one step, the last one repeats.

    position() is the first read of every ``_sample``, so it is where the script
    advances; every other read in the same sample sees the step it selected, so
    one poll describes one instant.
    """

    def __init__(self, steps):
        self.steps = list(steps)
        self.polls = 0
        self.now = self.steps[0]

    async def position(self):
        self.now = self.steps[min(self.polls, len(self.steps) - 1)]
        self.polls += 1
        step = self.now
        yield types.SimpleNamespace(
            latitude_deg=step.get("lat", 0.0),
            longitude_deg=step.get("lon", 0.0),
            relative_altitude_m=step["rel"],
            absolute_altitude_m=step["abs"],
        )

    async def battery(self):
        yield types.SimpleNamespace(voltage_v=12.4, remaining_percent=90.0)

    async def flight_mode(self):
        yield self.now.get("mode", "MISSION")

    async def armed(self):
        yield self.now["armed"]

    async def landed_state(self):
        state = self.now.get("landed_state")
        if state is None:
            raise RuntimeError("this firmware does not publish landed_state")
        yield _LandedState(state)

    async def in_air(self):
        in_air = self.now.get("in_air")
        if in_air is None:
            raise RuntimeError("this firmware does not publish in_air")
        yield in_air

    async def velocity_ned(self):
        if self.now.get("no_vz"):
            raise RuntimeError("this firmware does not publish velocity_ned")
        yield types.SimpleNamespace(north_m_s=0.0, east_m_s=0.0, down_m_s=-self.now.get("vz", 0.0))


class _MissionRaw:
    def __init__(self, current=3, total=6):
        self.current, self.total = current, total
        self.paused = 0

    async def mission_progress(self):
        yield types.SimpleNamespace(current=self.current, total=self.total)

    async def pause_mission(self):
        self.paused += 1


class _Action:
    def __init__(self):
        self.rtl = 0
        self.landings = 0

    async def return_to_launch(self):
        self.rtl += 1

    async def land(self):
        self.landings += 1


def _drone(steps, current=3):
    return types.SimpleNamespace(telemetry=_Telemetry(steps), mission_raw=_MissionRaw(current), action=_Action())


def _settings(tmp_path, **kw):
    defaults = {
        "poll_interval_s": 0.001,
        "state_path": str(tmp_path / "mission_state.json"),
        "auto_actions_enabled": False,
        "no_progress_timeout_s": 0.0,
    }
    return MissionSettings(**{**defaults, **kw})


async def _abort_after(runner, telemetry, polls: int) -> None:
    """Stop a monitor loop that must NOT end on its own.

    ``monitor`` only returns on a phase change, so a test asserting that a
    mission does not complete needs its own way out. ABORTED is the proof: the
    loop was still running when we stopped it.
    """
    while telemetry.polls < polls:
        await asyncio.sleep(0.001)
    runner.request_abort()


def _running(**kw) -> MissionRecord:
    r = _record(phase=Phase.RUNNING.value, **kw)
    r.mission_mode_confirmed = True
    r.baseline_item = 0
    r.items_reached = 4
    r.max_distance_from_start_m = 400.0
    return r


AIRBORNE = {
    "rel": 40.0 + DATUM_OFFSET_M,
    "abs": LAUNCH_AMSL + 40.0,
    "armed": True,
    "landed_state": "IN_AIR",
    "in_air": True,
    "lat": 0.004,
}
#: Landed and disarmed on the pad - and still reading +4.1 m.
DOWN_WITH_OFFSET = {
    "rel": DATUM_OFFSET_M,
    "abs": LAUNCH_AMSL,
    "armed": False,
    "landed_state": "ON_GROUND",
    "in_air": False,
    "lat": 0.0,
}


async def test_a_mission_that_flew_and_landed_completes_despite_the_offset(tmp_path):
    """THE BUG. Under the old check this mission never completed at all."""
    runner = MissionRunner()
    runner.record = _running(start_position=_position(0.0, LAUNCH_AMSL))
    drone = _drone([AIRBORNE, AIRBORNE, DOWN_WITH_OFFSET])

    await runner.monitor(drone, _settings(tmp_path))

    assert runner.record.phase_enum is Phase.COMPLETED
    assert runner.record.error is None
    assert runner.record.last_position["relative_altitude_m"] == pytest.approx(DATUM_OFFSET_M)
    assert runner.record.last_position["height_above_launch_m"] == pytest.approx(0.0)


async def test_the_completion_still_needs_a_flight_behind_it(tmp_path):
    """Disarmed on the pad from the first poll: nothing flew, nothing completes.

    With the old check the +4.1 m offset also latched was_airborne on a parked
    ARMED aircraft, which is the same class of defect one step earlier.
    """
    runner = MissionRunner()
    runner.record = _running()
    parked_armed = dict(DOWN_WITH_OFFSET, armed=True)
    drone = _drone([parked_armed, parked_armed, dict(DOWN_WITH_OFFSET)])

    task = asyncio.get_running_loop().create_task(_abort_after(runner, drone.telemetry, 8))
    await runner.monitor(drone, _settings(tmp_path))
    task.cancel()

    assert runner.record.phase_enum is Phase.ABORTED, "never completed: it never flew"


async def test_a_mission_that_landed_without_flying_still_fails(tmp_path):
    """The FIX from e5eb448 is not weakened: no progress means FAILED."""
    runner = MissionRunner()
    runner.record = _record(phase=Phase.RUNNING.value)
    runner.record.mission_mode_confirmed = True
    runner.record.items_reached = 0
    runner.record.max_distance_from_start_m = 0.6
    drone = _drone([AIRBORNE, DOWN_WITH_OFFSET], current=0)

    await runner.monitor(drone, _settings(tmp_path))

    assert runner.record.phase_enum is Phase.FAILED
    assert "without flying the mission" in (runner.record.error or "")


async def test_a_still_airborne_aircraft_is_never_completed(tmp_path):
    """Disarm is not the whole signal: it must also be down."""
    runner = MissionRunner()
    runner.record = _running()
    # An implausible pair on purpose - disarmed, but the autopilot says IN_AIR.
    contradiction = dict(AIRBORNE, armed=False)
    drone = _drone([AIRBORNE, contradiction])

    task = asyncio.get_running_loop().create_task(_abort_after(runner, drone.telemetry, 6))
    await runner.monitor(drone, _settings(tmp_path))
    task.cancel()

    assert runner.record.phase_enum is Phase.ABORTED, "not COMPLETED while the autopilot says IN_AIR"


async def test_a_firmware_that_only_publishes_position_still_completes(tmp_path):
    """No landed_state, no in_air: the height fallback carries the mission."""
    runner = MissionRunner()
    runner.record = _running()
    mute_air = {"rel": 40.0, "abs": LAUNCH_AMSL + 40.0, "armed": True, "lat": 0.004}
    mute_down = {"rel": DATUM_OFFSET_M, "abs": LAUNCH_AMSL, "armed": False, "lat": 0.0}
    drone = _drone([mute_air, mute_air, mute_down])

    await runner.monitor(drone, _settings(tmp_path))

    assert runner.record.phase_enum is Phase.COMPLETED


# --------------------------------------------------------- _descend_and_fail


async def test_a_failed_start_brings_a_flying_aircraft_down(tmp_path):
    """The fail-open direction: it reads 1.2 m, it is 30 m up, it must land."""
    runner = MissionRunner()
    runner.record = _record(
        phase=Phase.RUNNING.value,
        last_position=_position(1.2, LAUNCH_AMSL + 30.0),
        last_landed_state="IN_AIR",
        last_in_air=True,
    )
    drone = _drone([AIRBORNE])

    await runner._descend_and_fail(drone, _settings(tmp_path), "start")

    assert drone.action.rtl == 1, "an aircraft in the air must not be walked away from"
    assert runner.record.phase_enum is Phase.FAILED


async def test_a_failed_start_on_a_parked_aircraft_commands_nothing(tmp_path):
    """+4.1 m on the dial is not a reason to command a descent on the pad."""
    runner = MissionRunner()
    runner.record = _record(
        phase=Phase.RUNNING.value,
        last_position=PARKED_WITH_OFFSET,
        last_landed_state="ON_GROUND",
        last_in_air=False,
    )
    drone = _drone([DOWN_WITH_OFFSET])

    await runner._descend_and_fail(drone, _settings(tmp_path), "start")

    assert drone.action.rtl == 0
    assert runner.record.phase_enum is Phase.FAILED


async def test_an_unknown_state_is_brought_down_not_assumed_safe(tmp_path):
    """Fail-safe: nothing readable means the aircraft may still be up there."""
    runner = MissionRunner()
    runner.record = _record(phase=Phase.RUNNING.value)
    silent = {"rel": 0.0, "abs": LAUNCH_AMSL, "armed": True}

    class _Silent(_Telemetry):
        async def position(self):
            self.polls += 1
            raise RuntimeError("no position")
            yield  # pragma: no cover - keeps this an async generator

    drone = _drone([silent])
    drone.telemetry = _Silent([silent])

    await runner._descend_and_fail(drone, _settings(tmp_path), "start")

    assert drone.action.rtl == 1
    assert runner.record.phase_enum is Phase.FAILED


# ------------------------------------------------------------- the sample


async def test_the_launch_datum_is_read_once_while_the_aircraft_is_parked():
    runner = MissionRunner()
    runner.record = _record(launch_amsl_m=None)
    drone = _drone([{"rel": 0.0, "abs": LAUNCH_AMSL, "armed": False}])

    await runner._record_launch_datum(drone)
    assert runner.record.launch_amsl_m == pytest.approx(LAUNCH_AMSL)

    # A second call must not move it, whatever the aircraft reads by then.
    drone2 = _drone([{"rel": 0.0, "abs": 999.0, "armed": True}])
    await runner._record_launch_datum(drone2)
    assert runner.record.launch_amsl_m == pytest.approx(LAUNCH_AMSL)


async def test_a_missing_launch_datum_never_blocks_the_mission():
    class _NoPosition:
        async def position(self):
            raise RuntimeError("no fix")
            yield  # pragma: no cover

    runner = MissionRunner()
    runner.record = _record(launch_amsl_m=None)
    await runner._record_launch_datum(types.SimpleNamespace(telemetry=_NoPosition()))
    assert runner.record.launch_amsl_m is None


async def test_ground_evidence_is_never_stale(tmp_path):
    """A poll the firmware did not answer must not leave the last word standing.

    A stale "ON_GROUND" is the one value that could complete a mission on an
    aircraft that is still in the air.
    """
    runner = MissionRunner()
    runner.record = _record(last_landed_state="ON_GROUND", last_in_air=False, last_vertical_speed_m_s=0.0)
    mute = {"rel": 30.0, "abs": LAUNCH_AMSL + 30.0, "armed": True, "no_vz": True}

    await runner._sample(_drone([mute]), runner.record, _settings(tmp_path))

    assert runner.record.last_landed_state is None
    assert runner.record.last_in_air is None
    assert runner.record.last_vertical_speed_m_s is None
    assert runner._ground_state(runner.record) is False, "the height fallback answers instead"


async def test_the_sample_records_both_heights(tmp_path):
    runner = MissionRunner()
    runner.record = _record()

    await runner._sample(_drone([DOWN_WITH_OFFSET]), runner.record, _settings(tmp_path))

    assert runner.record.last_position["relative_altitude_m"] == pytest.approx(DATUM_OFFSET_M)
    assert runner.record.last_position["height_above_launch_m"] == pytest.approx(0.0)
    assert runner.record.last_landed_state == "ON_GROUND"
    assert runner.record.last_in_air is False


# ---------------------------------------------------------------- geofence


async def test_the_ceiling_is_measured_from_the_launch_elevation(tmp_path, monkeypatch):
    """A ceiling is a height above where it started, not above a moved datum."""
    from droneserver.missions import runner as runner_module

    seen: list[float | None] = []

    def _spy(fence, lat, lon, altitude):
        seen.append(altitude)
        return None

    monkeypatch.setattr(runner_module, "check_position", _spy)

    runner = MissionRunner()
    runner.record = _running()
    drone = _drone([AIRBORNE, AIRBORNE, DOWN_WITH_OFFSET])

    await runner.monitor(drone, _settings(tmp_path, auto_actions_enabled=True, geofence_breach_action="rtl"))

    assert seen, "the fence was never consulted"
    # 40 m above the launch pad, not the 44.1 m the moved datum reports.
    assert seen[0] == pytest.approx(40.0)


# --------------------------------------------------------------- checkpoint


def test_an_old_checkpoint_without_the_new_fields_still_loads():
    """Restart recovery must survive the schema growing."""
    old = {
        "mission_id": "m_old",
        "phase": "running",
        "last_position": {"latitude_deg": 0.0, "longitude_deg": 0.0, "relative_altitude_m": 4.1},
    }
    r = MissionRecord.from_dict(old)
    assert r.launch_amsl_m is None
    assert r.last_landed_state is None
    # And it degrades to exactly the old behaviour: the relative reading.
    assert MissionRunner()._height_above_launch(r) == pytest.approx(4.1)
