# Kalshi sports prediction agents

Two agents that forecast a Kalshi sports contract, built entirely on the arena
runtime (`rsi_arena/`) and the Kalshi data layer (`topics/kalshi/`). Nothing new
was added to either — if a tool needed a helper that did not exist, that is
recorded as a finding below rather than papered over.

```bash
export OPENROUTER_API_KEY=...

python -m topics.kalshi.agents --league MLB                 # pick a live game, forecast it
python -m topics.kalshi.agents TICKER --agent freeform
python -m topics.kalshi.agents TICKER --agent both --trace
python -m topics.kalshi.agents --league EPL --dry-run       # tools only, no spend
```

## The two agents

Same model, same tools, same $2.00 ceiling, same context. Only the order of
operations differs — which is the comparison the arena exists to make.

| | |
|---|---|
| **pipeline** | Fixed order: rules → orient → research loop → coherence → price → write. The plan decides what runs, and each step sees only the tools it needs. |
| **freeform** | One prompt, every tool, a 14-call budget. The model decides what to call and when. |

Neither is the framework. They are generation zero — the thing an optimizer is
meant to beat.

## The primitive set

Sixteen tools, each a thin adapter over a function that already exists.

| Group | Tools |
|---|---|
| What can I bet on | `list_markets` `event_markets` `market_rules` |
| What is it worth | `market_quote` `order_book` `price_history` `recent_trades` |
| What is happening | `todays_fixtures` `game_state` `recent_plays` `game_context` `sportsbook_line` |
| Structure and pricing | `coherence_check` `price_the_edge` `devig_odds` `find_game_for_market` |

Three of them carry the opinions that make the output honest rather than
plausible:

- **`market_rules` first.** The contract settles on its own terms, not on what
  the market is colloquially about.
- **`price_the_edge` before claiming one.** Kalshi's fee peaks near 50c, so a 2c
  edge at midprice is nothing and the same edge at 90c is real.
- **`sportsbook_line` de-vigs.** Comparing to a raw moneyline overstates edge by
  roughly half the margin, and soccer is three-way.

`PASS` is a valid position and the schema requires a stake of zero when the edge
after fees is not positive.

## Findings — what the infrastructure is missing

Building this exercised the data layer end to end. Four things surfaced.

**1. `SeriesClass.is_fixture` is not trustworthy.** It reads Kalshi's
`frequency`, but `custom` is a catch-all covering season futures as well as
single games — the World Series winner market is `custom`, so it passes
`fixtures_only=True`. The reliable test is whether `parse_event_ticker` returns
a fixture, since only a game encodes a date and two team codes. The CLI works
around it; the taxonomy should be fixed.

**2. Nothing resolves a market to its fixture in one call.** `find_game_for_market`
has to resolve the series, harvest team codes, then link the whole series just
to find one event. It works, but it is the most expensive tool in the set by a
wide margin, and a `link_event(event_ticker)` on `linking` would make it one
call.

**3. The data layer is synchronous.** Every tool blocks the event loop, so the
agent cannot overlap a slow ESPN call with a Kalshi one. Fine for a single run,
wrong for a slate.

**4. No historical fixture state.** `todays_fixtures` takes a date, but there is
no way to ask "what did the market and the game look like at 19:42" in one step
— `timeline.py` does exactly this and is not exposed as a tool, because a tool
returning an interleaved event stream needs a summarisation the agent layer
should not be inventing.

## Running without a model

`--dry-run` calls every primitive against the live exchange and prints what came
back. It costs nothing and is the fastest way to tell whether a failure is in
the data layer or the model.
