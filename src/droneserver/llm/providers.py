"""Talking to language models, behind one thin interface.

**Who this is for:** anyone who wants to run the LLM-in-the-loop mission suite
against a different model, or add a provider we do not support yet.

**What this does.** The mission harness needs exactly four things from a model:
hand it a system prompt and an operator's request, hand it the drone tools,
get back either some tool calls or a final answer, and hand it the tool
results. That is the whole contract, and it is expressed here as
:class:`ModelSession`. Everything provider-specific - URLs, authentication
headers, the shape of a "tool call" on the wire, where the token counts live -
sits behind that contract, so the agent loop in ``agent.py`` never mentions a
vendor.

**Terms used here**

- *provider*: the company whose API we call (OpenAI, Anthropic, ...).
- *wire format*: the JSON shape a provider's HTTP API expects. Several
  providers deliberately copy OpenAI's, so one implementation serves them all.
- *tool call*: the model's request that we run a named function with named
  arguments. It is a request, not an action: nothing happens until this
  harness executes it against the drone server.
- *aggregator*: a service that resells many vendors' models behind one API
  (OpenRouter). Useful when we have no direct key.

**Routing policy (locked in Plan 04).** Direct provider APIs are preferred.
An aggregator is used only for models we have no direct key for. That policy
is implemented in :func:`resolve_model`, not left to whoever runs the script.

**On adding a provider.** If its API copies OpenAI's shape (xAI, Mistral,
DeepSeek, OpenRouter all do), add a row to :data:`PROVIDERS` and it works the
moment a key exists - no code. If it has its own shape (Anthropic, Google), it
needs a ``ModelSession`` subclass; :data:`WIRES` says which shapes are
implemented, and asking for an unimplemented one fails loudly rather than
pretending.
"""

from __future__ import annotations

import asyncio
import json
import os
import time
from dataclasses import dataclass, field
from typing import Any

import httpx


class ProviderError(RuntimeError):
    """The model API could not be used (bad key, unknown provider, HTTP error)."""


class ProviderNotConfigured(ProviderError):
    """No API key is available for the provider this model needs."""


class ProviderQuotaError(ProviderError):
    """The account is out of credit or over quota.

    Distinct from every other failure on purpose: this is not the experiment
    going wrong, it is the money running out. The runner treats it as a clean,
    resumable stop rather than a failed trial, because recording "the model
    could not fly" when the truth is "we could not pay" would corrupt the
    results.
    """


#: Substrings that mean "out of credit", across providers that all phrase it
#: differently and none of which use a dedicated status code consistently.
QUOTA_MARKERS = (
    "insufficient_quota",
    "insufficient credit",
    "insufficient balance",
    "exceeded your current quota",
    "billing_hard_limit_reached",
    "requires more credits",
    "payment required",
)


# --------------------------------------------------------------- data carriers


@dataclass(frozen=True)
class ToolSpec:
    """One tool as the model sees it: a name, prose, and a JSON Schema.

    These come straight off the MCP server, so the model is offered the real
    interface under test - not a hand-written summary of it.
    """

    name: str
    description: str
    parameters: dict


@dataclass
class ToolCall:
    """The model's request to run one tool. Nothing has been executed yet."""

    call_id: str
    name: str
    arguments: dict
    raw_arguments: str
    #: Set when the model emitted arguments that were not valid JSON.
    parse_error: str | None = None


@dataclass
class ModelTurn:
    """One reply from the model, plus what it cost to get it.

    ``decision_latency_ms`` is the model's own thinking-and-answering time: the
    HTTP request that produced this reply, start to finish. It deliberately
    excludes retries after an error (those are in ``provider_wait_ms``) and
    excludes every millisecond spent talking to the drone, which is measured
    separately. Keeping the two apart is the point of the whole exercise: a
    slow flight and a slow model are different problems.
    """

    text: str
    tool_calls: list[ToolCall]
    finish_reason: str
    decision_latency_ms: float
    provider_wait_ms: float = 0.0
    attempts: int = 1
    input_tokens: int = 0
    cached_input_tokens: int = 0
    output_tokens: int = 0
    reasoning_tokens: int = 0
    error: str | None = None
    #: Exactly which model answered, as the provider reported it - e.g.
    #: "gpt-5.2-2025-12-11" rather than the alias we asked for. Reviewers
    #: asked for documented versions, and "gpt-5.2" is not one.
    resolved_model: str = ""
    #: The provider's own id for this generation, so a row in our CSV can be
    #: matched against the provider's records.
    generation_id: str = ""
    #: For an aggregator: which upstream company actually served it, and under
    #: what id. Empty for a direct call, where the answer is not in doubt.
    served_by: str = ""
    upstream_id: str = ""
    #: Weight precision of the serving endpoint (fp8, mxfp4, ...) where the
    #: provider discloses it. Two endpoints for the same model name can differ.
    quantization: str = ""


