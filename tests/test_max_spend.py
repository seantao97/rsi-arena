"""Max-spend mode and ``Agent.immediate_answer``.

The behaviour under test: when a run hits its ceiling with ``max_spend_mode``
on, it does not return nothing. It opens a small reserve, spends it on one
model call that turns whatever state it gathered into an answer, and records
the whole thing as a distinct kind of error — ``max_spend`` — so a cut-off
answer is never quietly counted as a clean one.
"""

from __future__ import annotations

import httpx

from rsi_arena.agent import Agent, AgentConfig, ErrorKind, Plan, PromptStep, ToolStep
from rsi_arena.agent.agent import BAILOUT_INSTRUCTIONS, bailout_prompt
from rsi_arena.core.costs import MaxSpendExceeded
from rsi_arena.core.ratelimit import RateLimit
from rsi_arena.llm import LLMClient
from tests.fakes import Fake


def spender(config: AgentConfig, toolbox) -> Agent:
    """Three prompt steps, so the ceiling can bite in the middle of the plan."""
    return Agent(
        name="spender",
        context="You are a careful research agent.",
        tools=toolbox,
        config=config,
        plan=Plan(steps=[
            ToolStep(name="count", tool="word_count", args={"text": "{{question}}"},
                     output_key="n"),
            PromptStep(name="research", prompt="Research {{question}}", output_key="notes"),
            PromptStep(name="more", prompt="Dig deeper into {{notes}}", output_key="deeper"),
            PromptStep(name="write", prompt="Write up {{deeper}}", output_key="memo"),
        ]),
    )


# --- immediate_answer on its own --------------------------------------------


def test_the_bailout_prompt_carries_the_question_state_and_reason():
    prompt = bailout_prompt("Did the ECB cut rates?", {"notes": "Rates held.", "question": "q"},
                            reason="budget exceeded")
    assert BAILOUT_INSTRUCTIONS in prompt
    assert "Did the ECB cut rates?" in prompt
    assert "## notes" in prompt and "Rates held." in prompt
    assert "budget exceeded" in prompt


def test_the_bailout_prompt_says_so_when_there_is_nothing_to_go_on():
    assert "nothing" in bailout_prompt("q", {})


async def test_immediate_answer_is_one_call_with_state_as_input(
    simple_agent: Agent, llm: LLMClient, fake: Fake
):
    text = await simple_agent.immediate_answer(
        {"notes": "The ECB held rates.", "sources": ["https://ecb.europa.eu/x"]},
        question="Did the ECB cut rates?",
        llm=llm,
    )
    assert text == fake.text
    assert fake.llm_calls == 1, "answering now is one call, not a plan"
    sent = fake.bodies[-1]
    assert "The ECB held rates." in sent["messages"][-1]["content"]
    assert sent["messages"][0]["content"] == simple_agent.context, "its own context still applies"


async def test_immediate_answer_offers_no_tools(simple_agent: Agent, llm: LLMClient, fake: Fake):
    # There is no budget left to gather anything more, so a tool it could call
    # is a tool it should not be offered.
    await simple_agent.immediate_answer({"notes": "n"}, llm=llm)
    assert "tools" not in fake.bodies[-1]


async def test_immediate_answer_can_be_traced(simple_agent: Agent, llm: LLMClient):
    from rsi_arena.core.trace import Tracer

    tracer = Tracer(agent="a")
    await simple_agent.immediate_answer({"notes": "n"}, llm=llm, tracer=tracer, reason="out")
    span = tracer.root.children[0]
    assert span.name == "immediate_answer" and span.kind == "llm"


async def test_immediate_answer_uses_the_bailout_model_when_one_is_set(
    simple_agent: Agent, llm: LLMClient, fake: Fake
):
    simple_agent.config.bailout_model = "cheap/model"
    await simple_agent.immediate_answer({"notes": "n"}, llm=llm)
    assert fake.bodies[-1]["model"] == "cheap/model"


async def test_immediate_answer_caps_its_own_length(simple_agent: Agent, llm: LLMClient,
                                                    fake: Fake):
    simple_agent.config.bailout_max_tokens = 250
    await simple_agent.immediate_answer({"notes": "n"}, llm=llm)
    assert fake.bodies[-1]["max_tokens"] == 250


# --- max_spend_mode during a run --------------------------------------------


async def test_without_max_spend_mode_the_ceiling_returns_nothing(config: AgentConfig, toolbox,
                                                                  llm: LLMClient):
    config = config.model_copy(update={"max_usd": 0.0015, "cache": False})
    result = await spender(config, toolbox).run("one two three", llm=llm)
    assert result.error_kind is ErrorKind.BUDGET
    assert result.bailed_out is False
    assert result.output in (None, "", result.state.get("last"))


async def test_max_spend_mode_answers_from_state_instead(config: AgentConfig, toolbox,
                                                         llm: LLMClient, fake: Fake):
    config = config.model_copy(update={
        "max_usd": 0.0015, "cache": False, "max_spend_mode": True, "bailout_reserve_usd": 0.05,
    })
    result = await spender(config, toolbox).run("one two three", llm=llm)

    assert result.bailed_out is True
    assert result.output == fake.text, "the bail-out answer is the run's output"
    assert result.state["bailout_answer"] == result.output


