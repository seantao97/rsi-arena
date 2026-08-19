"""``rsi_arena.evals.eval`` — one eval, a suite of them, and the store.

The whole requirement in one line: a class that takes an agent, gives it a
prompt, scores the text that comes back, and keeps the result.
"""

from __future__ import annotations

import json

import pytest

from rsi_arena.agent import Agent, AgentConfig, ErrorKind, Plan, PromptStep
from rsi_arena.evals import (
    Eval,
    EvalResult,
    EvalStore,
    EvalSuite,
    InMemoryEvalStore,
    Score,
    completed,
    contains,
    default_eval_store,
    non_empty,
)


# --- one eval ---------------------------------------------------------------


def test_the_constructor_resolves_the_scorer(simple_agent: Agent):
    # Resolved here so a bad scorer raises before the agent spends anything.
    ev = Eval(simple_agent, "a question", {"type": "contains", "value": "ecb"})
    assert callable(ev.scorer)


def test_a_bad_scorer_raises_at_construction_not_after_a_paid_run(simple_agent: Agent):
    with pytest.raises(KeyError):
        Eval(simple_agent, "a question", "no_such_scorer")


def test_the_name_defaults_to_the_agent_and_the_prompt(simple_agent: Agent):
    assert Eval(simple_agent, "Did the ECB cut rates?", non_empty()).name.startswith("simple:")
    assert Eval(simple_agent, "q", non_empty(), name="mine").name == "mine"


async def test_running_one_scores_the_agent_s_text(simple_agent: Agent, llm, fake):
    result = await Eval(simple_agent, "Did the ECB cut rates?", contains("unchanged")).run(llm=llm)
    assert isinstance(result, EvalResult)
    assert result.agent == "simple" and result.prompt == "Did the ECB cut rates?"
    assert result.output == fake.text
    assert result.score.passed is True and result.score.label == "contains"
    assert result.ok and result.cost_usd > 0 and result.run_id


async def test_a_plain_function_works_as_the_scorer(simple_agent: Agent, llm):
    result = await Eval(simple_agent, "q", lambda output: "ECB" in output).run(llm=llm)
    assert result.score.passed is True


async def test_the_expected_answer_reaches_the_scorer(simple_agent: Agent, llm):
    def scorer(output: str, ctx) -> bool:
        return ctx.expected == "rates held"

    result = await Eval(simple_agent, "q", scorer, expected="rates held").run(llm=llm)
    assert result.score.passed is True


async def test_extra_inputs_are_passed_to_the_run(simple_agent: Agent, llm, fake):
    simple_agent.plan.steps[0].prompt = "{{question}} for {{audience}}"
    await Eval(simple_agent, "explain", non_empty(), inputs={"audience": "a child"}).run(llm=llm)
    assert "a child" in fake.bodies[-1]["messages"][-1]["content"]


async def test_a_failing_agent_is_a_data_point_not_an_exception(config: AgentConfig, llm):
    broken = Agent(name="broken", context="", config=config,
                   plan=Plan(steps=[PromptStep(name="a", prompt="{{nowhere}}")]))
    result = await Eval(broken, "q", non_empty()).run(llm=llm)
    assert result.ok is False and result.error_kind is ErrorKind.PLAN
    assert result.score.passed is False, "no output means the scorer fails it"


async def test_a_dict_output_is_scored_as_json_text(config: AgentConfig, llm):
    agent = Agent(name="structured", context="", config=config, plan=Plan(steps=[
        PromptStep(name="answer", prompt="q",
                   output_schema={"type": "object",
                                  "properties": {"answer": {"type": "string"}},
                                  "required": ["answer"], "additionalProperties": False}),
    ]))
    result = await Eval(agent, "q", contains('"answer"')).run(llm=llm)
    assert result.score.passed is True
    assert json.loads(result.output)["answer"] == "42"


async def test_the_trace_is_kept_by_default_and_droppable(simple_agent: Agent, llm):
    kept = await Eval(simple_agent, "q", non_empty()).run(llm=llm)
    assert kept.trace is not None and kept.trace.root.name == "simple"
    dropped = await Eval(simple_agent, "q", non_empty(), keep_trace=False).run(llm=llm)
    assert dropped.trace is None


