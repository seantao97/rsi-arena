"""Async OpenRouter client.

One endpoint does everything: ``POST https://openrouter.ai/api/v1/chat/completions``.
What this module adds on top of it:

* **Retries and timeouts** — via :mod:`rsi_arena.retry`, which knows that 400,
  401 and 402 are permanent and that ``Retry-After`` beats guessing.
* **Rate limiting** — a shared token bucket plus a concurrency ceiling, so
  ``complete_many`` over 200 prompts does not open 200 sockets.
* **Caching** — every request is content-addressed through
  :mod:`rsi_arena.cache`; identical requests never leave the process twice,
  and concurrent identical requests collapse into one via single-flight.
* **Structured outputs** — a pydantic model in, a validated instance out,
  using ``response_format: {"type": "json_schema", ...}`` with ``strict``.
* **Web search** — either the ``:online`` model suffix or the ``web`` plugin,
  with the returned ``url_citation`` annotations lifted onto the result.
* **Streaming** — SSE, yielding deltas, with usage and cost arriving on the
  final chunk exactly as OpenRouter sends them.
* **Cost** — ``usage.cost`` is read straight off the response (OpenRouter
  reports it on every response now; the old ``usage: {include: true}`` opt-in
  is deprecated and ignored), falling back to a price table only if absent.

Everything is a pydantic model so a whole conversation can be dropped into a
trace and read back out.
"""

from __future__ import annotations

import asyncio
import json
import os
import time
from typing import Any, AsyncIterator, Iterable, Literal, Sequence, TypeVar

import httpx
from pydantic import BaseModel, Field

from .cache import Cache, default_cache, make_key
from .costs import Cost, Pricing, Usage
from .ratelimit import RateLimit, RateLimiter
from .retry import RetryPolicy, with_retry
from .trace import Tracer, current_span

DEFAULT_BASE_URL = "https://openrouter.ai/api/v1"
DEFAULT_MODEL = "anthropic/claude-sonnet-4.5"

T = TypeVar("T", bound=BaseModel)


# --- errors -----------------------------------------------------------------


class OpenRouterError(RuntimeError):
    """An error from OpenRouter, carrying enough to decide whether to retry.

    Documented codes: 400 bad params, 401 bad key, 402 out of credits, 403
    moderation, 408 timeout, 429 rate limited, 502 model down, 503 no provider
    meets the routing requirements.
    """

    def __init__(
        self,
        status: int | None,
        message: str,
        metadata: dict[str, Any] | None = None,
        retry_after: float | None = None,
    ) -> None:
        super().__init__(f"[{status}] {message}")
        self.status = status
        self.message = message
        self.metadata = metadata or {}
        self.retry_after = retry_after

    @property
    def retryable(self) -> bool:
        # 402 is explicitly not retryable: no amount of waiting adds credits.
        return self.status in {408, 409, 425, 429, 500, 502, 503, 504}


# --- messages ---------------------------------------------------------------


class FunctionCall(BaseModel):
    name: str
    arguments: str = "{}"

    def parsed_arguments(self) -> dict[str, Any]:
        try:
            value = json.loads(self.arguments or "{}")
        except json.JSONDecodeError:
            return {}
        return value if isinstance(value, dict) else {"value": value}


class ToolCall(BaseModel):
    id: str = ""
    type: str = "function"
    function: FunctionCall


class Citation(BaseModel):
    """One ``url_citation`` annotation returned by the web plugin."""

    url: str = ""
    title: str = ""
    content: str = ""
    start_index: int | None = None
    end_index: int | None = None


class Message(BaseModel):
    role: Literal["system", "user", "assistant", "tool"]
    content: str | list[dict[str, Any]] | None = None
    name: str | None = None
    tool_calls: list[ToolCall] | None = None
    tool_call_id: str | None = None

    def to_wire(self) -> dict[str, Any]:
        out: dict[str, Any] = {"role": self.role, "content": self.content or ""}
        if self.name:
            out["name"] = self.name
        if self.tool_calls:
            out["tool_calls"] = [tc.model_dump(exclude_none=True) for tc in self.tool_calls]
        if self.tool_call_id:
            out["tool_call_id"] = self.tool_call_id
        return out


