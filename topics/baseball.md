# MLB betting

Agents place bets on MLB games. Bets settle at the final out. P&L is real.

Running example: **Yankees at Red Sox, 19:10 ET, Fenway.**

## Objective

> For a given game, find the bet with positive expected value at the available price, or pass.

**Asked of the reader** — *"Which of these would you rather have bet?"* Winner or tie, then one tap for
why: model, evidence, price, or staking.

## Budget and scoring

| | |
|---|---|
| Bank | $50,000, marked daily |
| Max stake | 2% of bank per selection, 5% per game |
| Markets | Moneyline, run line, totals, first five innings, team totals, NRFI/YRFI, strikeout and hits props |
| Decision time | Any time up to first pitch. Timestamp recorded. |
| Scored by | Settled P&L, ROI per bet, and **closing-line value** |

CLV is the primary skill signal here for the same reason as soccer — 162 games a night makes volume
easy, but P&L variance still swamps edge for months.

## Answer contract

| | |
|---|---|
| **Selection** | Market, side, book, price taken, stake. Or `PASS`. |
| **Model probability** | The agent's probability for the selection. |
| **Fair price** | The de-vigged market probability and the edge in percentage points. |
| **Run distribution** | A distribution over final scores, from which totals, run line and team totals all price consistently. |
| **Key inputs** | Usually the starter, the bullpen state, the wind, or the umpire. |
| **Falsifier** | *"If the wind flips to blowing in, the over is wrong."* |
| **Staking** | Stake as a fraction of bank and the implied Kelly fraction. |

## Primitives

### Pitching

| | |
|---|---|
| `probable_pitchers(game) → starters` | Announced starters, with confirmation status. |
| `pitcher_stats(player, window) → data` | FIP, xFIP, SIERA, K%, BB%, pitch mix, velocity trend. ERA is a trap; the estimators are the input. |
| `times_through_order(pitcher) → splits` | Performance by time through the lineup. Drives when the bullpen enters, which drives the total. |
| `bullpen_state(team) → availability` | Appearances and pitches over the last three days. A gassed pen is worth a quarter-run. |

### Hitting and lineup

| | |
|---|---|
| `lineup(game) → order` | Posted lineup with batting order. Posts one to three hours before first pitch. |
| `batter_stats(player, split) → data` | wOBA, ISO, K% split by pitcher handedness. Platoon splits are the whole game in props. |
| `team_stats(team, window) → data` | Team wOBA, baserunning, defensive efficiency. |

### Environment

| | |
|---|---|
| `park_factors(venue) → factors` | Run, home run and handedness factors. Coors and Petco are different sports. |
| `weather_at_park(game) → conditions` | Temperature, humidity, and **wind speed and direction relative to the outfield**. Wind at Wrigley moves the total by a full run. |
| `umpire(game) → tendencies` | Home-plate umpire strike-zone size, K rate, and run environment against league baseline. |

### Market

| | |
|---|---|
| `read_book(game, market) → prices` | Prices across books. |
| `derive_implied(prices) → probabilities` | Removes vig before any edge is claimed. |
| `line_history(game, market) → series` | Movement, and whether it came on lineup or weather news. |
| `closing_line(game, market) → price` | The benchmark for CLV. |
| `check_coherence(markets) → violations` | Whether first-five-innings, full-game, run line and totals price consistently against each other. F5 versus full game is the most frequently inconsistent pair. |

### Modelling

| | |
|---|---|
| `simulate_game(params, draws) → distribution` | Inning-by-inning simulation returning a run distribution for both sides. Everything else prices off it. |
| `base_rate(reference_class) → frequency` | League baselines by park, handedness and total. |
| `kelly(p, price, fraction) → stake` | Fractional Kelly. |
| `cost_model(selection, stake) → costs` | Book margin, commission, and the price available at size. |

### Shared

`search` · `fetch` · `weigh_source` · `extract_claims` · `verify_claim` · `compute` · `run_code` ·
`decompose` · `estimate` · `calibrate` · `sensitivity` · `recall` · `remember` · `counter` · `cite` ·
`draft` · `critique`

## Runtime additions

| | |
|---|---|
| `MLBFeed` | Schedules, probables, lineups, player and team data. |
| `OddsFeed` | Prices across books, line history, closing prices. Read-only. |
| `Bank` | Simulated bankroll and open positions. |
| `Settlement` | Results and settled bets, readable by `recall`. |

## Notes

**Volume is the reason to build this one.** Fifteen games a night across a dozen markets each gives more
settled bets in a week than soccer gives in a season. If any topic can produce enough data to tell
whether preference tracks profit, it is this one.

**Props are where the softness is.** Strikeout and hits lines are priced off simpler models than the
main markets and move less on information. An agent that only bets moneylines will not clear the vig.
