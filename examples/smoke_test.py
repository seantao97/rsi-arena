"""Smallest useful agent: prompt → tool → prompt.

A Fermi-estimate harness. It exists to show the machinery end to end in a few
seconds and a fraction of a cent: a structured-output step, a deterministic
tool step that consumes the previous step's output, a final prose step, and
the trace and cost ledger that come out the other side.

    export OPENROUTER_API_KEY=...
    python examples/smoke_test.py "How many piano tuners are there in Chicago?"
    python examples/smoke_test.py --trace --json run.json
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path
from typing import Annotated

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from rsi_arena import Agent, AgentConfig, Plan, PromptStep, Toolbox, ToolStep, tool  # noqa: E402
from rsi_arena.template import evaluate  # noqa: E402

CONTEXT = """You are a careful estimator. You decompose a quantity into factors you can
defend, state each assumption plainly, and never state more precision than the weakest
factor supports. You do not do arithmetic in your head — you write the expression and let
the calculator run it."""


@tool
def calculator(
    expression: Annotated[str, "An arithmetic expression, e.g. '(2.7e6 / 4) * 0.02'."],
) -> float:
    """Evaluate an arithmetic expression exactly.

    Backed by the same restricted evaluator that runs loop conditions, so it
    computes arithmetic and nothing else — a model-authored string is never
    handed to ``eval``.
    """
    return float(evaluate(expression, {}))


DECOMPOSITION_SCHEMA = {
    "type": "object",
    "properties": {
        "expression": {"type": "string",
                       "description": "A single arithmetic expression, digits and operators only."},
        "factors": {"type": "array", "items": {
            "type": "object",
            "properties": {"name": {"type": "string"}, "value": {"type": "string"},
                           "justification": {"type": "string"}},
            "required": ["name", "value", "justification"],
            "additionalProperties": False}},
        "weakest_factor": {"type": "string"},
    },
    "required": ["expression", "factors", "weakest_factor"],
    "additionalProperties": False,
}


def build_agent(model: str) -> Agent:
    return Agent(
        name="fermi",
        description="Three-step Fermi estimator: decompose, compute, write up.",
        context=CONTEXT,
        tools=Toolbox([calculator]),
        config=AgentConfig(default_model=model, max_usd=0.25, temperature=0.2),
        plan=Plan(steps=[
            PromptStep(
                name="decompose",
                prompt="Decompose this into multiplicative factors you can defend:\n\n{{question}}",
                output_schema=DECOMPOSITION_SCHEMA,
                output_key="decomposition",
            ),
            ToolStep(
                name="compute",
                tool="calculator",
                args={"expression": "{{decomposition.expression}}"},
                output_key="value",
            ),
            PromptStep(
                name="write_up",
                prompt=(
                    "Question: {{question}}\n\n"
                    "Your decomposition:\n{{decomposition}}\n\n"
                    "The calculator returned: {{value}}\n\n"
                    "Write the estimate in under 200 words. State the number, the factors, "
                    "and say which one you would check first if the answer mattered "
                    "(you said it was {{decomposition.weakest_factor}})."
                ),
                output_key="memo",
            ),
        ]),
    )


async def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("question", nargs="?",
                        default="How many piano tuners are there in Chicago?")
    parser.add_argument("--model", default="anthropic/claude-sonnet-4.5")
    parser.add_argument("--trace", action="store_true", help="Print the span tree.")
    parser.add_argument("--json", metavar="PATH", help="Write the full result as JSON.")
    args = parser.parse_args()

    agent = build_agent(args.model)
    print(agent.outline(), "\n")
    result = await agent.run(args.question)

    if not result.ok:
        print(f"failed: {result.error}", file=sys.stderr)
    print(result.output or "")
    print("\n" + ("-" * 60))
    if args.trace:
        print(result.trace.render())
    print(json.dumps(result.summary(), indent=2))
    if args.json:
        Path(args.json).write_text(result.model_dump_json(indent=2))
        print(f"wrote {args.json}")
    return 0 if result.ok else 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