async def test_a_result_serialises_and_flattens_to_a_row(simple_agent: Agent, llm):
    result = await Eval(simple_agent, "q", contains("ECB")).run(llm=llm)
    json.dumps(result.model_dump(mode="json"))
    row = result.row()
    assert set(row) >= {"id", "agent", "score", "passed", "cost_usd", "error_kind"}
    json.dumps(row)


def test_passed_falls_back_to_the_number_when_there_is_no_verdict():
    assert EvalResult(score=Score(value=0.9)).passed is True
    assert EvalResult(score=Score(value=0.0)).passed is False


# --- eval-level ceilings ----------------------------------------------------


async def test_an_eval_can_tighten_the_ceiling_without_mutating_the_agent(simple_agent: Agent,
                                                                          llm):
    original = simple_agent.config.max_usd
    await Eval(simple_agent, "q", non_empty(), max_usd=0.0001).run(llm=llm)
    assert simple_agent.config.max_usd == original, "a suite shares one agent object"


async def test_max_spend_mode_is_scored_as_a_bailout(config: AgentConfig, toolbox, llm, fake):
    agent = Agent(name="spender", context="", tools=toolbox, config=config, plan=Plan(steps=[
        PromptStep(name="research", prompt="Research {{question}}", output_key="notes"),
        PromptStep(name="more", prompt="More on {{notes}}", output_key="deeper"),
        PromptStep(name="write", prompt="Write {{deeper}}"),
    ]))
    result = await Eval(agent, "q", completed(), max_usd=0.0015,
                        max_spend_mode=True).run(llm=llm)

    assert result.bailed_out is True
    assert result.error_kind is ErrorKind.MAX_SPEND
    assert result.output == fake.text, "there is an answer to score"
    assert result.score.value == 0.5, "an answered cut-off beats a dead run"


async def test_without_max_spend_mode_the_same_ceiling_scores_zero(config: AgentConfig, toolbox,
                                                                   llm):
    agent = Agent(name="spender", context="", tools=toolbox, config=config, plan=Plan(steps=[
        PromptStep(name="research", prompt="Research {{question}}", output_key="notes"),
        PromptStep(name="more", prompt="More on {{notes}}", output_key="deeper"),
    ]))
    result = await Eval(agent, "q", completed(), max_usd=0.0015).run(llm=llm)
    assert result.error_kind is ErrorKind.BUDGET and result.score.value == 0.0


# --- storing ----------------------------------------------------------------


async def test_a_run_is_stored_by_default(simple_agent: Agent, llm, eval_store):
    result = await Eval(simple_agent, "q", non_empty()).run(llm=llm)
    assert await eval_store.get(result.id) is not None
    assert default_eval_store() is eval_store


async def test_saving_can_be_turned_off(simple_agent: Agent, llm, eval_store):
    await Eval(simple_agent, "q", non_empty()).run(llm=llm, save=False)
    assert await eval_store.count() == 0


async def test_an_explicit_store_wins_over_the_default(simple_agent: Agent, llm, eval_store):
    mine = InMemoryEvalStore()
    result = await Eval(simple_agent, "q", non_empty(), store=mine).run(llm=llm)
    assert await mine.get(result.id) is not None
    assert await eval_store.count() == 0


# --- suites -----------------------------------------------------------------


async def test_a_suite_runs_every_eval_and_aggregates(simple_agent: Agent, llm):
    suite = EvalSuite([
        Eval(simple_agent, "q1", contains("ECB"), name="finds-ecb"),
        Eval(simple_agent, "q2", contains("never appears"), name="impossible"),
    ], name="demo")
    result = await suite.run(llm=llm)

    agg = result.aggregate()
    assert agg["evals"] == 2 and agg["passed"] == 1 and agg["pass_rate"] == 0.5
    assert agg["mean_score"] == 0.5 and agg["cost_usd"] > 0
    assert agg["errors_by_kind"] == {}


async def test_a_suite_stores_the_run_and_every_result(simple_agent: Agent, llm, eval_store):
    suite = EvalSuite([Eval(simple_agent, "q1", non_empty()),
                       Eval(simple_agent, "q2", non_empty())], name="demo")
    result = await suite.run(llm=llm)
    assert await eval_store.get_suite(result.id) is not None
    assert await eval_store.count() == 2


