# Kalshi in-play agent

A working agent that trades soccer while the match is being played, built only
on the data modules in `topics/kalshi/`. It is deliberately a **base harness**,
not a tuned model: RSI is meant to hand this to a model and let it write a
better one, and that only works if the starting point is simple enough to read
and honest enough to score.

## The task

The agent predicts **where the contract's mid price will be five minutes from
now.** Not who wins.

Asking for the true probability puts the agent against a market that has watched
the same match with more money on it, and gives one label per contract, hours
later, at settlement. Asking where the price goes next is a question the price
path, the tape and the clock actually bear on — and five minutes later the
answer is in the candlestick history, so every run is a scored example within
the same half. A night of soccer yields hundreds of labels instead of a handful.

It also removes the dependency that blocked soccer: no feed publishes
play-by-play for it, but the price path, the score and the clock are all
available, and those are what this task needs.

### It answers with a change, and a quote

Not a price. Two numbers:

- `delta_cents` — how far the mid moves. Zero is a real answer, and the right
  one on a quiet book.
- `half_width_cents` — half the width of the tightest two-sided market it would
  stand behind.

Code applies both to the **exchange's** mid. That matters for two measured
reasons. Asked for a price level, 63% of forecasts returned the current mid
exactly, which scores zero by construction. And asked for a level, a misread of
the book became an edge: one live forecast described a price "already fading
back to 0.105" while the market was at 0.295, and the further off the reading
the larger the fake edge, so the fee threshold selected for exactly those. With
a change, the anchor cannot be wrong and doing nothing costs a deliberate zero.

`horizon.decide()` then compares that quote to the live book. It takes when
taking clears the fee, and otherwise rests an order inside the spread — Kalshi's
maker fee is about a quarter of taker, and a resting order crosses no spread.
Nothing that arithmetic can settle is left to the model.

## Running it

```bash
# autonomous: sweep several leagues, adopt live markets, stop at $3
python -m topics.kalshi.agents.supervisor \
    --league EPL,LALIGA,MLS,USL --mode horizon --discover \
    --max-contracts 8 --poll 150 --budget 3.00

# score the windows that have come due
python -m topics.kalshi.agents.verify --mode horizon --plots
```

`--discover` is what makes it a service. It rescans the leagues, adopts live
markets with a two-sided quote, releases them at settlement and refills the
slot. Slots round-robin twice — one game per league before any league gets a
second, one contract per game before any game gets a second — because contracts
on one match are correlated observations where matches are independent ones.

State lives in `~/.kalshi-agent/` and survives restarts, including the budget:
a crash loop cannot spend the cap twice.

## Scoring

The benchmark is **no change**. Predicting the price stays put is free and right
most of the time, so `skill` reports the fraction of that benchmark's error the
agent removed, and goes negative when the agent would have done better saying
nothing. Absolute error alone flatters a quiet market.

| | |
|---|---|
| `mae` / `naive_mae` | mean price error, agent vs no-change |
| `skill` | `1 − mae/naive_mae`. Negative means worse than silence. |
| `direction_accuracy` | only on windows that moved ≥1¢, only on non-flat calls |
| `coverage` | how often the realised price landed inside the stated quote |
| `echoed the market` | how often it predicted the current mid *exactly* |
| `quoted` / `filled` / `traded` | orders placed, orders hit, positions taken |
| `pnl` / `roi` | entered at the order's price, marked out at the realised mid |

Three guards decide what counts as a scored window, all of them learned from
being wrong:

- **90s settle margin.** Kalshi's minute candle is not queryable the instant
  that minute ends; scoring on the dot reads the previous candle.
- **180s staleness limit.** Kalshi emits a candle only for minutes that saw
  activity — a settled EPL contract produced 29 candles across 75 minutes.
- **Two-sided only.** The post-close empty book quotes 0.00/1.00, which would
  score every prediction against a fictional 0.50.

A **fill needs a print**, not a quote. On a thin book the best bid collapses
whenever the makers pull, with nothing traded: one window showed
`bid[0.08..0.18]` against volume 0 while every print was 0.23 or higher. Scoring
that as a fill booked a phantom +$339 and turned a losing run into a winning
one. Fills are read from traded prices on candles with volume.

Reports count **distinct contracts** and say so when the number is small. Fifty
windows on one match are fifty correlated observations of one game.

## What it currently does

Measured live over 132 windows on six contracts, Newcastle vs Liverpool:

```
price error       0.0334   (no-change 0.0334)
skill             +0.1%    indistinguishable from it
direction         57.9% of 19 calls
coverage          71.2%
echoed the market 105/132
quoted 27 -> 13 filled (48%) -> -$407 (-8.07%), win rate 30.8%
```

The error matches the benchmark to four decimals because **80% of forecasts are
the current mid, repeated**. This is not a bad predictor so much as one that
declines to have a view — and on the minority of occasions it does have one, it
loses money.

That is the starting position, and each part of it is separately measurable:

1. **skill ≈ 0** — the headline number, and the easiest to move.
2. **80% echoing** — structural. The model will not disagree with the market.
3. **Quoting loses** — not "trades too little". Resting a quote is only possible
   where the spread is wide, and spreads are wide because those books are thin
   and hard to price. Participation went from 1.4% to 11% by allowing maker
   orders, and every added trade landed in the contracts it understands worst.

## Layout

| file | |
|---|---|
| `horizon.py` | the agent, `quote_from()`, and `decide()` — the trading rule |
| `agents.py` | the earlier probability agents (`pipeline`, `freeform`, `inplay`) |
| `tools.py` | 19 async tools over `topics/kalshi/` |
| `supervisor.py` | discovery, polling, budget, durable state |
| `validation.py` | refuses to act on output that disagrees with itself |
| `verify.py` | settlement scoring — Brier, calibration, paper pnl |
| `verify_horizon.py` | five-minute scoring — skill, fills, coverage, pnl |
| `__main__.py` | single-shot and `--watch` runs |

The probability agents are kept because settlement scoring answers a different
question — whether the agent understands the game — and both signals are useful
to whatever writes the next harness.
