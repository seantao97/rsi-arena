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

There is exactly **one model call per forecast**. The quote, the price path and
the tape come from tool steps, which make no model call, and the caller passes
the game state in — the supervisor already resolved the fixture, so paying a
tool-calling loop to rediscover it every two minutes bought nothing. Measured
live, that took a forecast from $0.13 across three calls to $0.018 across one,
which is the difference between a few dozen scored windows a night and a few
hundred.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime, timedelta, timezone

from rsi_arena import Agent, AgentConfig, Plan, PromptStep, Toolbox, ToolStep

from ..fees import taker_fee
from .tools import market_quote, price_history, recent_trades

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
                     "description": ("Low and high bound — your own two-sided "
                                     "quote. A trade is only taken when the "
                                     "exchange's price sits outside this, so "
                                     "widen it when you are unsure.")},
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
           min_edge: float = 0.02, max_fraction: float = 0.02,
           interval: list | tuple | None = None) -> Decision:
    """Turn a predicted price into an action against the live book.

    The prediction is a two-sided quote of the agent's own: ``interval`` is
    where it thinks the price will be, and the trade is only justified when the
    exchange's quote sits entirely outside it. So the edge is measured from the
    **near edge of the interval**, not from its middle — buying yes at the ask
    has to beat the low end of the predicted range, not just its centre.

    That makes the interval load-bearing. A wide interval is the agent saying it
    does not know, and it stops producing trades on its own, without a
    confidence threshold bolted on top.

    Falls back to the point prediction when no usable interval is given.

    Size scales with confidence and is capped, because being right about
    direction five minutes out says nothing about magnitude.
    """
    if bid is None or ask is None or not 0 < bid <= ask < 1:
        return Decision("PASS", 0.0, 0.0, 0.0, "no two-sided market")

    low = high = predicted_mid
    if isinstance(interval, (list, tuple)) and len(interval) == 2:
        try:
            low, high = sorted(float(x) for x in interval)
        except (TypeError, ValueError):
            low = high = predicted_mid
        # An interval that excludes its own point estimate is incoherent; the
        # point estimate is the thing the model was actually asked for.
        if not low <= predicted_mid <= high:
            low = high = predicted_mid

    yes_edge = low - (ask + taker_fee(ask))
    no_edge = (1 - high) - ((1 - bid) + taker_fee(1 - bid))

    if yes_edge >= no_edge and yes_edge > min_edge:
        action, edge, entry = "BUY_YES", yes_edge, ask
    elif no_edge > min_edge:
        action, edge, entry = "BUY_NO", no_edge, 1 - bid
    else:
        best = max(yes_edge, no_edge)
        note = "" if low == high else f" (worst case of [{low:.2f}, {high:.2f}])"
        return Decision("PASS", round(best, 4), 0.0, 0.0,
                        f"best edge {best:+.4f}{note} does not clear {min_edge:.0%}")

    fraction = max_fraction * max(0.0, min(1.0, confidence))
    bound = "point" if low == high else f"[{low:.2f}, {high:.2f}]"
    return Decision(action, round(edge, 4), entry,
                    round(bankroll * fraction, 2),
                    f"{action} at {entry:.2f}, edge {edge:+.4f} after fees "
                    f"vs {bound}")


def horizon_tools() -> Toolbox:
    """Just the three tools the plan calls.

    Worth about 70 tokens a forecast against the full nineteen-tool box —
    the saving is not the point. An agent that cannot reach a tool cannot
    surprise you by reaching for it, which matters once a model is rewriting
    this harness.
    """
    return Toolbox([market_quote, price_history, recent_trades])


def horizon_agent(config: AgentConfig | None = None,
                  tools: Toolbox | None = None,
                  minutes: int = HORIZON_MINUTES) -> Agent:
    """Predict the mid price ``minutes`` ahead. Trading is decided in code."""
    return Agent(
        name=f"kalshi-horizon-{minutes}m",
        description=f"Predicts this contract's mid price {minutes} minutes ahead.",
        context=CONTEXT,
        tools=tools or horizon_tools(),
        config=config or AgentConfig(default_model="anthropic/claude-sonnet-4.5",
                                     max_usd=0.20),
        plan=Plan(steps=[
            # Tool steps make no model call, so the quote, the path and the
            # tape are fetched rather than asked for. The game state is passed
            # in by the caller — the supervisor already resolved the fixture,
            # and paying a tool-calling loop to rediscover it every two minutes
            # bought nothing.
            ToolStep(name="quote", tool="market_quote",
                     args={"ticker": "{{question}}"}, output_key="quote",
                     fail_ok=True),
            ToolStep(name="path", tool="price_history",
                     args={"ticker": "{{question}}", "hours_back": 0.75,
                           "hourly": False},
                     output_key="path", fail_ok=True),
            ToolStep(name="tape", tool="recent_trades",
                     args={"ticker": "{{question}}", "limit": 12},
                     output_key="tape", fail_ok=True),
            PromptStep(
                name="predict",
                prompt=("Contract: {{question}}\n"
                        "Book now: {{quote}}\n"
                        "Game: {{game}}\n"
                        "Minute bars, last 45m: {{path}}\n"
                        "Recent prints: {{tape}}\n\n"
                        f"Where will the mid price be in {minutes} minutes? Give "
                        "the price, an interval, a direction and how confident you "
                        "are. FLAT is the right answer on a quiet market, and "
                        "predicting movement that does not come is how this loses "
                        "money."),
                tools=[],
                output_schema=PREDICTION_SCHEMA,
                output_key="prediction",
            ),
        ]),
    )


def target_time(minutes: int = HORIZON_MINUTES) -> str:
    """The instant a prediction made now should be scored against."""
    return (datetime.now(timezone.utc) + timedelta(minutes=minutes)).isoformat()
