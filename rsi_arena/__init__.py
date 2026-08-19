"""RSI Arena runtime: LLM calls, API calls, tools, plans, agents and evals.

Five packages, layered bottom to top:

=========================  =====================================================
:mod:`~rsi_arena.core`     cache, cost, rate limit, retry, trace, templates
:mod:`~rsi_arena.llm`      talking to models, through OpenRouter
:mod:`~rsi_arena.api`      talking to everything else, declared as data
:mod:`~rsi_arena.agent`    tools, steps, plans, and the agent that runs them
:mod:`~rsi_arena.evals`    give an agent a prompt, score what comes back, store it
=========================  =====================================================

Read them in that order and the whole thing follows. Everything a caller
normally needs is re-exported here, so ``from rsi_arena import Agent, Plan``
works regardless of which package a name actually lives in.
"""

from __future__ import annotations

from .agent import (
    Agent,
    AgentConfig,
    AgentResult,
    AnyStep,
    ErrorKind,
    LoopStep,
    Plan,
    PromptStep,
    Step,
    StepContext,
    Tool,
    ToolResult,
    Toolbox,
    ToolStep,
    api_tool,
    tool,
)
from .api import (
    APIClient,
    APIError,
    APIResponse,
    APISpec,
    BearerAuth,
    Endpoint,
    HeaderAuth,
    MissingCredential,
    NoAuth,
    Param,
    QueryAuth,
    Registry,
    get_api,
    register_api,
    registry,
)
from .core import (
    BudgetExceeded,
    Cache,
    Cost,
    CostTracker,
    MaxSpendExceeded,
    MemoryCache,
    NullCache,
    RateLimit,
    RetryPolicy,
    Span,
    Trace,
    Tracer,
    Usage,
    set_default_cache,
)
from .evals import (
    Eval,
    EvalResult,
    EvalStore,
    EvalSuite,
    InMemoryEvalStore,
    Score,
    Scorer,
    default_eval_store,
    get_scorer,
    register_scorer,
    scorer_from_spec,
    set_default_eval_store,
)
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

__version__ = "0.1.0"

__all__ = [
    "Agent", "AgentConfig", "AgentResult", "ErrorKind",
    "Plan", "Step", "AnyStep", "PromptStep", "ToolStep", "LoopStep", "StepContext",
    "Tool", "Toolbox", "ToolResult", "tool", "api_tool",
    "LLMClient", "LLMConfig", "Message", "Completion", "StreamEvent", "WebSearch",
    "Citation", "OpenRouterError",
    "APIClient", "APISpec", "Endpoint", "Param", "APIResponse", "APIError",
    "MissingCredential", "Registry", "registry", "register_api", "get_api",
    "NoAuth", "BearerAuth", "HeaderAuth", "QueryAuth",
    "Eval", "EvalSuite", "EvalResult", "Score", "Scorer",
    "EvalStore", "InMemoryEvalStore", "default_eval_store", "set_default_eval_store",
    "register_scorer", "get_scorer", "scorer_from_spec",
    "Cost", "Usage", "CostTracker", "BudgetExceeded", "MaxSpendExceeded",
    "Cache", "MemoryCache", "NullCache", "set_default_cache",
    "RateLimit", "RetryPolicy",
    "Trace", "Span", "Tracer",
    "__version__",
]
