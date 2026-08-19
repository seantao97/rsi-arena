"""Score the sample agents instead of voting on them.

A battle asks a human which of two answers is better. An eval asks a *function*
whether one answer is good — cheaper, repeatable, and runnable with nobody
watching, which is what makes it the thing a nightly job or the optimizer can
use.

    export OPENROUTER_API_KEY=...
    export SEARCHAPI_API_KEY=...                     # not needed for --agent plugin

    python examples/evals.py                         # every agent, every case
    python examples/evals.py --agent plugin --judge  # add a model-graded rubric
    python examples/evals.py --max-usd 0.05 --max-spend
                             # cut them off mid-run and score the bail-out answer

The last one is the interesting one. With ``--max-spend`` an agent that hits
its ceiling does not return nothing: it spends a small reserve on one call that
turns whatever it gathered into an answer, and the result is scored with
``bailed_out`` and ``error_kind='max_spend'`` on it — so the cut-off answer is
compared against the clean ones without being mistaken for one.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from examples import smoke_test, web_research  # noqa: E402
from rsi_arena import AgentConfig, APIClient, EvalSuite, LLMClient  # noqa: E402
from rsi_arena.evals import (  # noqa: E402
    all_of,
    completed,
    contains,
    llm_judge,
    non_empty,
    regex,
    under_cost,
)

# (prompt, scorer). The scorers are deliberately mundane: an answer of some
# substance, a source URL in it, and a run that finished. Cheap checks catch
# the regressions that should never reach a voter; the rubric judge below is
# for the part they cannot express.
CASES = [
    (
        "Did the ECB cut rates in July 2026?",
        all_of([non_empty(200), regex(r"https?://\S+"), completed()]),
    ),
    (
        "What is the current US federal funds target range?",
        all_of([regex(r"\d(\.\d+)?\s*%"), completed()]),
    ),
    (
        "How many piano tuners are there in Chicago?",
        all_of([non_empty(150), contains(["chicago"]), completed()]),
    ),
]

RUBRIC = """Every factual claim carries the URL it came from, the answer says plainly what \
it does not know, and it ends by naming what would change the conclusion. Fluent prose with \
no sources scores below 0.3."""


def build_agents(names: list[str], config: AgentConfig, api: APIClient) -> list:
    tools = web_research.search_tools(api)
    builders = {
        "pipeline": lambda: web_research.pipeline_agent(config, tools),
        "freeform": lambda: web_research.freeform_agent(config, tools),
        "plugin": lambda: web_research.plugin_agent(config),
        "fermi": lambda: smoke_test.build_agent(config.default_model),
    }
    return [builders[name]() for name in names]


async def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--agent", action="append",
                        choices=["pipeline", "freeform", "plugin", "fermi"],
                        help="Repeatable. Default: all four.")
    parser.add_argument("--model", default="anthropic/claude-sonnet-4.5")
    parser.add_argument("--max-usd", type=float, default=2.00, help="Per-agent ceiling.")
    parser.add_argument("--max-spend", action="store_true",
                        help="At the ceiling, answer from state instead of returning nothing.")
    parser.add_argument("--judge", action="store_true",
                        help="Add a model-graded rubric. Costs one extra call per eval.")
    parser.add_argument("--concurrency", type=int, default=4)
    parser.add_argument("--json", metavar="PATH")
    args = parser.parse_args()

    names = args.agent or ["pipeline", "freeform", "plugin", "fermi"]
    config = AgentConfig(
        default_model=args.model,
        max_usd=args.max_usd,
        temperature=0.3,
        max_spend_mode=args.max_spend,
    )
    api = APIClient()
    agents = build_agents(names, config, api)

    cases = list(CASES)
    if args.judge:
        cases = [(prompt, all_of([scorer, llm_judge(RUBRIC, model=args.model)]))
                 for prompt, scorer in cases]
    cases.append(("Did the ECB cut rates in July 2026?", under_cost(args.max_usd / 2)))

    suite = EvalSuite.over(agents, cases, name="samples")
    print(f"{len(suite.evals)} evals: {len(agents)} agents x {len(cases)} cases "
          f"at ${args.max_usd:.2f} each\n")

    # One client for every eval: they share a rate limiter and a cache, so a
    # question two agents both search for is paid for once.
    async with LLMClient(config=config.to_llm_config(),
                         rate_limit=config.rate_limit()) as llm:
        result = await suite.run(llm=llm)
    await api.close()

    print(result.table())
    print()
    print(json.dumps(result.aggregate(), indent=2))

    bailed = [r for r in result.results if r.bailed_out]
    if bailed:
        print(f"\n{len(bailed)} run(s) hit the ceiling and answered from state instead:")
        for one in bailed:
            print(f"  {one.name:24s} ${one.cost_usd:.4f}  scored {one.score.value:.2f}")

    if args.json:
        Path(args.json).write_text(json.dumps(result.model_dump(mode="json"), indent=2))
        print(f"\nwrote {args.json}")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
