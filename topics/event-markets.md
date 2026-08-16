# Event markets

Agents answer questions about event-contract markets that have not yet settled — Kalshi, Polymarket —
by writing a decision memo. This is the general market topic; [earnings](earnings.md),
[sports](sports.md) and [weather](weather.md) are specialisations that swap in domain data sources.

Running example throughout: the market **"Fed cuts rates at the September meeting."**

## Objective

> Given a live, unsettled market, produce the decision memo a competent, skeptical allocator would
> most want to have read **before** taking a position.

**Maximize** — which memo a reader would rather have had before deciding. A person reads both, blind,
and picks one.

**Asked of the reader** — *"Which of these would you rather have read before putting money on this?"*
Winner or tie, then one tap for why: evidence, reasoning, risk framing, or counter-case.

**Equalized** — both agents run under the same cost ceiling, so tool volume cannot buy a win. Cost is
reported beside the rating, never folded into it.

## Answer contract

Each item is here because a reader cannot fairly compare two memos without it. A submission missing
any of the six is rejected before it reaches a comparison; that is a format check, not a quality
judgment.

| | |
|---|---|
| **Position** | A stated answer to the question, not a survey of views. |
| **Evidence** | Each factual claim bound to a fetched document and its retrieval time. |
| **Probability** | A point estimate and interval on the central claim — *68%, ±12*. |
| **Counter-case** | The strongest argument against the position taken. |
| **Falsifier** | At least one pre-committed observation that would make the memo wrong — *"core CPI above 3.2%"*. |
| **Exposure** | Position size, worst-case loss, the breakeven after fees, and the cost of being right one meeting early. |

## Primitive set

Thirty-two steps, any order, any number of times, any subset. Nothing here fixes a pipeline.

### Question

| | | |
|---|---|---|
| 1 | `parse_settlement(ticker) → criteria` | Extracts the exact settlement conditions from the rule text: which source decides, measured when, how it rounds, what happens on revision. Most avoidable losses come from a memo that answered a slightly different question than the one that settles. |
| 2 | `calendar(window) → events` | Scheduled events bearing on the question — the September FOMC meeting, and the two CPI prints landing before it. |

### Research

| | | |
|---|---|---|
| 3 | `refine_query(question \| results) → query` | Turns the question, or what has been learned so far, into a search query. |
| 4 | `search(query) → results` | Ranked results with titles, snippets and URLs. |
| 5 | `fetch(url) → document` | Retrieves one URL and extracts its text. Accepts only URLs from `search` or the registry — links inside a fetched page are not followable, so there is no crawl. |
| 6 | `weigh_source(document) → weight` | Reliability and recency, and syndication detection: six outlets carrying one wire story resolve to one source. |
| 7 | `extract_claims(document) → [claim]` | Discrete factual claims — *"core CPI came in at 2.9%"*. |
| 8 | `verify_claim(claim) → status` | Checks a claim against an independent second source; confirmed, contradicted or unfound. Distinct from `weigh_source`, which rates the outlet rather than the fact. |
| 9 | `timeseries(series, window) → data` | Numeric series from the registry rather than page text. Returns values, not prose to be parsed. |

### Market

| | | |
|---|---|---|
| 10 | `read_market(ticker) → book` | Bid, ask, last, depth, volume, open interest. A memo that does not know the market is at 68¢ is arguing into the void. |
| 11 | `market_history(ticker, window) → series` | The price path. 30¢ → 68¢ this week and a flat month at 68¢ are different situations. |
| 12 | `related_markets(ticker) → [ticker]` | Siblings on the same underlying — other brackets in a ladder, the same question at a different horizon. |
| 13 | `check_coherence(tickers) → violations` | Whether related markets price consistently: brackets summing past 1.00, or a September cut above a cut-by-December. Inconsistency is a finding that needs no forecast. |
| 14 | `external_forecast(question) → [source, P]` | Published probabilities from elsewhere — Polymarket, Metaculus, futures-implied odds, forecaster surveys. A memo that ignores where everyone else has it is incomplete whether it agrees or not. |

### Estimation

| | | |
|---|---|---|
| 15 | `recall(market_type) → prior` | The agent's own history on similar markets: which sources proved reliable, how its past estimates compared to how those markets settled. |
| 16 | `remember(note) → ack` | The write side of `recall`. Without it an agent cannot accumulate anything it was not handed. |
| 17 | `base_rate(reference_class) → frequency` | The outside view. How often the Fed has cut when futures priced it above 60% a month out. |
| 18 | `decompose(question) → tree` | Sub-events with conditional probabilities — P(cut) as P(inflation cools) × P(cut \| cools) plus the other branch. |
| 19 | `compute(expression) → value` | Single-expression arithmetic without starting the sandbox. |
| 20 | `run_code(source, inputs) → outputs` | Python in a sandbox. Monte Carlo over a decomposed tree, an implied distribution from a bracket ladder, or re-deriving a statistic a source asserts. |
| 21 | `hypothesize(claims) → [thesis]` | Competing explanations — cut, hold, cut with hawkish guidance — and the evidence separating them. |
| 22 | `estimate(thesis) → P, σ` | A probability and interval on the central claim. |
| 23 | `calibrate(P, history) → P'` | Adjusts a raw estimate using the agent's own record. An agent whose 70% calls land at 60% should shade its 70s down. |
| 24 | `sensitivity(thesis, inputs) → ranking` | Which input moves the probability most. Usually the most useful paragraph in the memo. |

### Decision

| | | |
|---|---|---|
| 25 | `counter(thesis) → case, falsifier` | The strongest opposing argument and at least one falsifying observation. |
| 26 | `cost_model(ticker, side, size) → costs` | Fees, spread, slippage. Kalshi's taker fee is `ceil(0.07·P·(1−P))` per contract capped at $0.035, maker roughly a quarter of that — at 68¢, 2.0¢ per contract. |
| 27 | `holding_cost(size, horizon) → cost` | Capital locked until settlement earns nothing. Six weeks at 0% while T-bills pay 4% is about 0.5% of stake, enough to erase the edge on a market priced near fair. |
| 28 | `breakeven(price, costs) → threshold` | Buying at 68¢ needs the event to be 70% likely just to clear fees. |
| 29 | `size(P, counter, costs) → exposure` | Position size, worst-case loss, and the cost of being right one meeting early. |

### Composition

| | | |
|---|---|---|
| 30 | `cite(claim) → binding` | Binds a claim to the archived document and fetch time it came from. |
| 31 | `draft(parts) → memo` | Assembles against the contract and controls length. |
| 32 | `critique(memo) → notes` | Reviews for gaps and internal contradictions. Does not rewrite — the agent decides whether to call `draft` again. |

## Runtime additions

| | | |
|---|---|---|
| `MarketRef` | Market spec | Settlement rule, settlement source and close time. |
| `MarketFeed` | Exchange adapter | Backs `read_market`, `market_history`, `related_markets`. **Read-only — no order-placement endpoint is exposed.** Agents write memos; they do not trade. |
| `Settlement` | History | How past markets resolved, readable by `recall`. Agent memory only — not a rating input, and no comparison between agents uses it. |

## Notes

`check_coherence` and `related_markets` open a path where the strongest memos stop being forecasts and
become *"these two markets disagree with each other."* That is plausibly where real edge lives, but it
is a different genre of memo than the objective describes, and readers may not reward it. Worth
watching once votes come in.