# ------------------------------------------------------------------- registry


@dataclass(frozen=True)
class ProviderSpec:
    name: str
    #: Which wire format this provider speaks (see :data:`WIRES`).
    wire: str
    base_url: str
    api_key_env: str
    #: Slug this provider's models carry on OpenRouter, when we must fall back.
    openrouter_slug: str = ""
    extra_headers: dict = field(default_factory=dict)


PROVIDERS: dict[str, ProviderSpec] = {
    # Direct APIs. Everything below "openai" copies OpenAI's wire format, so
    # they need no code of their own - only a key.
    "openai": ProviderSpec("openai", "openai", "https://api.openai.com/v1", "OPENAI_API_KEY", "openai"),
    "xai": ProviderSpec("xai", "openai", "https://api.x.ai/v1", "XAI_API_KEY", "x-ai"),
    "mistral": ProviderSpec("mistral", "openai", "https://api.mistral.ai/v1", "MISTRAL_API_KEY", "mistralai"),
    "deepseek": ProviderSpec("deepseek", "openai", "https://api.deepseek.com/v1", "DEEPSEEK_API_KEY", "deepseek"),
    # Own wire formats. Registered so routing works the day a key arrives; the
    # adapters are not written yet and say so rather than failing obscurely.
    "anthropic": ProviderSpec(
        "anthropic", "anthropic", "https://api.anthropic.com/v1", "ANTHROPIC_API_KEY", "anthropic"
    ),
    # Google's OWN endpoint, which serves an OpenAI-shaped surface alongside
    # its native one. This is still a direct call to Google - not an
    # aggregator - so it satisfies the "direct APIs preferred" rule while
    # needing no separate adapter. The trade-off is real and worth knowing:
    # the compatibility surface exposes fewer Gemini-specific controls than
    # the native API, so anything needing those will want a native adapter.
    "google": ProviderSpec(
        "google",
        "openai",
        "https://generativelanguage.googleapis.com/v1beta/openai",
        "GEMINI_API_KEY",
        "google",
    ),
    # Aggregator: last resort, per the Plan 04 routing policy.
    "openrouter": ProviderSpec("openrouter", "openai", "https://openrouter.ai/api/v1", "OPENROUTER_API_KEY"),
}

#: Model-name prefix -> the provider that actually makes that model.
NATIVE_PROVIDER_BY_PREFIX: list[tuple[str, str]] = [
    ("gpt-", "openai"),
    ("o1", "openai"),
    ("o3", "openai"),
    ("o4", "openai"),
    ("chatgpt", "openai"),
    ("claude", "anthropic"),
    ("gemini", "google"),
    ("grok", "xai"),
    ("mistral", "mistral"),
    ("magistral", "mistral"),
    ("ministral", "mistral"),
    ("codestral", "mistral"),
    ("deepseek", "deepseek"),
]


@dataclass(frozen=True)
class Route:
    """Where a requested model will actually be called, and why."""

    provider: ProviderSpec
    #: Model id to send on the wire (an aggregator renames models).
    wire_model: str
    #: The name the experiment records - always the model the user asked for.
    requested_model: str
    #: "direct" or "aggregator (no direct key)".
    routing: str

    @property
    def label(self) -> str:
        return f"{self.provider.name}:{self.requested_model}"


def _have_key(spec: ProviderSpec, env: dict | None = None) -> bool:
    return bool((env or os.environ).get(spec.api_key_env, "").strip())


def native_provider_for(model: str) -> str | None:
    """Which company makes this model, judged from its name."""
    lowered = model.lower().lstrip()
    for prefix, provider in NATIVE_PROVIDER_BY_PREFIX:
        if lowered.startswith(prefix):
            return provider
    return None


