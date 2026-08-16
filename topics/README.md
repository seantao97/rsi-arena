# Topics

A **topic** is a self-contained problem domain. Each one supplies:

1. an **objective** — what a good answer is, stated so a reader can judge two of them side by side
2. a **budget and scoring rule** — the account, the limits, and how the answer is scored when truth arrives
3. an **answer contract** — required contents, checked mechanically before a comparison
4. a **primitive set** — the typed steps an agent may call

A topic never specifies orchestration. Which primitive runs first, how often, whether it loops — that is
what the optimizer discovers. If a topic doc contains a pipeline, it is wrong.

## Catalogue

| Topic | Decision | Settles | Scored by |
|---|---|---|---|
| [Crypto hourly](crypto-hourly.md) | BTC/ETH strike ladder | Every hour | P&L, Brier, calibration |
| [Baseball](baseball.md) | MLB bet or pass | Same night | P&L, ROI, CLV |
| [Index options](index-options.md) | NDX structure, any shape | Daily | P&L, Sharpe, drawdown |
| [ETF allocation](etf-allocation.md) | Long/short across 50 ETFs | Daily | Sharpe, return, drawdown |
| [Weather](weather.md) | Temperature distribution | Daily | CRPS, Brier |
| [Soccer](soccer.md) | Top-5 league or UCL bet | Per matchday | P&L, ROI, CLV |
| [FX](fx.md) | G10 positions | Daily | Sharpe, return, carry attribution |
| [Earnings](earnings.md) | Beat/miss + move | At the print | P&L, hit rate, magnitude error |
| [Macro nowcast](macro-nowcast.md) | Next data print | At release | MAE, direction vs consensus, CRPS |
| [Merger arb](merger-arb.md) | Take the spread or pass | On close or break | P&L, Brier on P(close) |
| [Event markets](event-markets.md) | Any binary contract | At close | P&L, CLV, Brier |
| [Essays](essays.md) | The essay itself | **Never** | Preference only |

## Verifiable and non-verifiable

Eleven of the twelve topics are **verifiable**: the world produces a number, and P&L or a proper scoring
rule computes automatically. One is not.

That split is the point of the roster, and it creates a tension worth stating plainly.

**Every verifiable topic runs two scoreboards.** Preference votes rate the reasoning; realised P&L rates
the decision. Both are published. Where they disagree, P&L is right — and the disagreement is the most
valuable number the arena produces, because it says whether reader preference tracks being correct or
merely tracks sounding correct.

**This has a consequence for the optimizer.** Where P&L exists it is a better training signal than votes,
and the honest thing is to say so. Votes are what make the arena a product; P&L is what makes it
trustworthy. Which one drives harness selection on verifiable topics is an open decision, not a settled
one.

**Essays are the control.** No outcome, no scorer, only preference. If the loop works there it works
anywhere; if it degenerates into longer and more confident writing, nothing is hiding that.

### Modelling versus research

Worth tracking as the roster grows, because it decides what the arena actually measures.

**Modelling topics** — [crypto-hourly](crypto-hourly.md), [index-options](index-options.md),
[fx](fx.md), [etf-allocation](etf-allocation.md), [weather](weather.md). A good closed-form beats
orchestration, so harness design has less room. Cheap to run, fast to settle, weak tests of scaffolding.

**Research topics** — [merger-arb](merger-arb.md), [macro-nowcast](macro-nowcast.md),
[event-markets](event-markets.md), [earnings](earnings.md). Retrieval, source triage and synthesis do
the work, which is where harness quality actually shows. Slower and more expensive per answer.

**Mixed** — [soccer](soccer.md), [baseball](baseball.md). Model plus a research edge on lineups,
officials and conditions.

If the point is measuring harnesses rather than models, weight the roster toward research.

### Where to start

Ordered by settled decisions per week, which is what every arena mechanism needs to calibrate against:

1. **Crypto hourly** — ~24 settlements per asset per day. A week here beats a year of earnings.
2. **Baseball** — fifteen games a night across a dozen markets.
3. **Weather** — daily, authoritative source, decades of free archived model data.

Build one of these first, get rating, pairing, voter weighting and canary catch-rates working against
real volume, then point the machinery at the slower topics.

## What every topic inherits

### The comparison

Two agents answer the same question under the same cost ceiling. Both answers are shown blind, side by
side, identity and formatting signatures stripped, sides randomised. The reader picks one, or a tie,
then taps one reason.

### Shared runtime

Agents call through it and cannot modify or bypass it, which is the only reason the caps and the
provenance rules hold.

| | | |
|---|---|---|
| `R1` | **Clock** | One source of time. Every search, fetch and submission is stamped with it. |
| `R2` | **Governor** | Per-answer caps — 40 searches, 120 fetches, 20 code runs, 25 MB, 8 minutes, $2.00 — plus domain denylist, robots rules, per-host rate limits. Calls past a cap are refused, not queued. |
| `R3` | **Archive** | Immutable copy of every fetched page, keyed by URL and fetch time. |
| `R4` | **Provenance** | Every claim must resolve to an archived document. Invented citations are rejected, not penalised. |
| `R5` | **Budget** | Meters spend in USD and enforces the shared ceiling for both agents. |
| `R6` | **Submission** | Runs the answer-contract check, then hashes and timestamps. |
| `R7` | **Sandbox** | Backs `run_code`. Library allowlist, no network, no filesystem outside a scratch dir, no state between calls. |
| `R8` | **Journal** | Per-agent memory behind `recall` and `remember`. Written every episode, never rewritten by the optimizer. |
| `R9` | **Registry** | The fixed list of data sources a topic may reach. An agent cannot invent a source. |

Trading topics add a `Bank` or `Book` — a simulated account with positions, cash and daily marks — and a
`Settlement` store the agent reads through `recall`. All market feeds are **read-only; no topic exposes
an order-entry endpoint.**

### Shared primitives

Most topics reuse this spine and spend their space on what is domain-specific:

`search` · `fetch` · `weigh_source` · `extract_claims` · `verify_claim` · `compute` · `run_code` ·
`estimate` · `calibrate` · `sensitivity` · `recall` · `remember` · `counter` · `cite` · `draft` ·
`critique`

Trading topics add `cost_model` · `breakeven` · `kelly` · `check_coherence` · `closing_line`.

[Crypto hourly](crypto-hourly.md) deliberately drops `search` and `fetch` — forty minutes of news moves a
settled probability less than a better volatility estimate does.

## Adding a topic

1. **Automatic scoring fails, or exists but is worth measuring against.** If a scorer exists and you trust
   it completely, use it and skip the arena.
2. **Competent judges agree with each other.** Measure this before building. If expert-vs-expert agreement
   is not clearly above chance, there is no objective, only a poll.
3. **Generating is much harder than recognising.** That gap is where harness quality lives.
4. **Settlement is fast enough to learn from.** Weekly beats quarterly by more than the ratio suggests.
