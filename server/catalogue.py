"""The agents the UI can run.

Deliberately thin: the sample agents are defined once, in ``examples/``, and
this module only builds them with a per-request config and describes them for
the picker. Anything worth knowing about an agent — its context, its plan, its
tools — already serialises, so ``describe`` is mostly ``Agent.to_dict``.

Adding an agent to the UI is one entry in :data:`BUILDERS`.
"""

from __future__ import annotations

from typing import Any, Callable

from examples import smoke_test, web_research
from rsi_arena import Agent, AgentConfig, APIClient, Toolbox

# Which environment keys an agent cannot run without. The UI greys out the
# ones you have no key for rather than letting you spend a minute finding out.
REQUIRES: dict[str, list[str]] = {
    "pipeline": ["OPENROUTER_API_KEY", "SEARCHAPI_API_KEY"],
    "freeform": ["OPENROUTER_API_KEY", "SEARCHAPI_API_KEY"],
    "plugin": ["OPENROUTER_API_KEY"],
    "fermi": ["OPENROUTER_API_KEY"],
}

TOPICS = {"pipeline": "web-research", "freeform": "web-research",
          "plugin": "web-research", "fermi": "estimation"}


def _tools(api: APIClient) -> Toolbox:
    return web_research.search_tools(api)


BUILDERS: dict[str, Callable[[AgentConfig, APIClient], Agent]] = {
    "pipeline": lambda config, api: web_research.pipeline_agent(config, _tools(api)),
    "freeform": lambda config, api: web_research.freeform_agent(config, _tools(api)),
    "plugin": lambda config, api: web_research.plugin_agent(config),
    "fermi": lambda config, api: smoke_test.build_agent(config.default_model),
}


def build(agent_id: str, config: AgentConfig, api: APIClient) -> Agent:
    """Instantiate one agent with the caller's config."""
    try:
        agent = BUILDERS[agent_id](config, api)
    except KeyError:
        raise KeyError(f"unknown agent {agent_id!r} (have: {', '.join(BUILDERS)})") from None
    # The UI wants the answer to appear as it is written, so the last step
    # streams — but only if it is a plain prompt step. A tool loop has several
    # model turns and no single final message to stream.
    last = agent.plan.steps[-1] if agent.plan.steps else None
    if last is not None and getattr(last, "type", "") == "prompt" and not getattr(last, "tools", []):
        last.stream = True
    return agent


def describe(agent_id: str, config: AgentConfig | None = None) -> dict[str, Any]:
    agent = build(agent_id, config or AgentConfig(), APIClient())
    return {
        "id": agent_id,
        "name": agent.name,
        "topic": TOPICS.get(agent_id, ""),
        "description": agent.description,
        "context": agent.context,
        "tools": agent.tools.names(),
        "outline": agent.plan.outline(),
        "steps": len(agent.plan),
        "requires": REQUIRES.get(agent_id, ["OPENROUTER_API_KEY"]),
        "plan": agent.plan.model_dump(),
        "config": agent.config.model_dump(),
    }


def catalogue() -> list[dict[str, Any]]:
    return [describe(agent_id) for agent_id in BUILDERS]
