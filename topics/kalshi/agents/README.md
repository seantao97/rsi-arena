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
| **pipeline** | Pre-game. Fixed order: rules → orient → research loop → coherence → price → write. Each step sees only the tools it needs. |
| **freeform** | Pre-game. One prompt, every tool, a 14-call budget. The model decides what to call and when. |
| **inplay** | A game already under way. State first, then how this contract has moved on earlier events, then whether the price has absorbed the latest one. |

Neither is the framework. They are generation zero — the thing an optimizer is
meant to beat.

## Continuous forecasting

```bash
python -m topics.kalshi.agents TICKER --watch --poll 30 --budget 5.00
```

`--watch` re-forecasts while a game runs, and **only when something material
changed** — the score, the period, or the price by more than `--price-step`
(3c default). Polling state is free; the model call is not, so a fixed interval
would keep paying to re-answer a state that has not moved. One-cent noise is
suppressed; a run scoring is not.

## The primitive set

Eighteen tools, each a thin adapter over a function that already exists.

| Group | Tools |
|---|---|
| What can I bet on | `list_markets` `event_markets` `market_rules` |
| What is it worth | `market_quote` `order_book` `price_history` `recent_trades` |
| What is happening | `todays_fixtures` `game_state` `recent_plays` `game_context` `sportsbook_line` |
| In-play | `market_reaction` `unexplained_moves` |
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

## Findings from building this

Three defects in the data layer surfaced and are fixed. One gap is mine and
remains open.

**1. `is_fixture` let season futures through — fixed.** It reads Kalshi's
`frequency`, and `custom` is a catch-all covering futures as well as games, so
the World Series winner market passed `fixtures_only=True`. Its docstring now
says it is a hint rather than a filter, and `whats_bettable` re-checks each
market with `linking.is_fixture_event`, which is definitive and costs nothing
because the market is already in hand. Zero futures now leak.

**2. Resolving a market to its fixture took a whole series sweep — fixed.**
`linking.link_event` resolves one event directly.

**3. Sync tools silently defeated the runtime's concurrency — fixed here, not
there.** The runtime runs a sync tool inline on the event loop while
`Toolbox.call_many` gathers calls expecting them to overlap, so four Kalshi
calls that looked concurrent ran one after another with the loop blocked. Every
tool in this file is now async and offloads with `asyncio.to_thread`, which
measures at **≈2× on four concurrent calls** and keeps the loop responsive. The
runtime is untouched.

Two smaller things fell out of that: `Discovery`'s catalogue cache is now
lock-guarded, since threads can request it at once, and `RateLimiter.take` no
longer sleeps while holding its lock, which would have serialised every caller
behind whichever one ran out of tokens.

**4. `todays_fixtures` and `game_detail` spoke different id spaces — fixed.**
MLB and NHL use their official feeds, which are richer for score and plays, so
`todays_fixtures` returns an MLB gamePk. ESPN is still the only source of odds,
injuries and boxscore, and it answers 404 to a foreign id — gamePk `824474` is
ESPN event `401816592`. `gamestate.resolve_espn_event` translates by date and
team name, trying the day either side because the two feeds date a late fixture
differently. Found by running the agent, which reported "no sportsbook lines
available" while the tool was quietly 404ing. 9 of 9 MLB fixtures now resolve.

**5. Sportsbook odds are soccer-only.** ESPN carries DraftKings and Bet365 for
EPL, Liga MX and the Champions League, and publishes nothing for MLB, NFL or
WNBA. `sportsbook_line` now says so explicitly rather than returning an error,
because a coverage gap and a broken call should not look the same to an agent.

**6. `timeline.py` was not exposed as a tool — fixed.** The earlier reasoning
was wrong: I claimed a timeline tool needed a summarisation I would have to
invent, but `reactions()` *is* that summarisation and it already existed. The
raw `build()` is 266 entries and ~3,300 tokens for one game, which is genuinely
the wrong shape for a context window; `reactions()` is 3 items and ~175 tokens.
`market_reaction` and `unexplained_moves` expose the summarised views. `build()`
stays internal.

`market_reaction` is what makes in-play forecasting more than restating the
score: it measures how much *this exact contract* moved on earlier events, so
the agent can judge whether the current price has already absorbed the latest
one rather than guessing the sensitivity.

## Running without a model

`--dry-run` calls every primitive against the live exchange and prints what came
back. It costs nothing and is the fastest way to tell whether a failure is in
the data layer or the model.
