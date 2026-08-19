"""Agents: LLM + primitives + orchestration.

The three things the README says an agent is, and nothing else:

======================  ==========================================================
:mod:`~rsi_arena.agent.tools`  the primitives — a function, an API endpoint, or by hand
:mod:`~rsi_arena.agent.steps`  the orchestration — ``PromptStep``, ``ToolStep``, ``LoopStep``, ``Plan``
:mod:`~rsi_arena.agent.agent`  the three put together, and the run that produces a result
======================  ==========================================================
"""

from __future__ import annotations

from .agent import Agent, AgentConfig, AgentResult, ErrorKind
from .steps import AnyStep, LoopStep, Plan, PromptStep, Step, StepContext, ToolStep
from .tools import Tool, ToolResult, Toolbox, api_tool, tool

__all__ = [
    "Agent", "AgentConfig", "AgentResult", "ErrorKind",
    "Plan", "Step", "AnyStep", "PromptStep", "ToolStep", "LoopStep", "StepContext",
    "Tool", "Toolbox", "ToolResult", "tool", "api_tool",
]
