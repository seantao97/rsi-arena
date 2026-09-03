"""``rsi_arena.evals`` — one eval, and where results go.

The whole requirement in one line: a class that takes an agent, runs it on an
input, scores what came back, and hands you a number with a reason attached.

An eval is the unit the arena ranks on, so the tests that matter are the ones
about what survives the run — the score, why, what was said, and what it was
supposed to be.
"""

from __future__ import annotations

import asyncio

import pytest

from rsi_arena.agent import Agent, AgentConfig, Plan, PromptStep
from rsi_arena.evals import (
    Eval,
    EvalOutput,
    EvalStore,
    InMemoryEvalStore,
    StoredEval,
    contains,
    non_empty,
    scored_by,
)


# --- helpers -----------------------------------------------------------------


def always(score: float, **fields) -> object:
    """An eval function with a fixed verdict."""
    return lambda result: EvalOutput(score=score, **fields)


def echoes(result) -> EvalOutput:
    """Score by what the agent actually said."""
    said = str(result.output or "")
    return EvalOutput(score=1.0 if said else 0.0, comments=said[:40],
                      output={"answer": said})


# --- EvalOutput: the verdict --------------------------------------------------


def test_an_output_needs_nothing_to_exist() -> None:
    """Every field defaults, so a scorer can fill in only what it knows."""
    out = EvalOutput()
    assert out.score == 0.0
    assert out.description == out.comments == ""
    assert out.metadata == out.output == out.ground_truth == {}


def test_the_answer_and_what_it_should_have_been_travel_together() -> None:
    """The commonest question about a low score is not what it scored but what
    it said, against what. Both are on the result, so neither needs a re-run."""
    out = EvalOutput(score=0.0, output={"answer": "1998"},
                     ground_truth={"answer": "1996"})
    assert out.output["answer"] != out.ground_truth["answer"]


# --- Eval: the unit -----------------------------------------------------------


def test_an_eval_holds_the_five_things_it_was_given(simple_agent: Agent) -> None:
    fn = always(1.0)
    ev = Eval(simple_agent, fn, description="a name", input={"question": "hi"})
    assert ev.agent is simple_agent
    assert ev.eval_function is fn
    assert ev.description == "a name"
    assert ev.input == {"question": "hi"}
    assert ev.agent_output is None       # nothing has run yet


async def test_running_scores_what_the_agent_said(simple_agent: Agent, llm) -> None:
    ev = Eval(simple_agent, echoes, input={"question": "Say hello."})
    out = await ev.run(llm=llm)
    assert out.score == 1.0
    assert out.output["answer"]


async def test_the_run_is_kept_so_a_score_can_be_traced(simple_agent: Agent, llm) -> None:
    """A score with no way back to the run that produced it cannot be argued
    with, which is most of what a leaderboard is for."""
    ev = Eval(simple_agent, echoes, input={"question": "Say hello."})
    await ev.run(llm=llm)
    assert ev.agent_output is not None
    assert ev.agent_output.trace is not None


async def test_an_eval_names_its_own_results(simple_agent: Agent, llm) -> None:
    """Otherwise every caller labels them, and most forget."""
    ev = Eval(simple_agent, always(1.0), description="the name",
              input={"question": "hi"})
    assert (await ev.run(llm=llm)).description == "the name"


async def test_a_scorer_that_names_itself_is_left_alone(simple_agent: Agent, llm) -> None:
    ev = Eval(simple_agent, always(1.0, description="its own"),
              description="the eval's", input={"question": "hi"})
    assert (await ev.run(llm=llm)).description == "its own"


async def test_overrides_beat_the_stored_input(simple_agent: Agent, llm) -> None:
    """Running one eval across a sweep of questions is the whole reason the
    input is held rather than passed."""
    seen = {}

    def watch(result) -> EvalOutput:
        seen["question"] = result.trace.root.input
        return EvalOutput(score=1.0)

    ev = Eval(simple_agent, watch, input={"question": "the original"})
    await ev.run(question="the override", llm=llm)
    assert "override" in str(seen["question"])


