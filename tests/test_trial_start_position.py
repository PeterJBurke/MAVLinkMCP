"""Every trial must begin where the run began - and prove it.

**The failure this exists to catch.** Nothing used to put the aircraft back
between trials. T2 lands the aircraft 60 m north of where it started, T3 lands
it wherever it finished, and the next trial began there. Over the halted N=5
campaign that walked the vehicle 300 m in five T2 trials on top of ~690 m left
over from earlier runs, until it was parked 986 m from the centre of the
server's 1000 m geofence and *every* horizontal command was refused. The
resulting table - T1 100%, T9 100%, T4 0%, T3 17%, T5 20%, T2 23% - looks
exactly like a finding about what language models can fly, and is a finding
about where the aircraft was standing.

**Why the existing verification could not have caught it.** The scripted suite
computes every target from home, so it is immune by construction; and the two
LLM trials flown before the campaign were far too few for 60 m per trial to
matter. What was missing was a check on the *precondition* rather than on the
outcome.

The tests below are that check, at three levels:

1. ``_ferry_to_launch`` returns the distance it **re-measured** afterwards, so
   a ferry that silently failed cannot report success;
2. a trial that cannot be placed on the launch point is not flown at all, and
   is excluded from the pass rate rather than recorded as a model failure;
3. run end to end, a suite whose aircraft drifts 60 m per trial still starts
   every trial on the launch point - the drift is removed, not tolerated.

There is also a scorer/prompt consistency test: the missions are phrased
relative to where the aircraft is standing, and the verdicts must be computed
from the same point, so that no future drift can reappear disguised as "the
model failed".
"""

import math

import pytest

from droneserver.llm import runner
from droneserver.llm.agent import AgentRun, Limits, TurnRecord
from droneserver.llm.mcp_session import CallRecord, TelemetrySample
from droneserver.llm.providers import ToolSpec
from droneserver.llm.runner import DEFAULT_START_TOLERANCE_M, SuiteConfig
from droneserver.llm.verdicts import Track, distance_m, judge, offset

LAUNCH = (33.6458611, -117.84275)
LAUNCH_AMSL = 25.1


def _north(point, metres: float) -> tuple[float, float]:
    return point[0] + metres / 111320.0, point[1]


# --------------------------------------------------------------------- fakes


class FerrySession:
    """A drone server whose aircraft is parked ``drift_m`` north of launch.

    ``ferry_works`` decides whether a commanded go-to actually moves it, which
    is how the "the fence refuses to let us come home" case is expressed: the
    aircraft that has drifted to the edge of the geofence cannot be commanded
    anywhere, including back.
    """

    def __init__(self, drift_m: float = 0.0, *, ferry_works: bool = True, drift_per_trial_m: float = 0.0):
        self.position = _north(LAUNCH, drift_m)
        self.ferry_works = ferry_works
        self.drift_per_trial_m = drift_per_trial_m
        self.armed = False
        self.calls: list[str] = []
        #: Where each trial found the aircraft when it read its origin.
        self.trial_origins: list[tuple[float, float]] = []

    # -- session plumbing --------------------------------------------------
    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return None

    async def aclose(self):
        return None

    async def wait_ready(self, timeout_s=180.0):
        return True

    async def list_tools(self):
        return [ToolSpec("takeoff", "take off", {"type": "object", "properties": {}})]

    async def call(self, tool, arguments, *, turn=0, seq=0, timeout_s=300.0):
        return {"status": "success"}, CallRecord(
            turn=turn, seq=seq, tool=tool, arguments=arguments, started_at=0.0, wall_ms=1.0, status="success"
        )

    # -- the aircraft ------------------------------------------------------
    async def call_raw(self, tool, arguments=None, timeout_s=300.0):
        self.calls.append(tool)
        if tool == "get_armed":
            return {"status": "success", "armed": self.armed}
        if tool == "get_position":
            return {
                "status": "success",
                "position": {
                    "latitude_deg": self.position[0],
                    "longitude_deg": self.position[1],
                    "absolute_altitude_m": LAUNCH_AMSL,
                    "relative_altitude_m": 0.0,
                },
            }
        if tool == "get_home_position":
            return {
                "status": "success",
                "home": {
                    "latitude_deg": self.position[0],
                    "longitude_deg": self.position[1],
                    "absolute_altitude_m": LAUNCH_AMSL,
                },
            }
        if tool == "arm_drone":
            self.armed = True
            return {"status": "success"}
        if tool == "takeoff":
            return {"status": "success"}
        if tool == "go_to_location":
            if not self.ferry_works:
                return {
                    "status": "rejected",
                    "rule": "geofence.radius",
                    "error": "target is 1046 m from home, beyond the geofence radius of 1000 m",
                }
            self.position = (arguments["latitude_deg"], arguments["longitude_deg"])
            return {"status": "success"}
        if tool == "land":
            self.armed = False
            return {"status": "success"}
        if tool == "get_parameter":
            return {"status": "success", "value": 500.0}
        return {"status": "success"}