Prompt = str | Message | Sequence[Message] | Sequence[dict[str, Any]]


def to_messages(prompt: Prompt, system: str | None = None) -> list[Message]:
    """Accept a bare string, a Message, or a list of either, and normalise."""
    if isinstance(prompt, str):
        messages = [Message(role="user", content=prompt)]
    elif isinstance(prompt, Message):
        messages = [prompt]
    else:
        messages = [m if isinstance(m, Message) else Message(**m) for m in prompt]
    if system and not any(m.role == "system" for m in messages):
        messages = [Message(role="system", content=system), *messages]
    return messages


# --- request configuration --------------------------------------------------


class WebSearch(BaseModel):
    """Web search settings, mapped onto OpenRouter's ``web`` plugin.

    ``:online`` on the model slug is shorthand for this plugin with defaults;
    we always send the explicit plugin instead, because the defaults (5 Exa
    results) are a cost decision worth making on purpose. Exa bills about
    $0.007 per request for the first 10 results and $0.001 per result after.
    """

    max_results: int = 5
    engine: Literal["native", "exa", "firecrawl", "parallel", "perplexity"] | None = None
    search_prompt: str | None = None
    include_domains: list[str] | None = None
    exclude_domains: list[str] | None = None

    def to_plugin(self) -> dict[str, Any]:
        plugin: dict[str, Any] = {"id": "web", "max_results": self.max_results}
        if self.engine:
            plugin["engine"] = self.engine
        if self.search_prompt:
            plugin["search_prompt"] = self.search_prompt
        if self.include_domains:
            plugin["include_domains"] = self.include_domains
        if self.exclude_domains:
            plugin["exclude_domains"] = self.exclude_domains
        return plugin


class LLMConfig(BaseModel):
    """Defaults for every call. An agent owns one; a call may override any field."""

    model: str = DEFAULT_MODEL
    fallback_models: list[str] = Field(default_factory=list)
    temperature: float | None = None
    top_p: float | None = None
    max_tokens: int | None = None
    seed: int | None = None
    stop: list[str] | None = None
    reasoning: dict[str, Any] | None = None
    provider: dict[str, Any] | None = None
    timeout_s: float = 120.0
    cache: bool = True
    cache_ttl_s: float | None = None
    web_search: WebSearch | bool = False
    extra_body: dict[str, Any] = Field(default_factory=dict)

    def merged(self, **overrides: Any) -> "LLMConfig":
        clean = {k: v for k, v in overrides.items() if v is not None}
        return self.model_copy(update=clean)


# --- results ----------------------------------------------------------------


class Completion(BaseModel):
    """One response. ``parsed`` is populated when a schema was requested."""

    id: str = ""
    model: str = ""
    text: str = ""
    tool_calls: list[ToolCall] = Field(default_factory=list)
    finish_reason: str | None = None
    native_finish_reason: str | None = None
    citations: list[Citation] = Field(default_factory=list)
    usage: Usage = Field(default_factory=Usage)
    cost: Cost = Field(default_factory=Cost)
    cached: bool = False
    latency_s: float = 0.0
    attempts: int = 1
    parsed: dict[str, Any] | None = None
    raw: dict[str, Any] | None = None

    @property
    def message(self) -> Message:
        return Message(role="assistant", content=self.text, tool_calls=self.tool_calls or None)

    def json_output(self) -> Any:
        """The response body parsed as JSON, tolerating a fenced code block."""
        if self.parsed is not None:
            return self.parsed
        return parse_json_loose(self.text)


class StreamEvent(BaseModel):
    """One event from :meth:`LLMClient.stream`.

    ``delta`` events carry incremental text; exactly one ``done`` event
    arrives last and carries the assembled :class:`Completion` with usage and
    cost, which OpenRouter puts in the final SSE message.
    """

    type: Literal["delta", "tool_call", "done"]
    text: str = ""
    tool_call: ToolCall | None = None
    completion: Completion | None = None


# --- JSON helpers -----------------------------------------------------------


