"""Generic API client and registry.

Defining a new API is meant to be one declaration and nothing else:

.. code-block:: python

    NWS = APISpec(
        name="nws",
        base_url="https://api.weather.gov",
        auth=NoAuth(),
        rate_limit=RateLimit(per_second=5),
        endpoints=[
            Endpoint("forecast", "/gridpoints/{office}/{grid_x},{grid_y}/forecast",
                     required=["office", "grid_x", "grid_y"]),
        ],
    )
    register_api(NWS)

After that, ``await api.call("nws", "forecast", office="OKX", grid_x=33, grid_y=37)``
works, it is retried, rate limited, cached and costed like everything else,
and ``NWS.as_tool("forecast")`` hands the same endpoint to an LLM as a
callable tool with a generated JSON Schema.

The split is deliberate: :class:`APISpec` is *declarative data* — no HTTP, no
state, serialisable, safe to define in a user's own module — while
:class:`APIClient` owns the connection pool, the limiters and the cache. That
is what makes third-party API definitions cheap: they are data, not plumbing.
Anything genuinely custom hooks in through the ``parse`` and ``cost``
callables on an endpoint rather than by subclassing a client.
"""

from __future__ import annotations

import asyncio
import os
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Iterable, Literal

import httpx
from pydantic import BaseModel, Field

from .cache import Cache, default_cache, make_key
from .costs import Cost
from .ratelimit import RateLimit, RateLimiter
from .retry import RetryPolicy, with_retry
from .trace import Tracer, current_span


class APIError(RuntimeError):
    def __init__(self, api: str, status: int | None, message: str, retry_after: float | None = None):
        super().__init__(f"{api}: [{status}] {message}")
        self.api = api
        self.status = status
        self.message = message
        self.retry_after = retry_after

    @property
    def retryable(self) -> bool:
        return self.status in {408, 409, 425, 429, 500, 502, 503, 504}


class MissingCredential(RuntimeError):
    """The API needs a key and the environment does not have one."""


# --- auth -------------------------------------------------------------------


@dataclass(frozen=True)
class Auth:
    """Base auth. Subclasses mutate the outgoing headers/params in place."""

    env_var: str | None = None
    required: bool = True

    def key(self, api: str) -> str:
        if not self.env_var:
            return ""
        value = os.environ.get(self.env_var, "")
        if not value and self.required:
            raise MissingCredential(f"{api} needs {self.env_var} in the environment")
        return value

    def apply(self, api: str, headers: dict[str, str], params: dict[str, Any]) -> None:
        return None


@dataclass(frozen=True)
class NoAuth(Auth):
    required: bool = False


@dataclass(frozen=True)
class BearerAuth(Auth):
    def apply(self, api: str, headers: dict[str, str], params: dict[str, Any]) -> None:
        headers["Authorization"] = f"Bearer {self.key(api)}"


@dataclass(frozen=True)
class HeaderAuth(Auth):
    header: str = "X-API-Key"
    prefix: str = ""

    def apply(self, api: str, headers: dict[str, str], params: dict[str, Any]) -> None:
        headers[self.header] = f"{self.prefix}{self.key(api)}"


@dataclass(frozen=True)
class QueryAuth(Auth):
    param: str = "api_key"

    def apply(self, api: str, headers: dict[str, str], params: dict[str, Any]) -> None:
        params[self.param] = self.key(api)


# --- declarations -----------------------------------------------------------

ParamType = Literal["string", "number", "integer", "boolean"]


@dataclass(frozen=True)
class Param:
    """One accepted parameter. Doubles as the JSON Schema for tool exposure."""

    name: str
    description: str = ""
    type: ParamType = "string"
    required: bool = False
    default: Any = None
    enum: list[Any] | None = None

    def to_schema(self) -> dict[str, Any]:
        schema: dict[str, Any] = {"type": self.type}
        if self.description:
            schema["description"] = self.description
        if self.enum:
            schema["enum"] = self.enum
        return schema


