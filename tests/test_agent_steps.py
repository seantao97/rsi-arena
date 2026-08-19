"""``rsi_arena.agent.steps`` — prompt, tool and loop steps, and the plan.

The two orchestrations the arena exists to compare are one field apart:
``PromptStep.tools`` empty is a fixed pipeline, ``PromptStep.tools`` set is a
free-form agent over the same primitives. Both are exercised here.
"""

from __future__ import annotations

import json

import pytest

from rsi_arena.agent.steps import LoopStep, Plan, PromptStep, StepContext, ToolStep
from rsi_arena.core.costs import BudgetExceeded, CostTracker
from rsi_arena.core.trace import Tracer
from rsi_arena.llm import LLMClient


def make_ctx(llm: LLMClient, toolbox, *, state=None, costs=None, on_token=None) -> StepContext:
    tracer = Tracer(agent="test", costs=costs or CostTracker())
    return StepContext(
        llm=llm,
        tools=toolbox,
        tracer=tracer,
        config=llm.config,
        context="You are a test agent.",
        state=state or {"question": "how many?"},
        on_token=on_token,
    )


# --- prompt steps -----------------------------------------------------------


async def test_a_prompt_step_renders_state_into_the_prompt(llm, toolbox, fake):
    ctx = make_ctx(llm, toolbox, state={"question": "did the ECB cut rates?"})
    await PromptStep(name="ask", prompt="Answer: {{question}}").execute(ctx)
    assert "did the ECB cut rates?" in fake.bodies[-1]["messages"][-1]["content"]


async def test_the_agent_context_becomes_the_system_message(llm, toolbox, fake):
    ctx = make_ctx(llm, toolbox)
    await PromptStep(name="ask", prompt="hi").execute(ctx)
    assert fake.bodies[-1]["messages"][0]["content"] == "You are a test agent."


async def test_a_step_system_overrides_the_agent_context(llm, toolbox, fake):
    ctx = make_ctx(llm, toolbox)
    await PromptStep(name="ask", prompt="hi", system="Only this step.").execute(ctx)
    assert fake.bodies[-1]["messages"][0]["content"] == "Only this step."


async def test_output_lands_in_state_under_output_key_and_last(llm, toolbox):
    ctx = make_ctx(llm, toolbox)
    result = await PromptStep(name="ask", prompt="hi", output_key="answer").execute(ctx)
    assert ctx.state["answer"] == result and ctx.state["last"] == result


async def test_an_output_schema_returns_parsed_json(llm, toolbox):
    ctx = make_ctx(llm, toolbox)
    out = await PromptStep(
        name="stop_check",
        prompt="done?",
        output_schema={"type": "object", "properties": {"done": {"type": "boolean"}},
                       "required": ["done"], "additionalProperties": False},
    ).execute(ctx)
    assert out == {"done": True, "reason": "the press release is a primary source"}


async def test_the_schema_is_named_after_the_step(llm, toolbox, fake):
    # Providers echo the schema name back in errors, and "output" in every
    # error is useless.
    ctx = make_ctx(llm, toolbox)
    await PromptStep(name="take_notes", prompt="notes",
                     output_schema={"type": "object"}).execute(ctx)
    assert fake.bodies[-1]["response_format"]["json_schema"]["name"] == "take_notes"


async def test_a_missing_placeholder_stops_the_step(llm, toolbox, fake):
    ctx = make_ctx(llm, toolbox)
    with pytest.raises(KeyError):
        await PromptStep(name="ask", prompt="{{nowhere}}").execute(ctx)
    assert fake.llm_calls == 0, "nothing should have been sent"


async def test_skip_if_skips_without_spending(llm, toolbox, fake):
    ctx = make_ctx(llm, toolbox, state={"question": "q", "have_enough": True})
    await PromptStep(name="ask", prompt="hi", skip_if="have_enough").execute(ctx)
    assert fake.llm_calls == 0
    assert ctx.tracer.root.children[0].status == "skipped"


async def test_memory_carries_the_exchange_forward(llm, toolbox, fake):
    ctx = make_ctx(llm, toolbox)
    await PromptStep(name="one", prompt="first", memory=True).execute(ctx)
    await PromptStep(name="two", prompt="second", memory=True).execute(ctx)
    roles = [m["role"] for m in fake.bodies[-1]["messages"]]
    assert roles.count("assistant") >= 1, "the second call should see the first answer"