def resolve_model(spec: str, env: dict | None = None) -> Route:
    """Decide which API to call for ``spec``.

    ``spec`` is either ``"provider:model"`` - which is obeyed exactly, no
    second-guessing - or a bare model name, which is routed by policy:

    1. the company that makes the model, if we hold its key (**preferred**);
    2. otherwise OpenRouter, if we hold *its* key;
    3. otherwise refuse, naming the environment variable that is missing.
    """
    env = env if env is not None else dict(os.environ)
    if ":" in spec:
        provider_name, _, model = spec.partition(":")
        provider = PROVIDERS.get(provider_name)
        if provider is None:
            raise ProviderError(f"unknown provider '{provider_name}'; known: {sorted(PROVIDERS)}")
        if not _have_key(provider, env):
            raise ProviderNotConfigured(
                f"{provider_name} was requested explicitly but ${provider.api_key_env} is not set"
            )
        return Route(provider, model, model, "direct")

    model = spec
    native = native_provider_for(model)
    if native and _have_key(PROVIDERS[native], env):
        return Route(PROVIDERS[native], model, model, "direct")

    router = PROVIDERS["openrouter"]
    if _have_key(router, env):
        slug = PROVIDERS[native].openrouter_slug if native else ""
        wire_model = f"{slug}/{model}" if slug and "/" not in model else model
        why = f"aggregator (no ${PROVIDERS[native].api_key_env})" if native else "aggregator (unknown vendor)"
        return Route(router, wire_model, model, why)

    missing = PROVIDERS[native].api_key_env if native else "OPENROUTER_API_KEY"
    raise ProviderNotConfigured(
        f"cannot run '{model}': set ${missing} for a direct call, or $OPENROUTER_API_KEY to route it "
        f"through the aggregator"
    )


# ------------------------------------------------------------------- sessions


class ModelSession:
    """A running conversation with one model.

    The session owns the message history in whatever shape its provider wants.
    The agent loop only ever calls the four methods below, which is what makes
    swapping providers a one-line change.
    """

    provider: str
    model: str
    route: Route

    def start(self, system_prompt: str, user_prompt: str) -> None:
        raise NotImplementedError

    async def next_turn(self, tools: list[ToolSpec]) -> ModelTurn:
        raise NotImplementedError

    def record_tool_result(self, call: ToolCall, result: Any) -> None:
        raise NotImplementedError

    async def aclose(self) -> None:
        raise NotImplementedError

    @property
    def messages(self) -> list:
        raise NotImplementedError


