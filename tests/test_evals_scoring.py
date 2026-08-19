"""``rsi_arena.evals.scoring`` — scores, the built-in scorers and the registry."""

from __future__ import annotations

import pytest

from rsi_arena.agent import Agent, AgentResult
from rsi_arena.core.trace import Tracer
from rsi_arena.evals.scoring import (
    SCORERS,
    EvalContext,
    Score,
    all_of,
    apply,
    completed,
    contains,
    get_scorer,
    json_valid,
    llm_judge,
    non_empty,
    not_contains,
    regex,
    register_scorer,
    scorer_from_spec,
    under_cost,
)


def make_ctx(simple_agent: Agent, *, llm=None, expected=None, **result_kwargs) -> EvalContext:
    trace = Tracer(agent="simple").finish()
    result = AgentResult(agent="simple", run_id="r1", trace=trace, **result_kwargs)
    return EvalContext(prompt="a question", result=result, agent=simple_agent, llm=llm,
                       expected=expected)


# --- Score ------------------------------------------------------------------


@pytest.mark.parametrize(
    "raw,value,passed",
    [
        (True, 1.0, True),
        (False, 0.0, False),
        (0.75, 0.75, None),
        (1, 1.0, None),
        (None, 0.0, None),
        ({"value": 0.5, "passed": True}, 0.5, True),
    ],
)
def test_score_coerces_what_a_scorer_returned(raw, value, passed):
    score = Score.of(raw)
    assert score.value == value and score.passed is passed


def test_a_string_becomes_a_failing_score_that_explains_itself():
    score = Score.of("the answer never mentions a date")
    assert score.value == 0.0 and "never mentions a date" in score.notes


def test_an_unusable_return_value_raises():
    with pytest.raises(TypeError):
        Score.of(object())


def test_truthiness_prefers_the_verdict_then_the_number():
    assert bool(Score(value=0.0, passed=True)) is True
    assert bool(Score(value=0.9, passed=False)) is False
    assert bool(Score(value=0.9)) is True


# --- apply ------------------------------------------------------------------


async def test_a_one_argument_scorer_gets_the_output(simple_agent: Agent):
    score = await apply(lambda output: "yes" in output, "yes indeed", make_ctx(simple_agent))
    assert score.passed is True


async def test_a_two_argument_scorer_gets_the_context(simple_agent: Agent):
    def scorer(output: str, ctx: EvalContext) -> bool:
        return ctx.prompt == "a question" and ctx.agent.name == "simple"

    assert (await apply(scorer, "x", make_ctx(simple_agent))).passed is True


async def test_an_async_scorer_is_awaited(simple_agent: Agent):
    async def scorer(output: str) -> float:
        return 0.42

    assert (await apply(scorer, "x", make_ctx(simple_agent))).value == 0.42


async def test_a_broken_scorer_is_a_failed_row_not_a_lost_run(simple_agent: Agent):
    def scorer(output: str) -> bool:
        raise ZeroDivisionError("oops")

    score = await apply(scorer, "x", make_ctx(simple_agent))
    assert score.passed is False and score.label == "scorer_error"
    assert "ZeroDivisionError" in score.notes


# --- built-in scorers -------------------------------------------------------


async def test_contains(simple_agent: Agent):
    ctx = make_ctx(simple_agent)
    assert (await apply(contains("unchanged"), "rates unchanged", ctx)).passed is True
    assert (await apply(contains("UNCHANGED"), "rates unchanged", ctx)).passed is True
    assert (await apply(contains("UNCHANGED", case_sensitive=True), "unchanged", ctx)).passed is False


async def test_contains_several_needles(simple_agent: Agent):
    ctx = make_ctx(simple_agent)
    both = contains(["ecb", "2026"])
    assert (await apply(both, "the ECB in 2026", ctx)).passed is True
    partial = await apply(both, "the ECB", ctx)
    assert partial.passed is False and partial.value == 0.5
    assert partial.details["missing"] == ["2026"]
    assert (await apply(contains(["ecb", "2026"], all_of=False), "the ECB", ctx)).passed is True


async def test_not_contains(simple_agent: Agent):
    ctx = make_ctx(simple_agent)
    assert (await apply(not_contains("as an AI"), "The ECB held rates.", ctx)).passed is True
    assert (await apply(not_contains("as an AI"), "As an AI, I cannot", ctx)).passed is False


async def test_regex(simple_agent: Agent):
    ctx = make_ctx(simple_agent)
    assert (await apply(regex(r"https?://\S+"), "see https://ecb.europa.eu/x", ctx)).passed is True
    assert (await apply(regex(r"\d{4}-\d{2}-\d{2}"), "no date here", ctx)).passed is False


async def test_non_empty_scales_with_length(simple_agent: Agent):
    ctx = make_ctx(simple_agent)
    assert (await apply(non_empty(10), "x" * 20, ctx)).passed is True
    short = await apply(non_empty(100), "x" * 50, ctx)
    assert short.passed is False and short.value == 0.5


async def test_json_valid_tolerates_a_fence_and_checks_keys(simple_agent: Agent):
    ctx = make_ctx(simple_agent)
    fenced = '```json\n{"answer": "42", "confidence": 0.9}\n```'
    assert (await apply(json_valid(), fenced, ctx)).passed is True
    assert (await apply(json_valid(["answer"]), fenced, ctx)).passed is True
    missing = await apply(json_valid(["sources"]), fenced, ctx)
    assert missing.passed is False and "sources" in missing.notes
    assert (await apply(json_valid(), "not json", ctx)).passed is False


