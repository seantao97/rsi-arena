"""Two orchestrations over one Kalshi primitive set.

Same model, same tools, same ceiling — only the order of operations differs.
That is the comparison the arena exists to make, so it is the comparison these
two agents make.

``pipeline``   Fixed order, written down. Read the rules, find the game, gather
               state, check the book, price the edge, write it up. The plan
               decides what runs.

``freeform``   One prompt holding the same tools and a call budget. The model
               decides what to call, in what order, and how often.

Neither is the framework. They are contenders — generation zero, and the thing
the optimizer is meant to beat.
"""

from __future__ import annotations

from rsi_arena import Agent, AgentConfig, LoopStep, Plan, PromptStep, Toolbox, ToolStep

from .tools import kalshi_tools

# Identical across both agents on purpose. If the context differs, a battle
# between them measures the context and not the orchestration.
CONTEXT = """You forecast Kalshi event-contract markets on sport. You are paid for being
right about probabilities, not for sounding confident.

Rules you never break:
- Read the settlement rules before forming a view. The contract decides on its own terms,
  not on what the market is colloquially about.
- A price is not a probability until fees are accounted for. Kalshi's fee peaks near 50c,
  so a 2c edge at midprice is nothing. Run price_the_edge before claiming an edge.
- A sportsbook line is a strong prior, but only after the margin is removed. Soccer is
  three-way: home and away alone do not partition the outcome space.
- Where the market and your model disagree, the burden is on you. Say what the market
  might know that you do not.
- State what would change your mind, specifically enough to check later.
- If nothing clears the fee, the answer is PASS. Passing is a real answer and often the
  correct one."""

PREDICTION_SCHEMA = {
    "type": "object",
    "properties": {
        "ticker": {"type": "string"},
        "position": {"type": "string", "enum": ["YES", "NO", "PASS"]},
        "probability": {"type": "number", "description": "Your probability the contract settles yes, 0-1."},
        "interval": {"type": "array", "items": {"type": "number"},
                     "description": "Low and high bound on that probability."},
        "market_price": {"type": "number", "description": "Yes ask if buying yes, yes bid if selling."},
        "edge_after_fees": {"type": "number", "description": "From price_the_edge, in dollars per contract."},
        "reasoning": {"type": "string", "description": "Why, in a few sentences. Cite what you actually looked at."},
        "counter_case": {"type": "string", "description": "The strongest argument against this position."},
        "falsifier": {"type": "string", "description": "One observation that would make this wrong."},
        "stake_usd": {"type": "number"},
        "settlement_note": {"type": "string", "description": "What the contract actually settles on."},
    },
    "required": ["ticker", "position", "probability", "interval", "market_price",
                 "edge_after_fees", "reasoning", "counter_case", "falsifier",
                 "stake_usd", "settlement_note"],
    "additionalProperties": False,
}

RESEARCH_SCHEMA = {
    "type": "object",
    "properties": {
        "findings": {"type": "array", "items": {"type": "string"},
                     "description": "What the tool calls established, one per line."},
        "still_unknown": {"type": "string"},
        "sufficient": {"type": "boolean",
                       "description": "True only when you could price the contract now."},
    },
    "required": ["findings", "still_unknown", "sufficient"],
    "additionalProperties": False,
}


def default_config(max_usd: float = 2.00) -> AgentConfig:
    """The arena's shared ceiling. Both agents run under the same one."""
    return AgentConfig(default_model="anthropic/claude-sonnet-4.5", max_usd=max_usd)