async def test_the_bailout_is_recorded_as_its_own_kind_of_error(config: AgentConfig, toolbox,
                                                                llm: LLMClient):
    config = config.model_copy(update={
        "max_usd": 0.0015, "cache": False, "max_spend_mode": True,
    })
    result = await spender(config, toolbox).run("one two three", llm=llm)

    # An answer, but not a clean run: the arena must be able to tell them apart.
    assert result.ok is False
    assert result.error_kind is ErrorKind.MAX_SPEND
    assert "MaxSpendExceeded" in (result.error or "")
    assert result.summary()["error_kind"] == "max_spend"
    assert result.summary()["bailed_out"] is True


async def test_the_bailout_call_appears_in_the_trace_and_the_ledger(config: AgentConfig, toolbox,
                                                                    llm: LLMClient):
    config = config.model_copy(update={
        "max_usd": 0.0015, "cache": False, "max_spend_mode": True,
    })
    result = await spender(config, toolbox).run("one two three", llm=llm)

    assert any(s.name == "immediate_answer" for s in result.trace.spans())
    assert result.trace.root.attributes["max_spend_bailout"] is True
    # The real cost, not the allowance: a call is only priced once it is made.
    assert result.cost_usd > config.max_usd
    assert result.summary()["reserve_used_usd"] > 0


async def test_the_bailout_sees_what_the_plan_managed_to_gather(config: AgentConfig, toolbox,
                                                                llm: LLMClient, fake: Fake):
    config = config.model_copy(update={
        "max_usd": 0.0015, "cache": False, "max_spend_mode": True,
    })
    await spender(config, toolbox).run("one two three", llm=llm)
    prompt = fake.bodies[-1]["messages"][-1]["content"]
    assert "## notes" in prompt, "the research step's output should be in the bail-out prompt"
    assert "## n" in prompt, "so should the tool step's"


async def test_the_reserve_is_not_spendable_by_the_plan(config: AgentConfig, toolbox,
                                                        llm: LLMClient, fake: Fake):
    # The reserve exists for the bail-out; a plan that could dip into it would
    # simply have a slightly higher ceiling and no bail-out budget.
    config = config.model_copy(update={
        "max_usd": 0.0015, "cache": False, "max_spend_mode": True, "bailout_reserve_usd": 1.0,
    })
    result = await spender(config, toolbox).run("one two three", llm=llm)
    assert result.bailed_out is True
    steps_run = {s.name for s in result.trace.spans()}
    assert "write" not in steps_run, "the last step must not have run on reserve money"


async def test_a_call_cap_also_triggers_the_bailout(config: AgentConfig, toolbox, llm: LLMClient):
    config = config.model_copy(update={
        "max_usd": 10.0, "max_calls": 2, "cache": False, "max_spend_mode": True,
    })
    result = await spender(config, toolbox).run("one two three", llm=llm)
    assert result.bailed_out is True and result.error_kind is ErrorKind.MAX_SPEND


async def test_a_failed_bailout_is_still_a_max_spend_error(config: AgentConfig, toolbox, cache):
    """The bail-out itself can fail. That must not hide why the run stopped."""
    calls = {"n": 0}

    def dies_after_two(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        if calls["n"] > 2:
            return httpx.Response(402, json={"error": {"code": 402, "message": "no credits"}})
        return Fake().handle(request)

    llm = LLMClient(api_key="test", cache=cache, auto_pricing=False,
                    http_client=httpx.AsyncClient(transport=httpx.MockTransport(dies_after_two)),
                    rate_limit=RateLimit(per_second=1000))
    config = config.model_copy(update={
        "max_usd": 0.0015, "cache": False, "max_spend_mode": True,
    })
    result = await spender(config, toolbox).run("one two three", llm=llm)

    assert result.bailed_out is False, "there is no answer to hand back"
    assert result.error_kind is ErrorKind.MAX_SPEND
    assert "no answer" in (result.error or "")
    assert result.trace.root.attributes["bailout_failed"].startswith("OpenRouterError")
    await llm.close()


async def test_a_run_that_never_hits_the_ceiling_is_untouched(config: AgentConfig, toolbox,
                                                              llm: LLMClient):
    config = config.model_copy(update={"max_usd": 5.0, "max_spend_mode": True})
    result = await spender(config, toolbox).run("one two three", llm=llm)
    assert result.ok and result.bailed_out is False and result.error_kind is None
    assert not any(s.name == "immediate_answer" for s in result.trace.spans())


def test_max_spend_exceeded_reports_whether_it_got_an_answer():
    answered = MaxSpendExceeded(1.05, 1.0, "llm:m", reserve_usd=0.05, answered=True)
    assert answered.answered and "answered from state" in str(answered)
    assert "no answer" in str(MaxSpendExceeded(1.0, 1.0, "llm:m", answered=False))


async def test_immediate_answer_sends_the_agents_settings_to_a_shared_client(
    simple_agent: Agent, llm: LLMClient, fake: Fake
):
    simple_agent.config.default_model = "picked/model"
    simple_agent.config.temperature = 0.15
    await simple_agent.immediate_answer({"notes": "n"}, llm=llm)
    assert fake.bodies[-1]["model"] == "picked/model"
    assert fake.bodies[-1]["temperature"] == 0.15