async def test_a_suite_saves_nothing_when_it_is_told_not_to(simple_agent: Agent, llm, eval_store):
    await EvalSuite([Eval(simple_agent, "q", non_empty())]).run(llm=llm, save=False)
    assert await eval_store.count() == 0


async def test_over_crosses_agents_with_cases(simple_agent: Agent, config: AgentConfig, llm):
    other = Agent(name="other", context="", config=config,
                  plan=Plan(steps=[PromptStep(name="a", prompt="{{question}}")]))
    suite = EvalSuite.over([simple_agent, other],
                           [("q1", contains("ECB")), ("q2", non_empty())])
    result = await suite.run(llm=llm)
    assert len(result.results) == 4
    assert {r.agent for r in result.results} == {"simple", "other"}


async def test_a_suite_table_reads_as_a_table(simple_agent: Agent, llm):
    result = await EvalSuite([Eval(simple_agent, "q", non_empty(), name="one")]).run(llm=llm)
    table = result.table()
    assert "one" in table and "simple" in table and "1 evals" in table


async def test_concurrency_is_bounded(simple_agent: Agent, llm):
    suite = EvalSuite([Eval(simple_agent, f"q{i}", non_empty()) for i in range(6)],
                      concurrency=2)
    result = await suite.run(llm=llm)
    assert len(result.results) == 6 and all(r.ok for r in result.results)


# --- the store --------------------------------------------------------------


async def test_the_store_lists_newest_first_and_filters(eval_store: InMemoryEvalStore):
    for index in range(3):
        await eval_store.save(EvalResult(name=f"e{index}", agent="a" if index else "b",
                                         created_at=100 + index))
    assert [r.name for r in await eval_store.list()] == ["e2", "e1", "e0"]
    assert [r.name for r in await eval_store.list(agent="b")] == ["e0"]
    assert await eval_store.count(agent="a") == 2
    assert [r.name for r in await eval_store.list(limit=1, offset=1)] == ["e1"]


async def test_delete_and_clear(eval_store: InMemoryEvalStore):
    result = EvalResult(name="one")
    await eval_store.save(result)
    assert await eval_store.delete(result.id) is True
    assert await eval_store.delete(result.id) is False
    await eval_store.save(EvalResult(name="two"))
    await eval_store.clear()
    assert await eval_store.count() == 0


async def test_the_store_evicts_oldest_first(eval_store):
    store = InMemoryEvalStore(max_results=2)
    kept = [EvalResult(name=f"e{i}") for i in range(3)]
    for result in kept:
        await store.save(result)
    assert await store.get(kept[0].id) is None
    assert await store.get(kept[2].id) is not None


async def test_the_leaderboard_is_counts_not_a_rating(eval_store: InMemoryEvalStore):
    await eval_store.save(EvalResult(agent="a", score=Score(value=1.0, passed=True),
                                     cost_usd=0.01))
    await eval_store.save(EvalResult(agent="a", score=Score(value=0.0, passed=False),
                                     cost_usd=0.02))
    await eval_store.save(EvalResult(agent="b", score=Score(value=1.0, passed=True),
                                     cost_usd=0.03, bailed_out=True, ok=False))
    board = await eval_store.leaderboard()
    by_agent = {row["agent"]: row for row in board}
    assert by_agent["a"]["mean_score"] == 0.5 and by_agent["a"]["pass_rate"] == 0.5
    assert by_agent["a"]["cost_usd"] == pytest.approx(0.03)
    assert by_agent["b"]["bailed_out"] == 1 and by_agent["b"]["errors"] == 1
    assert board[0]["agent"] == "b", "sorted by mean score"


def test_the_store_interface_is_abstract():
    with pytest.raises(TypeError):
        EvalStore()  # type: ignore[abstract]


async def test_a_custom_store_drops_straight_in(simple_agent: Agent, llm):
    """The seam a database arrives through: implement six methods, nothing else changes."""

    class ListStore(InMemoryEvalStore):
        def __init__(self) -> None:
            super().__init__()
            self.saved: list[str] = []

        async def save(self, result):
            self.saved.append(result.id)
            return await super().save(result)

    store = ListStore()
    result = await Eval(simple_agent, "q", non_empty(), store=store).run(llm=llm)
    assert store.saved == [result.id]
