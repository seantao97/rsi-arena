"""``rsi_arena.agent.agent`` — running one, what comes back, and serialisation."""

from __future__ import annotations

import json

import httpx
import pytest

from rsi_arena.agent import Agent, AgentConfig, ErrorKind, LoopStep, Plan, PromptStep, ToolStep
from rsi_arena.agent.agent import classify, public_state, render_state
from rsi_arena.api import APIError
from rsi_arena.core.costs import BudgetExceeded, MaxSpendExceeded
from rsi_arena.core.template import ConditionError
from rsi_arena.llm import LLMClient, OpenRouterError
from tests.fakes import Fake


@pytest.fixture
def pipeline_agent(config: AgentConfig, toolbox) -> Agent:
    return Agent(
        name="demo-agent",
        context="You are a careful research agent.",
        tools=toolbox,
        config=config,
        description="A demo.",
        plan=Plan(steps=[
            ToolStep(name="count", tool="word_count", args={"text": "{{question}}"},
                     output_key="n"),
            LoopStep(name="refine", max_loops=3, until="n >= 1", steps=[
                PromptStep(name="think", prompt="Refine: {{question}}", output_key="thought"),
            ]),
            PromptStep(name="write", prompt="Write up {{thought}}", output_key="memo"),
        ]),
    )


# --- config -----------------------------------------------------------------


def test_config_defaults_to_the_shared_ceiling():
    config = AgentConfig()
    assert config.max_usd == 2.00 and config.max_calls == 200
    assert config.max_spend_mode is False


def test_config_projects_onto_an_llm_config():
    llm_config = AgentConfig(default_model="m", temperature=0.3, max_tokens=99).to_llm_config()
    assert llm_config.model == "m" and llm_config.temperature == 0.3


def test_no_reserve_unless_max_spend_mode_is_on():
    assert AgentConfig(max_usd=1.0).reserve_usd() == 0.0


def test_the_derived_reserve_is_five_percent_with_a_floor():
    assert AgentConfig(max_usd=1.0, max_spend_mode=True).reserve_usd() == pytest.approx(0.05)
    assert AgentConfig(max_usd=0.10, max_spend_mode=True).reserve_usd() == pytest.approx(0.02)


def test_an_explicit_reserve_wins():
    config = AgentConfig(max_usd=1.0, max_spend_mode=True, bailout_reserve_usd=0.5)
    assert config.reserve_usd() == 0.5


# --- running ----------------------------------------------------------------


async def test_a_run_produces_answer_state_trace_and_ledger(pipeline_agent: Agent, llm):
    result = await pipeline_agent.run("one two three", llm=llm)
    assert result.ok and result.error is None and result.error_kind is None
    assert isinstance(result.output, str) and result.output
    assert result.state["n"] == 3 and result.state["memo"] == result.output
    assert result.cost_usd > 0
    assert {s.name for s in result.trace.spans()} >= {"count", "refine", "write"}


def test_result_text_renders_a_dict_output_as_json():
    from rsi_arena.agent import AgentResult
    from rsi_arena.core.trace import Tracer

    trace = Tracer().finish()
    assert AgentResult(agent="a", run_id="1", output={"answer": "42"}, trace=trace).text == (
        '{\n  "answer": "42"\n}'
    )
    assert AgentResult(agent="a", run_id="1", output=None, trace=trace).text == ""


async def test_summary_is_flat_and_serialisable(pipeline_agent: Agent, llm):
    summary = (await pipeline_agent.run("one two", llm=llm)).summary()
    assert summary["ok"] is True and summary["error_kind"] is None
    assert summary["bailed_out"] is False
    assert set(summary) >= {"agent", "run_id", "duration_s", "total_usd", "calls", "by_kind"}
    json.dumps(summary)


async def test_extra_inputs_become_run_state(simple_agent: Agent, llm, fake):
    simple_agent.plan.steps[0].prompt = "{{question}} for {{audience}}"
    await simple_agent.run("explain", llm=llm, audience="a five-year-old")
    assert "a five-year-old" in fake.bodies[-1]["messages"][-1]["content"]