@dataclass(frozen=True)
class Endpoint:
    """One callable operation on an API.

    ``path`` may contain ``{placeholders}``, which are filled from the call
    parameters and then dropped from the query string.

    ``parse`` reshapes the raw JSON into whatever the caller actually wants —
    the single most valuable hook here, because raw search-engine JSON is
    enormous and an agent should not be paying tokens to read it.
    """

    name: str
    path: str = ""
    method: str = "GET"
    description: str = ""
    params: tuple[Param, ...] = ()
    defaults: dict[str, Any] = field(default_factory=dict)
    body_params: tuple[str, ...] = ()
    parse: Callable[[Any], Any] | None = None
    cost_usd: float | None = None
    cache_ttl_s: float | None = None

    def schema(self) -> dict[str, Any]:
        """JSON Schema for the parameters, used when exposing this as a tool."""
        return {
            "type": "object",
            "properties": {p.name: p.to_schema() for p in self.params},
            "required": [p.name for p in self.params if p.required],
            "additionalProperties": False,
        }


@dataclass
class APISpec:
    """Everything needed to talk to one API. Data only — no connections."""

    name: str
    base_url: str
    endpoints: Iterable[Endpoint] = ()
    auth: Auth = field(default_factory=NoAuth)
    headers: dict[str, str] = field(default_factory=dict)
    defaults: dict[str, Any] = field(default_factory=dict)
    rate_limit: RateLimit = field(default_factory=lambda: RateLimit(per_second=5, concurrency=5))
    retry: RetryPolicy = field(default_factory=RetryPolicy)
    timeout_s: float = 30.0
    cost_per_call: float = 0.0
    cache_ttl_s: float | None = None
    description: str = ""

    def __post_init__(self) -> None:
        self._by_name = {e.name: e for e in self.endpoints}

    def endpoint(self, name: str) -> Endpoint:
        try:
            return self._by_name[name]
        except KeyError:
            known = ", ".join(sorted(self._by_name)) or "none"
            raise KeyError(f"{self.name} has no endpoint {name!r} (have: {known})") from None

    def endpoint_names(self) -> list[str]:
        return sorted(self._by_name)

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "base_url": self.base_url,
            "description": self.description,
            "auth": type(self.auth).__name__,
            "auth_env": self.auth.env_var,
            "cost_per_call": self.cost_per_call,
            "endpoints": {
                e.name: {"method": e.method, "path": e.path, "description": e.description,
                         "schema": e.schema()}
                for e in self._by_name.values()
            },
        }


# --- registry ---------------------------------------------------------------


class Registry:
    """Name → :class:`APISpec`. Global by default; instantiate for tests."""

    def __init__(self) -> None:
        self._specs: dict[str, APISpec] = {}

    def register(self, spec: APISpec, *, replace: bool = False) -> APISpec:
        if spec.name in self._specs and not replace:
            raise ValueError(f"api {spec.name!r} already registered; pass replace=True to override")
        self._specs[spec.name] = spec
        return spec

    def get(self, name: str) -> APISpec:
        try:
            return self._specs[name]
        except KeyError:
            known = ", ".join(sorted(self._specs)) or "none"
            raise KeyError(f"unknown api {name!r} (registered: {known})") from None

    def names(self) -> list[str]:
        return sorted(self._specs)

    def to_dict(self) -> dict[str, Any]:
        return {name: spec.to_dict() for name, spec in sorted(self._specs.items())}


registry = Registry()


def default_registry() -> Registry:
    return registry


def register_api(spec: APISpec, *, replace: bool = False) -> APISpec:
    """Add a spec to the global registry and hand it back, so this works:

    ``MY_API = register_api(APISpec(...))``
    """
    return registry.register(spec, replace=replace)


def get_api(name: str) -> APISpec:
    return registry.get(name)


# --- responses --------------------------------------------------------------


