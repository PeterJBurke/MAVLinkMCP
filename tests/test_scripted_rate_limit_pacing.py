"""The scripted mission harness must pace critical calls between trials, the
same way the LLM-driven arm already does.

The regression (PX4 N=5 scripted validation, 2026-08-12): T7 and T9 fire several
CRITICAL calls per trial (set_parameter, kill_motors, an absurd takeoff). The
critical-tier rate limiter keys on the client_id every trial shares, so 5 trials
run in ~12 s starved the budget and scored T7/T9 down - even though the safety
layer behaved correctly (the dangerous calls were all refused, just by the
limiter instead of the token gate). The LLM path had pacing; the scripted path
did not. These tests pin the scripted twin of that pacing.
"""

from droneserver.benchmark.client import CallRecord
from droneserver.benchmark.runner import DEFAULT_CRITICAL_RATE_WINDOW_S, _critical_drain_deadline


def _call(tool, started_at, wall_ms=100.0, status="success", **arguments):
    return CallRecord(tool=tool, started_at=started_at, wall_ms=wall_ms, status=status, arguments=arguments)


def test_no_critical_calls_means_no_wait():
    calls = [_call("get_armed", 1000.0), _call("get_position", 1001.0)]
    assert _critical_drain_deadline(calls, DEFAULT_CRITICAL_RATE_WINDOW_S) == 0.0


def test_set_parameter_is_paced_only_via_its_argument():
    """A safety-critical param name escalates set_parameter to CRITICAL; a
    harmless one does not - so pacing must see the arguments, not just the tool."""
    critical = [_call("set_parameter", 1000.0, wall_ms=500.0, name="MPC_XY_CRUISE", value=15.0)]
    assert _critical_drain_deadline(critical, 60.0) == 1000.5 + 60.0

    harmless = [_call("set_parameter", 1000.0, wall_ms=500.0, name="LOG_BITMASK", value=1.0)]
    assert _critical_drain_deadline(harmless, 60.0) == 0.0


def test_base_critical_tool_is_paced_with_no_args():
    # kill_motors is CRITICAL by base tier, independent of arguments.
    calls = [_call("kill_motors", 1000.0, wall_ms=100.0)]
    assert _critical_drain_deadline(calls, 60.0) == 1000.1 + 60.0


def test_latest_critical_call_wins():
    calls = [
        _call("kill_motors", 1000.0, wall_ms=100.0),
        _call("kill_motors", 1005.0, wall_ms=200.0),
    ]
    assert _critical_drain_deadline(calls, 60.0) == 1005.2 + 60.0


def test_transport_error_critical_call_is_not_paced_for():
    """A call that never reached the server never touched the limiter."""
    calls = [_call("kill_motors", 1000.0, status="transport_error")]
    assert _critical_drain_deadline(calls, 60.0) == 0.0


def test_pacing_disabled_when_window_non_positive():
    calls = [_call("kill_motors", 1000.0)]
    assert _critical_drain_deadline(calls, 0.0) == 0.0
