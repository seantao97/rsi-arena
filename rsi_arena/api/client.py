"""Executes any :class:`~rsi_arena.api.spec.APISpec`.

Retries, per-API rate limits, caching and cost, in one client that serves
every registered API. Limiters are per-API and created on first use, so two
agents hitting the same vendor share one budget instead of each getting their
own.
"""

from __future__ import annotations

import asyncio
import time
from typing import Any, Iterable

import httpx
from pydantic import BaseModel, Field

from ..core.cache import Cache, default_cache, make_key
from ..core.costs import Cost
from ..core.ratelimit import RateLimiter
from ..core.retry import with_retry
from ..core.trace import Tracer, current_span
from .errors import APIError
from .registry import Registry, default_registry
from .spec import APISpec, Endpoint

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
