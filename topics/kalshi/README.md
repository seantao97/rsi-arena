# Kalshi sports infrastructure

Data layer for the sports topics. The questions an agent needs answered, plus a
collector that records the answers at high frequency.

| Question | Module |
|---|---|
| *What can I bet on?* | [`discovery.py`](discovery.py) — series, events, markets, classified by sport, league and market type |
| *What is this market worth right now?* | [`quotes.py`](quotes.py) — live quotes and order books |
| *What was it worth at 14:03, or over its whole life?* | [`history.py`](history.py) — candlesticks from the API, open or settled markets |
| *What is actually happening in the game?* | [`gamestate.py`](gamestate.py) — scores, situation and play-by-play |
| *Which game is this market about?* | [`linking.py`](linking.py) — parses fixtures out of event tickers and matches them to the game feed |
| *Is anything priced inconsistently?* | [`coherence.py`](coherence.py) — no-arb checks across related markets, net of fees |
| *Is there edge left after costs?* | [`fees.py`](fees.py) — fee schedule, breakeven, Kelly, CLV |
| *Record all of it* | [`recorder.py`](recorder.py), [`storage.py`](storage.py) |

Credentials are optional — [`credentials.py`](credentials.py) reads the same
environment variables the other Kalshi tools in this account already use.

## Quickstart

```python
from topics.kalshi.discovery import Discovery
from topics.kalshi.quotes import Quotes
from topics.kalshi import gamestate

d = Discovery()
markets = d.whats_bettable(league="MLB", game_level_only=True)   # 802 open, Aug 2026

q = Quotes()
snap = q.get_markets([m.ticker for m in markets[:100]])          # one call per 100
book = q.get_orderbook(markets[0].ticker)

for g in gamestate.todays_games("MLB"):
    st = gamestate.mlb_game_state(g["id"])
    print(st.away, st.away_score, "@", st.home, st.home_score, st.detail["outs"], "outs")
```

Record continuously:

```bash
python -m topics.kalshi.recorder --leagues MLB,NFL,NBA --db kalshi.db   # game state only
```

## History — no storage, always the API

Kalshi keeps the whole life of every market, open or settled, so nothing is
recorded locally. There is no collector to run and no database that can drift
from the exchange.

```python
from topics.kalshi import History, MINUTE, HOUR

h = History()
h.full_history(ticker)                       # listing to close, minute bars
h.quote_at(ticker, when)                     # point in time; never returns later data
h.closing_quote(ticker)                      # last two-sided quote — the CLV benchmark
h.event_history(event_ticker)                # every market on a fixture
h.series_history(series, start, end, HOUR)   # a whole competition
h.settlement(ticker)                         # "yes" / "no" / None
h.trades(ticker, start, end)                 # the print tape, with taker side
h.volume_profile(ticker)                     # contracts traded at each price
h.rules(ticker)                              # settlement terms as written
```

Three constraints, all found by hitting the endpoint:

- `period_interval` accepts **1, 60 or 1440 only**. 5, 15 and 240 return a 400.
- A request spans at most **5000 periods** — 3.47 days at minute resolution.
  `candles()` chunks automatically.
- The path needs the **series** ticker, which is not on the market object.
  `resolve_series()` fetches it from `/events` and caches it.

Settled markets answer exactly like open ones. `request_count()` prices a sweep
before you make it — 500 markets over 30 days is 4,500 calls at minute
resolution and 500 at hourly.

## What we found in the API

Verified against the live exchange on 2026-08-17.

- **Reads need no auth.** `/series`, `/events`, `/markets` and `/markets/*/orderbook`
  are public. Credentials are only needed for the WebSocket and `/portfolio`.
- **13,029 series exist; 3,403 are sports** — the largest category on the exchange,
  ahead of Entertainment (2,500) and Politics (2,150).
- **Sport *is* an API field, in `tags`.** It covers 96% of sports series and is
  far better than pattern-matching a ticker, so [`taxonomy.py`](taxonomy.py)
  reads it first and falls back to regex. Sport is unresolved for 4% of series;
  126 have no derivable league, reported as `UNKNOWN` rather than guessed.
- **`frequency` separates fixtures from futures.** `custom`/`daily`/`weekly`
  mean one fixture; `annual`/`one_off` mean a season or tournament. That is
  authoritative, where inferring it from market type was not — it finds 2,464
  fixture-level series against 1,547 by heuristic.
- **Market type is not an API field** and is still derived from ticker plus
  title.
- **Rate limits are token-cost** with separate read and write buckets, refilling
  at 200/s on Basic. `RateLimiter` runs at 70% of budget.
- **Event tickers cannot be split on `-`** to recover the series: some series
  stems contain hyphens (`KXNFLWINS-KC`). Resolve by longest matching prefix.
- **Batch quoting works** — `/markets?tickers=a,b,c` takes 100 at a time, so a
  400-market slate costs four calls per tick and sustains 1Hz inside Basic.
- **Page-size caps differ by endpoint.** `/markets` accepts `limit=1000`;
  `/events` and `/series` reject anything above 200 with a 400 rather than
  clamping. `paginate` enforces the right cap per path.
- **Open-market pagination is not sport-first.** The first several thousand
  results of `/markets?status=open` contain no sports at all, so sweeping the
  global list to find them does not work — query by `series_ticker`.

## Game state sources

All free and keyless as of 2026-08-17.