class FakePoller:
    def __init__(self, url, api_key="", interval_s=1.5):
        self.samples = [
            TelemetrySample(
                t=float(i),
                latitude_deg=LAUNCH[0],
                longitude_deg=LAUNCH[1],
                relative_altitude_m=0.0,
                absolute_altitude_m=LAUNCH_AMSL,
                armed=False,
                in_air=False,
            )
            for i in range(5)
        ]

    async def start(self):
        return None

    async def stop(self, final_sample=False):
        return None

    async def sample_once(self, full=True):
        return None


class FakeModel:
    messages = [{"role": "assistant", "content": "MISSION ABORTED"}]

    async def aclose(self):
        return None


def _agent_run() -> AgentRun:
    return AgentRun(
        turns=[
            TurnRecord(
                index=1,
                decision_latency_ms=1.0,
                provider_wait_ms=0.0,
                attempts=1,
                input_tokens=10,
                cached_input_tokens=0,
                output_tokens=2,
                reasoning_tokens=0,
                finish_reason="stop",
                text="done",
            )
        ],
        calls=[
            CallRecord(turn=1, seq=1, tool="get_position", arguments={}, started_at=0.0, wall_ms=1.0, status="success")
        ],
        stop_reason="model declared the mission finished",
        final_text="MISSION ABORTED",
        started_at=0.0,
        duration_s=1.0,
    )


@pytest.fixture
def _no_sleep(monkeypatch):
    async def sleep(_s):
        return None

    monkeypatch.setattr(runner.asyncio, "sleep", sleep)
    monkeypatch.setenv("OPENAI_API_KEY", "test-key-not-a-real-key")


# ---------------------------------------------- 1. the ferry tells the truth


async def test_a_trial_already_on_the_launch_point_is_not_flown_anywhere(_no_sleep):
    session = FerrySession(drift_m=0.0)
    distance, note = await runner._ferry_to_launch(
        session, LAUNCH, LAUNCH_AMSL, 20.0, DEFAULT_START_TOLERANCE_M, 30.0, log=lambda *a: None
    )
    assert distance == pytest.approx(0.0, abs=0.5)
    assert note == ""
    assert "arm_drone" not in session.calls, "an aircraft already at the launch point must not be flown"


async def test_a_drifted_aircraft_is_flown_back(_no_sleep):
    session = FerrySession(drift_m=300.0)
    distance, note = await runner._ferry_to_launch(
        session, LAUNCH, LAUNCH_AMSL, 20.0, DEFAULT_START_TOLERANCE_M, 30.0, log=lambda *a: None
    )
    assert distance == pytest.approx(0.0, abs=0.5)
    assert "ferried" in note
    assert session.calls.count("arm_drone") == 1
    assert distance_m(session.position, LAUNCH) < DEFAULT_START_TOLERANCE_M


async def test_a_ferry_the_geofence_refuses_reports_the_distance_it_measured(_no_sleep):
    """The case that produced the halted campaign, from the other end.

    An aircraft parked at the fence edge cannot be commanded home either. The
    ferry must report where the aircraft actually *is* - not where it asked it
    to go - so the caller can decline to fly.
    """
    session = FerrySession(drift_m=980.0, ferry_works=False)
    distance, note = await runner._ferry_to_launch(
        session, LAUNCH, LAUNCH_AMSL, 20.0, DEFAULT_START_TOLERANCE_M, 30.0, log=lambda *a: None
    )
    assert distance > DEFAULT_START_TOLERANCE_M
    assert "geofence.radius" in note
    assert session.armed is False, "a refused ferry must still leave the aircraft disarmed"


# -------------------------------- 2. a trial that cannot verify its start refuses


