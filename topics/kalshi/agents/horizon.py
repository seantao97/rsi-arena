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

**The model predicts; the code decides.** The agent returns a *change* and a
quote width, and :func:`decide` turns those into an action against the live book
and the fee schedule. Nothing is left to the model that arithmetic can settle,
which removes an entire class of defect seen live — a position that contradicted
the edge the same output reported.

Asking for the change rather than the price is not a detail. Two failure modes
measured over 144 live forecasts both come from asking for a level:

* 63% of forecasts returned the current mid *exactly*. Copying the visible
  number is the path of least resistance when a level is what is wanted, and it
  scores zero by construction. Asked for a change, doing nothing costs the model
  a deliberate ``0``.
* Both trades the run produced came from misreading the book. One explained a
  price "already fading back to 0.105" while the market was at 0.295. Because
  the anchor was the model's own reading, a misreading manufactured an edge out
  of nothing — and the further off it was, the larger the fake edge, so the fee
  threshold selected for exactly these. A change is applied by code to the true
  mid, so the anchor can no longer be wrong.

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

from ..fees import maker_fee, taker_fee
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

You answer with two numbers.

**How many cents it moves.** Not where the price is — where it goes. Zero is a real
answer and often the right one; a quiet book with nothing happening does not move.
But zero is a decision, not a default, and if the game has just changed you are
expected to say so in cents.

**How wide a market you would make.** Half the width of the tightest two-sided quote
you would actually stand behind. Two cents means you would buy two under your number
and sell two over it, and you would honour both sides. Ten cents means you barely
know. Quote what you would trade, not what is safe — but a width you cannot defend
will be paid for, because the trade goes on at your price and settles at the
market's."""

PREDICTION_SCHEMA = {
    "type": "object",
    "properties": {
        "delta_cents": {
            "type": "number",
            "description": ("How many cents the mid will MOVE over the next five "
                            "minutes. Negative for down, 0 for no move. Do not "
                            "state a price — state the change."),
        },
        "half_width_cents": {
            "type": "number",
            "description": ("Half the width of the tightest two-sided market you "
                            "would actually stand behind, in cents. This is your "
                            "quote: you are saying you would buy at "
                            "(prediction - this) and sell at (prediction + this). "
                            "Narrow means you will trade; wide means you will not."),
        },
        "confidence": {"type": "number",
                       "description": "0-1. How sure, given how thin and noisy this book is."},
        "driver": {"type": "string",
                   "description": "The one thing you expect to move it, or why nothing will."},
        "falsifier": {"type": "string",
                      "description": "What would show this call was wrong, before settlement."},
    },
    "required": ["delta_cents", "half_width_cents", "confidence",
                 "driver", "falsifier"],
    "additionalProperties": False,
}


@dataclass(frozen=True)
class Decision:
    """What the arithmetic says to do with a prediction."""

    action: str                    # BUY_YES | BUY_NO | MAKE_YES | MAKE_NO | PASS
    edge: float                    # expected move per contract, net of fees
    entry_price: float
    size_usd: float
    reason: str

    def to_dict(self) -> dict:
        return asdict(self)


def quote_from(mid: float, delta_cents: float,
               half_width_cents: float) -> tuple[float, float, float]:
    """Turn the model's change and width into a price and a two-sided quote.

    Anchored on the exchange's mid, not on anything the model read off. Clamped
    into (0, 1) because a contract cannot be worth less than nothing or more
    than a dollar, however far the stated move goes.
    """
    predicted = min(0.99, max(0.01, mid + delta_cents / 100))
    width = max(0.0, half_width_cents) / 100
    return predicted, max(0.0, predicted - width), min(1.0, predicted + width)


def decide(predicted_mid: float, bid: float | None, ask: float | None,
           confidence: float = 0.5, bankroll: float = 50_000.0,
           min_edge: float = 0.01, max_fraction: float = 0.02,
           interval: list | tuple | None = None,
           allow_maker: bool = True) -> Decision:
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
    elif allow_maker and predicted_mid is not None:
        return _make(predicted_mid, low, high, bid, ask, confidence,
                     bankroll, min_edge, max_fraction)
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


def _make(predicted: float, low: float, high: float, bid: float, ask: float,
          confidence: float, bankroll: float, min_edge: float,
          max_fraction: float) -> Decision:
    """Post inside the spread instead of crossing it.

    Taking is expensive on these books: the spread is one to three cents and the
    taker fee another one to two, so a taker has to disagree with the market by
    two or three cents before anything clears. Measured over 146 live windows,
    only 4% did — and loosening the threshold did not help, because the binding
    constraint was never the threshold.

    A resting order changes the arithmetic. It does not cross the spread, and
    Kalshi's maker fee is about a quarter of the taker fee. Quoting one cent
    inside the market at a two-cent half width earns the difference rather than
    paying it.

    What it buys instead is adverse selection: a resting bid fills precisely
    when the market is coming to meet it, which is when the forecast is wrong.
    That is a real cost and the scoring has to see it — a fill is only counted
    when the price actually traded through, so being filled on the way down
    shows up as the loss it is.
    """
    # Improve the market rather than join it, and never quote through the far
    # side — a bid above the ask is a taker order wearing a maker's clothes.
    post_bid = min(max(low, bid + 0.01), ask - 0.01)
    post_ask = max(min(high, ask - 0.01), bid + 0.01)

    yes_edge = predicted - (post_bid + maker_fee(post_bid))
    no_edge = (1 - predicted) - ((1 - post_ask) + maker_fee(1 - post_ask))

    if yes_edge >= no_edge and yes_edge > min_edge and bid < post_bid < ask:
        action, edge, entry = "MAKE_YES", yes_edge, post_bid
    elif no_edge > min_edge and bid < post_ask < ask:
        action, edge, entry = "MAKE_NO", no_edge, 1 - post_ask
    else:
        best = max(yes_edge, no_edge)
        return Decision("PASS", round(best, 4), 0.0, 0.0,
                        f"no restable quote inside {bid:.2f}/{ask:.2f} "
                        f"clears {min_edge:.0%} (best {best:+.4f})")

    fraction = max_fraction * max(0.0, min(1.0, confidence))
    return Decision(action, round(edge, 4), entry,
                    round(bankroll * fraction, 2),
                    f"{action} resting at {entry:.2f} inside {bid:.2f}/{ask:.2f}, "
                    f"edge {edge:+.4f} after maker fees")


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
                        f"How many cents does this mid move over the next "
                        f"{minutes} minutes, and how wide a market would you make "
                        "around that? Zero cents is a real answer on a quiet book. "
                        "Quote a width you would actually stand behind on both "
                        "sides."),
                tools=[],
                output_schema=PREDICTION_SCHEMA,
                output_key="prediction",
            ),
        ]),
    )


def target_time(minutes: int = HORIZON_MINUTES) -> str:
    """The instant a prediction made now should be scored against."""
    return (datetime.now(timezone.utc) + timedelta(minutes=minutes)).isoformat()
