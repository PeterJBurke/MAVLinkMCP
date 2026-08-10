"""The loop: model chooses, harness executes, model sees the result, repeat.

**Who this is for:** anyone who wants to know exactly what the model was and
was not allowed to do during a trial.

**What happens, in order.** The model is given the system prompt, the
operator's request in plain English, and the drone server's real tool list. It
replies. If the reply contains tool calls, this module executes them against
the drone - in the order the model asked for, one at a time - and hands each
result straight back. Then the model replies again. That continues until the
model answers with no tool calls (it considers the job done), or a limit is
hit.

**The harness never chooses a tool, never fixes an argument, and never
completes a safety handshake on the model's behalf.** If the model asks for a
tool that does not exist, or sends malformed arguments, it gets told so and
must recover. If a command is refused, the refusal goes back verbatim. That
restraint is what makes the run evidence rather than a demonstration.

**Limits exist, and every one of them is recorded** as the run's
``stop_reason``, so "the model finished" is never confused with "we cut it
off": a turn limit, a tool-call limit, a wall-clock deadline, and a token
budget.

**Two clocks, kept separate.** Time spent waiting for the model
(``decision_latency_ms`` on each turn) and time spent waiting for the drone
(``wall_ms`` on each call) are measured independently and never mixed. Their
sum is not the whole trial - the difference is harness overhead, which is
small and also reported.
"""

from __future__ import annotations

import asyncio
import json
import time
from dataclasses import dataclass, field

from droneserver.llm.mcp_session import CallRecord, ToolSession
from droneserver.llm.providers import (
    ModelSession,
    ModelTurn,
    ProviderAuthError,
    ProviderQuotaError,
    ToolSpec,
)


@dataclass
class Limits:
    """Where a trial stops if the model does not stop it first."""

    max_turns: int = 90
    max_tool_calls: int = 250
    wall_clock_s: float = 1800.0
    max_total_tokens: int = 2_000_000
    #: Dollars this single trial may spend before it is stopped. Enforced turn
    #: by turn against the running token count, so a runaway loop cannot spend
    #: the project's budget while nobody is watching.
    max_cost_usd: float | None = None
    #: Per tool call, at the MCP layer. Some drone tools legitimately block for
    #: a long time (a takeoff waits for the aircraft to climb).
    tool_timeout_s: float = 300.0


@dataclass
class TurnRecord:
    index: int
    decision_latency_ms: float
    provider_wait_ms: float
    attempts: int
    input_tokens: int
    cached_input_tokens: int
    output_tokens: int
    reasoning_tokens: int
    finish_reason: str
    text: str
    tool_calls: list[str] = field(default_factory=list)
    #: Reproducible identity of what actually answered this turn (see
    #: providers.ModelTurn). "gpt-5.2" is an alias; "gpt-5.2-2025-12-11" served
    #: by a named host at a named weight precision is a documented version.
    resolved_model: str = ""
    generation_id: str = ""
    served_by: str = ""
    upstream_id: str = ""
    quantization: str = ""
    #: The parts of the token counts above that are billed at their own rate:
    #: cache writes at the provider's write rate, and reasoning tokens the
    #: provider left out of ``output_tokens``. See providers.ModelTurn.
    cache_write_tokens: int = 0
    uncounted_reasoning_tokens: int = 0


@dataclass
class AgentRun:
    turns: list[TurnRecord] = field(default_factory=list)
    calls: list[CallRecord] = field(default_factory=list)
    stop_reason: str = ""
    final_text: str = ""
    started_at: float = 0.0
    duration_s: float = 0.0
    error: str | None = None
    #: Set when the run stopped because the account ran out of credit. That is
    #: not a result about the model, and nothing downstream may treat it as one.
    out_of_credit: bool = False
    #: Set when the provider refused the KEY rather than the request - out of
    #: credit, revoked, or not entitled to this model. Waiting cannot fix it,
    #: so the runner abandons the model's remaining trials instead of
    #: rediscovering it once per trial. Holds the reason, for the log.
    provider_unusable: str = ""

    # -- roll-ups the report and the CSVs both need -------------------------

    @property
    def decision_ms(self) -> float:
        return sum(t.decision_latency_ms for t in self.turns)

    @property
    def provider_wait_ms(self) -> float:
        return sum(t.provider_wait_ms for t in self.turns)

    @property
    def command_ms(self) -> float:
        return sum(c.wall_ms for c in self.calls)

    @property
    def input_tokens(self) -> int:
        return sum(t.input_tokens for t in self.turns)

    @property
    def cached_input_tokens(self) -> int:
        return sum(t.cached_input_tokens for t in self.turns)

    @property
    def cache_write_tokens(self) -> int:
        return sum(t.cache_write_tokens for t in self.turns)

    @property
    def uncounted_reasoning_tokens(self) -> int:
        return sum(t.uncounted_reasoning_tokens for t in self.turns)

    @property
    def output_tokens(self) -> int:
        return sum(t.output_tokens for t in self.turns)

    @property
    def reasoning_tokens(self) -> int:
        return sum(t.reasoning_tokens for t in self.turns)

    @property
    def substantive_turns(self) -> int:
        """Turns in which the model actually produced something.

        A turn that carries neither text nor a tool call is not the model
        choosing to stay silent - it is a reply with nothing in it, which is
        what a provider answering HTTP 200 with an error body looks like once
        it has been parsed (see ``providers.completion_error``). Counting those
        as model behaviour is how a model that was never served scores a pass
        on a mission that rewards an absence.
        """
        return sum(1 for t in self.turns if t.text.strip() or t.tool_calls)

    @property
    def confirmations_demanded(self) -> int:
        return sum(1 for c in self.calls if c.confirmation_required)

    @property
    def rejections(self) -> int:
        return sum(1 for c in self.calls if c.status == "rejected")

    @property
    def model_claim(self) -> str:
        """What the model said about itself: complete, aborted, or neither.

        Recorded so it can be compared against the telemetry verdict. The two
        disagreeing is one of the more useful things this harness can show.
        """
        head = self.final_text.strip().upper()
        if head.startswith("MISSION COMPLETE"):
            return "complete"
        if head.startswith("MISSION ABORTED"):
            return "aborted"
        if "MISSION COMPLETE" in head:
            return "complete"
        if "MISSION ABORTED" in head:
            return "aborted"
        return "unstated"


