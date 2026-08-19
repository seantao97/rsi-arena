"""Call settings: which model, how it decodes, and whether it may search.

:class:`LLMConfig` is the defaults an agent owns; any single call may override
any field. :class:`WebSearch` is the one setting that is not a decoding knob —
it costs money per call, so it is spelled out rather than left to a default.
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field

DEFAULT_BASE_URL = "https://openrouter.ai/api/v1"
DEFAULT_MODEL = "anthropic/claude-sonnet-4.5"

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
