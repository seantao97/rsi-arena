# Sports

Agents answer questions about a scheduled fixture before kickoff. The topic is deliberately weighted
toward **granular** targets — scorelines, team totals, player lines — rather than who wins.

Running example throughout: **Arsenal vs Liverpool, Saturday 17:30.**

## Objective

> Given an upcoming fixture, produce the memo a competent, skeptical allocator would most want to have
> read **before** the match.

**Maximize** — which memo a reader would rather have had before deciding.

**Asked of the reader** — *"Which of these would you rather have read before this match?"* Winner or
tie, then one tap for why: evidence, reasoning, risk framing, or counter-case.

**Equalized** — same cost ceiling for both agents.

## Why granular

The moneyline is the most efficiently priced number in sports. A memo that says "Arsenal 58% to win"
is competing directly with the sharpest line in the market and will lose on average.

Correct-score grids, team totals, card and corner lines, and player props are priced far more loosely,
carry wider margins, and depend on inputs — a specific full-back's availability, a referee's card
rate — that reward research rather than raw modelling. **The topic is scoped there on purpose.** A
memo that only produces a win probability satisfies the contract but is unlikely to be preferred.

## Answer contract

| | |
|---|---|
| **Position** | A stated call on a named market, not a survey of the card. |
| **Scoreline distribution** | A probability over exact scorelines, not merely 1X2. The marginals for both team totals must be derivable from it. |
| **Evidence** | Each factual claim bound to a fetched document and its retrieval time. |
| **Probability** | A point estimate and interval on the named call. |
| **Versus line** | The best available price on that call, and the implied probability after removing vig. |
| **Counter-case** | The strongest argument against, including what the line knows that the memo may not. |
| **Falsifier** | A pre-committed observation — *"if Saliba is confirmed out, this is wrong."* |
| **Exposure** | Stake, worst case, and breakeven after margin. |

## Primitive set

Thirty-two steps.

### Question

| | | |
|---|---|---|
| 1 | `parse_settlement(market) → criteria` | What actually counts: regulation time only, own goals, abandonment and postponement rules, whether extra time is included. Settlement disputes in sports are almost entirely about this. |
| 2 | `calendar(team, window) → fixtures` | Congestion and rest. A midweek European tie three days earlier changes selection more than most in-game factors. |

### Team and player state

| | | |
|---|---|---|
| 3 | `roster(team, date) → players` | Registered and available squad. |
| 4 | `injury_report(team) → report` | Injuries, suspensions, and reporting confidence. Official lists lag beat reporters by hours, which is the window worth researching. |
| 5 | `lineup(fixture) → projected \| confirmed` | Projected XI before the announcement, confirmed after. The single highest-value input in the hour before kickoff. |
| 6 | `player_stats(player, window) → data` | Per-90 rates, shot and chance volume, minutes and usage. |
| 7 | `team_stats(team, window) → data` | Expected goals for and against, tempo, set-piece rates, style indicators. |
| 8 | `matchup_history(teamA, teamB) → record` | Prior meetings, with enough context to tell a real pattern from ten-year-old noise. |

### Context

| | | |
|---|---|---|
| 9 | `venue(fixture) → conditions` | Ground, surface, altitude, travel distance, and forecast weather at kickoff. Rain suppresses goals and raises cards. |
| 10 | `official(fixture) → tendencies` | The referee's card, foul and penalty rates against league baseline. Among the most reliably mispriced inputs on card and foul markets. |
| 11 | `stakes(fixture) → context` | Table position, elimination scenarios, rotation risk before a bigger fixture. A team safe in mid-table in May is a different team. |

### Market

| | | |
|---|---|---|
| 12 | `read_book(fixture, market_type) → prices` | Prices across books for the named market. |
| 13 | `derive_implied(prices) → probabilities` | Removes vig and returns a clean probability. Comparing to a raw price systematically overstates edge. |
| 14 | `line_history(fixture, market) → series` | How the line moved and when. A line that moved on lineup news carries different information than one that drifted on volume. |
| 15 | `read_market(ticker) → book` | The event contract, where one exists. |
| 16 | `related_markets(fixture) → [market]` | The rest of the card — 1X2, totals, both-teams-to-score, correct score, team totals. |
| 17 | `check_coherence(markets) → violations` | Whether the card is internally consistent: a correct-score grid summing past 1.00, or a both-teams-to-score price that contradicts the totals line. **This is where the topic's real edge is most likely to sit**, because inconsistency needs no forecast at all. |
| 18 | `external_forecast(fixture) → [source, P]` | Published models and ratings systems. |

### Modelling

| | | |
|---|---|---|
| 19 | `rating(team) → strength` | Elo-style or possession-adjusted team strength, with home adjustment. |
| 20 | `simulate_match(params, draws) → distribution` | The core granular primitive. Bivariate-Poisson or drive-based simulation returning a full scoreline distribution, from which every derived market can be priced consistently. |
| 21 | `base_rate(reference_class) → frequency` | League and situation baselines — how often a two-goal favourite at home keeps a clean sheet. |
| 22 | `decompose(question) → tree` | Chance creation into conversion into scoreline, rather than jumping straight to an outcome. |
| 23 | `compute` · `run_code` | *Shared.* Fitting the rating model and running the simulation. |
| 24 | `estimate(thesis) → P, σ` | The named call, derived from the simulated distribution rather than asserted separately. |
| 25 | `calibrate(P, history) → P'` | *Shared.* |
| 26 | `sensitivity(thesis, inputs) → ranking` | Usually one absence or one tempo assumption. |
| 27 | `recall(league) → prior` / `remember(note) → ack` | *Shared.* Which beat reporters are reliable is a durable, learnable fact. |

### Research, decision and composition

| | | |
|---|---|---|
| 28 | `refine_query` · `search` · `fetch` · `weigh_source` · `extract_claims` · `verify_claim` | *Shared.* |
| 29 | `counter` · `cost_model` · `breakeven` · `size` | *Shared.* `cost_model` covers book margin and exchange commission. |
| 30 | `cite` · `draft` · `critique` | *Shared.* |

## Runtime additions

| | | |
|---|---|---|
| `SportsFeed` | Stats adapter | Fixtures, rosters, lineups, per-player and per-team data. |
| `OddsFeed` | Book adapter | Prices across books and line history. **Read-only — no bet-placement endpoint.** |
| `Settlement` | History | Final scores and settled markets, readable by `recall`. Agent memory only. |

## Notes

**Lineup timing dominates everything.** Confirmed lineups land about an hour before kickoff and move
prices immediately. Whether questions are asked before or after that announcement changes the topic
completely — before, it rewards research into likely selection; after, it rewards fast modelling. Both
are valid, and they should probably be tracked as separate question types rather than mixed into one
leaderboard.

**Prior work on this repo's authors' side found that results-only models cannot beat sharp lines**, and
that the edge sat in line-shopping and internal inconsistency instead. That finding is why
`check_coherence`, `derive_implied` and `read_book` are first-class here rather than afterthoughts.