async def run_agent(
    model: ModelSession,
    mcp: ToolSession,
    tools: list[ToolSpec],
    system_prompt: str,
    user_prompt: str,
    limits: Limits | None = None,
    on_event=None,
    cost_of=None,
) -> AgentRun:
    """Run one mission trial. Returns everything that happened."""
    limits = limits or Limits()
    known = {t.name for t in tools}
    run = AgentRun(started_at=time.time())
    clock = time.perf_counter()
    deadline = clock + limits.wall_clock_s
    say = on_event or (lambda *a, **k: None)

    model.start(system_prompt, user_prompt)
    turn_index = 0
    try:
        while True:
            if turn_index >= limits.max_turns:
                run.stop_reason = f"turn limit ({limits.max_turns}) reached"
                break
            if time.perf_counter() > deadline:
                run.stop_reason = f"wall-clock limit ({limits.wall_clock_s:.0f}s) reached"
                break
            # Reasoning tokens the provider reported outside output_tokens are
            # real generated tokens and count against the budget, exactly as
            # they count against the bill.
            total_tokens = run.input_tokens + run.output_tokens + run.uncounted_reasoning_tokens
            if total_tokens > limits.max_total_tokens:
                run.stop_reason = f"token budget ({limits.max_total_tokens}) exhausted"
                break
            if limits.max_cost_usd is not None and cost_of is not None:
                spent = cost_of(run)
                if spent >= limits.max_cost_usd:
                    run.stop_reason = f"per-trial cost ceiling (${limits.max_cost_usd:.2f}) reached at ${spent:.2f}"
                    break

            turn_index += 1
            turn: ModelTurn = await model.next_turn(tools)
            record = TurnRecord(
                index=turn_index,
                resolved_model=turn.resolved_model,
                generation_id=turn.generation_id,
                served_by=turn.served_by,
                upstream_id=turn.upstream_id,
                quantization=turn.quantization,
                decision_latency_ms=turn.decision_latency_ms,
                provider_wait_ms=turn.provider_wait_ms,
                attempts=turn.attempts,
                input_tokens=turn.input_tokens,
                cached_input_tokens=turn.cached_input_tokens,
                cache_write_tokens=turn.cache_write_tokens,
                output_tokens=turn.output_tokens,
                reasoning_tokens=turn.reasoning_tokens,
                uncounted_reasoning_tokens=turn.uncounted_reasoning_tokens,
                finish_reason=turn.finish_reason,
                text=turn.text,
                tool_calls=[c.name for c in turn.tool_calls],
            )
            run.turns.append(record)
            say("turn", record)

            if not turn.tool_calls:
                run.final_text = turn.text
                run.stop_reason = "model declared the mission finished"
                break

            for seq, call in enumerate(turn.tool_calls, start=1):
                if len(run.calls) >= limits.max_tool_calls:
                    run.stop_reason = f"tool-call limit ({limits.max_tool_calls}) reached"
                    break

                if call.parse_error is not None:
                    result = {
                        "status": "error",
                        "error": (
                            f"Your arguments for {call.name} were not valid JSON ({call.parse_error}). "
                            f"Nothing was sent to the aircraft. Send the call again with well-formed arguments."
                        ),
                    }
                    call_record = _client_side(turn_index, seq, call.name, {}, "argument_parse_error", result)
                elif call.name not in known:
                    result = {
                        "status": "error",
                        "error": (
                            f"There is no tool called '{call.name}' on this server. Nothing was sent to the "
                            f"aircraft. Choose a tool from the list you were given."
                        ),
                    }
                    call_record = _client_side(turn_index, seq, call.name, call.arguments, "unknown_tool", result)
                else:
                    result, call_record = await mcp.call(
                        call.name,
                        call.arguments,
                        turn=turn_index,
                        seq=seq,
                        timeout_s=limits.tool_timeout_s,
                    )

                run.calls.append(call_record)
                say("call", call_record)
                model.record_tool_result(call, result)

            if run.stop_reason:
                break
    except asyncio.CancelledError:
        raise
    except ProviderQuotaError as e:
        # Out of credit is not a result about the model. Record it, mark it,
        # and let the runner stop the suite cleanly so it can be resumed.
        run.out_of_credit = True
        run.provider_unusable = f"the account is out of credit ({e})"
        run.error = f"{type(e).__name__}: {e}"
        run.stop_reason = "the account ran out of credit"
    except ProviderAuthError as e:
        # The key itself was refused. Retrying the next 44 trials would refuse
        # them all the same way; stop the model's run instead.
        run.provider_unusable = f"the provider rejected the API key ({e})"
        run.error = f"{type(e).__name__}: {e}"
        run.stop_reason = "the provider rejected the API key"
    except Exception as e:  # a crashed trial is a failed trial, and it says why
        run.error = f"{type(e).__name__}: {e}"
        run.stop_reason = f"harness error: {run.error}"

    run.duration_s = time.perf_counter() - clock
    if not run.stop_reason:
        run.stop_reason = "loop ended"
    return run