async def test_loop_bookkeeping_is_hidden_from_the_public_state(pipeline_agent: Agent, llm):
    result = await pipeline_agent.run("one two", llm=llm)
    assert "loop_results" not in result.state and "loop_index" not in result.state


async def test_errors_are_captured_rather_than_raised(config: AgentConfig, llm):
    # A run that failed still produced a partial trace, and in the arena that
    # trace is evidence.
    agent = Agent(name="broken", context="", config=config,
                  plan=Plan(steps=[PromptStep(name="a", prompt="{{nowhere}}")]))
    result = await agent.run("q", llm=llm)
    assert not result.ok and result.error_kind is ErrorKind.PLAN
    assert result.trace.root.status == "error"


async def test_raise_on_error_reraises(config: AgentConfig, llm):
    agent = Agent(name="broken", context="", config=config,
                  plan=Plan(steps=[PromptStep(name="a", prompt="{{nowhere}}")]))
    with pytest.raises(KeyError):
        await agent.run("q", llm=llm, raise_on_error=True)


async def test_the_budget_ceiling_stops_the_run(toolbox, llm):
    agent = Agent(name="broke", context="", tools=toolbox,
                  config=AgentConfig(default_model="test/model", max_usd=0.0001, cache=False),
                  plan=Plan(steps=[PromptStep(name="a", prompt="hi"),
                                   PromptStep(name="b", prompt="hi again")]))
    result = await agent.run("q", llm=llm)
    assert not result.ok and result.error_kind is ErrorKind.BUDGET
    assert result.bailed_out is False


async def test_events_and_tokens_reach_their_listeners(simple_agent: Agent, llm):
    simple_agent.plan.steps[0].stream = True
    events, tokens = [], []
    await simple_agent.run("q", llm=llm, on_event=events.append, on_token=tokens.append)
    assert {e["type"] for e in events} >= {"span_start", "span_end", "cost"}
    assert "".join(tokens)


async def test_label_replaces_the_name_everywhere_the_client_can_see(simple_agent: Agent, llm):
    # Blinding has to hold in the payload, not just in the UI.
    result = await simple_agent.run("q", llm=llm, label="Agent A")
    assert result.agent == "Agent A"
    assert result.trace.agent == "Agent A" and result.trace.root.name == "Agent A"
    assert "simple" not in json.dumps(result.summary())


async def test_run_many_shares_one_client(simple_agent: Agent, llm, fake):
    results = await simple_agent.run_many(["a", "b", "c"], llm=llm)
    assert len(results) == 3 and all(r.ok for r in results)
    assert fake.llm_calls == 3


async def test_a_run_without_a_client_creates_and_closes_its_own(simple_agent: Agent, monkeypatch,
                                                                 fake: Fake):
    created: list[LLMClient] = []
    original = LLMClient.__init__

    def spy(self, *args, **kwargs):
        original(self, *args, **{**kwargs, "http_client": fake.client(), "auto_pricing": False})
        created.append(self)

    monkeypatch.setattr(LLMClient, "__init__", spy)
    result = await simple_agent.run("q")
    assert result.ok and len(created) == 1


# --- error classification ---------------------------------------------------


@pytest.mark.parametrize(
    "exc,kind",
    [
        (MaxSpendExceeded(1.0, 1.0, "x"), ErrorKind.MAX_SPEND),
        (BudgetExceeded(1.0, 1.0, "x"), ErrorKind.BUDGET),
        (OpenRouterError(503, "no provider"), ErrorKind.PROVIDER),
        (httpx.TimeoutException("slow"), ErrorKind.PROVIDER),
        (APIError("searchapi", 500, "down"), ErrorKind.API),
        (ConditionError("bad condition"), ErrorKind.PLAN),
        (KeyError("unknown tool"), ErrorKind.PLAN),
        (RuntimeError("something else"), ErrorKind.OTHER),
    ],
)
def test_classify_maps_failures_to_kinds(exc, kind):
    # The arena needs to tell "this harness is bad" from "it ran out of money"
    # from "the provider was down": only the first is the agent's fault.
    assert classify(exc) is kind


