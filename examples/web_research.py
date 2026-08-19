"""Three web-research agents over one primitive set.

Same question, same model, same tools, same cost ceiling — only the
orchestration differs. That is the comparison the arena is built to make, so
it is the comparison the sample agents make:

``pipeline``   Fixed order, written down. Plan queries, then loop
               search → take notes until the notes are sufficient, then draft,
               then critique. The plan decides what runs.

``freeform``   One prompt holding the same tools, a tool-calling budget, and a
               description of what the tools cost. The model decides what runs,
               in what order, and how many times.

``plugin``     No search tool at all — OpenRouter's ``web`` plugin does the
               retrieval inside the model call. Fewer moving parts, no control
               over what gets fetched.

Search comes from SearchApi.io (``rsi_arena/apis/searchapi.py``), the first
registered API. Adding a second source is one ``APISpec`` literal — see the
module docstring in ``rsi_arena/api/__init__.py``.

    export OPENROUTER_API_KEY=...
    export SEARCHAPI_API_KEY=...          # not needed for --agent plugin

    python examples/web_research.py "Did the ECB cut rates in July 2026?"
    python examples/web_research.py "..." --agent all --trace
    python examples/web_research.py "..." --agent freeform --stream
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from rsi_arena import (  # noqa: E402
    Agent, AgentConfig, APIClient, LLMClient, LoopStep, Plan, PromptStep, Toolbox, ToolStep,
    WebSearch, api_tool,
)
from rsi_arena.api.apis import SEARCHAPI  # noqa: E402  (importing registers it)

# The orchestration prompt. Identical across all three agents on purpose: if
# the context differs, a battle between them measures the context and not the
# orchestration, which is the mistake the arena exists to avoid.
CONTEXT = """You are a research agent. You answer questions from sources you have actually
read, and you say plainly when the sources do not settle the question.

Rules you never break:
- Every factual claim carries the URL it came from. A claim you cannot source, you drop.
- You prefer a primary source to a report about it, and a dated source to an undated one.
- You state what would change your answer. A finding with no falsifier is a guess.
- Searching costs money and time. Search when a new query would plausibly change the
  answer, not to look thorough."""

QUERY_SCHEMA = {
    "type": "object",
    "properties": {
        "queries": {"type": "array", "items": {"type": "string"},
                    "description": "Search queries, most promising first."},
        "what_would_settle_it": {"type": "string"},
    },
    "required": ["queries", "what_would_settle_it"],
    "additionalProperties": False,
}

NOTES_SCHEMA = {
    "type": "object",
    "properties": {
        "claims": {"type": "array", "items": {
            "type": "object",
            "properties": {"claim": {"type": "string"}, "url": {"type": "string"},
                           "date": {"type": "string"}, "confidence": {"type": "number"}},
            "required": ["claim", "url", "date", "confidence"],
            "additionalProperties": False}},
        "still_missing": {"type": "string"},
        "sufficient": {"type": "boolean",
                       "description": "True only if the claims so far settle the question."},
    },
    "required": ["claims", "still_missing", "sufficient"],
    "additionalProperties": False,
}


def search_tools(client: APIClient | None = None) -> Toolbox:
    """The primitive set. Human-supplied and fixed — agents use it, not extend it."""
    shared = client or APIClient()
    return Toolbox([
        api_tool(SEARCHAPI, "search", client=shared, name="search",
                 description="Google web search. Returns titles, links, snippets and dates.",
                 fixed={"num": 10, "gl": "us", "hl": "en"}),
        api_tool(SEARCHAPI, "news", client=shared, name="search_news",
                 description="Google News search. Use for events in the last few weeks.",
                 fixed={"num": 10, "gl": "us", "hl": "en"}),
    ])


def pipeline_agent(config: AgentConfig, tools: Toolbox) -> Agent:
    """Fixed pipeline: plan → (search → note)* → draft → critique."""
    return Agent(
        name="researcher-pipeline",
        description="Fixed pipeline. The plan decides what runs.",
        context=CONTEXT,
        tools=tools,
        config=config,
        plan=Plan(steps=[
            PromptStep(
                name="plan_queries",
                prompt=("Question: {{question}}\n\n"
                        "List up to 4 search queries, most promising first, and say what "
                        "evidence would settle the question."),
                output_schema=QUERY_SCHEMA,
                output_key="plan",
            ),
            LoopStep(
                name="research",
                output_key="evidence",
                max_loops=4,
                # Two stopping conditions, cheap one first: a free expression
                # over state short-circuits before the paid LLM judgement runs.
                until="notes.sufficient",
                until_prompt=("Question: {{question}}\n\nEvidence gathered:\n{{loop_results}}\n\n"
                              "Is this enough to answer the question with sourced claims?"),
                steps=[
                    PromptStep(
                        name="choose_query",
                        prompt=("Planned queries: {{plan.queries}}\n"
                                "Evidence gathered so far: {{loop_results}}\n\n"
                                "Output ONLY the next query string to run — no quotes, no "
                                "explanation. Deviate from the plan if what you have learned "
                                "warrants it."),
                        output_key="query",
                        max_tokens=60,
                    ),
                    ToolStep(name="search", tool="search", args={"q": "{{query}}"},
                             output_key="hits", fail_ok=True),
                    PromptStep(
                        name="take_notes",
                        prompt=("Question: {{question}}\n\nResults for {{query}}:\n{{hits}}\n\n"
                                "Extract only claims these results actually support, each with "
                                "its URL and date. Then say whether the question is settled."),
                        output_schema=NOTES_SCHEMA,
                        output_key="notes",
                    ),
                ],
            ),
            PromptStep(
                name="draft",
                prompt=("Question: {{question}}\n\nEvidence gathered:\n"
                        "{{evidence}}\n\nWrite the answer. Every claim carries its URL. "
                        "End with what would change your mind."),
                output_key="draft",
            ),
            PromptStep(
                name="critique",
                prompt=("Draft:\n{{draft}}\n\nEvidence:\n{{evidence}}\n\n"
                        "Find every claim in the draft not supported by the evidence and "
                        "remove or weaken it. Return the corrected answer only."),
                output_key="answer",
            ),
        ]),
    )


def freeform_agent(config: AgentConfig, tools: Toolbox) -> Agent:
    """Free-form: same tools, the model decides the order and the loop count."""
    return Agent(
        name="researcher-freeform",
        description="Free-form tool loop. The model decides what runs.",
        context=CONTEXT,
        tools=tools,
        config=config,
        plan=Plan(steps=[
            PromptStep(
                name="research",
                prompt=("Question: {{question}}\n\n"
                        "You have `search` (Google) and `search_news` (recent events). Each "
                        "call costs about $0.004 and takes a second. You may call them up to "
                        "8 times, together or in sequence. Stop as soon as further searching "
                        "would not change your answer.\n\n"
                        "Then write the answer: every claim with its URL, and what would "
                        "change your mind."),
                tools=["*"],
                max_tool_iterations=8,
                output_key="draft",
            ),
            PromptStep(
                name="critique",
                prompt=("Draft:\n{{draft}}\n\nStrike every claim you cannot point to a URL for. "
                        "Return the corrected answer only."),
                output_key="answer",
            ),
        ]),
    )


def plugin_agent(config: AgentConfig) -> Agent:
    """No search tool: OpenRouter's web plugin retrieves inside the model call."""
    return Agent(
        name="researcher-plugin",
        description="OpenRouter web plugin. No search tool, no orchestration.",
        context=CONTEXT,
        config=config.model_copy(update={"web_search": WebSearch(max_results=8, engine="exa")}),
        plan=Plan(steps=[
            PromptStep(
                name="answer",
                prompt=("Question: {{question}}\n\nAnswer it from current sources. Every claim "
                        "carries its URL. End with what would change your mind."),
                web_search=WebSearch(max_results=8),
                output_key="answer",
            ),
        ]),
    )