async def test_a_step_without_memory_starts_clean(llm, toolbox, fake):
    ctx = make_ctx(llm, toolbox)
    await PromptStep(name="one", prompt="first", memory=True).execute(ctx)
    await PromptStep(name="two", prompt="second").execute(ctx)
    assert all(m["role"] != "assistant" for m in fake.bodies[-1]["messages"])


async def test_citations_accumulate_in_state(llm, toolbox):
    ctx = make_ctx(llm, toolbox)
    await PromptStep(name="ask", prompt="hi").execute(ctx)
    assert ctx.state["citations"][0]["url"] == "https://ecb.europa.eu/x"


# --- the free-form half: a prompt step that drives tools --------------------


async def test_a_tool_enabled_step_runs_the_model_s_tool_loop(llm, toolbox):
    ctx = make_ctx(llm, toolbox)
    await PromptStep(name="work", prompt="count the words", tools=["*"]).execute(ctx)
    kinds = [(s.name, s.kind) for s in ctx.tracer.root.walk()]
    assert ("word_count", "tool") in kinds
    assert ("llm[0]", "llm") in kinds and ("llm[1]", "llm") in kinds


async def test_tools_can_be_narrowed_to_a_subset(llm, toolbox, fake):
    from rsi_arena.agent.tools import tool

    @tool
    def other(x: str) -> str:
        """Other."""
        return x

    toolbox.add(other)
    ctx = make_ctx(llm, toolbox)
    await PromptStep(name="work", prompt="go", tools=["other"]).execute(ctx)
    offered = [t["function"]["name"] for t in fake.bodies[0]["tools"]]
    assert offered == ["other"]


async def test_the_last_tool_iteration_offers_no_tools(llm, toolbox, fake):
    # Otherwise the loop ends on a turn asking for a call nobody will run.
    ctx = make_ctx(llm, toolbox)
    await PromptStep(name="work", prompt="go", tools=["*"], max_tool_iterations=1).execute(ctx)
    assert "tools" not in fake.bodies[-1]


# --- streaming --------------------------------------------------------------


async def test_a_streaming_step_emits_tokens(llm, toolbox):
    seen: list[str] = []
    ctx = make_ctx(llm, toolbox, on_token=seen.append)
    result = await PromptStep(name="write", prompt="hi", stream=True).execute(ctx)
    assert "".join(seen) == result


async def test_streaming_is_ignored_when_the_step_uses_tools(llm, toolbox):
    # A tool loop has several model turns and no single final message.
    seen: list[str] = []
    ctx = make_ctx(llm, toolbox, on_token=seen.append)
    await PromptStep(name="work", prompt="go", tools=["*"], stream=True).execute(ctx)
    assert seen == []


# --- tool steps -------------------------------------------------------------


async def test_a_tool_step_calls_with_templated_arguments(llm, toolbox):
    ctx = make_ctx(llm, toolbox, state={"question": "one two three"})
    out = await ToolStep(name="count", tool="word_count",
                         args={"text": "{{question}}"}, output_key="n").execute(ctx)
    assert out == 3 and ctx.state["n"] == 3


async def test_nested_arguments_are_templated_too(llm, toolbox):
    from rsi_arena.agent.tools import tool

    @tool
    def echo(payload: dict) -> dict:
        """Echo."""
        return payload

    toolbox.add(echo)
    ctx = make_ctx(llm, toolbox, state={"question": "q"})
    out = await ToolStep(name="e", tool="echo",
                         args={"payload": {"nested": ["{{question}}"]}}).execute(ctx)
    assert out == {"nested": ["q"]}


async def test_a_failing_tool_step_raises_by_default(llm, toolbox):
    from rsi_arena.agent.tools import tool

    @tool
    def explode(x: str) -> str:
        """Fails."""
        raise ValueError("no")

    toolbox.add(explode)
    ctx = make_ctx(llm, toolbox)
    with pytest.raises(RuntimeError):
        await ToolStep(name="boom", tool="explode", args={"x": "a"}).execute(ctx)