def parse_json_loose(text: str) -> Any:
    """Parse JSON that may be wrapped in prose or a ```json fence.

    Strict structured outputs make this unnecessary, but not every model or
    provider endpoint honours ``strict``, and a fenced block is the single
    most common way the contract gets broken.
    """
    text = (text or "").strip()
    if not text:
        raise ValueError("empty response")
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass
    if "```" in text:
        block = text.split("```", 2)[1]
        block = block.split("\n", 1)[1] if block.lower().startswith("json") else block
        try:
            return json.loads(block.strip())
        except json.JSONDecodeError:
            pass
    for opener, closer in (("{", "}"), ("[", "]")):
        start, end = text.find(opener), text.rfind(closer)
        if start != -1 and end > start:
            try:
                return json.loads(text[start : end + 1])
            except json.JSONDecodeError:
                continue
    raise ValueError(f"could not parse JSON from response: {text[:200]!r}")


def strict_schema(model: type[BaseModel]) -> dict[str, Any]:
    """Turn a pydantic model into a schema that satisfies ``strict`` mode.

    Strict requires every object to set ``additionalProperties: false`` and to
    list every property in ``required`` — including ones pydantic left out
    because they have defaults. Providers reject the schema otherwise, and the
    rejection is a 400 that no retry will fix.
    """
    schema = model.model_json_schema()

    def walk(node: Any) -> Any:
        if isinstance(node, dict):
            node = {k: walk(v) for k, v in node.items()}
            if node.get("type") == "object" and "properties" in node:
                node["additionalProperties"] = False
                node["required"] = list(node["properties"].keys())
            return node
        if isinstance(node, list):
            return [walk(v) for v in node]
        return node

    return walk(schema)


def response_format_for(model: type[BaseModel]) -> dict[str, Any]:
    return {
        "type": "json_schema",
        "json_schema": {
            "name": model.__name__,
            "strict": True,
            "schema": strict_schema(model),
        },
    }


# --- client -----------------------------------------------------------------