class OpenAICompatibleSession(ModelSession):
    """Chat Completions, as spoken by OpenAI and the providers that copy it.

    Retries are deliberate about time: a rate-limit retry is *not* charged to
    the model's decision latency, because it measures the queue we were put in,
    not how long the model took to choose. It is reported as
    ``provider_wait_ms`` so nothing is hidden.
    """

    MAX_ATTEMPTS = 4
    RETRY_STATUS = {408, 409, 429, 500, 502, 503, 504}

    def __init__(
        self,
        route: Route,
        api_key: str,
        *,
        temperature: float | None = None,
        reasoning_effort: str | None = None,
        max_output_tokens: int | None = None,
        timeout_s: float = 240.0,
        parallel_tool_calls: bool | None = True,
        tool_choice: str | None = "auto",
        endpoint_only: list[str] | None = None,
        pinned_quantization: str = "",
    ) -> None:
        self.route = route
        self.provider = route.provider.name
        self.model = route.requested_model
        self._wire_model = route.wire_model
        self._temperature = temperature
        self._reasoning_effort = reasoning_effort
        self._max_output_tokens = max_output_tokens
        self._parallel_tool_calls = parallel_tool_calls
        self._tool_choice = tool_choice
        self._endpoint_only = endpoint_only or []
        self._pinned_quantization = pinned_quantization
        self._messages: list[dict] = []
        headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}
        headers.update(route.provider.extra_headers)
        # A whole-request deadline, separate from httpx's timeouts. httpx's read
        # timeout measures the gap between bytes, and it resets every time one
        # arrives - so a provider that dribbles data can hold a request open
        # indefinitely without ever tripping it. One turn did exactly that and
        # hung a run for eight minutes with an aircraft airborne. asyncio's
        # wait_for is measured from the start of the request and cannot be
        # reset by the far end.
        self._deadline_s = timeout_s
        self._http = httpx.AsyncClient(
            base_url=route.provider.base_url,
            headers=headers,
            timeout=httpx.Timeout(connect=20.0, read=timeout_s, write=60.0, pool=60.0),
        )

    # -- conversation ------------------------------------------------------

    def start(self, system_prompt: str, user_prompt: str) -> None:
        self._messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ]

    def record_tool_result(self, call: ToolCall, result: Any) -> None:
        content = result if isinstance(result, str) else json.dumps(result, default=str)
        self._messages.append({"role": "tool", "tool_call_id": call.call_id, "content": content})

    @property
    def messages(self) -> list:
        return self._messages

    # -- the model call ----------------------------------------------------

    def _body(self, tools: list[ToolSpec]) -> dict:
        body: dict[str, Any] = {
            "model": self._wire_model,
            "messages": self._messages,
            "tools": [
                {
                    "type": "function",
                    "function": {"name": t.name, "description": t.description, "parameters": t.parameters},
                }
                for t in tools
            ],
        }
        if self._temperature is not None:
            body["temperature"] = self._temperature
        if self._reasoning_effort:
            body["reasoning_effort"] = self._reasoning_effort
        if self._max_output_tokens:
            body["max_completion_tokens"] = self._max_output_tokens
        # Sent ALWAYS, never inherited. Providers disagree on the default -
        # it is on for Grok and off for Qwen - so leaving it unset would make
        # "how many commands did the model issue at once" a property of the
        # vendor rather than of the model. An unstated default is a confound.
        if self._parallel_tool_calls is not None:
            body["parallel_tool_calls"] = self._parallel_tool_calls
        # Also always explicit. Note that some providers (GLM) support only
        # "auto" and cannot be made to call a tool, so "auto" is the only
        # setting every provider in the matrix can honour; anything else would
        # quietly mean different things to different models.
        if self._tool_choice:
            body["tool_choice"] = self._tool_choice
        if self._endpoint_only:
            # Tool support varies by SERVING ENDPOINT, not just by model: the
            # same model name is tool-capable on one host and tool-blind on
            # another. Without pinning, a run can score a capable model as
            # incapable - a false negative that would end up in the paper.
            body["provider"] = {"only": list(self._endpoint_only), "allow_fallbacks": False}
        return body

    async def _enrich_identity(self, turn: ModelTurn) -> None:
        """Ask an aggregator which upstream actually served this generation.

        A direct call needs no such question. Through an aggregator, "Qwen via
        OpenRouter" is not a documented model version: the same name can be
        served by different hosts at different weight precisions. This records
        the host, the upstream id and the quantization per call, so the paper
        can name what it actually measured.
        """
        if self.provider != "openrouter" or not turn.generation_id:
            return
        try:
            response = await self._http.get("/generation", params={"id": turn.generation_id})
            if response.status_code != 200:
                return
            data = (response.json() or {}).get("data") or {}
        except Exception:
            return
        turn.served_by = data.get("provider_name") or turn.served_by
        turn.upstream_id = data.get("upstream_id") or data.get("id") or ""
        turn.resolved_model = data.get("model") or turn.resolved_model
        turn.quantization = data.get("quantization") or self._pinned_quantization

    async def next_turn(self, tools: list[ToolSpec]) -> ModelTurn:
        body = self._body(tools)
        wait_ms = 0.0
        last_error = ""
        for attempt in range(1, self.MAX_ATTEMPTS + 1):
            clock = time.perf_counter()
            try:
                response = await asyncio.wait_for(
                    self._http.post("/chat/completions", json=body), timeout=self._deadline_s
                )
            except (httpx.HTTPError, asyncio.TimeoutError) as e:
                last_error = (
                    f"no reply within {self._deadline_s:.0f}s"
                    if isinstance(e, asyncio.TimeoutError)
                    else f"{type(e).__name__}: {e}"
                )
                wait_ms += (time.perf_counter() - clock) * 1000
            else:
                latency_ms = (time.perf_counter() - clock) * 1000
                if response.status_code == 200:
                    turn = self._parse(response.json(), latency_ms, wait_ms, attempt)
                    await self._enrich_identity(turn)
                    return turn
                last_error = f"HTTP {response.status_code}: {response.text[:400]}"
                wait_ms += latency_ms
                lowered = response.text.lower()
                if response.status_code == 402 or any(m in lowered for m in QUOTA_MARKERS):
                    raise ProviderQuotaError(f"{self.route.label}: {last_error}")
                if response.status_code not in self.RETRY_STATUS:
                    break
            if attempt < self.MAX_ATTEMPTS:
                backoff = min(2.0**attempt, 20.0)
                await asyncio.sleep(backoff)
                wait_ms += backoff * 1000
        raise ProviderError(f"{self.route.label}: {last_error}")

    def _parse(self, payload: dict, latency_ms: float, wait_ms: float, attempts: int) -> ModelTurn:
        choice = (payload.get("choices") or [{}])[0]
        message = choice.get("message") or {}
        # Keep the assistant message verbatim: providers reject a history whose
        # tool_calls have been reshaped, and the transcript should show exactly
        # what the model emitted.
        self._messages.append({k: v for k, v in message.items() if v is not None})

        calls: list[ToolCall] = []
        for raw in message.get("tool_calls") or []:
            fn = raw.get("function") or {}
            text = fn.get("arguments") or "{}"
            try:
                arguments = json.loads(text) if text.strip() else {}
                parse_error = None
                if not isinstance(arguments, dict):
                    arguments, parse_error = {}, f"arguments were {type(arguments).__name__}, not an object"
            except json.JSONDecodeError as e:
                arguments, parse_error = {}, f"invalid JSON: {e}"
            calls.append(
                ToolCall(
                    call_id=raw.get("id") or f"call_{len(calls)}",
                    name=fn.get("name") or "",
                    arguments=arguments,
                    raw_arguments=text,
                    parse_error=parse_error,
                )
            )

        usage = payload.get("usage") or {}
        details = usage.get("completion_tokens_details") or {}
        prompt_details = usage.get("prompt_tokens_details") or {}
        provider_meta = payload.get("provider") or ""
        return ModelTurn(
            resolved_model=payload.get("model") or self._wire_model,
            generation_id=payload.get("id") or "",
            served_by=provider_meta if isinstance(provider_meta, str) else "",
            text=message.get("content") or "",
            tool_calls=calls,
            finish_reason=choice.get("finish_reason") or "",
            decision_latency_ms=latency_ms,
            provider_wait_ms=wait_ms,
            attempts=attempts,
            input_tokens=int(usage.get("prompt_tokens") or 0),
            cached_input_tokens=int(prompt_details.get("cached_tokens") or 0),
            output_tokens=int(usage.get("completion_tokens") or 0),
            reasoning_tokens=int(details.get("reasoning_tokens") or 0),
        )

    async def aclose(self) -> None:
        await self._http.aclose()


