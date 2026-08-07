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
    "google": ProviderSpec(
        "google", "google", "https://generativelanguage.googleapis.com/v1beta", "GOOGLE_API_KEY", "google"
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
        timeout_s: float = 300.0,
        parallel_tool_calls: bool | None = None,
    ) -> None:
        self.route = route
        self.provider = route.provider.name
        self.model = route.requested_model
        self._wire_model = route.wire_model
        self._temperature = temperature
        self._reasoning_effort = reasoning_effort
        self._max_output_tokens = max_output_tokens
        self._parallel_tool_calls = parallel_tool_calls
        self._messages: list[dict] = []
        headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}
        headers.update(route.provider.extra_headers)
        self._http = httpx.AsyncClient(
            base_url=route.provider.base_url, headers=headers, timeout=httpx.Timeout(timeout_s)
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
        if self._parallel_tool_calls is not None:
            body["parallel_tool_calls"] = self._parallel_tool_calls
        return body

    async def next_turn(self, tools: list[ToolSpec]) -> ModelTurn:
        body = self._body(tools)
        wait_ms = 0.0
        last_error = ""
        for attempt in range(1, self.MAX_ATTEMPTS + 1):
            clock = time.perf_counter()
            try:
                response = await self._http.post("/chat/completions", json=body)
            except httpx.HTTPError as e:
                last_error = f"{type(e).__name__}: {e}"
                wait_ms += (time.perf_counter() - clock) * 1000
            else:
                latency_ms = (time.perf_counter() - clock) * 1000
                if response.status_code == 200:
                    return self._parse(response.json(), latency_ms, wait_ms, attempt)
                last_error = f"HTTP {response.status_code}: {response.text[:400]}"
                wait_ms += latency_ms
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
        return ModelTurn(
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


#: Wire format -> the class that speaks it. Anthropic's and Google's formats
#: are registered in PROVIDERS for routing, but have no adapter yet; asking for
#: one raises a message that says exactly what to write, rather than silently
#: doing something else.
WIRES: dict[str, type[ModelSession]] = {"openai": OpenAICompatibleSession}


def open_session(spec: str, *, env: dict | None = None, **options) -> ModelSession:
    """Open a conversation with the model named by ``spec``.

    ``spec`` is ``"provider:model"`` or a bare model name (see
    :func:`resolve_model`).
    """
    env = env if env is not None else dict(os.environ)
    route = resolve_model(spec, env)
    session_class = WIRES.get(route.provider.wire)
    if session_class is None:
        raise ProviderError(
            f"{route.provider.name} speaks the '{route.provider.wire}' wire format, for which no adapter is "
            f"written yet. Add a ModelSession subclass implementing start/next_turn/record_tool_result and "
            f"register it in providers.WIRES. Until then, route this model through OpenRouter by setting "
            f"$OPENROUTER_API_KEY and asking for '{route.requested_model}' without a provider prefix."
        )
    return session_class(route, env[route.provider.api_key_env].strip(), **options)
