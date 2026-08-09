"""A dead key must abort the model's run, not be rediscovered once per trial.

Regression tests for the 2026-08-08 campaign, in which Anthropic's real
out-of-credit reply - HTTP 400, "Your credit balance is too low to access the
Anthropic API" - matched none of the harness's quota markers, was treated as an
ordinary provider error, and produced **eighty consecutive VOID trials** before
anyone noticed. The trials were correctly VOID rather than false passes; the
defect is that the harness kept flying.

Everything here is offline: no provider is called and no money is spent.
"""

from __future__ import annotations

import pytest

from droneserver.llm.agent import AgentRun, Limits, run_agent
from droneserver.llm.providers import (
    ProviderAuthError,
    ProviderError,
    ProviderQuotaError,
    ToolSpec,
    fatal_provider_error,
)
from droneserver.llm.runner import VOID_STREAK_LIMIT, TrialResult, abandon_reason

ANTHROPIC_OUT_OF_CREDIT = (
    '{"type":"error","error":{"type":"invalid_request_error","message":'
    '"Your credit balance is too low to access the Anthropic API. Please go to Plans & Billing '
    'to upgrade or purchase credits."}}'
)


# ------------------------------------------------------- classifying a failure


def test_the_anthropic_out_of_credit_reply_is_recognised_as_a_quota_error():
    """The exact reply that cost 80 trials. HTTP 400, not 402, not 429."""
    fatal = fatal_provider_error(400, ANTHROPIC_OUT_OF_CREDIT, "anthropic:claude-opus-5")
    assert isinstance(fatal, ProviderQuotaError)


@pytest.mark.parametrize(
    "status,body",
    [
        (402, "payment required"),
        (400, "Your credit balance is too low"),
        (429, "You exceeded your current quota, please check your plan and billing details"),
        (403, "insufficient_quota"),
        (400, "You have exceeded your monthly spend limit"),
    ],
)
def test_out_of_credit_phrasings_are_all_fatal(status, body):
    assert isinstance(fatal_provider_error(status, body, "p:m"), ProviderQuotaError)


@pytest.mark.parametrize(
    "status,body",
    [
        (401, "invalid x-api-key"),
        (403, "Forbidden"),
        (400, "API key not valid. Please pass a valid API key."),
        (400, '{"error":{"code":"invalid_api_key"}}'),
    ],
)
def test_a_rejected_key_is_fatal_and_is_not_a_quota_error(status, body):
    fatal = fatal_provider_error(status, body, "p:m")
    assert isinstance(fatal, ProviderAuthError)
    assert not isinstance(fatal, ProviderQuotaError)


@pytest.mark.parametrize(
    "status,body",
    [
        (429, "Rate limit reached for requests"),
        (403, "rate_limit_exceeded: too many requests, slow down"),
        (500, "internal server error"),
        (529, "overloaded_error"),
        (503, "service unavailable"),
        (408, "request timeout"),
    ],
)
def test_a_transient_failure_is_not_fatal_and_stays_on_the_retry_path(status, body):
    """Over-eager classification would be worse than the bug it guards against."""
    assert fatal_provider_error(status, body, "p:m") is None


def test_both_fatal_errors_are_provider_errors():
    """So an existing `except ProviderError` cannot start leaking them."""
    assert issubclass(ProviderQuotaError, ProviderError)
    assert issubclass(ProviderAuthError, ProviderError)


# --------------------------------------------------- what the agent loop does


class _RefusingModel:
    """A model session whose provider refuses the key on the first turn."""

    def __init__(self, error: Exception):
        self._error = error
        self.turns_attempted = 0
        self.messages: list = []

    def start(self, system_prompt: str, user_prompt: str) -> None:
        self.messages = [{"role": "user", "content": user_prompt}]

    async def next_turn(self, tools):
        self.turns_attempted += 1
        raise self._error

    def record_tool_result(self, call, result) -> None:  # pragma: no cover - never reached
        raise AssertionError("no tool call can happen when the model never answered")

    async def aclose(self) -> None:
        return None


class _NoMcp:
    async def __aenter__(self):
        return self

    async def list_tools(self):  # pragma: no cover - not used by run_agent
        return []

    async def call(self, tool, arguments, *, turn=0, seq=0, timeout_s=300.0):  # pragma: no cover
        raise AssertionError("no tool call can happen when the model never answered")

    async def aclose(self) -> None:
        return None


@pytest.mark.parametrize(
    "error,expect_out_of_credit",
    [
        (ProviderQuotaError("anthropic:claude-opus-5: credit balance is too low"), True),
        (ProviderAuthError("anthropic:claude-opus-5: the API key was rejected"), False),
    ],
)
async def test_a_refused_key_marks_the_run_unusable_and_produces_no_model_result(error, expect_out_of_credit):
    model = _RefusingModel(error)
    run = await run_agent(
        model=model,
        mcp=_NoMcp(),
        tools=[ToolSpec("takeoff", "take off", {})],
        system_prompt="s",
        user_prompt="u",
        limits=Limits(max_turns=90),
    )
    assert model.turns_attempted == 1, "the loop must not keep asking a provider that refused the key"
    assert run.provider_unusable, "the run must say the provider, not the model, ended it"
    assert run.out_of_credit is expect_out_of_credit
    assert run.turns == [], "there is no model behaviour here to judge"


async def test_an_ordinary_provider_error_does_not_mark_the_run_unusable():
    """A one-off failure must not abandon 44 remaining trials."""
    run = await run_agent(
        model=_RefusingModel(ProviderError("openai:gpt-5.2: HTTP 500")),
        mcp=_NoMcp(),
        tools=[],
        system_prompt="s",
        user_prompt="u",
    )
    assert run.provider_unusable == ""
    assert run.out_of_credit is False
    assert run.error and "HTTP 500" in run.error


# ------------------------------------------------- when to abandon a model run


def _void(error: str = "ProviderError: HTTP 404") -> TrialResult:
    run = AgentRun(stop_reason=f"harness error: {error}", error=error)
    return TrialResult("T1", 1, False, "not evaluated", not_evaluated=True, run=run)


def test_a_refused_key_abandons_the_run_on_the_very_first_trial():
    result = _void()
    assert result.run is not None
    result.run.provider_unusable = "the account is out of credit"
    assert abandon_reason(result, void_streak=1) == "the account is out of credit"


def test_an_unrecognised_failure_abandons_the_run_after_a_short_streak():
    """The backstop: eighty identical VOIDs must never happen again."""
    assert abandon_reason(_void(), void_streak=VOID_STREAK_LIMIT - 1) == ""
    reason = abandon_reason(_void(), void_streak=VOID_STREAK_LIMIT)
    assert reason
    assert "HTTP 404" in reason, "the reason must name what actually kept failing"


def test_the_streak_limit_is_short_but_not_a_hair_trigger():
    """One provider error can be a blip; one model's quota message can be about
    that model (gemini-3.1-pro-preview reported "out of credit" while
    gemini-3.6-flash then ran fine on the same key)."""
    assert 2 <= VOID_STREAK_LIMIT <= 5


def test_a_trial_that_produced_model_behaviour_does_not_abandon_the_run():
    flown = TrialResult("T1", 1, True, "flew it", run=AgentRun(stop_reason="model declared the mission finished"))
    assert abandon_reason(flown, void_streak=0) == ""