class APIResponse(BaseModel):
    api: str
    endpoint: str
    url: str = ""
    status: int = 200
    data: Any = None
    cached: bool = False
    latency_s: float = 0.0
    attempts: int = 1
    cost: Cost = Field(default_factory=Cost)


# --- client -----------------------------------------------------------------


class APIClient:
    """Executes any :class:`APISpec`, with retries, limits, cache and cost.

    One client serves every registered API. Rate limiters are per-API and
    created on first use, so two agents hitting the same vendor share one
    budget instead of each getting their own.
    """

    def __init__(
        self,
        *,
        registry: Registry | None = None,
        cache: Cache | None = None,
        http_client: httpx.AsyncClient | None = None,
        user_agent: str = "rsi-arena/0.1",
    ) -> None:
        self.registry = registry or default_registry()
        self.cache = cache if cache is not None else default_cache()
        self.user_agent = user_agent
        self._client = http_client
        self._owns_client = http_client is None
        self._limiters: dict[str, RateLimiter] = {}

    def _http(self) -> httpx.AsyncClient:
        if self._client is None:
            self._client = httpx.AsyncClient(
                timeout=httpx.Timeout(30.0, connect=10.0),
                headers={"User-Agent": self.user_agent},
                follow_redirects=True,
            )
        return self._client

    def _limiter(self, spec: APISpec) -> RateLimiter:
        limiter = self._limiters.get(spec.name)
        if limiter is None:
            limiter = spec.rate_limit.build()
            self._limiters[spec.name] = limiter
        return limiter

    async def close(self) -> None:
        if self._client is not None and self._owns_client:
            await self._client.aclose()
            self._client = None

    async def __aenter__(self) -> "APIClient":
        return self

    async def __aexit__(self, *exc_info: object) -> None:
        await self.close()

    def _prepare(
        self, spec: APISpec, endpoint: Endpoint, params: dict[str, Any]
    ) -> tuple[str, dict[str, Any], dict[str, Any], dict[str, str]]:
        merged: dict[str, Any] = {**spec.defaults, **endpoint.defaults}
        for param in endpoint.params:
            if param.default is not None:
                merged.setdefault(param.name, param.default)
        merged.update({k: v for k, v in params.items() if v is not None})

        missing = [p.name for p in endpoint.params if p.required and p.name not in merged]
        if missing:
            raise ValueError(f"{spec.name}.{endpoint.name} missing required: {', '.join(missing)}")

        path = endpoint.path
        for token in _placeholders(path):
            if token not in merged:
                raise ValueError(f"{spec.name}.{endpoint.name} path needs {token!r}")
            path = path.replace("{" + token + "}", str(merged.pop(token)))

        headers = {**spec.headers}
        body = {k: merged.pop(k) for k in list(merged) if k in endpoint.body_params}
        spec.auth.apply(spec.name, headers, merged)
        url = spec.base_url.rstrip("/") + path
        return url, merged, body, headers

    async def call(
        self,
        api: str | APISpec,
        endpoint: str,
        *,
        tracer: Tracer | None = None,
        cache: bool = True,
        **params: Any,
    ) -> APIResponse:
        """Call one endpoint. Extra kwargs become query (or body) parameters."""
        spec = api if isinstance(api, APISpec) else self.registry.get(api)
        ep = spec.endpoint(endpoint)
        url, query, body, headers = self._prepare(spec, ep, params)

        # The cache key excludes auth headers on purpose: the same query is the
        # same query regardless of which key paid for it.
        key = make_key(f"api:{spec.name}:{ep.name}", {"url": url, "q": query, "b": body})
        attempts = {"n": 0}

        async def call_once() -> dict[str, Any]:
            attempts["n"] += 1
            started = time.monotonic()
            limiter = self._limiter(spec)
            await limiter.acquire()
            try:
                response = await self._http().request(
                    ep.method,
                    url,
                    params=query if ep.method in {"GET", "DELETE"} else None,
                    json=body or (query if ep.method not in {"GET", "DELETE"} else None),
                    headers=headers,
                    timeout=httpx.Timeout(spec.timeout_s, connect=10.0),
                )
            finally:
                limiter.release()
            if response.status_code >= 400:
                raise APIError(
                    spec.name,
                    response.status_code,
                    response.text[:500],
                    _retry_after_header(response.headers),
                )
            try:
                data = response.json()
            except ValueError:
                data = response.text
            return {
                "url": str(response.url),
                "status": response.status_code,
                "data": data,
                "latency_s": time.monotonic() - started,
            }

        async def produce() -> dict[str, Any]:
            return await with_retry(
                call_once,
                spec.retry,
                is_retryable=_is_retryable,
                retry_after=lambda e: _pause(self._limiter(spec), getattr(e, "retry_after", None)),
                status_of=lambda e: getattr(e, "status", None),
                on_retry=lambda a: _note_retry(tracer, a),
            )

        ttl = ep.cache_ttl_s if ep.cache_ttl_s is not None else spec.cache_ttl_s
        if cache:
            payload, was_cached = await self.cache.get_or_set(key, produce, ttl=ttl)
        else:
            payload, was_cached = await produce(), False

        data = payload["data"]
        result = APIResponse(
            api=spec.name,
            endpoint=ep.name,
            url=payload["url"],
            status=payload["status"],
            data=ep.parse(data) if ep.parse else data,
            cached=was_cached,
            latency_s=payload["latency_s"],
            attempts=attempts["n"] or 1,
        )
        per_call = ep.cost_usd if ep.cost_usd is not None else spec.cost_per_call
        result.cost = (
            Cost.free(cached=True)
            if was_cached
            else (Cost.flat(per_call, api=spec.name, endpoint=ep.name) if per_call else Cost.free())
        )
        self._record(tracer, spec, ep, result)
        return result

    async def call_many(self, calls: Iterable[dict[str, Any]], **shared: Any) -> list[APIResponse]:
        """Fan out. Each item is ``{"api": ..., "endpoint": ..., **params}``."""

        async def one(item: dict[str, Any]) -> APIResponse:
            params = {**shared, **item}
            return await self.call(params.pop("api"), params.pop("endpoint"), **params)

        return await asyncio.gather(*(one(item) for item in calls))

    def _record(
        self, tracer: Tracer | None, spec: APISpec, ep: Endpoint, result: APIResponse
    ) -> None:
        if tracer is None:
            return
        span = tracer.current()
        span.annotate(
            api=spec.name,
            endpoint=ep.name,
            url=result.url,
            status=result.status,
            cached=result.cached,
            latency_s=round(result.latency_s, 3),
        )
        tracer.record_cost("api", f"{spec.name}.{ep.name}", result.cost, span)


# --- helpers ----------------------------------------------------------------


def _placeholders(path: str) -> list[str]:
    out, rest = [], path
    while "{" in rest and "}" in rest:
        start = rest.index("{")
        end = rest.index("}", start)
        out.append(rest[start + 1 : end])
        rest = rest[end + 1 :]
    return out


def _is_retryable(exc: BaseException) -> bool:
    if isinstance(exc, APIError):
        return exc.retryable
    return isinstance(exc, (httpx.TimeoutException, httpx.NetworkError, httpx.RemoteProtocolError))


def _pause(limiter: RateLimiter, retry_after: float | None) -> float | None:
    if retry_after:
        limiter.pause_for(retry_after)
    return retry_after


def _retry_after_header(headers: Any) -> float | None:
    raw = headers.get("retry-after") or headers.get("Retry-After")
    try:
        return float(raw) if raw else None
    except (TypeError, ValueError):
        return None


def _note_retry(tracer: Tracer | None, attempt: Any) -> None:
    span = (tracer.current() if tracer else None) or current_span()
    if span is not None:
        span.attributes.setdefault("retries", []).append(attempt.to_dict())
