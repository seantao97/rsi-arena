"""Scorers: the function an :class:`~rsi_arena.evals.eval.Eval` is built around.

A scorer reads the agent's text output and returns a :class:`Score`. That is
the whole contract, and it is deliberately loose about what it returns —
``True``, ``0.8``, a dict, or a full :class:`Score` all work, because a scorer
is the part users write and it should not require reading a class first.

.. code-block:: python

    def mentions_the_date(output: str) -> bool:
        return "2026" in output

    Eval(agent, "When did the ECB last meet?", mentions_the_date)

A scorer may take one argument (the output) or two (the output and an
:class:`EvalContext` carrying the run, the agent and a shared LLM client), and
may be sync or async. :func:`apply` sorts that out.

Named scorers are registered so they can be selected over HTTP, where a
callable cannot be sent: ``{"type": "contains", "value": "ECB"}`` is resolved
by :func:`scorer_from_spec`. Registering your own with
:func:`register_scorer` makes it available to the endpoint too.
"""

from __future__ import annotations

import inspect
import re
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Awaitable, Callable, Union

from pydantic import BaseModel, Field

from ..llm import LLMClient, parse_json_loose

if TYPE_CHECKING:
    from ..agent import Agent, AgentResult


class Score(BaseModel):
    """What a scorer decided.

    ``value`` is the number things get averaged on and is conventionally 0–1.
    ``passed`` is the yes/no where there is one — a rubric judge has both, a
    substring check really only has the second, and a cost check only the
    first. Anything a scorer wants to keep for later goes in ``details``.
    """

    value: float = 0.0
    passed: bool | None = None
    label: str = ""
    notes: str = ""
    details: dict[str, Any] = Field(default_factory=dict)

    @classmethod
    def of(cls, value: Any) -> "Score":
        """Coerce whatever a scorer returned into a :class:`Score`.

        ``True``/``False`` become 1.0/0.0 with ``passed`` set; a number becomes
        a value with no verdict; a string becomes a failing score whose notes
        are the string, since a scorer that returns prose is explaining itself.
        """
        if isinstance(value, Score):
            return value
        if isinstance(value, bool):
            return cls(value=1.0 if value else 0.0, passed=value)
        if isinstance(value, (int, float)):
            return cls(value=float(value))
        if isinstance(value, str):
            return cls(value=0.0, notes=value)
        if isinstance(value, dict):
            return cls.model_validate(value)
        if value is None:
            return cls()
        raise TypeError(f"a scorer returned {type(value).__name__}, which is not a score")

    def __bool__(self) -> bool:
        return self.passed if self.passed is not None else self.value > 0


@dataclass
class EvalContext:
    """Everything a two-argument scorer gets besides the output text."""

    prompt: str
    result: "AgentResult"
    agent: "Agent"
    llm: LLMClient | None = None
    expected: Any = None
    metadata: dict[str, Any] = field(default_factory=dict)


Scorer = Callable[..., Union[Score, bool, float, str, dict, Awaitable[Any]]]


async def apply(scorer: Scorer, output: str, ctx: EvalContext) -> Score:
    """Call a scorer however it wants to be called, and normalise the result.

    One positional parameter gets the output; two get the output and the
    context. Async scorers are awaited. A scorer that raises produces a failing
    score with the exception in its notes rather than taking the eval down with
    it — a broken scorer should show up as a broken row, not a lost run.
    """
    try:
        wants_ctx = _arity(scorer) >= 2
        result = scorer(output, ctx) if wants_ctx else scorer(output)
        if inspect.isawaitable(result):
            result = await result
        return Score.of(result)
    except Exception as exc:  # noqa: BLE001 - reported as a failed score
        return Score(
            value=0.0,
            passed=False,
            label="scorer_error",
            notes=f"{type(exc).__name__}: {exc}",
        )


def _arity(scorer: Scorer) -> int:
    try:
        params = list(inspect.signature(scorer).parameters.values())
    except (TypeError, ValueError):  # builtins and C callables have no signature
        return 1
    positional = [
        p for p in params
        if p.kind in (p.POSITIONAL_ONLY, p.POSITIONAL_OR_KEYWORD) and p.default is p.empty
    ]
    if any(p.kind is p.VAR_POSITIONAL for p in params):
        return 2
    return len(positional)


# --- built-in scorers -------------------------------------------------------
#
# Each is a *factory*: it takes the check's settings and returns the scorer.
# That is what lets the same object be built from a Python call and from a
# JSON spec arriving over HTTP.