class AnthropicSession(ModelSession):
    """Anthropic's Messages API, which is its own shape rather than OpenAI's.

    Four differences matter enough to name:

    1. **The system prompt is a top-level field**, not a message.
    2. **Tool results go back as a *user* message** containing one
       ``tool_result`` block per call - and every call in an assistant turn
       must be answered in a *single* user message. Sending one message per
       result is rejected. This class buffers them and flushes on the next
       turn.
    3. **The assistant's content is a list of blocks**, and it is stored back
       verbatim. That matters beyond tidiness: with extended thinking enabled,
       dropping the thinking blocks silently degrades tool use.
    4. **``max_tokens`` is required**, not optional.

    Prompt caching is requested explicitly. The drone server publishes 98 tool
    schemas - roughly 22,000 tokens re-sent every single turn - and without a
    cache breakpoint on the tools and the system prompt, a mission costs
    something like an order of magnitude more than it should.
    """

    MAX_ATTEMPTS = 4
    RETRY_STATUS = {408, 409, 429, 500, 502, 503, 504, 529}
    API_VERSION = "2023-06-01"

    def __init__(
        self,
        route: Route,
        api_key: str,
        *,
        temperature: float | None = None,
        reasoning_effort: str | None = None,
        max_output_tokens: int | None = None,
        timeout_s: float = 240.0,
        parallel_tool_calls: bool | None = True,
        tool_choice: str | None = "auto",
        endpoint_only: list[str] | None = None,
        pinned_quantization: str = "",
    ) -> None:
        self.route = route
        self.provider = route.provider.name
        self.model = route.requested_model
        self._wire_model = route.wire_model
        self._temperature = temperature
        self._reasoning_effort = reasoning_effort
        self._max_output_tokens = max_output_tokens or 8192
        self._parallel_tool_calls = parallel_tool_calls
        self._tool_choice = tool_choice or "auto"
        self._pinned_quantization = pinned_quantization
        self._system = ""
        self._messages: list[dict] = []
        self._pending_results: list[dict] = []
        self._deadline_s = timeout_s
        self._http = httpx.AsyncClient(
            base_url=route.provider.base_url,
            headers={
                "x-api-key": api_key,
                "anthropic-version": self.API_VERSION,
                "content-type": "application/json",
            },
            timeout=httpx.Timeout(connect=20.0, read=timeout_s, write=60.0, pool=60.0),
        )

    def start(self, system_prompt: str, user_prompt: str) -> None:
        self._system = system_prompt
        self._messages = [{"role": "user", "content": user_prompt}]
        self._pending_results = []

    def record_tool_result(self, call: ToolCall, result: Any) -> None:
        content = result if isinstance(result, str) else json.dumps(result, default=str)
        self._pending_results.append({"type": "tool_result", "tool_use_id": call.call_id, "content": content})

    @property
    def messages(self) -> list:
        return self._messages

    def _flush_results(self) -> None:
        if self._pending_results:
            self._messages.append({"role": "user", "content": self._pending_results})
            self._pending_results = []

    def _body(self, tools: list[ToolSpec]) -> dict:
        wire_tools = [{"name": t.name, "description": t.description, "input_schema": t.parameters} for t in tools]
        if wire_tools:
            # One breakpoint at the end of the tool list caches all of them.
            wire_tools[-1] = {**wire_tools[-1], "cache_control": {"type": "ephemeral"}}
        body: dict[str, Any] = {
            "model": self._wire_model,
            "max_tokens": self._max_output_tokens,
            "system": [{"type": "text", "text": self._system, "cache_control": {"type": "ephemeral"}}],
            "messages": self._messages,
            "tools": wire_tools,
            # Always explicit, like every other provider in the matrix.
            "tool_choice": {
                "type": self._tool_choice,
                "disable_parallel_tool_use": not self._parallel_tool_calls,
            },
        }
        if self._temperature is not None:
            body["temperature"] = self._temperature
        if self._reasoning_effort:
            # Extended thinking needs a token budget, and temperature must be
            # left alone when it is on.
            budget = {"low": 2048, "medium": 8192, "high": 16384}.get(self._reasoning_effort, 8192)
            body["thinking"] = {"type": "enabled", "budget_tokens": budget}
            body["max_tokens"] = max(self._max_output_tokens, budget + 4096)
            body.pop("temperature", None)
            body["tool_choice"] = {"type": "auto"}  # parallel toggle is invalid with thinking
        return body

    async def next_turn(self, tools: list[ToolSpec]) -> ModelTurn:
        self._flush_results()
        body = self._body(tools)
        wait_ms = 0.0
        last_error = ""
        for attempt in range(1, self.MAX_ATTEMPTS + 1):
            clock = time.perf_counter()
            try:
                response = await asyncio.wait_for(self._http.post("/messages", json=body), timeout=self._deadline_s)
            except (httpx.HTTPError, asyncio.TimeoutError) as e:
                last_error = (
                    f"no reply within {self._deadline_s:.0f}s"
                    if isinstance(e, asyncio.TimeoutError)
                    else f"{type(e).__name__}: {e}"
                )
                wait_ms += (time.perf_counter() - clock) * 1000
            else:
                latency_ms = (time.perf_counter() - clock) * 1000
                if response.status_code == 200:
                    return self._parse(response.json(), latency_ms, wait_ms, attempt)
                last_error = f"HTTP {response.status_code}: {response.text[:400]}"
                wait_ms += latency_ms
                lowered = response.text.lower()
                if response.status_code == 402 or any(m in lowered for m in QUOTA_MARKERS):
                    raise ProviderQuotaError(f"{self.route.label}: {last_error}")
                if response.status_code not in self.RETRY_STATUS:
                    break
            if attempt < self.MAX_ATTEMPTS:
                backoff = min(2.0**attempt, 20.0)
                await asyncio.sleep(backoff)
                wait_ms += backoff * 1000
        raise ProviderError(f"{self.route.label}: {last_error}")

    def _parse(self, payload: dict, latency_ms: float, wait_ms: float, attempts: int) -> ModelTurn:
        blocks = payload.get("content") or []
        # Verbatim, blocks and all - see point 3 in the class docstring.
        self._messages.append({"role": "assistant", "content": blocks})

        text_parts, calls = [], []
        for block in blocks:
            if block.get("type") == "text":
                text_parts.append(block.get("text") or "")
            elif block.get("type") == "tool_use":
                arguments = block.get("input")
                calls.append(
                    ToolCall(
                        call_id=block.get("id") or f"call_{len(calls)}",
                        name=block.get("name") or "",
                        arguments=arguments if isinstance(arguments, dict) else {},
                        raw_arguments=json.dumps(arguments, default=str),
                        parse_error=None if isinstance(arguments, dict) else "arguments were not an object",
                    )
                )

        usage = payload.get("usage") or {}
        cached = int(usage.get("cache_read_input_tokens") or 0)
        written = int(usage.get("cache_creation_input_tokens") or 0)
        return ModelTurn(
            text="\n".join(p for p in text_parts if p),
            tool_calls=calls,
            finish_reason=payload.get("stop_reason") or "",
            decision_latency_ms=latency_ms,
            provider_wait_ms=wait_ms,
            attempts=attempts,
            # Anthropic reports fresh, cache-read and cache-write separately;
            # the harness's "input_tokens" means everything that went in.
            input_tokens=int(usage.get("input_tokens") or 0) + cached + written,
            cached_input_tokens=cached,
            output_tokens=int(usage.get("output_tokens") or 0),
            resolved_model=payload.get("model") or self._wire_model,
            generation_id=payload.get("id") or "",
        )

    async def aclose(self) -> None:
        await self._http.aclose()