def test_error_kinds_serialise_as_strings():
    assert ErrorKind.MAX_SPEND.value == "max_spend"
    assert json.dumps({"kind": ErrorKind.BUDGET.value}) == '{"kind": "budget"}'


# --- state rendering --------------------------------------------------------


def test_render_state_is_newest_first_and_skips_bookkeeping():
    rendered = render_state({"question": "q", "first": "A", "loop_index": 2, "second": "B"})
    assert rendered.index("## second") < rendered.index("## first")
    assert "loop_index" not in rendered and "## question" not in rendered


def test_render_state_truncates_to_a_budget():
    rendered = render_state({"huge": "x" * 100_000}, budget_chars=1000)
    assert len(rendered) < 2000 and "characters dropped" in rendered


def test_render_state_skips_empty_values():
    assert render_state({"a": "", "b": None, "c": [], "d": "kept"}) == "## d\nkept"


def test_public_state_drops_only_the_loop_keys():
    assert public_state({"a": 1, "loop_index": 0, "loop_results": []}) == {"a": 1}


# --- serialisation ----------------------------------------------------------


def test_an_agent_round_trips_through_json(pipeline_agent: Agent, toolbox):
    blob = json.dumps(pipeline_agent.to_dict())
    rebuilt = Agent.from_dict(json.loads(blob), tools=toolbox)
    assert json.dumps(rebuilt.to_dict()) == blob


def test_only_tool_names_are_serialised(pipeline_agent: Agent):
    # Tools are the human-supplied primitive set: an agent may reference one
    # but never define one.
    assert pipeline_agent.to_dict()["tools"] == ["word_count"]


def test_from_dict_rejects_an_unknown_tool_at_load_time(pipeline_agent: Agent, toolbox):
    # A bad mutation should fail when it is loaded, not halfway through a battle.
    blob = pipeline_agent.to_dict()
    blob["tools"] = ["invented_by_the_optimizer"]
    with pytest.raises(KeyError):
        Agent.from_dict(blob, tools=toolbox)


def test_max_spend_settings_survive_a_round_trip(pipeline_agent: Agent, toolbox):
    pipeline_agent.config.max_spend_mode = True
    pipeline_agent.config.bailout_reserve_usd = 0.25
    rebuilt = Agent.from_dict(pipeline_agent.to_dict(), tools=toolbox)
    assert rebuilt.config.max_spend_mode is True
    assert rebuilt.config.bailout_reserve_usd == 0.25


def test_outline_and_repr_describe_the_shape(pipeline_agent: Agent):
    outline = pipeline_agent.outline()
    assert "demo-agent" in outline and "word_count" in outline and "count (tool)" in outline
    assert "steps=3" in repr(pipeline_agent)


# --- config through a shared client -----------------------------------------


async def test_an_agents_model_wins_over_the_shared_clients(simple_agent: Agent, llm, fake):
    # The backend runs every agent through one client, so this is what makes
    # its model picker mean anything at all.
    simple_agent.config.default_model = "picked/model"
    await simple_agent.run("q", llm=llm)
    assert fake.bodies[-1]["model"] == "picked/model"


async def test_two_agents_on_one_client_keep_their_own_settings(config: AgentConfig, llm, fake):
    cheap = Agent(name="cheap", context="", plan=Plan(steps=[PromptStep(name="a", prompt="q")]),
                  config=config.model_copy(update={"default_model": "cheap/model",
                                                   "temperature": 0.0}))
    fancy = Agent(name="fancy", context="", plan=Plan(steps=[PromptStep(name="a", prompt="q")]),
                  config=config.model_copy(update={"default_model": "fancy/model",
                                                   "temperature": 0.9}))
    await cheap.run("q", llm=llm)
    await fancy.run("q", llm=llm)
    sent = {body["model"]: body.get("temperature") for body in fake.bodies}
    assert sent == {"cheap/model": 0.0, "fancy/model": 0.9}