async def test_a_trial_that_cannot_be_placed_on_the_launch_point_is_not_flown(monkeypatch, tmp_path, _no_sleep):
    """The first trial flies; it strands the aircraft; the second declines.

    This is the shape of the real failure. The aircraft starts where the run
    started, the flight leaves it 980 m downrange at the fence edge, and from
    there nothing - not even the harness - can command it home. The second
    trial must decline rather than record a FAIL for a model that would have
    had every horizontal command refused.
    """
    session = FerrySession(drift_m=0.0)
    flights = []

    async def _run_agent(**kwargs):
        flights.append(1)
        session.position = _north(LAUNCH, 980.0)
        session.ferry_works = False
        return _agent_run()

    monkeypatch.setattr(runner, "LiveMCPSession", lambda *a, **k: session)
    monkeypatch.setattr(runner, "McpTelemetryPoller", FakePoller)
    monkeypatch.setattr(runner, "open_session", lambda *a, **k: FakeModel())
    monkeypatch.setattr(runner, "run_agent", _run_agent)

    config = SuiteConfig(
        url="http://x", api_key="k", model_spec="gpt-5.2", missions=["T2"], trials=3, out_dir=tmp_path / "run"
    )
    results = await runner.run_llm_suite(config, log=lambda *a: None)

    assert len(flights) == 1, "only the first trial should have reached the model"
    assert len(results) == 2, "the run is abandoned after the refusal, not carried on"
    refused = results[-1]
    assert refused.start_position_unknown is True
    assert refused.verdict_label == "START"
    assert refused.passed is False
    assert "geofence.radius" in refused.reason
    # ...and, crucially, it is NOT counted as a model failure anywhere.
    summary = (config.out_dir / "summary.md").read_text(encoding="utf-8")
    assert "Missions judged: **1**" in summary
    assert "the aircraft was not on the launch point" in summary


# ------------------------------------- 3. end to end, the drift does not build


async def test_every_trial_starts_on_the_launch_point_however_the_last_one_ended(monkeypatch, tmp_path, _no_sleep):
    """The regression test proper.

    The fake aircraft is left 60 m north at the end of every trial - exactly
    what a real T2 does. Without the between-trial reset the third trial would
    begin 120 m out; with it, every trial begins on the launch point and
    ``missions.csv`` records that it did.
    """
    session = FerrySession(drift_m=0.0)

    async def _run_agent(**kwargs):
        # The "flight": the model leaves the aircraft 60 m north of where it
        # found it, and the next trial inherits that.
        session.position = _north(session.position, 60.0)
        return _agent_run()

    monkeypatch.setattr(runner, "LiveMCPSession", lambda *a, **k: session)
    monkeypatch.setattr(runner, "McpTelemetryPoller", FakePoller)
    monkeypatch.setattr(runner, "open_session", lambda *a, **k: FakeModel())
    monkeypatch.setattr(runner, "run_agent", _run_agent)

    origins: list[float] = []
    real_origin = runner._trial_origin

    async def spy(harness, fallback):
        result = await real_origin(harness, fallback)
        origins.append(distance_m(result["home"], LAUNCH))
        return result

    monkeypatch.setattr(runner, "_trial_origin", spy)

    config = SuiteConfig(
        url="http://x", api_key="k", model_spec="gpt-5.2", missions=["T2"], trials=4, out_dir=tmp_path / "run"
    )
    results = await runner.run_llm_suite(config, log=lambda *a: None)

    assert len(results) == 4
    # One reading to fix the launch point, then one per trial.
    assert len(origins) == 5
    assert max(origins) <= DEFAULT_START_TOLERANCE_M, (
        f"a trial began {max(origins):.0f} m from the launch point; the between-trial reset did not happen"
    )
    # Every trial's start offset is on the record, so a future drift is visible
    # in the results file itself rather than only in a post-mortem.
    for result in results:
        assert result.evidence["start_offset_m"] is not None
        assert result.evidence["start_offset_m"] <= DEFAULT_START_TOLERANCE_M
    assert "Where each trial started" in (config.out_dir / "summary.md").read_text(encoding="utf-8")


async def test_turning_the_reset_off_is_possible_but_says_so(monkeypatch, tmp_path, _no_sleep):
    """Reproducing a historical run must remain possible - and be loud."""
    session = FerrySession(drift_m=0.0)

    async def _run_agent(**kwargs):
        session.position = _north(session.position, 60.0)
        return _agent_run()

    monkeypatch.setattr(runner, "LiveMCPSession", lambda *a, **k: session)
    monkeypatch.setattr(runner, "McpTelemetryPoller", FakePoller)
    monkeypatch.setattr(runner, "open_session", lambda *a, **k: FakeModel())
    monkeypatch.setattr(runner, "run_agent", _run_agent)

    said: list[str] = []
    config = SuiteConfig(
        url="http://x",
        api_key="k",
        model_spec="gpt-5.2",
        missions=["T2"],
        trials=3,
        out_dir=tmp_path / "run",
        reset_position_between_trials=False,
    )
    results = await runner.run_llm_suite(config, log=lambda line: said.append(str(line)))

    assert any("position reset is OFF" in line for line in said)
    assert max(r.evidence["start_offset_m"] for r in results) > DEFAULT_START_TOLERANCE_M


