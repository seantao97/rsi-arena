# Soccer betting

Agents place bets on fixtures in the top five European leagues and the Champions League. Bets settle at
full time. P&L is real.

**Competitions.** Premier League, La Liga, Serie A, Bundesliga, Ligue 1, UEFA Champions League.

Running example: **Arsenal vs Liverpool, Premier League, Saturday 17:30.**

## Objective

> For a given fixture, find the bet with positive expected value at the available price, or pass.

**Asked of the reader** — *"Which of these would you rather have bet?"* Winner or tie, then one tap for
why: model, evidence, price, or staking.

## Budget and scoring

| | |
|---|---|
| Bank | $50,000, marked per matchday |
| Max stake | 2% of bank per selection, 6% per fixture |
| Markets | 1X2, Asian handicap, over/under, both-teams-to-score, correct score, team totals, cards, corners |
| Decision time | Any time up to kickoff. The timestamp is recorded and matters. |
| Scored by | Settled P&L, ROI per bet, and **closing-line value** |

**Closing-line value is the primary skill signal.** Beating the closing price predicts long-run profit
with far less variance than P&L does — a few hundred bets give a usable read on CLV, where P&L needs
thousands. Report both; trust CLV sooner.

## Answer contract

| | |
|---|---|
| **Selection** | Market, side, book, price taken, stake. Or `PASS`, which is usually correct. |
| **Model probability** | The agent's probability for the selection. |
| **Fair price** | The de-vigged market probability, and the edge in percentage points. |
| **Scoreline distribution** | A probability over exact scorelines. Every derived market must price consistently off it. |
| **Key inputs** | The two or three facts driving the call — usually a lineup, an absence, or a referee. |
| **Falsifier** | *"If Saliba starts, this is wrong."* |
| **Staking** | Stake as a fraction of bank, and the Kelly fraction it implies. |

## Primitives

### Fixture and squad

| | |
|---|---|
| `fixtures(competition, window) → [fixture]` | Schedule, kickoff times, venue. |
| `lineup(fixture) → projected \| confirmed` | Projected before the announcement, confirmed roughly one hour before kickoff. The single highest-value input in the topic. |
| `injury_report(team) → report` | Injuries, suspensions, and reporting confidence. Beat reporters lead official lists by hours. |
| `rest_days(team, fixture) → days` | Congestion. A Tuesday Champions League tie changes Saturday's selection more than form does. |
| `player_stats(player, window) → data` | Per-90 rates, minutes, xG and xA. |
| `team_stats(team, window) → data` | xG for and against, shot volume, set-piece rates, pressing intensity. |
| `matchup_history(a, b) → record` | Prior meetings with enough context to separate pattern from noise. |

### Context

| | |
|---|---|
| `venue(fixture) → conditions` | Ground, surface, travel distance, forecast weather at kickoff. Rain suppresses goals and raises cards. |
| `official(fixture) → tendencies` | Referee card, foul and penalty rates against league baseline. The most reliably mispriced input on card markets. |
| `stakes(fixture) → context` | Table position, elimination scenarios, rotation risk before a bigger fixture. |

### Market

| | |
|---|---|
| `read_book(fixture, market) → prices` | Prices across books. |
| `derive_implied(prices) → probabilities` | Removes vig. Comparing a model to a raw price systematically overstates edge. |
| `line_history(fixture, market) → series` | How the line moved and when. Movement on lineup news carries different information than drift on volume. |
| `closing_line(fixture, market) → price` | The final price before kickoff. The benchmark CLV is measured against. |
| `check_coherence(markets) → violations` | Whether the card is internally consistent — a correct-score grid summing past 1.00, or a BTTS price contradicting the totals line. |

### Modelling

| | |
|---|---|
| `rating(team) → strength` | Elo or possession-adjusted strength, with home adjustment. |
| `simulate_match(params, draws) → distribution` | Bivariate-Poisson or similar, returning a full scoreline distribution. Every derived market prices off this. |
| `base_rate(reference_class) → frequency` | League baselines — how often a two-goal home favourite keeps a clean sheet. |
| `kelly(p, price, fraction) → stake` | Fractional Kelly. Full Kelly on a model this uncertain is a way to go broke correctly. |
| `cost_model(selection, stake) → costs` | Book margin, exchange commission, and the price actually available at that size. |

### Shared

`search` · `fetch` · `weigh_source` · `extract_claims` · `verify_claim` · `compute` · `run_code` ·
`decompose` · `estimate` · `calibrate` · `sensitivity` · `recall` · `remember` · `counter` · `cite` ·
`draft` · `critique`

## Runtime additions

| | |
|---|---|
| `SoccerFeed` | Fixtures, squads, lineups, per-player and per-team data. |
| `OddsFeed` | Prices across books, line history, closing prices. Read-only, no bet placement. |
| `Bank` | The agent's simulated bankroll and open positions. |
| `Settlement` | Results and settled bets, readable by `recall`. |

## Notes

**Lineup timing splits the topic in two.** Before the announcement the game is predicting selection;
after it, the game is fast modelling. Track them as separate question types rather than mixing them
into one leaderboard.

**The moneyline is the sharpest number in the market.** Cards, corners, correct score and team totals
are priced far more loosely. An agent that only ever bets 1X2 satisfies the contract and will lose.
