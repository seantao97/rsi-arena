# Kalshi sports infrastructure

Data layer for the sports topics. The questions an agent needs answered, plus a
collector that records the answers at high frequency.

| Question | Module |
|---|---|
| *What can I bet on?* | [`discovery.py`](discovery.py) — series, events, markets, classified by sport, league and market type |
| *What is this market worth, now or at 14:03?* | [`quotes.py`](quotes.py) — live quotes, order books, candles, and point-in-time lookup from recorded snapshots |
| *What is actually happening in the game?* | [`gamestate.py`](gamestate.py) — scores, situation and play-by-play |
| *Which game is this market about?* | [`linking.py`](linking.py) — parses fixtures out of event tickers and matches them to the game feed |
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
python -m topics.kalshi.recorder --leagues MLB,NFL,NBA --interval 1.0 --db kalshi.db
```

Then replay any instant:

```python
q = Quotes(db_path="kalshi.db")
q.state_at("KXMLBGAME-26AUG17STLCIN-CIN", datetime(2026, 8, 17, 19, 3, tzinfo=timezone.utc))
q.closing_price("KXMLBGAME-26AUG17STLCIN-CIN")   # the CLV benchmark
```

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
| NFL, NBA, WNBA, NCAA, major soccer | ESPN public endpoints | Scores, situation, play-by-play |

ESPN is undocumented. It has been stable for years but treat a schema change as
expected rather than exceptional — every adapter normalises to the same
`GameState`, so a break is contained to one function.

## Storage

SQLite, WAL mode, append-only. Nothing is updated in place, so a replay of any
past instant returns exactly what was observed then. A busy slate is a few
hundred rows a second, comfortably inside what SQLite absorbs.

Tables: `quotes`, `books`, `trades`, `markets`, `game_states`, `plays`, `links`.

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

- **Order book depth at scale.** REST books are one call per market, so
  recording them for a full slate is expensive. Use the stream, or
  `--book-depth N` to cap it.
- **126 series have no derivable league.** Mostly one-off world-soccer
  competitions. They still carry a correct sport and a `SOCCER_OTHER`-style
  generic league, so filtering by sport loses nothing.