async def test_a_plain_function_is_a_valid_eval_function(simple_agent: Agent, llm) -> None:
    """No registry, no base class. A scorer is ordinary code."""
    out = await Eval(simple_agent, lambda r: EvalOutput(score=0.5),
                     input={"question": "hi"}).run(llm=llm)
    assert out.score == 0.5


# --- scored_by: the built-in scorers still work -------------------------------


async def test_a_built_in_scorer_can_be_adapted(simple_agent: Agent, llm) -> None:
    """`scoring.py` answers with a Score — a value, a verdict and a note, which
    is the same verdict in a narrower shape. Wrapping is a rename."""
    out = await Eval(simple_agent, scored_by(non_empty()),
                     input={"question": "Say hello."}).run(llm=llm)
    assert out.score == 1.0
    assert "passed" in out.metadata


async def test_an_adapted_scorer_that_fails_scores_zero(simple_agent: Agent, llm) -> None:
    out = await Eval(simple_agent, scored_by(contains("a string nobody says")),
                     input={"question": "Say hello."}).run(llm=llm)
    assert out.score == 0.0
    assert out.metadata["passed"] is False


# --- the store ----------------------------------------------------------------


async def test_identity_belongs_to_the_store_not_the_verdict() -> None:
    """An EvalOutput is a verdict; an id is a fact about having stored one. So
    an eval can be run and read with no store anywhere in the picture."""
    assert not hasattr(EvalOutput(), "id")
    store = InMemoryEvalStore()
    eval_id = await store.save(EvalOutput(score=1.0), agent="a", name="n")
    assert eval_id and (await store.get(eval_id)).output.score == 1.0


async def test_listing_is_newest_first_and_filters() -> None:
    store = InMemoryEvalStore()
    for i in range(3):
        await store.save(EvalOutput(score=float(i)), agent="a" if i < 2 else "b",
                         name="n")
        await asyncio.sleep(0.001)          # created_at is a float clock
    assert [r.output.score for r in await store.list()] == [2.0, 1.0, 0.0]
    assert await store.count(agent="a") == 2
    assert len(await store.list(agent="b")) == 1


async def test_the_store_is_bounded_and_evicts_oldest_first() -> None:
    """A long-running server cannot be allowed to grow without end."""
    store = InMemoryEvalStore(max_results=2)
    for i in range(4):
        await store.save(EvalOutput(score=float(i)), agent="a")
    assert await store.count() == 2
    assert sorted(r.output.score for r in await store.list()) == [2.0, 3.0]


async def test_delete_and_clear() -> None:
    store = InMemoryEvalStore()
    eval_id = await store.save(EvalOutput(), agent="a")
    assert await store.delete(eval_id) is True
    assert await store.delete(eval_id) is False
    await store.save(EvalOutput(), agent="a")
    await store.clear()
    assert await store.count() == 0


async def test_the_leaderboard_is_counts_not_a_rating() -> None:
    """Mean score per agent, best first. It says what happened; it does not
    model skill, and calling it a rating would imply it did."""
    store = InMemoryEvalStore()
    for score in (1.0, 1.0):
        await store.save(EvalOutput(score=score), agent="good", name="n")
    for score in (0.0, 1.0):
        await store.save(EvalOutput(score=score), agent="mixed", name="n")
    table = await store.leaderboard(name="n")
    assert [row["agent"] for row in table] == ["good", "mixed"]
    assert table[0]["mean_score"] == 1.0 and table[0]["runs"] == 2
    assert table[1]["mean_score"] == 0.5


async def test_a_row_carries_the_score_without_the_payload() -> None:
    """A listing of a thousand results should not drag a thousand traces."""
    store = InMemoryEvalStore()
    eval_id = await store.save(
        EvalOutput(score=0.5, comments="why", output={"big": "payload"}),
        agent="a", name="n")
    row = (await store.get(eval_id)).row()
    assert row["score"] == 0.5 and row["comments"] == "why"
    assert "output" not in row


def test_the_store_interface_is_abstract() -> None:
    with pytest.raises(TypeError):
        EvalStore()                          # type: ignore[abstract]


