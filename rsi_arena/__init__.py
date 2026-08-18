"""RSI Arena runtime: LLM calls, API calls, tools, plans and agents.

Read the module docstrings in this order to understand the whole thing:
:mod:`~rsi_arena.llm` (talking to models), :mod:`~rsi_arena.api` (talking to
everything else), :mod:`~rsi_arena.tools` (the primitives),
:mod:`~rsi_arena.steps` (orchestration) and :mod:`~rsi_arena.agent` (the three
put together).
"""

from __future__ import annotations

from .agent import Agent, AgentConfig, AgentResult
from .api import (
    APIClient,
    APIError,
    APIResponse,
    APISpec,
    BearerAuth,
    Endpoint,
    HeaderAuth,
    NoAuth,
    Param,
    QueryAuth,
    Registry,
    get_api,
    register_api,
    registry,
)
from .cache import Cache, MemoryCache, NullCache, set_default_cache
from .costs import BudgetExceeded, Cost, CostTracker, Usage
from .llm import (
    Citation,
    Completion,
    LLMClient,
    LLMConfig,
    Message,
    OpenRouterError,
    StreamEvent,
    WebSearch,
)
from .ratelimit import RateLimit
from .retry import RetryPolicy
from .steps import LoopStep, Plan, PromptStep, StepContext, ToolStep
from .tools import Tool, ToolResult, Toolbox, api_tool, tool
from .trace import Span, Trace, Tracer

__version__ = "0.1.0"

__all__ = [
    "Agent", "AgentConfig", "AgentResult",
    "Plan", "PromptStep", "ToolStep", "LoopStep", "StepContext",
    "Tool", "Toolbox", "ToolResult", "tool", "api_tool",
    "LLMClient", "LLMConfig", "Message", "Completion", "StreamEvent", "WebSearch",
    "Citation", "OpenRouterError",
    "APIClient", "APISpec", "Endpoint", "Param", "Registry", "APIResponse", "APIError",
    "NoAuth", "BearerAuth", "HeaderAuth", "QueryAuth", "register_api", "get_api", "registry",
    "Cache", "MemoryCache", "NullCache", "set_default_cache",
    "Cost", "CostTracker", "Usage", "BudgetExceeded",
    "RateLimit", "RetryPolicy",
    "Trace", "Tracer", "Span",
    "__version__",
]