def contains(value: str | list[str], *, all_of: bool = True, case_sensitive: bool = False) -> Scorer:
    """Passes when the output contains the needle (or all/any of several)."""
    needles = [value] if isinstance(value, str) else list(value)

    def score(output: str) -> Score:
        hay = output if case_sensitive else output.lower()
        found = [n for n in needles if (n if case_sensitive else n.lower()) in hay]
        ok = len(found) == len(needles) if all_of else bool(found)
        return Score(
            value=len(found) / len(needles) if needles else 0.0,
            passed=ok,
            label="contains",
            notes=f"found {len(found)} of {len(needles)}",
            details={"found": found, "missing": [n for n in needles if n not in found]},
        )

    return score


def not_contains(value: str | list[str], *, case_sensitive: bool = False) -> Scorer:
    """Passes when none of the needles appear. For things an answer must not say."""
    needles = [value] if isinstance(value, str) else list(value)

    def score(output: str) -> Score:
        hay = output if case_sensitive else output.lower()
        hits = [n for n in needles if (n if case_sensitive else n.lower()) in hay]
        return Score(
            value=0.0 if hits else 1.0,
            passed=not hits,
            label="not_contains",
            notes=f"found {hits}" if hits else "clean",
            details={"found": hits},
        )

    return score


def regex(pattern: str, *, flags: str = "i") -> Scorer:
    """Passes when the pattern matches anywhere in the output."""
    compiled = re.compile(pattern, re.IGNORECASE if "i" in flags else 0)

    def score(output: str) -> Score:
        match = compiled.search(output or "")
        return Score(
            value=1.0 if match else 0.0,
            passed=bool(match),
            label="regex",
            notes=f"matched {match.group(0)[:120]!r}" if match else "no match",
            details={"pattern": pattern},
        )

    return score


def non_empty(min_chars: int = 40) -> Scorer:
    """Passes when the agent said something of substance. The cheapest smoke test."""

    def score(output: str) -> Score:
        length = len((output or "").strip())
        return Score(
            value=min(1.0, length / min_chars) if min_chars else float(bool(length)),
            passed=length >= min_chars,
            label="non_empty",
            notes=f"{length} characters",
        )

    return score


def json_valid(required_keys: list[str] | None = None) -> Scorer:
    """Passes when the output parses as JSON and has the keys asked for.

    Uses the same tolerant parser as a schema step, so an answer wrapped in a
    ``` fence still counts — the point is whether the data is there, not
    whether the model was tidy about delivering it.
    """
    keys = list(required_keys or [])

    def score(output: str) -> Score:
        try:
            data = parse_json_loose(output or "")
        except ValueError as exc:
            return Score(value=0.0, passed=False, label="json_valid", notes=str(exc)[:200])
        missing = [k for k in keys if not isinstance(data, dict) or k not in data]
        return Score(
            value=0.0 if missing else 1.0,
            passed=not missing,
            label="json_valid",
            notes=f"missing {missing}" if missing else "parsed",
            details={"keys": list(data) if isinstance(data, dict) else None},
        )

    return score


def under_cost(max_usd: float) -> Scorer:
    """Passes when the run came in under a price. Scores how far under.

    Worth having as a first-class scorer: the README's open question about cost
    asymmetry is exactly the observation that an answer is not good in the
    abstract, it is good *for what it cost*.
    """

    def score(output: str, ctx: EvalContext) -> Score:
        spent = ctx.result.cost_usd
        return Score(
            value=max(0.0, min(1.0, 1.0 - spent / max_usd)) if max_usd else 0.0,
            passed=spent <= max_usd,
            label="under_cost",
            notes=f"${spent:.4f} of ${max_usd:.4f}",
            details={"cost_usd": spent, "max_usd": max_usd},
        )

    return score


def completed() -> Scorer:
    """Passes when the run finished without an error.

    A bail-out under ``max_spend_mode`` fails this but still scores 0.5: it
    produced an answer, which is strictly better than the run that produced
    nothing, and an eval that scored them the same would give the arena no
    reason to prefer bailing out.
    """

    def score(output: str, ctx: EvalContext) -> Score:
        kind = ctx.result.error_kind.value if ctx.result.error_kind else None
        if ctx.result.ok:
            return Score(value=1.0, passed=True, label="completed", notes="finished the plan")
        return Score(
            value=0.5 if ctx.result.bailed_out else 0.0,
            passed=False,
            label="completed",
            notes=f"{kind}: {ctx.result.error}",
            details={"error_kind": kind, "bailed_out": ctx.result.bailed_out},
        )

    return score


JUDGE_SCHEMA = {
    "type": "json_schema",
    "json_schema": {
        "name": "judgement",
        "strict": True,
        "schema": {
            "type": "object",
            "properties": {
                "score": {"type": "number", "description": "0.0 to 1.0."},
                "passed": {"type": "boolean"},
                "reason": {"type": "string", "description": "One or two sentences."},
            },
            "required": ["score", "passed", "reason"],
            "additionalProperties": False,
        },
    },
}