async def test_a_custom_store_drops_straight_in() -> None:
    """Async throughout so a real database is a subclass, not a rewrite."""

    class Counting(InMemoryEvalStore):
        saves = 0

        async def save(self, output, *, agent="", name=""):
            type(self).saves += 1
            return await super().save(output, agent=agent, name=name)

    store = Counting()
    await store.save(EvalOutput())
    assert Counting.saves == 1
    assert isinstance(await store.list(), list)


async def test_an_eval_can_score_without_running(simple_agent: Agent) -> None:
    """Not every eval can run its agent at the moment it scores. A forecast
    about the next five minutes is answerable five minutes later, and one about
    a match after the whistle — by which time the run is gone and lives in a
    file. Those score what was recorded."""
    ev = Eval(simple_agent, lambda r: EvalOutput(score=0.75, comments="from a file"),
              description="deferred")
    out = await ev.score(None)
    assert out.score == 0.75
    assert out.description == "deferred", "still names itself"
    assert ev.agent_output is None, "nothing ran"


async def test_run_is_score_with_the_run_in_front_of_it(simple_agent: Agent, llm) -> None:
    """One scoring path, so the two cannot drift."""
    ev = Eval(simple_agent, echoes, input={"question": "Say hello."})
    ran = await ev.run(llm=llm)
    scored = await ev.score(ev.agent_output)
    assert ran.model_dump() == scored.model_dump()


# --- a run that never finished is not a bad answer ---------------------------


class Crashed:
    """An AgentResult that carries an error and no answer."""

    output = None
    error = "OpenRouterError: OPENROUTER_API_KEY is not set in the environment"
    error_kind = "provider"


class BailedOut:
    """Stopped at its ceiling, but answered from what it already had."""

    output = "a short answer written from state"
    error = "budget exhausted"
    error_kind = "max_spend"


async def test_a_crashed_run_never_reaches_the_scoring_function(simple_agent: Agent) -> None:
    """An expired key, a bad template and a harness that genuinely cannot
    answer would otherwise be indistinguishable at the bottom of a leaderboard,
    which reads infrastructure failure as evidence about the harness."""
    def explode(result):
        raise AssertionError("the scoring function should not have been called")

    out = await Eval(simple_agent, explode, description="d").score(Crashed())
    assert out.score == 0.0
    assert out.metadata["run_failed"] is True
    assert out.metadata["error_kind"] == "provider"
    assert "did not finish" in out.comments


async def test_an_answered_cut_off_is_still_scored(simple_agent: Agent) -> None:
    """A run stopped at its budget ceiling carries an error *and* an answer
    written from what it already had. Grading that as a dead run would remove
    the reason to bail out at all."""
    out = await Eval(simple_agent, echoes).score(BailedOut())
    assert out.score == 1.0
    assert not out.metadata.get("run_failed")


async def test_every_eval_gets_the_guard_without_asking(simple_agent: Agent) -> None:
    """It is in the core, so no eval has to remember to check."""
    out = await Eval(simple_agent, lambda r: EvalOutput(score=1.0)).score(Crashed())
    assert out.score == 0.0


# --- an eval that cannot run is refused before it is paid for -----------------


async def test_a_run_missing_an_input_its_plan_reads_is_refused(config, llm) -> None:
    needs_game = Agent(
        name="needs-game", context="c", config=config,
        plan=Plan(steps=[PromptStep(name="a", prompt="{{question}} {{game}}",
                                    output_key="a")]))
    ev = Eval(needs_game, echoes, input={"question": "hi"})
    with pytest.raises(ValueError) as exc:
        await ev.run(llm=llm)
    assert "game" in str(exc.value)
    assert ev.agent_output is None, "refused before the agent was called"


async def test_an_eval_that_never_runs_owes_no_inputs(simple_agent: Agent) -> None:
    """One that grades a recorded feed has no business supplying the agent's
    inputs — which is why the check lives in run() and not the constructor."""
    ev = Eval(simple_agent, lambda r: EvalOutput(score=0.25), description="feed")
    assert (await ev.score(None)).score == 0.25
