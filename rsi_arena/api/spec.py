"""An API declared as data.

:class:`Param`, :class:`Endpoint` and :class:`APISpec` are declarative and
serialisable: no HTTP, no state, safe to define in a user's own module. The
client next door owns the connection pool, the limiters and the cache. That
split is what keeps a third-party API definition to one literal.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable, Iterable, Literal

from ..core.ratelimit import RateLimit
from ..core.retry import RetryPolicy
from .auth import Auth, NoAuth

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