JUDGE_SYSTEM = """You are grading one answer against one rubric. You are strict and you are \
specific: cite the part of the answer that earned or lost the score. An answer that is \
fluent but does not meet the rubric scores low. Answer only with JSON matching the schema."""


def llm_judge(
    rubric: str,
    *,
    model: str | None = None,
    threshold: float = 0.6,
    include_question: bool = True,
) -> Scorer:
    """Score against a written rubric, using a model.

    The escape hatch for everything a substring check cannot express. It costs
    money — one call per eval — and it is charged to the *eval*, not to the
    agent's ledger, so a judge can never eat the budget it is judging.
    """

    async def score(output: str, ctx: EvalContext) -> Score:
        if ctx.llm is None:
            return Score(value=0.0, passed=False, label="llm_judge",
                         notes="no LLM client available to judge with")
        question = f"The question asked:\n{ctx.prompt}\n\n" if include_question else ""
        expected = f"What a good answer contains:\n{ctx.expected}\n\n" if ctx.expected else ""
        completion = await ctx.llm.complete(
            f"{question}{expected}Rubric:\n{rubric}\n\nThe answer to grade:\n{output}",
            system=JUDGE_SYSTEM,
            model=model,
            schema=JUDGE_SCHEMA,
        )
        try:
            verdict = parse_json_loose(completion.text)
        except ValueError:
            return Score(value=0.0, passed=False, label="llm_judge",
                         notes=f"unparseable judgement: {completion.text[:200]}")
        value = float(verdict.get("score", 0.0))
        return Score(
            value=value,
            passed=bool(verdict.get("passed", value >= threshold)),
            label="llm_judge",
            notes=str(verdict.get("reason", "")),
            details={"rubric": rubric, "threshold": threshold, "judge_usd": completion.cost.usd},
        )

    return score


def all_of(scorers: list[Scorer], *, weights: list[float] | None = None) -> Scorer:
    """Combine scorers: mean value, and passes only if every part passes.

    Parts that express no verdict (``passed is None``) do not veto — they still
    move the number, which is the point of having both fields on a
    :class:`Score`.
    """
    parts = list(scorers)
    factors = list(weights or [1.0] * len(parts))

    async def score(output: str, ctx: EvalContext) -> Score:
        results = [await apply(s, output, ctx) for s in parts]
        total = sum(f for f in factors[: len(results)]) or 1.0
        value = sum(r.value * f for r, f in zip(results, factors)) / total
        verdicts = [r.passed for r in results if r.passed is not None]
        return Score(
            value=round(value, 4),
            passed=all(verdicts) if verdicts else None,
            label="all_of",
            notes="; ".join(f"{r.label or 'part'}: {r.notes}" for r in results if r.notes)[:600],
            details={"parts": [r.model_dump() for r in results]},
        )

    return score


# --- registry ---------------------------------------------------------------

SCORERS: dict[str, Callable[..., Scorer]] = {
    "contains": contains,
    "not_contains": not_contains,
    "regex": regex,
    "non_empty": non_empty,
    "json_valid": json_valid,
    "under_cost": under_cost,
    "completed": completed,
    "llm_judge": llm_judge,
}


def register_scorer(name: str, factory: Callable[..., Scorer], *, replace: bool = False) -> None:
    """Add a scorer factory under a name, so the HTTP API can select it.

    ``factory(**spec)`` must return a scorer. Registering is what makes a
    custom check usable from a request body, where a callable cannot travel.
    """
    if name in SCORERS and not replace:
        raise ValueError(f"scorer {name!r} already registered; pass replace=True to override")
    SCORERS[name] = factory


def get_scorer(name: str) -> Callable[..., Scorer]:
    try:
        return SCORERS[name]
    except KeyError:
        known = ", ".join(sorted(SCORERS))
        raise KeyError(f"unknown scorer {name!r} (have: {known})") from None


def scorer_from_spec(spec: Any) -> Scorer:
    """Build a scorer from data. What the endpoint calls.

    Accepts a callable (returned as-is), a name, a ``{"type": ..., ...}`` dict
    whose remaining keys are the factory's arguments, or a list of any of those
    — which becomes :func:`all_of`.
    """
    if callable(spec):
        return spec
    if isinstance(spec, str):
        return get_scorer(spec)()
    if isinstance(spec, (list, tuple)):
        return all_of([scorer_from_spec(s) for s in spec])
    if isinstance(spec, dict):
        settings = dict(spec)
        name = settings.pop("type", None) or settings.pop("name", None)
        if not name:
            raise ValueError(f"scorer spec needs a 'type': {spec!r}")
        return get_scorer(str(name))(**settings)
    raise TypeError(f"cannot build a scorer from {type(spec).__name__}")