#: Wire format -> the class that speaks it. Anthropic's and Google's formats
#: are registered in PROVIDERS for routing, but have no adapter yet; asking for
#: one raises a message that says exactly what to write, rather than silently
#: doing something else.
WIRES: dict[str, type[ModelSession]] = {
    "openai": OpenAICompatibleSession,
    "anthropic": AnthropicSession,
}


async def list_openrouter_endpoints(model: str, api_key: str = "", timeout_s: float = 30.0) -> list[dict]:
    """Which hosts serve this model on OpenRouter, and what each supports.

    Needed because **tool-calling support varies by serving endpoint, not just
    by model**: the same model name is tool-capable on one host and tool-blind
    on another. Pick a host from this list and pin it (``endpoint_only``), or a
    run can score a capable model as incapable.
    """
    slug = model if "/" in model else model
    headers = {"Authorization": f"Bearer {api_key}"} if api_key else {}
    async with httpx.AsyncClient(timeout=timeout_s) as http:
        response = await http.get(f"https://openrouter.ai/api/v1/models/{slug}/endpoints", headers=headers)
        response.raise_for_status()
        data = (response.json() or {}).get("data") or {}
    return [
        {
            "provider_name": e.get("provider_name") or e.get("name"),
            "tag": e.get("tag") or e.get("provider_name"),
            "quantization": e.get("quantization") or "unknown",
            "supports_tools": "tools" in (e.get("supported_parameters") or []),
            "context_length": e.get("context_length"),
        }
        for e in (data.get("endpoints") or [])
    ]


def open_session(spec: str, *, env: dict | None = None, **options) -> ModelSession:
    """Open a conversation with the model named by ``spec``.

    ``spec`` is ``"provider:model"`` or a bare model name (see
    :func:`resolve_model`).
    """
    from droneserver.llm.spend import check_not_retired

    env = env if env is not None else dict(os.environ)
    route = resolve_model(spec, env)
    check_not_retired(route.requested_model)
    session_class = WIRES.get(route.provider.wire)
    if session_class is None:
        raise ProviderError(
            f"{route.provider.name} speaks the '{route.provider.wire}' wire format, for which no adapter is "
            f"written yet. Add a ModelSession subclass implementing start/next_turn/record_tool_result and "
            f"register it in providers.WIRES. Until then, route this model through OpenRouter by setting "
            f"$OPENROUTER_API_KEY and asking for '{route.requested_model}' without a provider prefix."
        )
    return session_class(route, env[route.provider.api_key_env].strip(), **options)