| League | Source | Depth |
|---|---|---|
| MLB | `statsapi.mlb.com` (official) | Full play-by-play, pitch level, count, runners, current pitcher and batter |
| NHL | `api-web.nhle.com` (official) | Play-by-play, shifts |
| NFL, NBA, WNBA, NCAA | ESPN public endpoints | Scores, situation, play-by-play |
| 51 soccer competitions | ESPN, by validated slug | Scores, events, lineups |

`taxonomy.SOCCER_COMPETITIONS` maps ticker stems to ESPN slugs, and every slug
was checked against the live scoreboard endpoint. Routing is automatic:

```python
gamestate.fixtures_for_series(series_class)          # picks the slug
gamestate.for_series(series_class, game_id)
```

**ESPN throttles concurrency.** Twelve parallel requests failed for every slug,
including ones that work serially — requests are paced at 4/s through a shared
limiter. Do not fan out around it.

Coverage: **77% of fixture-level series** have a game-state feed. The remainder
is esports, Olympics, chess and four soccer leagues ESPN does not carry
(Korean, Egyptian, Polish, Canadian).

ESPN is undocumented. It has been stable for years but treat a schema change as
expected rather than exceptional — every adapter normalises to the same
`GameState`, so a break is contained to one function.

## Coherence and costs

A coherence violation needs no forecast: if "over 6.5 runs" can be bought below
where "over 7.5 runs" can be sold, the first strictly contains the second and
the difference is locked.

```python
Coherence().check_event(event_ticker)          # complement, partition, ladder
Coherence().check_series("KXMLBSPREAD")        # sweep a competition
```

Two things make the difference between a real finding and a fake one:

- **Executable prices only.** Buy at the ask, sell at the bid. Using mids
  manufactures edge that cannot be traded.
- **Fees netted off every leg.** A 1c gross edge at midprice is negative after
  the 2c taker fee, and is suppressed.

One event can hold **several independent ladders** — a spread event carries a
full strike ladder per team, so SF over 1.5 and CLE over 1.5 are not a
contradiction. Rungs are grouped by subject before anything is compared. An
earlier version did not do this and reported a steady stream of fake arbitrage.

`fees.py` is pure arithmetic, no API: `taker_fee` peaks at 1.75c at 50c and
approaches zero in the tails, so `breakeven(0.68)` is 0.70 and a 2c edge at
midprice is nothing.

## Storage

Only what Kalshi does not hold: `markets`, `game_states`, `plays`, `links`.
Quotes and order books are never persisted — history comes from the API.

`links` is the join table between a Kalshi event and a fixture in the game-state
feed; `linking.py` fills it — see below.

## Fixture matching

Kalshi writes fixtures into the event ticker with no separator between the team
codes:

    KXMLBSPREAD-26AUG171910AZBOS       26 Aug 2026, 19:10, AZ at BOS
    KXWNBAGAME-26AUG19MINGS            19 Aug 2026, MIN at GS

Splitting `AZBOS` needs to know which codes exist, and `MINGS` is MIN+GS rather
than MI+NGS. Rather than maintain a table per league, `harvest_team_codes`
reads the codes out of the market tickers under a series — `...-BOS4` is a
Boston spread line — so the set is always current.

```python
codes = linking.harvest_team_codes(client, "KXMLBSPREAD")     # 32 codes
links = linking.link_series(client, "KXMLBSPREAD", "MLB",
                            lambda lg, d: gamestate.todays_games(lg, d))
# KXMLBSPREAD-26AUG171910AZBOS -> game 824725  Arizona Diamondbacks @ Boston Red Sox
```

Matching to the feed is date plus a scored name comparison, since Kalshi's
codes are its own. Confidence is returned with every link; 0.9 means the code
is a clean prefix of the team name, 0.7 means it matched on initials.

## Field events

Golf, motorsport, Olympics and chess are priced as a field of entrants rather
than a two-sided fixture, so there is no team blob to split — the entrants are
the market tickers.

```python
linking.field_entrants(client, "KXKFTOUR-ADC26")   # 160 entrants
```

`linking.FIELD_SPORTS` says which sports to route this way.

## Streaming

[`stream.py`](stream.py) consumes `orderbook_delta` for true tick resolution.
It maintains the book locally from one snapshot plus deltas, and **drops deltas
that arrive before a snapshot or out of sequence**, marking the book desynced
rather than silently serving a wrong one.

```python
s = KalshiStream(tickers)
s.on_book = lambda t, b: print(t, b.best_bid, b.best_ask)
asyncio.run(s.run())
```

Needs `pip install websockets` and credentials — Kalshi authenticates the socket
even for public channels. Polling via `recorder.py` needs neither.

The wire format does not match what the schema names suggest, and all four of
these were found by running it, not by reading docs:

- `seq` is at the **top level** of the frame, not inside `msg`
- snapshot levels are `yes_dollars_fp` / `no_dollars_fp` as `[["0.0100","16.00"]]`
  — dollar strings, not integer cents
- deltas carry `price_dollars`, `delta_fp`, `side`
- **`seq` counts per subscription, not per market.** Every market on one `sid`
  shares the counter, so checking continuity per book reports a desync on
  almost every delta. It is checked once per frame instead.

## Known gaps

- **Order book history does not exist.** Candlesticks carry best bid and ask,
  not depth. Historical depth is unavailable at any price; only live depth is,
  via `stream.py`.
- **126 series have no derivable league.** Mostly one-off world-soccer
  competitions. They still carry a correct sport and a `SOCCER_OTHER`-style
  generic league, so filtering by sport loses nothing.