async def test_fail_ok_returns_the_error_instead(llm, toolbox):
    from rsi_arena.agent.tools import tool

    @tool
    def explode(x: str) -> str:
        """Fails."""
        raise ValueError("no")

    toolbox.add(explode)
    ctx = make_ctx(llm, toolbox)
    out = await ToolStep(name="boom", tool="explode", args={"x": "a"}, fail_ok=True).execute(ctx)
    assert "ValueError" in out["error"]


# --- loops ------------------------------------------------------------------


async def test_a_loop_stops_on_its_condition(llm, toolbox):
    ctx = make_ctx(llm, toolbox, state={"question": "q", "hits": [1, 2]})
    results = await LoopStep(
        name="refine", max_loops=5, until="len(hits) >= 2",
        steps=[PromptStep(name="think", prompt="think")],
    ).execute(ctx)
    assert len(results) == 1, "the condition held after the first pass"
    loop_span = ctx.tracer.root.children[0]
    assert loop_span.attributes["stopped_by"] == "condition"


async def test_max_loops_is_the_backstop(llm, toolbox):
    ctx = make_ctx(llm, toolbox, state={"question": "q", "hits": []})
    results = await LoopStep(
        name="refine", max_loops=3, until="len(hits) >= 99",
        steps=[PromptStep(name="think", prompt="think")],
    ).execute(ctx)
    assert len(results) == 3
    assert ctx.tracer.root.children[0].attributes["stopped_by"] == "max_loops"


async def test_a_model_stop_check_can_end_the_loop(llm, toolbox):
    ctx = make_ctx(llm, toolbox)
    results = await LoopStep(
        name="refine", max_loops=3, until_prompt="Is that enough?",
        steps=[PromptStep(name="think", prompt="think")],
    ).execute(ctx)
    assert len(results) == 1, "the fake judge says done"


@pytest.mark.parametrize("verdict", ["not json at all", '"a bare string"', "[1, 2, 3]"])
async def test_an_unusable_stop_check_never_ends_the_loop_early(llm, toolbox, fake, verdict):
    # Neither ends it early nor crashes it: a model that ignores its schema is
    # a bad answer, not a broken agent. The loop ceiling is the real backstop.
    fake.raw_answers["stop_check"] = verdict
    ctx = make_ctx(llm, toolbox)
    results = await LoopStep(
        name="refine", max_loops=2, until_prompt="enough?",
        steps=[PromptStep(name="think", prompt="think")],
    ).execute(ctx)
    assert len(results) == 2


async def test_loop_bookkeeping_is_visible_to_inner_steps(llm, toolbox):
    ctx = make_ctx(llm, toolbox)
    await LoopStep(
        name="refine", max_loops=2, until="loop_iteration >= 2",
        steps=[ToolStep(name="count", tool="word_count", args={"text": "{{loop_iteration}}"})],
    ).execute(ctx)
    assert ctx.state["loop_iteration"] == 2


async def test_a_nested_loop_restores_the_outer_bookkeeping(llm, toolbox):
    ctx = make_ctx(llm, toolbox)
    await LoopStep(
        name="outer", max_loops=2, steps=[
            LoopStep(name="inner", max_loops=2, steps=[PromptStep(name="t", prompt="t")]),
        ],
    ).execute(ctx)
    assert ctx.state["loop_index"] == 1, "the outer loop's index should survive the inner one"


async def test_collect_false_returns_only_the_last_result(llm, toolbox):
    ctx = make_ctx(llm, toolbox)
    out = await LoopStep(name="refine", max_loops=2, collect=False,
                         steps=[PromptStep(name="think", prompt="think")]).execute(ctx)
    assert isinstance(out, str)


# --- plans ------------------------------------------------------------------


async def test_a_plan_runs_in_order_and_returns_the_last_result(llm, toolbox):
    ctx = make_ctx(llm, toolbox, state={"question": "one two"})
    plan = Plan(steps=[
        ToolStep(name="count", tool="word_count", args={"text": "{{question}}"}, output_key="n"),
        PromptStep(name="write", prompt="There are {{n}} words."),
    ])
    result = await plan.execute(ctx)
    assert ctx.state["n"] == 2 and isinstance(result, str)


