"""The sample agents, end to end against the fake backend.

Catches the failure mode that only shows up on a real run and costs money to
find: a ``{{placeholder}}`` that never resolves, a loop condition that names
something not in state, a schema a step cannot fill.
"""

from __future__ import annotations

import pytest

from examples import smoke_test, web_research
from rsi_arena import AgentConfig, APIClient, LLMClient, Toolbox


@pytest.fixture
def tools(api: APIClient) -> Toolbox:
    return web_research.search_tools(api)


@pytest.fixture
def agents(config: AgentConfig, tools: Toolbox) -> dict:
    return {
        "pipeline": web_research.pipeline_agent(config, tools),
        "freeform": web_research.freeform_agent(config, tools),
        "plugin": web_research.plugin_agent(config),
        "fermi": smoke_test.build_agent("test/model"),
    }


QUESTIONS = {
    "pipeline": "Did the ECB cut rates in July 2026?",
    "freeform": "Did the ECB cut rates in July 2026?",
    "plugin": "Did the ECB cut rates in July 2026?",
    "fermi": "How many piano tuners are there in Chicago?",
}


@pytest.mark.parametrize("name", ["pipeline", "freeform", "plugin", "fermi"])
async def test_the_sample_agent_runs(name, agents, llm: LLMClient):
    agent = agents[name]
    result = await agent.run(QUESTIONS[name], llm=llm)
    assert result.ok, f"{agent.name} failed: {result.error}\n{result.trace.render()}"
    assert result.output, f"{agent.name} produced nothing"
    assert result.cost_usd > 0 and result.trace.costs.calls > 0


async def test_the_pipeline_searches_and_collects_evidence(agents, llm: LLMClient, fake):
    result = await agents["pipeline"].run(QUESTIONS["pipeline"], llm=llm)
    assert result.state["evidence"], "the loop collected nothing"
    assert fake.search_calls >= 1, "the pipeline never searched"


async def test_the_freeform_agent_drives_the_same_tools_itself(agents, llm: LLMClient):
    result = await agents["freeform"].run(QUESTIONS["freeform"], llm=llm)
    tool_spans = [s for s in result.trace.spans() if s.kind == "tool"]
    assert tool_spans, "the model should have called a search tool"


async def test_the_plugin_agent_needs_no_search_key(agents, llm: LLMClient, fake):
    result = await agents["plugin"].run(QUESTIONS["plugin"], llm=llm)
    assert result.ok and fake.search_calls == 0
    assert any("plugins" in body for body in fake.bodies), "the web plugin should be requested"


async def test_the_fermi_agent_uses_its_calculator(agents, llm: LLMClient):
    result = await agents["fermi"].run(QUESTIONS["fermi"], llm=llm)
    assert any(s.name == "calculator" for s in result.trace.spans())
    assert isinstance(result.state["value"], float)


async def test_repeated_searches_are_paid_for_once(agents, llm: LLMClient, fake):
    # The sharing that also makes an arena battle fair.
    await agents["pipeline"].run(QUESTIONS["pipeline"], llm=llm)
    before = fake.search_calls
    await agents["pipeline"].run(QUESTIONS["pipeline"], llm=llm)
    assert fake.search_calls == before, "the second run should be served from the cache"


async def test_every_sample_agent_round_trips_through_json(agents, tools: Toolbox):
    from rsi_arena import Agent

    for name, agent in agents.items():
        toolbox = agent.tools if name == "fermi" else tools
        rebuilt = Agent.from_dict(agent.to_dict(), tools=toolbox)
        assert rebuilt.to_dict() == agent.to_dict(), name


# --- the eval example -------------------------------------------------------


async def test_the_eval_example_runs_end_to_end(config: AgentConfig, api: APIClient,
                                                llm: LLMClient):
    from examples import evals as eval_example
    from rsi_arena import EvalSuite

    agents = eval_example.build_agents(["fermi", "plugin"], config, api)
    suite = EvalSuite.over(agents, eval_example.CASES, name="samples")
    result = await suite.run(llm=llm)

    assert len(result.results) == len(agents) * len(eval_example.CASES)
    agg = result.aggregate()
    assert agg["evals"] == len(result.results) and agg["cost_usd"] > 0
    assert result.table()


async def test_the_eval_example_records_a_bailout_under_max_spend(config: AgentConfig,
                                                                  api: APIClient, llm: LLMClient):
    from examples import evals as eval_example
    from rsi_arena import EvalSuite

    tight = config.model_copy(update={"max_usd": 0.002, "cache": False, "max_spend_mode": True})
    agents = eval_example.build_agents(["pipeline"], tight, api)
    result = await EvalSuite.over(agents, eval_example.CASES[:1]).run(llm=llm)

    assert result.results[0].bailed_out is True
    assert result.aggregate()["bailed_out"] == 1