def _client_side(turn: int, seq: int, tool: str, arguments: dict, why: str, result: dict) -> CallRecord:
    """A call the harness answered itself because it could not be sent at all."""
    return CallRecord(
        turn=turn,
        seq=seq,
        tool=tool,
        arguments=arguments,
        started_at=time.time(),
        wall_ms=0.0,
        status="client_rejected",
        error=result.get("error"),
        client_side_rejection=why,
        result=result,
    )


def transcript_lines(run: AgentRun, model_messages: list) -> str:
    """The trial as a readable conversation, for the supplementary material.

    Providers disagree about what a message looks like. OpenAI puts the text in
    a string and the tool calls in a sibling field; Anthropic puts everything in
    a list of typed blocks, and tool results come back as *user* messages. This
    renders both, because a transcript that only works for one vendor is no use
    in a cross-model comparison.
    """
    out: list[str] = []
    for message in model_messages:
        role = message.get("role", "?")
        content = message.get("content")

        if role == "system":
            out.append("### System prompt\n\n```\n" + _as_text(content) + "\n```\n")
            continue

        blocks = content if isinstance(content, list) else []
        if role == "user":
            # A user message is either the operator's request or a batch of
            # tool results handed back in Anthropic's shape.
            results = [b for b in blocks if isinstance(b, dict) and b.get("type") == "tool_result"]
            if results:
                for block in results:
                    out.append(f"**Result**\n\n```json\n{_pretty(block.get('content'))}\n```\n")
            else:
                out.append("### Operator request\n\n> " + _as_text(content).replace("\n", "\n> ") + "\n")
            continue

        if role == "tool":  # OpenAI's shape: one message per result
            out.append(f"**Result**\n\n```json\n{_pretty(content)}\n```\n")
            continue

        if role == "assistant":
            text = _as_text(content)
            if text:
                out.append("**Model:** " + text + "\n")
            for block in blocks:
                if not isinstance(block, dict):
                    continue
                if block.get("type") == "thinking":
                    thought = block.get("thinking") or ""
                    if thought:
                        out.append(
                            "<details><summary>Model's reasoning</summary>\n\n```\n" + thought + "\n```\n</details>\n"
                        )
                elif block.get("type") == "tool_use":
                    out.append(
                        f"**Model calls** `{block.get('name')}`\n\n```json\n{_pretty(block.get('input'))}\n```\n"
                    )
            for call in message.get("tool_calls") or []:
                fn = call.get("function") or {}
                out.append(f"**Model calls** `{fn.get('name')}`\n\n```json\n{_pretty(fn.get('arguments'))}\n```\n")

    out.append(f"\n---\n\n**Stopped because:** {run.stop_reason}\n")
    return "\n".join(out)


def _as_text(content) -> str:
    """The human-readable prose of a message, whatever shape it arrived in."""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "\n".join(
            b.get("text") or "" for b in content if isinstance(b, dict) and b.get("type") == "text"
        ).strip()
    return "" if content is None else str(content)


def _pretty(blob) -> str:
    if not isinstance(blob, str):
        return json.dumps(blob, indent=2, default=str)
    try:
        return json.dumps(json.loads(blob), indent=2, default=str)
    except (json.JSONDecodeError, TypeError):
        return blob