async def test_a_step_over_the_ceiling_is_refused_before_it_runs(llm, toolbox, fake):
    ctx = make_ctx(llm, toolbox, costs=CostTracker(max_usd=0.001))
    with pytest.raises(BudgetExceeded):
        await Plan(steps=[PromptStep(name="a", prompt="hi"),
                          PromptStep(name="b", prompt="hi again")]).execute(ctx)
    assert fake.llm_calls == 1, "the second step should never have been sent"


def test_a_plan_round_trips_through_json():
    plan = Plan(steps=[
        ToolStep(name="search", tool="search", args={"q": "{{question}}"}),
        LoopStep(name="refine", max_loops=2, steps=[PromptStep(name="think", prompt="t")]),
    ])
    blob = json.dumps(plan.model_dump())
    assert Plan.model_validate(json.loads(blob)).model_dump() == plan.model_dump()


def test_outline_shows_nesting():
    plan = Plan(steps=[
        PromptStep(name="plan_queries", prompt="p"),
        LoopStep(name="research", steps=[ToolStep(name="search", tool="search")]),
    ])
    outline = plan.outline()
    assert "plan_queries (prompt)" in outline
    assert "  search (tool)" in outline, "an inner step should be indented"


def test_len_counts_top_level_steps():
    assert len(Plan(steps=[PromptStep(name="a", prompt="p")])) == 1


# --- the agent's config reaches a shared client -----------------------------
#
# The client is usually shared — one per process in the backend, one per battle,
# one per eval suite — so a step that leaned on the client's own defaults would
# run on whatever the last caller happened to set.


def test_settings_start_from_the_agent_config(llm, toolbox):
    from rsi_arena.llm import LLMConfig

    ctx = make_ctx(llm, toolbox)
    ctx.config = LLMConfig(model="agent/model", temperature=0.4, max_tokens=500, cache=False)
    settings = ctx.settings()
    assert settings["model"] == "agent/model" and settings["temperature"] == 0.4
    assert settings["max_tokens"] == 500 and settings["cache"] is False


def test_a_step_field_overrides_the_agent_config(llm, toolbox):
    from rsi_arena.llm import LLMConfig

    ctx = make_ctx(llm, toolbox)
    ctx.config = LLMConfig(model="agent/model", temperature=0.4)
    settings = ctx.settings(model="step/model", temperature=None)
    assert settings["model"] == "step/model"
    assert settings["temperature"] == 0.4, "None means 'not specified', not 'zero'"


async def test_a_prompt_step_sends_the_agent_model_not_the_client_default(llm, toolbox, fake):
    from rsi_arena.llm import LLMConfig

    ctx = make_ctx(llm, toolbox)
    ctx.config = LLMConfig(model="agent/model", temperature=0.25, max_tokens=400)
    await PromptStep(name="ask", prompt="hi").execute(ctx)
    sent = fake.bodies[-1]
    assert sent["model"] == "agent/model", "the shared client's model must not win"
    assert sent["temperature"] == 0.25 and sent["max_tokens"] == 400


async def test_a_streaming_step_carries_them_too(llm, toolbox, fake):
    from rsi_arena.llm import LLMConfig

    ctx = make_ctx(llm, toolbox, on_token=lambda _t: None)
    ctx.config = LLMConfig(model="agent/model", temperature=0.25)
    await PromptStep(name="write", prompt="hi", stream=True).execute(ctx)
    assert fake.bodies[-1]["model"] == "agent/model"


async def test_a_loop_stop_check_carries_them_too(llm, toolbox, fake):
    from rsi_arena.llm import LLMConfig

    ctx = make_ctx(llm, toolbox)
    ctx.config = LLMConfig(model="agent/model")
    await LoopStep(name="refine", max_loops=1, until_prompt="enough?",
                   steps=[PromptStep(name="t", prompt="t")]).execute(ctx)
    assert all(body["model"] == "agent/model" for body in fake.bodies)


async def test_cache_off_on_the_agent_reaches_a_shared_client(llm, toolbox, fake):
    from rsi_arena.llm import LLMConfig

    ctx = make_ctx(llm, toolbox)
    ctx.config = LLMConfig(model="agent/model", cache=False)
    await PromptStep(name="ask", prompt="the same prompt").execute(ctx)
    await PromptStep(name="ask", prompt="the same prompt").execute(ctx)
    assert fake.llm_calls == 2, "cache=False on the agent must beat cache=True on the client"