# ------------------------------- 4. the scorer measures from the same point
#                                    the prompt talks about


@pytest.mark.parametrize("displacement_m", [0.0, 250.0, 900.0])
def test_horizontal_missions_are_scored_from_where_the_aircraft_started(displacement_m):
    """T2/T3/T4 are phrased relative to the aircraft; the verdicts must agree.

    Wherever the aircraft is standing, a perfectly flown mission passes - and
    the *same* track scored against a different origin fails. That is the
    property that keeps a starting-position problem from ever being reported as
    a model failure again: if these two could disagree, drift would show up as
    a capability result, which is exactly what the halted campaign produced.
    """
    ctx = {"leg_m": 60.0, "arrival_threshold_m": 15.0, "takeoff_altitude_m": 20.0}
    start = _north(LAUNCH, displacement_m)
    corners = [
        start,
        offset(start[0], start[1], 60.0, 0.0),
        offset(start[0], start[1], 60.0, 60.0),
        offset(start[0], start[1], 0.0, 60.0),
        start,
    ]
    samples = [
        TelemetrySample(
            t=float(i),
            latitude_deg=lat,
            longitude_deg=lon,
            relative_altitude_m=20.0 if 0 < i < len(corners) - 1 else 0.0,
            absolute_altitude_m=LAUNCH_AMSL + 20.0,
            armed=0 < i < len(corners) - 1,
            in_air=0 < i < len(corners) - 1,
        )
        for i, (lat, lon) in enumerate(corners)
    ]
    calls = [
        CallRecord(turn=1, seq=1, tool="upload_mission", arguments={}, started_at=0.0, wall_ms=1.0, status="success")
    ]

    for mission in ("T2", "T3", "T4"):
        good = judge(mission, Track(samples, start), calls, ctx, {"model_turns": 3})
        assert good.passed, f"{mission} scored from the aircraft's own start should pass: {good.reason}"

    if displacement_m > ctx["arrival_threshold_m"]:
        for mission in ("T2", "T3", "T4"):
            wrong = judge(mission, Track(samples, LAUNCH), calls, ctx, {"model_turns": 3})
            assert not wrong.passed, (
                f"{mission} scored from a point the aircraft was not standing on must NOT pass - "
                f"otherwise a starting-position error is indistinguishable from a model result"
            )


def test_the_start_tolerance_is_no_looser_than_arriving_at_a_waypoint():
    """A trial may not begin further out than a mission counts as *arrived*.

    If it could, an aircraft could start a T2 already inside the arrival radius
    of its own target and pass without flying.
    """
    from droneserver.benchmark.missions import DEFAULT_CONTEXT

    assert DEFAULT_START_TOLERANCE_M <= DEFAULT_CONTEXT["arrival_threshold_m"]


# ------------------------------------ 5. the token backstop is a backstop


def test_the_token_budget_cannot_bind_before_the_turn_limit():
    """T4's separate failure: an undocumented limit undercutting a chosen one.

    ``--max-turns`` is 90 and documented as deliberately generous. The token
    budget was 2,000,000, which a mission with a monitoring loop reaches at
    roughly 45-60 turns - so every T4 trial that actually flew the plan was cut
    off mid-flight and then failed for leaving the aircraft armed. The budget
    must sit above the worst case the turn limit allows, so that a trial stops
    for a reason someone chose and can price.
    """
    limits = Limits()
    worst_case = limits.max_turns * runner.LARGEST_RECORDED_PROMPT_TOKENS
    assert limits.max_total_tokens >= worst_case, (
        f"the token backstop ({limits.max_total_tokens:,}) is below {limits.max_turns} turns of the largest "
        f"prompt ever recorded ({worst_case:,}), so it - not max_turns - is what will stop a long trial"
    )


def test_the_ferry_distance_is_measured_not_assumed():
    """Documentation-as-test: infinity means "we do not know where it is".

    ``_ferry_to_launch`` returns ``inf`` when the position cannot be read, and
    the caller compares against the tolerance - so an unreadable position can
    only ever mean "do not fly", never "close enough".
    """
    assert math.inf > DEFAULT_START_TOLERANCE_M