async def test_under_cost_scores_how_far_under(simple_agent: Agent):
    ctx = make_ctx(simple_agent)
    ctx.result.trace.costs.max_usd = None
    from rsi_arena.core.costs import Cost

    ctx.result.trace.costs.add("llm", "m", Cost(usd=0.25))
    score = await apply(under_cost(1.0), "x", ctx)
    assert score.passed is True and score.value == pytest.approx(0.75)
    assert (await apply(under_cost(0.1), "x", ctx)).passed is False


async def test_completed_distinguishes_a_bailout_from_a_dead_run(simple_agent: Agent):
    from rsi_arena.agent import ErrorKind

    clean = await apply(completed(), "x", make_ctx(simple_agent))
    assert clean.passed is True and clean.value == 1.0

    bailed = await apply(completed(), "x", make_ctx(
        simple_agent, error="MaxSpendExceeded: ...", error_kind=ErrorKind.MAX_SPEND,
        bailed_out=True))
    # An answer beats no answer, or the arena has no reason to prefer bailing out.
    assert bailed.passed is False and bailed.value == 0.5

    dead = await apply(completed(), "", make_ctx(
        simple_agent, error="BudgetExceeded: ...", error_kind=ErrorKind.BUDGET))
    assert dead.value == 0.0


async def test_llm_judge_uses_a_model_and_reports_its_reasoning(simple_agent: Agent, llm, fake):
    ctx = make_ctx(simple_agent, llm=llm, expected="rates were held")
    score = await apply(llm_judge("Every claim carries its URL."), "The ECB held rates.", ctx)
    assert score.value == 0.8 and score.passed is True
    assert score.notes == "Sourced and specific."
    sent = fake.bodies[-1]["messages"][-1]["content"]
    assert "Every claim carries its URL." in sent
    assert "rates were held" in sent, "the expected answer should reach the judge"


async def test_llm_judge_says_so_when_there_is_no_client(simple_agent: Agent):
    score = await apply(llm_judge("rubric"), "x", make_ctx(simple_agent, llm=None))
    assert score.passed is False and "no LLM client" in score.notes


async def test_llm_judge_survives_an_unparseable_verdict(simple_agent: Agent, llm, fake):
    fake.raw_answers["judgement"] = "I would rather not say"
    score = await apply(llm_judge("rubric"), "x", make_ctx(simple_agent, llm=llm))
    assert score.passed is False and "unparseable" in score.notes


# --- combining --------------------------------------------------------------


async def test_all_of_averages_and_needs_every_verdict(simple_agent: Agent):
    ctx = make_ctx(simple_agent)
    both = all_of([contains("ecb"), contains("2026")])
    assert (await apply(both, "the ECB in 2026", ctx)).passed is True
    half = await apply(both, "the ECB", ctx)
    assert half.passed is False and half.value == 0.5
    assert len(half.details["parts"]) == 2


async def test_a_part_with_no_verdict_moves_the_number_but_does_not_veto(simple_agent: Agent):
    ctx = make_ctx(simple_agent)
    combined = all_of([contains("ecb"), lambda output: 0.5])
    score = await apply(combined, "the ECB", ctx)
    assert score.passed is True and score.value == 0.75


# --- the registry -----------------------------------------------------------


def test_every_builtin_is_registered():
    assert set(SCORERS) >= {"contains", "not_contains", "regex", "non_empty", "json_valid",
                            "under_cost", "completed", "llm_judge"}


def test_get_scorer_names_what_exists():
    with pytest.raises(KeyError) as exc:
        get_scorer("invented")
    assert "contains" in str(exc.value)


def test_register_scorer_refuses_to_clobber():
    with pytest.raises(ValueError):
        register_scorer("contains", lambda: None)


def test_register_scorer_adds_one_and_replace_overrides():
    try:
        register_scorer("shouty", lambda: (lambda output: output.isupper()))
        assert "shouty" in SCORERS
        register_scorer("shouty", lambda: (lambda output: True), replace=True)
    finally:
        SCORERS.pop("shouty", None)


async def test_a_spec_can_be_a_name_a_dict_or_a_list(simple_agent: Agent):
    ctx = make_ctx(simple_agent)
    assert (await apply(scorer_from_spec("non_empty"), "x" * 100, ctx)).passed is True
    assert (await apply(scorer_from_spec({"type": "contains", "value": "ecb"}),
                        "the ECB", ctx)).passed is True
    combined = scorer_from_spec([{"type": "contains", "value": "ecb"}, "non_empty"])
    assert (await apply(combined, "the ECB " + "x" * 100, ctx)).passed is True


def test_a_callable_spec_is_returned_unchanged():
    def scorer(output: str) -> bool:
        return True

    assert scorer_from_spec(scorer) is scorer


@pytest.mark.parametrize("spec", [{"value": "x"}, {"type": "invented"}, 42])
def test_a_bad_spec_raises_rather_than_scoring_nothing(spec):
    with pytest.raises((KeyError, ValueError, TypeError)):
        scorer_from_spec(spec)
