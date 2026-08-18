"""Cost and token accounting.

Every LLM call and every API call produces a :class:`Cost`. They are summed
into a :class:`CostTracker` that hangs off an agent run, so the question "what
did this answer cost?" is answerable without parsing a log.

Where the number comes from matters, so :class:`Cost` records its ``source``:

* ``reported``  — OpenRouter returned ``usage.cost``. Authoritative, and now
  present on every response (the old ``usage: {include: true}`` opt-in is
  deprecated). This is the normal case.
* ``estimated`` — computed from a token count and a price table. Used when a
  response somehow arrives without ``usage.cost``.
* ``fixed``     — a flat per-call price declared by an API spec, which is how
  most search and data vendors bill.
* ``free``      — no charge, or a cache hit.

Cache hits are recorded at zero with ``cached=True`` rather than dropped, so a
trace still shows that the step happened and what it *would* have cost.
"""

from __future__ import annotations

import time
from typing import Any, Literal

from pydantic import BaseModel, Field

CostSource = Literal["reported", "estimated", "fixed", "free"]


class Usage(BaseModel):
    """Token counts, in OpenRouter's field names where they exist."""

    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0
    reasoning_tokens: int = 0
    cached_tokens: int = 0
    cache_write_tokens: int = 0

    @classmethod
    def from_openrouter(cls, usage: dict[str, Any] | None) -> "Usage":
        if not usage:
            return cls()
        prompt_details = usage.get("prompt_tokens_details") or {}
        completion_details = usage.get("completion_tokens_details") or {}
        return cls(
            prompt_tokens=int(usage.get("prompt_tokens") or 0),
            completion_tokens=int(usage.get("completion_tokens") or 0),
            total_tokens=int(usage.get("total_tokens") or 0),
            reasoning_tokens=int(completion_details.get("reasoning_tokens") or 0),
            cached_tokens=int(prompt_details.get("cached_tokens") or 0),
            cache_write_tokens=int(prompt_details.get("cache_write_tokens") or 0),
        )

    def __add__(self, other: "Usage") -> "Usage":
        return Usage(
            prompt_tokens=self.prompt_tokens + other.prompt_tokens,
            completion_tokens=self.completion_tokens + other.completion_tokens,
            total_tokens=self.total_tokens + other.total_tokens,
            reasoning_tokens=self.reasoning_tokens + other.reasoning_tokens,
            cached_tokens=self.cached_tokens + other.cached_tokens,
            cache_write_tokens=self.cache_write_tokens + other.cache_write_tokens,
        )


class Cost(BaseModel):
    """What one call cost, and how confident we are in that number."""

    usd: float = 0.0
    source: CostSource = "free"
    cached: bool = False
    usage: Usage = Field(default_factory=Usage)
    details: dict[str, Any] = Field(default_factory=dict)

    @classmethod
    def free(cls, *, cached: bool = False, usage: Usage | None = None) -> "Cost":
        return cls(usd=0.0, source="free", cached=cached, usage=usage or Usage())

    @classmethod
    def flat(cls, usd: float, **details: Any) -> "Cost":
        return cls(usd=usd, source="fixed", details=details)


class Pricing(BaseModel):
    """Per-token prices as OpenRouter publishes them: USD per single token."""

    prompt: float = 0.0
    completion: float = 0.0
    request: float = 0.0
    web_search: float = 0.0
    internal_reasoning: float = 0.0

    @classmethod
    def from_models_endpoint(cls, pricing: dict[str, Any]) -> "Pricing":
        def num(key: str) -> float:
            try:
                return float(pricing.get(key) or 0.0)
            except (TypeError, ValueError):
                return 0.0

        return cls(
            prompt=num("prompt"),
            completion=num("completion"),
            request=num("request"),
            web_search=num("web_search"),
            internal_reasoning=num("internal_reasoning"),
        )

    def estimate(self, usage: Usage, *, web_searches: int = 0) -> float:
        return (
            usage.prompt_tokens * self.prompt
            + usage.completion_tokens * self.completion
            + usage.reasoning_tokens * self.internal_reasoning
            + self.request
            + web_searches * self.web_search
        )


class CostRecord(BaseModel):
    """One line in the ledger."""

    kind: Literal["llm", "api", "tool"]
    name: str
    cost: Cost
    at: float = Field(default_factory=time.time)
    span_id: str | None = None


class BudgetExceeded(RuntimeError):
    """Raised when a run would cross its ceiling. The run stops; it is not queued.

    Mirrors the arena runtime's Governor (README ``R2``/``R5``): calls past a
    cap are refused, not delayed, because a delayed call still spends the
    budget it was supposed to protect.
    """

    def __init__(self, spent: float, limit: float, what: str) -> None:
        super().__init__(f"budget exceeded: {what} would take spend to ${spent:.4f} of ${limit:.4f}")
        self.spent = spent
        self.limit = limit


class CostTracker(BaseModel):
    """Ledger for one agent run. Enforces the ceiling as records land."""

    max_usd: float | None = None
    max_calls: int | None = None
    records: list[CostRecord] = Field(default_factory=list)

    @property
    def total_usd(self) -> float:
        return sum(r.cost.usd for r in self.records)

    @property
    def usage(self) -> Usage:
        total = Usage()
        for record in self.records:
            total = total + record.cost.usage
        return total

    @property
    def calls(self) -> int:
        return len(self.records)

    def add(self, kind: str, name: str, cost: Cost, span_id: str | None = None) -> CostRecord:
        record = CostRecord(kind=kind, name=name, cost=cost, span_id=span_id)  # type: ignore[arg-type]
        self.records.append(record)
        if self.max_usd is not None and self.total_usd > self.max_usd:
            raise BudgetExceeded(self.total_usd, self.max_usd, f"{kind}:{name}")
        return record

    def check(self, name: str = "next call") -> None:
        """Pre-flight the ceiling, so we refuse *before* spending, not after."""
        if self.max_calls is not None and self.calls >= self.max_calls:
            raise BudgetExceeded(self.total_usd, self.max_usd or 0.0, f"call limit ({name})")
        if self.max_usd is not None and self.total_usd >= self.max_usd:
            raise BudgetExceeded(self.total_usd, self.max_usd, name)

    def by(self, field: str = "name") -> dict[str, float]:
        out: dict[str, float] = {}
        for record in self.records:
            key = getattr(record, field)
            out[key] = out.get(key, 0.0) + record.cost.usd
        return out

    def summary(self) -> dict[str, Any]:
        return {
            "total_usd": round(self.total_usd, 6),
            "calls": self.calls,
            "cached_calls": sum(1 for r in self.records if r.cost.cached),
            "by_kind": {k: round(v, 6) for k, v in self.by("kind").items()},
            "by_name": {k: round(v, 6) for k, v in self.by("name").items()},
            "usage": self.usage.model_dump(),
        }
