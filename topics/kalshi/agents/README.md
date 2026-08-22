# Kalshi in-play agent

A working agent that trades soccer while the match is being played, built only
on the data modules in `topics/kalshi/`. It is deliberately a **base harness**,
not a tuned model: RSI is meant to hand this to a model and let it write a
better one, and that only works if the starting point is simple enough to read
and honest enough to score.

## The task

The agent predicts **where the contract's mid price will be five minutes from
now.** Not who wins.

That choice is the whole design.

Asking for the true probability puts the agent against a market that has watched
the same match with more money on it, and gives one label per contract, hours
later, at settlement. Asking where the price goes next is a question the price
path, the tape and the clock actually bear on — and every five minutes the
answer appears in the candlestick history, so each run is a scored example
within the same half. A night of soccer yields hundreds of labels instead of a
handful.

It also removes the dependency that blocked soccer: no feed publishes
play-by-play for it, but the price path, the score and the clock are all
available, and those are what this task needs.

**The model predicts; the code decides.** The agent returns a price, an interval
and a confidence. `horizon.decide()` compares that to the live book and the fee
schedule and produces the action. Nothing that arithmetic can settle is left to
the model — which removes the failure seen in the earlier probability agent,
where an output reported a negative edge and took a position anyway.

## Running it

```bash
# autonomous: find live Liga MX markets, predict every 150s, stop at $2.50
python -m topics.kalshi.agents.supervisor \
    --league LIGAMX --mode horizon --discover --max-contracts 2 \
    --poll 150 --budget 2.50

# score the windows that have come due
python -m topics.kalshi.agents.verify --mode horizon --plots
```

`--discover` is what makes it a service. It rescans the league, adopts live
markets with a two-sided quote, releases them at settlement and refills the
slot. State lives in `~/.kalshi-agent/` and survives restarts, including the
budget — a crash loop cannot spend the cap twice.

## Scoring

The benchmark is **no change**. Predicting that the price stays put is free and
right most of the time, so `skill` reports the fraction of that benchmark's
error the agent removed, and goes negative when the agent would have done better
saying nothing. Absolute error on its own flatters a quiet market.

| | |
|---|---|
| `mae` / `naive_mae` | mean price error, agent vs no-change |
| `skill` | `1 − mae/naive_mae`. Negative means worse than silence. |
| `direction_accuracy` | only on windows that moved ≥1¢, only on non-FLAT calls |
| `coverage` | how often the realised price landed in the stated interval |
| `pnl` / `roi` | enter at the quoted price, mark out at the realised mid, taker fees paid |

Both reports count **distinct contracts** and say so when the number is small.
Fifty windows on one match are fifty correlated observations of one game, and a
win rate computed over them is not evidence.

## Layout

| file | |
|---|---|
| `horizon.py` | the five-minute agent, and `decide()` — the trading rule |
| `agents.py` | the earlier probability agents (`pipeline`, `freeform`, `inplay`) |
| `tools.py` | 19 async tools over `topics/kalshi/` |
| `supervisor.py` | discovery, polling, budget, durable state |
| `validation.py` | arithmetic check on probability-mode output before it is recorded |
| `verify.py` | settlement scoring — Brier, calibration, paper pnl |
| `verify_horizon.py` | five-minute scoring — skill, direction, coverage, pnl |
| `__main__.py` | single-shot and `--watch` runs |

The probability agents are kept because settlement scoring answers a different
question — whether the agent understands the game — and both signals are useful
to whatever writes the next harness.