class LLMClient:
    """Async OpenRouter client. One per process is plenty; it is safe to share.

    ``async with LLMClient() as llm:`` closes the underlying HTTP pool. The
    client owns its rate limiter, so every call made through the same instance
    shares one budget — which is what you want when a dozen agents run at once.
    """

    def __init__(
        self,
        api_key: str | None = None,
        *,
        base_url: str = DEFAULT_BASE_URL,
        config: LLMConfig | None = None,
        cache: Cache | None = None,
        rate_limit: RateLimit | RateLimiter | None = None,
        retry: RetryPolicy | None = None,
        app_url: str | None = None,
        app_title: str | None = "RSI Arena",
        auto_pricing: bool = True,
        http_client: httpx.AsyncClient | None = None,
    ) -> None:
        self.api_key = api_key or os.environ.get("OPENROUTER_API_KEY", "")
        self.base_url = base_url.rstrip("/")
        self.config = config or LLMConfig()
        self.cache = cache if cache is not None else default_cache()
        self.retry = retry or RetryPolicy()
        self.auto_pricing = auto_pricing
        self.app_url = app_url or os.environ.get("OPENROUTER_APP_URL", "")
        self.app_title = app_title or ""
        if isinstance(rate_limit, RateLimiter):
            self.limiter = rate_limit
        else:
            self.limiter = (rate_limit or RateLimit(per_second=8, burst=16, concurrency=8)).build()
        self._client = http_client
        self._owns_client = http_client is None
        self._pricing: dict[str, Pricing] | None = None
        self._pricing_lock = asyncio.Lock()

    # -- lifecycle --

    def _http(self) -> httpx.AsyncClient:
        if self._client is None:
            self._client = httpx.AsyncClient(
                timeout=httpx.Timeout(self.config.timeout_s, connect=10.0),
                headers=self._headers(),
                follow_redirects=True,
            )
        return self._client

    def _headers(self) -> dict[str, str]:
        if not self.api_key:
            # Fail here with the fix in the message rather than sending
            # "Bearer " and getting an opaque 401 back from the provider.
            raise OpenRouterError(None, "OPENROUTER_API_KEY is not set in the environment")
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }
        # Attribution headers are optional but get the app listed on
        # openrouter.ai; harmless when empty, so only send what we have.
        if self.app_url:
            headers["HTTP-Referer"] = self.app_url
        if self.app_title:
            headers["X-Title"] = self.app_title
        return headers

    async def close(self) -> None:
        if self._client is not None and self._owns_client:
            await self._client.aclose()
            self._client = None

    async def __aenter__(self) -> "LLMClient":
        return self

    async def __aexit__(self, *exc_info: object) -> None:
        await self.close()

    # -- request assembly --

    def build_body(
        self,
        messages: list[Message],
        config: LLMConfig,
        *,
        tools: list[dict[str, Any]] | None = None,
        tool_choice: str | dict[str, Any] | None = None,
        response_format: dict[str, Any] | None = None,
        stream: bool = False,
    ) -> dict[str, Any]:
        body: dict[str, Any] = {
            "model": config.model,
            "messages": [m.to_wire() for m in messages],
        }
        if config.fallback_models:
            # OpenRouter walks this list when the primary model is down (502)
            # or has no provider meeting the routing requirements (503).
            body["models"] = [config.model, *config.fallback_models]
        for field in ("temperature", "top_p", "max_tokens", "seed", "stop", "reasoning"):
            value = getattr(config, field)
            if value is not None:
                body[field] = value

        provider = dict(config.provider or {})
        if response_format is not None:
            body["response_format"] = response_format
            # Only route to endpoints that actually implement the parameter.
            # Support is per-endpoint, not per-model: the same model served by
            # a different provider may silently ignore the schema.
            provider.setdefault("require_parameters", True)
        if provider:
            body["provider"] = provider

        if tools:
            body["tools"] = tools
            if tool_choice is not None:
                body["tool_choice"] = tool_choice

        web = config.web_search
        if web:
            plugin = (web if isinstance(web, WebSearch) else WebSearch()).to_plugin()
            body["plugins"] = [*body.get("plugins", []), plugin]

        if stream:
            body["stream"] = True
        body.update(config.extra_body)
        return body

    # -- core call --

    async def complete(
        self,
        prompt: Prompt,
        *,
        system: str | None = None,
        model: str | None = None,
        tools: list[dict[str, Any]] | None = None,
        tool_choice: str | dict[str, Any] | None = None,
        schema: type[BaseModel] | dict[str, Any] | None = None,
        web_search: WebSearch | bool | None = None,
        temperature: float | None = None,
        max_tokens: int | None = None,
        cache: bool | None = None,
        tracer: Tracer | None = None,
        **overrides: Any,
    ) -> Completion:
        """One chat completion, with retries, caching and cost accounting.

        ``schema`` may be a pydantic model (validated into ``parsed``) or a raw
        JSON Schema dict.
        """
        config = self.config.merged(
            model=model,
            temperature=temperature,
            max_tokens=max_tokens,
            web_search=web_search,
            cache=cache,
            **overrides,
        )
        messages = to_messages(prompt, system=system)

        response_format = None
        pydantic_model: type[BaseModel] | None = None
        if schema is not None:
            if isinstance(schema, type) and issubclass(schema, BaseModel):
                pydantic_model = schema
                response_format = response_format_for(schema)
            else:
                response_format = schema

        body = self.build_body(
            messages,
            config,
            tools=tools,
            tool_choice=tool_choice,
            response_format=response_format,
        )
        completion = await self._dispatch(body, config, tracer=tracer)
        if pydantic_model is not None:
            completion.parsed = self._validate(completion, pydantic_model)
        return completion

    def _validate(self, completion: Completion, model: type[BaseModel]) -> dict[str, Any]:
        data = parse_json_loose(completion.text)
        return model.model_validate(data).model_dump()

    async def structured(
        self,
        prompt: Prompt,
        schema: type[T],
        *,
        repair_attempts: int = 1,
        **kwargs: Any,
    ) -> tuple[T, Completion]:
        """Structured output as a validated instance, with a repair round.

        ``strict`` is not honoured identically by every provider endpoint, so a
        model can still hand back something that parses as JSON but fails the
        schema. Rather than fail the step, we show the model its own output and
        the validation error once — which fixes it in nearly every case and is
        much cheaper than re-running the whole prompt from scratch.
        """
        messages = to_messages(prompt, system=kwargs.pop("system", None))
        last_error: Exception | None = None
        for attempt in range(repair_attempts + 1):
            completion = await self.complete(messages, schema=schema, **kwargs)
            try:
                return schema.model_validate(parse_json_loose(completion.text)), completion
            except Exception as exc:  # noqa: BLE001 - fed back to the model
                last_error = exc
                if attempt >= repair_attempts:
                    break
                messages = [
                    *messages,
                    Message(role="assistant", content=completion.text),
                    Message(
                        role="user",
                        content=(
                            f"That response failed schema validation: {exc}\n"
                            "Reply with corrected JSON only, matching the schema exactly."
                        ),
                    ),
                ]
        raise ValueError(f"structured output failed validation: {last_error}")

    async def complete_many(
        self,
        prompts: Iterable[Prompt | dict[str, Any]],
        *,
        return_exceptions: bool = False,
        **kwargs: Any,
    ) -> list[Completion]:
        """Run many completions concurrently.

        Concurrency is bounded by the client's rate limiter, not by this call,
        so passing a thousand prompts is fine — they queue rather than
        stampede. Each item may be a prompt or a dict of ``complete`` kwargs.
        """

        async def one(item: Prompt | dict[str, Any]) -> Completion:
            if isinstance(item, dict) and "prompt" in item:
                params = {**kwargs, **item}
                return await self.complete(params.pop("prompt"), **params)
            return await self.complete(item, **kwargs)  # type: ignore[arg-type]

        return await asyncio.gather(
            *(one(item) for item in prompts), return_exceptions=return_exceptions
        )  # type: ignore[return-value]

    # -- streaming --

    async def stream(
        self,
        prompt: Prompt,
        *,
        system: str | None = None,
        model: str | None = None,
        tools: list[dict[str, Any]] | None = None,
        schema: type[BaseModel] | dict[str, Any] | None = None,
        web_search: WebSearch | bool | None = None,
        cache: bool | None = None,
        tracer: Tracer | None = None,
        **overrides: Any,
    ) -> AsyncIterator[StreamEvent]:
        """Stream a completion as :class:`StreamEvent` values.

        A cache hit replays as a single ``delta`` followed by ``done``, so a
        caller written against the stream never has to know whether the tokens
        came off the wire or out of the cache.
        """
        config = self.config.merged(model=model, web_search=web_search, cache=cache, **overrides)
        messages = to_messages(prompt, system=system)
        response_format = None
        if schema is not None:
            response_format = (
                response_format_for(schema)
                if isinstance(schema, type) and issubclass(schema, BaseModel)
                else schema
            )
        body = self.build_body(
            messages, config, tools=tools, response_format=response_format, stream=True
        )

        span_tracer = tracer
        key = make_key("llm", body)
        if config.cache:
            hit = await self.cache.get(key)
            if hit is not None:
                completion = Completion.model_validate(hit)
                completion.cached = True
                completion.cost = completion.cost.model_copy(update={"usd": 0.0, "cached": True})
                self._record(span_tracer, completion, config)
                yield StreamEvent(type="delta", text=completion.text)
                yield StreamEvent(type="done", completion=completion)
                return

        started = time.monotonic()
        text_parts: list[str] = []
        tool_parts: dict[int, dict[str, Any]] = {}
        final: dict[str, Any] = {}

        await self.limiter.acquire()
        try:
            async with self._http().stream(
                "POST",
                f"{self.base_url}/chat/completions",
                json=body,
                headers=self._headers(),
                timeout=httpx.Timeout(config.timeout_s, connect=10.0),
            ) as response:
                if response.status_code >= 400:
                    payload = await response.aread()
                    raise self._error_from(response.status_code, payload, dict(response.headers))
                async for line in response.aiter_lines():
                    if not line or line.startswith(":"):
                        # ": OPENROUTER PROCESSING" keepalive comments.
                        continue
                    if not line.startswith("data: "):
                        continue
                    data = line[6:].strip()
                    if data == "[DONE]":
                        break
                    try:
                        chunk = json.loads(data)
                    except json.JSONDecodeError:
                        continue
                    if "error" in chunk and chunk["error"]:
                        err = chunk["error"]
                        raise OpenRouterError(
                            err.get("code"), err.get("message", "stream error"), err.get("metadata")
                        )
                    final = chunk
                    for choice in chunk.get("choices") or []:
                        delta = choice.get("delta") or {}
                        piece = delta.get("content")
                        if piece:
                            text_parts.append(piece)
                            yield StreamEvent(type="delta", text=piece)
                        for tc in delta.get("tool_calls") or []:
                            index = tc.get("index", 0)
                            slot = tool_parts.setdefault(
                                index, {"id": "", "function": {"name": "", "arguments": ""}}
                            )
                            if tc.get("id"):
                                slot["id"] = tc["id"]
                            fn = tc.get("function") or {}
                            if fn.get("name"):
                                slot["function"]["name"] = fn["name"]
                            if fn.get("arguments"):
                                slot["function"]["arguments"] += fn["arguments"]
        finally:
            self.limiter.release()

        completion = self._completion_from(final, "".join(text_parts))
        completion.tool_calls = [
            ToolCall(id=v["id"], function=FunctionCall(**v["function"]))
            for _, v in sorted(tool_parts.items())
        ]
        completion.latency_s = time.monotonic() - started
        for choice in final.get("choices") or []:
            completion.finish_reason = choice.get("finish_reason") or completion.finish_reason
        await self._finalise_cost(completion, config)
        if config.cache:
            await self.cache.set(key, completion.model_dump(), ttl=config.cache_ttl_s)
        for tc in completion.tool_calls:
            yield StreamEvent(type="tool_call", tool_call=tc)
        self._record(span_tracer, completion, config)
        yield StreamEvent(type="done", completion=completion)

    # -- plumbing --

    async def _dispatch(
        self, body: dict[str, Any], config: LLMConfig, tracer: Tracer | None
    ) -> Completion:
        key = make_key("llm", body)
        attempts = {"n": 0}

        async def call() -> Completion:
            attempts["n"] += 1
            started = time.monotonic()
            await self.limiter.acquire()
            try:
                response = await self._http().post(
                    f"{self.base_url}/chat/completions",
                    json=body,
                    headers=self._headers(),
                    timeout=httpx.Timeout(config.timeout_s, connect=10.0),
                )
            finally:
                self.limiter.release()
            if response.status_code >= 400:
                raise self._error_from(response.status_code, response.content, dict(response.headers))
            payload = response.json()
            if payload.get("error"):
                err = payload["error"]
                raise OpenRouterError(err.get("code"), err.get("message", ""), err.get("metadata"))
            completion = self._completion_from(payload)
            completion.latency_s = time.monotonic() - started
            return completion

        async def produce() -> dict[str, Any]:
            completion = await with_retry(
                call,
                self.retry,
                is_retryable=self._is_retryable,
                retry_after=self._retry_after,
                status_of=lambda e: getattr(e, "status", None),
                on_retry=lambda a: self._note_retry(tracer, a),
            )
            completion.attempts = attempts["n"]
            await self._finalise_cost(completion, config)
            return completion.model_dump()

        if config.cache:
            payload, was_cached = await self.cache.get_or_set(key, produce, ttl=config.cache_ttl_s)
        else:
            payload, was_cached = await produce(), False
        completion = Completion.model_validate(payload)
        if was_cached:
            # A replay is free, but it still gets a ledger line at $0 so the
            # trace shows the step ran and what it would otherwise have cost.
            completion.cached = True
            completion.cost = completion.cost.model_copy(update={"usd": 0.0, "cached": True})
        self._record(tracer, completion, config)
        return completion

    @staticmethod
    def _is_retryable(exc: BaseException) -> bool:
        if isinstance(exc, OpenRouterError):
            return exc.retryable
        return isinstance(exc, (httpx.TimeoutException, httpx.NetworkError, httpx.RemoteProtocolError))

    def _retry_after(self, exc: BaseException) -> float | None:
        after = getattr(exc, "retry_after", None)
        if after:
            # One 429 means the whole fleet is over the limit, not just this
            # call, so hold every waiter rather than only the one that lost.
            self.limiter.pause_for(after)
        return after

    @staticmethod
    def _note_retry(tracer: Tracer | None, attempt: Any) -> None:
        span = (tracer.current() if tracer else None) or current_span()
        if span is not None:
            span.attributes.setdefault("retries", []).append(attempt.to_dict())

    @staticmethod
    def _error_from(status: int, content: bytes, headers: dict[str, str]) -> OpenRouterError:
        message, metadata = content[:500].decode("utf-8", "replace"), None
        try:
            payload = json.loads(content)
            err = payload.get("error") or {}
            message = err.get("message") or message
            metadata = err.get("metadata")
        except (json.JSONDecodeError, AttributeError):
            pass
        retry_after = None
        raw = headers.get("retry-after") or headers.get("Retry-After")
        if raw:
            try:
                retry_after = float(raw)
            except ValueError:
                retry_after = None
        return OpenRouterError(status, message, metadata, retry_after)

    @staticmethod
    def _completion_from(payload: dict[str, Any], text_override: str | None = None) -> Completion:
        choices = payload.get("choices") or [{}]
        choice = choices[0] if choices else {}
        message = choice.get("message") or {}
        content = text_override if text_override is not None else (message.get("content") or "")
        if isinstance(content, list):  # multipart content blocks
            content = "".join(part.get("text", "") for part in content if isinstance(part, dict))

        citations = []
        for note in message.get("annotations") or []:
            if note.get("type") == "url_citation":
                citations.append(Citation(**(note.get("url_citation") or {})))

        return Completion(
            id=payload.get("id", ""),
            model=payload.get("model", ""),
            text=content or "",
            tool_calls=[ToolCall(**tc) for tc in (message.get("tool_calls") or [])],
            finish_reason=choice.get("finish_reason"),
            native_finish_reason=choice.get("native_finish_reason"),
            citations=citations,
            usage=Usage.from_openrouter(payload.get("usage")),
            raw=payload,
        )

    async def _finalise_cost(self, completion: Completion, config: LLMConfig) -> None:
        """Prefer the reported cost; estimate only when it is missing."""
        reported = ((completion.raw or {}).get("usage") or {}).get("cost")
        if reported is not None:
            details = ((completion.raw or {}).get("usage") or {}).get("cost_details") or {}
            completion.cost = Cost(
                usd=float(reported),
                source="reported",
                usage=completion.usage,
                details=details,
            )
            return
        pricing = await self._pricing_for(completion.model or config.model)
        if pricing is not None:
            searches = 1 if config.web_search else 0
            completion.cost = Cost(
                usd=pricing.estimate(completion.usage, web_searches=searches),
                source="estimated",
                usage=completion.usage,
                details={"model": completion.model},
            )
            return
        completion.cost = Cost.free(usage=completion.usage)

    async def _pricing_for(self, model: str) -> Pricing | None:
        if not self.auto_pricing or not model:
            return None
        async with self._pricing_lock:
            if self._pricing is None:
                self._pricing = await self._load_pricing()
        return self._pricing.get(model.split(":")[0])

    async def _load_pricing(self) -> dict[str, Pricing]:
        """Fetch the public model list once, for the estimate fallback path."""
        try:
            response = await self._http().get(f"{self.base_url}/models", timeout=30.0)
            response.raise_for_status()
            data = response.json().get("data") or []
        except (httpx.HTTPError, json.JSONDecodeError, ValueError):
            return {}
        return {
            entry["id"]: Pricing.from_models_endpoint(entry.get("pricing") or {})
            for entry in data
            if entry.get("id")
        }

    def _record(self, tracer: Tracer | None, completion: Completion, config: LLMConfig) -> None:
        if tracer is None:
            return
        span = tracer.current()
        span.annotate(
            model=completion.model or config.model,
            finish_reason=completion.finish_reason,
            cached=completion.cached,
            latency_s=round(completion.latency_s, 3),
            attempts=completion.attempts,
        )
        tracer.record_cost("llm", completion.model or config.model, completion.cost, span)
