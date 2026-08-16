# Event markets (general)

The general case: any binary event contract on Kalshi or Polymarket. Agents take a position, or pass.
Contracts settle to 0 or 1, so P&L is exact.

The domain-specific topics — [soccer](soccer.md), [baseball](baseball.md),
[index options](index-options.md), [ETF](etf-allocation.md), [earnings](earnings.md),
[weather](weather.md) — are this topic with better data. Use this one for everything else: politics,
macro, company events, awards, anything with a rulebook and a close time.

Running example: **"Fed cuts rates at the September meeting."**

## Objective

> For a given unsettled market, take the position with positive expected value at the available price,
> or pass — and explain why.

**Asked of the reader** — *"Which of these would you rather have traded?"* Winner or tie, then one tap
for why: evidence, reasoning, price, or risk.

## Budget and scoring

| | |
|---|---|
| Bank | $50,000 |
| Max stake | 5% of bank per market, 15% per correlated group |
| Decision time | Any time while the market is open. Timestamp recorded. |
| Scored by | Settled P&L, ROI per position, **closing-line value**, and Brier score on the stated probability |

Brier is worth tracking separately from P&L: an agent can profit by trading only mispriced tails while
being badly calibrated, and it can be well calibrated and never find an edge. Those are different
failures and both are worth seeing.

## Answer contract

| | |
|---|---|
| **Position** | Side, contracts, limit price. Or `PASS`. |
| **Probability** | Point estimate and interval — *68%, ±12*. |
| **Edge** | The market price, and the edge in percentage points after fees. |
| **Evidence** | Each factual claim bound to a fetched document and its retrieval time. |
| **Counter-case** | The strongest argument against the position. |
| **Falsifier** | *"If core CPI prints above 3.2%, this is wrong."* |
| **Exposure** | Stake, worst-case loss, breakeven after fees, and cost of capital to settlement. |

## Primitives

### Question

| | |
|---|---|
| `parse_settlement(ticker) → criteria` | Which source decides, measured when, how it rounds, what happens on revision. Most avoidable losses come from answering a slightly different question than the one that settles. |
| `calendar(window) → events` | Scheduled events inside the contract's life — the FOMC meeting, and the two CPI prints before it. |

### Research

| | |
|---|---|
| `refine_query(question \| results) → query` | Question, or what has been learned, into a search query. |
| `search(query) → results` | Ranked results with titles, snippets, URLs. |
| `fetch(url) → document` | One URL to text. Only URLs from `search` or the registry — no crawl. |
| `weigh_source(document) → weight` | Reliability, recency, and syndication: six outlets on one wire story is one source. |
| `extract_claims(document) → [claim]` | Discrete claims — *"core CPI came in at 2.9%"*. |
| `verify_claim(claim) → status` | Confirmed, contradicted or unfound against a second source. |
| `timeseries(series, window) → data` | Numeric series from the registry, not prose to be parsed. |

### Market

| | |
|---|---|
| `read_market(ticker) → book` | Bid, ask, last, depth, volume, open interest. |
| `market_history(ticker, window) → series` | The price path. 30¢ → 68¢ this week and a flat month at 68¢ are different situations. |
| `related_markets(ticker) → [ticker]` | Other brackets in a ladder, the same question at another horizon. |
| `check_coherence(tickers) → violations` | Brackets summing past 1.00, or a September cut priced above a cut-by-December. Inconsistency needs no forecast. |
| `closing_line(ticker) → price` | Final price before settlement, for CLV. |
| `external_forecast(question) → [source, P]` | Polymarket on the same question, Metaculus, futures-implied odds, forecaster surveys. |

### Estimation

| | |
|---|---|
| `base_rate(reference_class) → frequency` | How often the Fed has cut when futures priced it above 60% a month out. |
| `decompose(question) → tree` | P(cut) as P(inflation cools) × P(cut \| cools) plus the other branch. |
| `compute(expression) → value` | Arithmetic without starting the sandbox. |
| `run_code(source, inputs) → outputs` | Python in a sandbox. Monte Carlo over the tree, implied distribution from a ladder, re-deriving a statistic a source asserts. |
| `hypothesize(claims) → [thesis]` | Competing explanations and the evidence separating them. |
| `estimate(thesis) → P, σ` | Probability and interval on the settleable claim. |
| `calibrate(P, history) → P'` | An agent whose 70% calls land at 60% should shade its 70s down. |
| `sensitivity(thesis, inputs) → ranking` | Which input moves the probability most. |
| `recall(market_type) → prior` / `remember(note) → ack` | Cross-market memory, read and write. |

### Decision

| | |
|---|---|
| `cost_model(ticker, side, size) → costs` | Kalshi taker fee is `ceil(0.07·P·(1−P))` per contract capped at $0.035, maker about a quarter of that — at 68¢, 2.0¢. |
| `holding_cost(size, horizon) → cost` | Capital locked to settlement earns nothing. Six weeks at 0% against 4% bills is ~0.5% of stake. |
| `breakeven(price, costs) → threshold` | Buying at 68¢ needs 70% just to clear fees. |
| `kelly(p, price, fraction) → stake` | Fractional Kelly against the bank. |
| `counter(thesis) → case, falsifier` | Strongest opposing argument and a falsifying observation. |
| `cite` · `draft` · `critique` | Binding, assembly, review. |

## Runtime additions

| | |
|---|---|
| `MarketRef` | Settlement rule, source, close time. |
| `MarketFeed` | Backs `read_market`, `market_history`, `related_markets`. Read-only — no order entry. |
| `Bank` | Simulated bankroll and open positions. |
| `Settlement` | Resolved outcomes and settled P&L, readable by `recall`. |

## Notes

`check_coherence` opens a path where the best answers stop being forecasts and become *"these two
markets disagree."* That is plausibly where the real edge is, but it is a different genre of answer than
the objective describes. Watch whether readers reward it.
