"""One capture command line, shared by both harnesses (blocker B-2).

``--capture`` existed only on ``scripts/run_mission_suite.py``. The harness the
N=5 campaign actually runs - ``scripts/run_llm_missions.py`` - had no capture
flag at all, so the campaign would have produced no Plan 19 bundles. The flags
now come from one module, and this pins that down: the same flag set on both
scripts, mapped onto the same config, with the one flag combination that would
otherwise be a trap rejected outright.
"""

import argparse

import pytest

from droneserver.benchmark.capture_cli import (
    add_capture_arguments,
    build_capture_config,
    report_capture,
)

#: Flags that must exist on every harness that can capture.
EXPECTED_FLAGS = {
    "--capture",
    "--mavlink-endpoint",
    "--telemetry-address",
    "--dataflash-dir",
    "--dataflash-remote",
    "--vehicle-sysid",
    "--telemetry-rate",
    "--min-telemetry-rows",
    "--require-complete-capture",
    "--firmware",
    "--firmware-version",
    "--sitl-host",
    "--sim-params",
}


def _flags_of(script_module_argv_parser) -> set[str]:
    return {option for action in script_module_argv_parser._actions for option in action.option_strings}


def _parser(**kwargs) -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser()
    add_capture_arguments(parser, **kwargs)
    return parser


def test_both_harnesses_expose_the_same_capture_flags():
    """The drift that caused B-2, made impossible to repeat silently."""
    scripted = _flags_of(_parser())
    llm = _flags_of(_parser(model_provenance=False))
    assert EXPECTED_FLAGS <= scripted
    assert EXPECTED_FLAGS <= llm
    # The only permitted difference: the LLM harness owns --model itself and
    # takes its provenance from the resolved route.
    assert scripted - llm == {"--model", "--provider", "--decoding"}


def test_no_capture_means_no_config_and_no_capture_import():
    args = _parser().parse_args([])
    assert build_capture_config(args, error=pytest.fail) is None


def test_flags_map_onto_the_config():
    args = _parser().parse_args(
        [
            "--capture",
            "--mavlink-endpoint",
            "udpin:127.0.0.1:14655",
            "--telemetry-address",
            "udpin://127.0.0.1:14541",
            "--dataflash-remote",
            "llmuavsitl:/home/dronepilot/ardupilot/ArduCopter/logs",
            "--vehicle-sysid",
            "1",
            "--telemetry-rate",
            "10",
            "--min-telemetry-rows",
            "25",
            "--firmware",
            "ArduCopter",
            "--firmware-version",
            "4.5.7 (SITL)",
            "--sitl-host",
            "llmuavsitl",
            "--sim-params",
            '{"frame":"quad"}',
            "--model",
            "gpt-5.2",
            "--provider",
            "openai",
            "--decoding",
            '{"temperature":0}',
        ]
    )
    cfg = build_capture_config(args, error=pytest.fail)
    assert cfg.mavlink_endpoint == "udpin:127.0.0.1:14655"
    assert cfg.dataflash_remote.endswith("/ArduCopter/logs")
    assert cfg.min_telemetry_rows == 25
    assert cfg.sim_params == {"frame": "quad"}
    assert cfg.firmware_version == "4.5.7 (SITL)"
    assert cfg.model == "gpt-5.2" and cfg.provider == "openai"
    assert cfg.decoding == {"temperature": 0}


def test_the_caller_can_override_the_provenance():
    """The LLM harness knows the resolved route; a typed flag is only a claim."""
    args = _parser(model_provenance=False).parse_args(["--capture"])
    cfg = build_capture_config(
        args, error=pytest.fail, model="gemini-3.5-flash-lite", provider="google", decoding={"tool_choice": "auto"}
    )
    assert cfg.model == "gemini-3.5-flash-lite"
    assert cfg.provider == "google"
    assert cfg.decoding == {"tool_choice": "auto"}


def test_malformed_json_is_rejected_by_the_caller_s_error_hook():
    args = _parser().parse_args(["--capture", "--sim-params", "not json"])
    seen = []
    build_capture_config(args, error=seen.append)
    assert seen and "--sim-params must be valid JSON" in seen[0]


def test_requiring_complete_capture_without_capture_is_refused():
    """Together they would be a trap: nothing captured satisfies 'none degraded'."""
    args = _parser().parse_args(["--require-complete-capture"])
    seen = []
    build_capture_config(args, error=seen.append)
    assert seen and "has no meaning without --capture" in seen[0]


# --- the run-end summary ---------------------------------------------------


def test_the_summary_line_is_printed_even_when_everything_is_fine():
    """ "0 of 9 degraded" is what makes the absence of a warning mean something."""
    out = []
    failed = report_capture(["complete"] * 9, require_complete=True, out=out.append)
    assert failed is False
    assert out[0] == "capture: 0/9 trial(s) degraded - every bundle is complete"


def test_degraded_bundles_are_named_and_can_fail_the_run():
    out = []
    statuses = ["complete", "degraded[mavlink.jsonl: one direction only]", ""]
    assert report_capture(statuses, require_complete=True, out=out.append) is True
    assert out[0] == "capture: 1/2 trial(s) degraded"
    assert "one direction only" in out[1]
    assert any("--require-complete-capture" in line for line in out)


def test_degraded_bundles_alone_do_not_fail_a_run_that_did_not_ask():
    out = []
    assert report_capture(["degraded[telemetry.csv: 0 rows]"], require_complete=False, out=out.append) is False
    assert out[0] == "capture: 1/1 trial(s) degraded"


def test_a_run_without_capture_prints_nothing_about_it():
    out = []
    assert report_capture(["", "", ""], require_complete=True, out=out.append) is False
    assert out == []
