# Kalshi agent configs

**This directory holds JSON and nothing else.** Each file is one agent: its
name, its context, its model settings, the tool names its plan may call, and the
plan itself. Every line of Python that runs them is in [`../eval/`](../eval/), and
everything that scores them is in [`../eval/`](../eval/).

The split is what makes a harness comparable. A config is data the arena can
diff, version and mutate; if the same file also held the loop that drives it,
"the harness changed" would stop meaning anything specific.

| config | |
|---|---|
| `kalshi-horizon-5m.json` | where this contract's mid goes in five minutes |

There used to be three more — settlement-probability harnesses with the full
tool set. They are gone because **they cannot be replayed**, and replay is the
only way this topic scores anything now. A harness that researches the news or
reads live game state cannot be put back at a past instant honestly: a story
filed after that instant would be answering with the future. A replayable
harness is one every tool of which can be frozen, which today means the book,
the price path and the tape.

```python
from topics.kalshi.eval.load import load_agent
agent = load_agent("horizon")          # short label, or the full file name
```

Binding is by tool *name*, which is the useful part: the same config loads
against the live exchange or against `eval.replay`'s frozen tools, because both
boxes answer to `market_quote`, `candlesticks` and `previous_trades`. That is
how a benchmark replays a harness without the harness knowing it is being
replayed.

An unknown tool name raises when the config loads, not at the step that calls
it — so a bad mutation fails immediately instead of four hours into a night.

The agent below is deliberately a **base harness**, not a tuned model: RSI is
meant to hand this to a model and let it write a better one, and that only works
if the starting point is simple enough to read and honest enough to score.

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

`horizon.decide()` then compares that quote to the live book, and prices the
**round trip**: buying yes costs the ask plus a fee, and getting out means
selling at the *bid* later and paying another. The mid is not a price anyone
trades at, so an edge measured to it is short by half a spread on each leg.
Where taking does not clear, it rests an order inside the spread instead —
Kalshi's maker fee is about a quarter of taker.

**Getting out is a decision too.** Nothing is marked out on a timer. Each tick,
if something is open the only question is whether to close it; if nothing is,
whether to open. A position the agent never closes is carried to settlement and
pays a dollar or nothing — the honest treatment, and the one that has so far
made the money.

## Running it

```python
from datetime import datetime, timezone
from topics.kalshi.eval import HorizonWindow

ev = HorizonWindow("KXEPLGAME-26AUG23NEWLFC-NEW",
                   datetime(2026, 8, 23, 15, 30, tzinfo=timezone.utc))
out = await ev.run()
```

Or over many windows:

```bash
python -m topics.kalshi.eval.run --help
```

Needs `OPENROUTER_API_KEY`. Kalshi reads need no credentials; only the portfolio
endpoints do.

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
| `opened` / `closed` / settled | positions taken, ended by decision, carried to the end |
| `pnl` / `roi` | booked on a close or a settlement; nothing is marked out on a timer |

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
one. Fills are read from traded prices on candles with volume, over the window
the order actually stood — and checked that way, a resting order here often does
not fill at all.

**Contracts are counted per match, not per market type.** One fixture is listed
under a series for each kind of bet — winner, spread, first half, corners — so
keying on the event ticker reported five games where there was one, and let a
single match take every slot the supervisor had.

Reports count **distinct contracts** and say so when the number is small. Fifty
windows on one match are fifty correlated observations of one game.

## What it currently does

Two evenings, 1,600-odd scored windows across two dozen matches, under the
pricing described above:

```
skill              about zero, run to run between -5% and +1%
echoed the market  70-80% of forecasts are the current mid, repeated
```

The error matches the no-change benchmark to three or four decimals, and it does
so because most forecasts *are* the benchmark. This is not a bad predictor so
much as one that declines to have a view.

Trading is rarer and more interesting than that summary suggests. Once the round
trip is priced properly, the arithmetic almost never clears: on one full evening,
**211 decisions produced a best edge of 0.0000 and not one positive**. That is
not a fault. This harness predicts moves of two to three cents and a round trip
costs two to three cents, so on a quiet one-cent book there is nothing there —
and 72% of the books it sees are a cent or two wide.

It does act when the move dwarfs the cost, and the two cases it has found are
worth naming:

- **Time decay.** A 0-0 match approaching full time drags the draw contract
  toward 1.00. Bought at 0.80 with five minutes left, carried to settlement.
- **A goal that overshoots.** A spread contract fell 0.73 to 0.33 in four
  minutes on a Chelsea goal; the agent called it an overreaction, bought the
  other side at 0.72 on a ten-cent spread, and Fulham pulled one back.

Both settled in full. The one position it *closed* by decision netted exactly
$0.00 — bought at 0.80, sold at 0.82, and the two cents paid for the round trip
to the penny. It then watched the same contract run to 0.99 and bought back in
higher.

That is the shape of the target. Not "trade more": **predict a move large enough
to pay for itself**, and stop selling the winners.

## Where the rest of it lives

| | |
|---|---|
| `../eval/load.py` | JSON to `Agent`, and the short labels the CLI takes |
| `../eval/trading.py` | `quote_from()` and `decide()` — the trading rule |
| `../eval/supervisor.py` | discovery, polling, budget, durable state |
| `../eval/__main__.py` | single-shot and `--watch` runs |
| `../eval/slate.py` | what is actually tradeable today |
| `../eval/validation.py` | refuses to act on output that disagrees with itself |
| `../eval/verify.py` | settlement scoring — Brier, calibration, paper pnl |
| `../eval/verify_horizon.py` | five-minute scoring — skill, fills, coverage, pnl |
| `../eval/replay.py` | the same windows of a finished match, for comparing harnesses |

**The model predicts; the code decides.** A config returns a *change* and a quote
width; `decide()` turns those into an action against the live book and the fee
schedule. Nothing is left to the model that arithmetic can settle, which removed
a defect seen live — a position that contradicted the edge the same output
reported. That rule is why `trading.py` is Python and not part of the config.

The probability agents are kept because settlement scoring answers a different
question — whether the agent understands the game — and both signals are useful
to whatever writes the next harness.
