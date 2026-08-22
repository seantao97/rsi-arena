"""Predict where this market will be in five minutes, and trade the difference.

A different task from "what is the true probability". That one competes with a
market that has watched the same match and usually knows more; this one asks
only where the price is going next, which is a question the price path, the
tape and the clock actually bear on.

It is also self-verifying. Five minutes later the answer exists in the
candlestick history, so every prediction is scored without waiting for the game
to end — thousands of labelled examples a night instead of one per contract.
That is the point: the harness that follows is meant to be evolved against data,
and this is the shape that produces it.

**The model predicts; the code decides.** The agent returns a price and an
interval, and :func:`decide` turns that into an action against the live book and
the fee schedule. Nothing is left to the model that arithmetic can settle, which
removes an entire class of defect seen live — a position that contradicted the
edge the same output reported.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime, timedelta, timezone

from rsi_arena import Agent, AgentConfig, Plan, PromptStep, Toolbox, ToolStep

from ..fees import taker_fee
from .tools import kalshi_tools

HORIZON_MINUTES = 5

CONTEXT = """You forecast the short-term path of a Kalshi sports contract while the match
is being played.

You are not asked who wins. You are asked where this contract's mid price will be in a
few minutes, which is a narrower and more answerable question.

What moves a price on this horizon:
- the game state changing — a goal, a red card, a period ending
- time simply passing, which decays any "will happen" contract toward no
- the book being thin, so a single order moves the mid and it drifts back
- the market still absorbing something that already happened

What does not:
- your view on which team is better. The market has that already.

Say plainly when you expect no move. FLAT is the correct answer most of the time on a
quiet market, and predicting movement that does not come is how this loses money."""

PREDICTION_SCHEMA = {
    "type": "object",
    "properties": {
        "predicted_mid": {"type": "number",
                          "description": "Mid price you expect in 5 minutes, 0-1."},
        "interval": {"type": "array", "items": {"type": "number"},
                     "description": "Low and high bound on that price."},
        "direction": {"type": "string", "enum": ["UP", "DOWN", "FLAT"]},
        "confidence": {"type": "number",
                       "description": "0-1. How sure, given how thin and noisy this book is."},
        "driver": {"type": "string",
                   "description": "The one thing you expect to move it, or why nothing will."},
        "falsifier": {"type": "string",
                      "description": "What would show this call was wrong, before settlement."},
    },
    "required": ["predicted_mid", "interval", "direction", "confidence",
                 "driver", "falsifier"],
    "additionalProperties": False,
}


@dataclass(frozen=True)
class Decision:
    """What the arithmetic says to do with a prediction."""

    action: str                    # BUY_YES | BUY_NO | PASS
    edge: float                    # expected move per contract, net of fees
    entry_price: float
    size_usd: float
    reason: str

    def to_dict(self) -> dict:
        return asdict(self)


def decide(predicted_mid: float, bid: float | None, ask: float | None,
           confidence: float = 0.5, bankroll: float = 50_000.0,
           min_edge: float = 0.02, max_fraction: float = 0.02) -> Decision:
    """Turn a predicted price into an action against the live book.

    Buying yes costs the ask and is worth the predicted mid; buying no costs
    ``1 - bid`` and is worth ``1 - predicted``. Both pay a fee on entry. The
    larger of the two, if it clears ``min_edge``, is the trade.

    Size scales with confidence and is capped, because this predicts a price
    five minutes out and being right about direction says nothing about
    magnitude.
    """
    if bid is None or ask is None or not 0 < bid <= ask < 1:
        return Decision("PASS", 0.0, 0.0, 0.0, "no two-sided market")

    yes_edge = predicted_mid - (ask + taker_fee(ask))
    no_edge = (1 - predicted_mid) - ((1 - bid) + taker_fee(1 - bid))

    if yes_edge >= no_edge and yes_edge > min_edge:
        action, edge, entry = "BUY_YES", yes_edge, ask
    elif no_edge > min_edge:
        action, edge, entry = "BUY_NO", no_edge, 1 - bid
    else:
        best = max(yes_edge, no_edge)
        return Decision("PASS", round(best, 4), 0.0, 0.0,
                        f"best edge {best:+.4f} does not clear {min_edge:.0%}")

    fraction = max_fraction * max(0.0, min(1.0, confidence))
    return Decision(action, round(edge, 4), entry,
                    round(bankroll * fraction, 2),
                    f"{action} at {entry:.2f}, edge {edge:+.4f} after fees")


def horizon_agent(config: AgentConfig | None = None,
                  tools: Toolbox | None = None,
                  minutes: int = HORIZON_MINUTES) -> Agent:
    """Predict the mid price ``minutes`` ahead. Trading is decided in code."""
    return Agent(
        name=f"kalshi-horizon-{minutes}m",
        description=f"Predicts this contract's mid price {minutes} minutes ahead.",
        context=CONTEXT,
        tools=tools or kalshi_tools(),
        config=config or AgentConfig(default_model="anthropic/claude-sonnet-4.5",
                                     max_usd=0.20),
        plan=Plan(steps=[
            ToolStep(name="quote", tool="market_quote",
                     args={"ticker": "{{question}}"}, output_key="quote", fail_ok=True),
            ToolStep(name="path", tool="price_history",
                     args={"ticker": "{{question}}", "hours_back": 1.5, "hourly": False},
                     output_key="path", fail_ok=True),
            PromptStep(
                name="read",
                prompt=(f"Contract: {{{{question}}}}\n\nNow: {{{{quote}}}}\n\n"
                        "Recent minute bars:\n{{path}}\n\n"
                        "Get the live game state, and the recent tape. Report in three "
                        "lines: the score and clock, whether the price has been moving "
                        "or flat, and whether anything just happened that the price may "
                        "not have finished absorbing."),
                tools=["find_game_for_market", "game_state", "recent_trades",
                       "recent_plays", "order_book"],
                max_tool_iterations=4,
                output_key="state",
            ),
            PromptStep(
                name="predict",
                prompt=(f"Contract: {{{{question}}}}\nNow: {{{{quote}}}}\n"
                        "Recent bars: {{path}}\nState: {{state}}\n\n"
                        f"Where will the mid price be in {minutes} minutes? Give the "
                        "price, an interval, a direction and how confident you are. "
                        "FLAT is the right answer on a quiet market."),
                output_schema=PREDICTION_SCHEMA,
                output_key="prediction",
            ),
        ]),
    )


def target_time(minutes: int = HORIZON_MINUTES) -> str:
    """The instant a prediction made now should be scored against."""
    return (datetime.now(timezone.utc) + timedelta(minutes=minutes)).isoformat()