def pipeline_agent(config: AgentConfig | None = None,
                   tools: Toolbox | None = None) -> Agent:
    """Fixed order: rules → fixture → state → book → price → write."""
    return Agent(
        name="kalshi-sports-pipeline",
        description="Fixed pipeline. The plan decides what runs.",
        context=CONTEXT,
        tools=tools or kalshi_tools(),
        config=config or default_config(),
        plan=Plan(steps=[
            ToolStep(name="rules", tool="market_rules",
                     args={"ticker": "{{question}}"}, output_key="rules", fail_ok=True),
            ToolStep(name="quote", tool="market_quote",
                     args={"ticker": "{{question}}"}, output_key="quote", fail_ok=True),
            PromptStep(
                name="orient",
                prompt=("Contract: {{question}}\n\nSettlement terms:\n{{rules}}\n\n"
                        "Current market:\n{{quote}}\n\n"
                        "State in one line what this contract settles on, which league and "
                        "fixture it refers to, and which league code to use for the game "
                        "tools (MLB, NFL, NBA, EPL, LIGAMX and so on)."),
                output_key="orientation",
                max_tokens=220,
            ),
            LoopStep(
                name="research",
                output_key="evidence",
                max_loops=4,
                until="notes.sufficient",
                steps=[
                    PromptStep(
                        name="gather",
                        prompt=("Contract: {{question}}\nOrientation: {{orientation}}\n"
                                "Learned so far: {{loop_results}}\n\n"
                                "Call the tools that would most change your estimate. Game "
                                "state and the sportsbook line usually matter most; price "
                                "history and the tape tell you what the market already knows."),
                        tools=["todays_fixtures", "find_game_for_market", "game_state",
                               "recent_plays", "game_context", "sportsbook_line",
                               "price_history", "recent_trades", "event_markets"],
                        max_tool_iterations=4,
                        output_key="gathered",
                    ),
                    PromptStep(
                        name="notes",
                        prompt=("Contract: {{question}}\n\nJust gathered:\n{{gathered}}\n\n"
                                "List only what the tool output actually establishes. Then say "
                                "whether you could price the contract now."),
                        output_schema=RESEARCH_SCHEMA,
                        output_key="notes",
                    ),
                ],
            ),
            ToolStep(name="structure", tool="coherence_check",
                     args={"event_ticker": "{{orientation}}"},
                     output_key="coherence", fail_ok=True),
            PromptStep(
                name="estimate",
                prompt=("Contract: {{question}}\nSettlement: {{rules}}\n"
                        "Market: {{quote}}\nEvidence: {{evidence}}\n\n"
                        "Give your probability that this contract settles YES, with an "
                        "interval. Then call price_the_edge with that probability and the "
                        "price you would actually pay, and report what it returns."),
                tools=["price_the_edge", "devig_odds"],
                max_tool_iterations=3,
                output_key="pricing",
            ),
            PromptStep(
                name="write",
                prompt=("Contract: {{question}}\nSettlement: {{rules}}\nMarket: {{quote}}\n"
                        "Evidence: {{evidence}}\nPricing: {{pricing}}\n"
                        "Structural findings: {{coherence}}\n\n"
                        "Write the final call. If the edge after fees is not positive, "
                        "position is PASS and stake is 0."),
                output_schema=PREDICTION_SCHEMA,
                output_key="prediction",
            ),
        ]),
    )


def freeform_agent(config: AgentConfig | None = None,
                   tools: Toolbox | None = None) -> Agent:
    """Same tools, no plan. The model decides what to call and when."""
    return Agent(
        name="kalshi-sports-freeform",
        description="One prompt, same tools, model-chosen order.",
        context=CONTEXT,
        tools=tools or kalshi_tools(),
        config=config or default_config(),
        plan=Plan(steps=[
            PromptStep(
                name="predict",
                prompt=("Forecast this Kalshi contract: {{question}}\n\n"
                        "You have tools for settlement rules, live quotes, price history, "
                        "the trade tape, fixtures, live game state, plays, injuries and form, "
                        "the sportsbook line, cross-market coherence and fee-aware pricing.\n\n"
                        "Start by reading the settlement rules, and finish by pricing the edge "
                        "after fees. Roughly twelve tool calls is a sensible budget — spend "
                        "them where they would change the answer, not to look thorough."),
                # "*" offers every tool in the box and lets the model pick. The
                # pipeline restricts them per step instead — that difference is
                # the whole comparison.
                tools=["*"],
                max_tool_iterations=14,
                output_schema=PREDICTION_SCHEMA,
                output_key="prediction",
            ),
        ]),
    )


AGENTS = {"pipeline": pipeline_agent, "freeform": freeform_agent}