BUILDERS = {"pipeline": pipeline_agent, "freeform": freeform_agent, "plugin": plugin_agent}


async def stream_one(agent: Agent, question: str) -> None:
    """Stream the first prompt step's tokens — what a live UI would render."""
    step = agent.plan.steps[0]
    async with LLMClient(config=agent.config.to_llm_config()) as llm:
        async for event in llm.stream(
            step.prompt.replace("{{question}}", question), system=agent.context
        ):
            if event.type == "delta":
                print(event.text, end="", flush=True)
            elif event.type == "done" and event.completion:
                print(f"\n\n[${event.completion.cost.usd:.5f}, "
                      f"{event.completion.usage.total_tokens} tokens]")


async def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("question")
    parser.add_argument("--agent", choices=[*BUILDERS, "all"], default="pipeline")
    parser.add_argument("--model", default="anthropic/claude-sonnet-4.5")
    parser.add_argument("--max-usd", type=float, default=2.00,
                        help="Per-agent ceiling. Both sides of a battle get the same one.")
    parser.add_argument("--trace", action="store_true")
    parser.add_argument("--stream", action="store_true", help="Stream the first step instead.")
    parser.add_argument("--json", metavar="PATH")
    args = parser.parse_args()

    config = AgentConfig(default_model=args.model, max_usd=args.max_usd, temperature=0.3)
    api = APIClient()
    tools = search_tools(api)
    names = list(BUILDERS) if args.agent == "all" else [args.agent]
    agents = [
        BUILDERS[n](config) if n == "plugin" else BUILDERS[n](config, tools)  # type: ignore[operator]
        for n in names
    ]

    if args.stream:
        await stream_one(agents[0], args.question)
        await api.close()
        return 0

    for agent in agents:
        print(agent.outline(), "\n")

    # One client for every agent: they then share a rate limiter and a cache,
    # which is also what makes an arena battle fair — one search paid for once.
    async with LLMClient(config=config.to_llm_config(),
                         rate_limit=config.rate_limit()) as llm:
        results = await asyncio.gather(*(a.run(args.question, llm=llm) for a in agents))
    await api.close()

    for agent, result in zip(agents, results):
        print("=" * 70)
        print(f"{agent.name}  ${result.cost_usd:.4f}  {result.trace.duration_s:.1f}s")
        print("=" * 70)
        print(result.output if result.ok else f"FAILED: {result.error}")
        if args.trace:
            print("\n" + result.trace.render())
        print()

    print(json.dumps([r.summary() for r in results], indent=2))
    if args.json:
        Path(args.json).write_text(
            json.dumps([json.loads(r.model_dump_json()) for r in results], indent=2)
        )
        print(f"wrote {args.json}")
    return 0 if all(r.ok for r in results) else 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
