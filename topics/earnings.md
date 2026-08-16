# Earnings

Agents answer questions about a company's upcoming earnings report before it prints: whether the
company beats or misses, and which way the stock moves the next day and by how much.

Running example throughout: **NVDA reports Q3 after the close on Wednesday.**

## Objective

> Given a company with earnings scheduled, produce the memo a competent, skeptical allocator would
> most want to have read **before** the print.

**Maximize** — which memo a reader would rather have had before deciding. A person reads both, blind,
and picks one.

**Asked of the reader** — *"Which of these would you rather have read before this print?"* Winner or
tie, then one tap for why: evidence, reasoning, risk framing, or counter-case.

**Equalized** — same cost ceiling for both agents.

## Answer contract

| | |
|---|---|
| **Position** | Beat, miss or in-line, stated separately for EPS and revenue. |
| **Evidence** | Each factual claim bound to a fetched document and its retrieval time. |
| **Probability** | P(beat) with an interval, and a predicted one-day move — direction and magnitude, with an interval. |
| **Versus implied** | The options-implied move, and whether the memo expects realised to exceed it. A directional call and a volatility call are different trades and must be stated separately. |
| **Counter-case** | The strongest argument against the position taken. |
| **Falsifier** | A pre-committed observation that would make the memo wrong — *"if data-centre revenue guidance lands below $28B, this is wrong."* |
| **Exposure** | Position size, worst-case loss, and cost after spread and fees. |

The **versus implied** line is what separates this topic from a coin flip. Direction into a print is
close to unforecastable; whether the market has *priced the move correctly* is a real question with a
public benchmark sitting right next to it.

## Primitive set

Thirty-one steps. Shared spine plus what is specific to company fundamentals.

### Question

| | | |
|---|---|---|
| 1 | `parse_event(ticker) → spec` | Confirmed or estimated report date, before or after the close, what is reported, and what the company has pre-announced. |
| 2 | `calendar(window) → events` | Peer prints, guidance updates, conferences, and macro releases landing in the same window. A print two days after a competitor's blowout is a different event. |

### Research

| | | |
|---|---|---|
| 3 | `refine_query(question \| results) → query` | *Shared.* |
| 4 | `search(query) → results` | *Shared.* |
| 5 | `fetch(url) → document` | *Shared.* No crawl. |
| 6 | `weigh_source(document) → weight` | *Shared.* |
| 7 | `extract_claims(document) → [claim]` | *Shared.* |
| 8 | `verify_claim(claim) → status` | *Shared.* |

### Fundamentals

| | | |
|---|---|---|
| 9 | `filings(ticker, form, window) → documents` | 10-K, 10-Q, 8-K and their exhibits from the primary source rather than coverage of them. |
| 10 | `transcript(ticker, quarter) → text` | Earnings-call transcripts, including the Q&A, which is where guidance actually gets qualified. |
| 11 | `segment_data(ticker) → table` | Revenue and margin by segment and geography. Company-level beats often hide a segment that missed. |
| 12 | `guidance_history(ticker) → record` | What management guided, what they delivered, and how conservatively they have set the bar. |
| 13 | `alt_data(ticker, series) → data` | Registry-listed alternative series — card spend, app downloads, web traffic, job postings, shipping. |
| 14 | `peer_results(sector, window) → results` | What comparable companies just reported and how their stocks reacted. |

### Estimates

| | | |
|---|---|---|
| 15 | `consensus(ticker, metric) → estimate` | Sell-side consensus for EPS, revenue and key segment lines, with dispersion — the spread across analysts matters as much as the mean. |
| 16 | `revisions(ticker, window) → trend` | How estimates have moved into the print. Direction of revisions is among the better-documented pre-earnings signals. |
| 17 | `surprise_history(ticker) → record` | Historical beat rate, average surprise size, and post-earnings drift. Some companies beat by a penny structurally. |

### Market

| | | |
|---|---|---|
| 18 | `price_history(ticker, window) → series` | The run-up. A stock up 30% into a print needs a bigger beat to rally. |
| 19 | `read_options(ticker, expiry) → chain` | Implied move from the at-the-money straddle, IV term structure, and skew. This is the market's own forecast of the magnitude. |
| 20 | `short_interest(ticker) → data` | Days-to-cover and borrow cost, which shape how violent a beat reaction gets. |
| 21 | `read_market(ticker) → book` | The event contract itself, where one exists. |
| 22 | `external_forecast(question) → [source, P]` | Published probabilities and price targets from elsewhere. |

### Estimation

| | | |
|---|---|---|
| 23 | `base_rate(reference_class) → frequency` | How often companies in this situation — this sector, this run-up, this revision trend — beat. |
| 24 | `decompose(question) → tree` | Revenue by segment, margin, share count, and the path to an EPS number. |
| 25 | `compute(expression) → value` | *Shared.* |
| 26 | `run_code(source, inputs) → outputs` | *Shared.* Building the model, fitting the seasonal pattern, simulating the move distribution. |
| 27 | `estimate(thesis) → P, σ` | P(beat), and a distribution over the one-day move. |
| 28 | `calibrate(P, history) → P'` | *Shared.* |
| 29 | `sensitivity(thesis, inputs) → ranking` | Which line item moves the outcome most — usually one segment or one margin assumption. |
| 30 | `recall(sector) → prior` / `remember(note) → ack` | *Shared.* |

### Decision and composition

| | | |
|---|---|---|
| 31 | `counter` · `cost_model` · `breakeven` · `size` · `cite` · `draft` · `critique` | *Shared.* `cost_model` here covers equity and option spreads, not event-contract fees. |

## Runtime additions

| | | |
|---|---|---|
| `FilingsFeed` | EDGAR adapter | Primary filings and exhibits, with the accepted timestamp rather than the publication timestamp. |
| `EstimatesFeed` | Vendor adapter | Consensus, dispersion and revision history. The single most expensive dependency in this topic. |
| `OptionsFeed` | Market data | Chains, implied volatility, and the implied move. Read-only. |
| `Settlement` | History | Reported actuals and realised next-day moves, readable by `recall`. Agent memory only. |

## Notes

**Two questions are hiding in one topic.** Beat/miss and the one-day move are only loosely coupled — a
company can beat and fall 8%. The answer contract forces both to be stated because a memo that
conflates them is exactly the failure mode a reader should be able to see.

**The settlement horizon is tight**, roughly sixteen hours from close to open, which makes this the
best market topic for iterating on the arena mechanics. Weather is denser still, but earnings has
readers who actually care about the answer.
