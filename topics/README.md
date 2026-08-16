# Topics

A **topic** is a self-contained problem domain for the arena. Each one supplies three things:

1. an **objective** — what a good answer is, stated so a reader can judge two of them side by side
2. an **answer contract** — the required contents of a submission, checked mechanically before it reaches a battle
3. a **primitive set** — the typed steps an agent may call in that domain

A topic never specifies orchestration. Which primitive runs first, how often, and whether it loops is
what the optimizer discovers. If a topic doc contains a pipeline, it is wrong.

Topics are scoped independently because a good weather harness tells you nothing about a good essay
harness — the primitive sets barely overlap, and neither do the leaderboards.

## Catalogue

| Topic | Output | Truth | Notes |
|---|---|---|---|
| [Event markets](event-markets.md) | Decision memo | Settles | The general case. The other market topics specialise it. |
| [Earnings](earnings.md) | Pre-print memo | Settles | Beat/miss plus one-day move, priced against the implied move. |
| [Index options](index-options.md) | Trade plan | Settles | NDX, 0DTE to weekly. Fastest-settling market topic. |
| [ETF allocation](etf-allocation.md) | Target weights | Settles | Weekly rebalance, net of cost. |
| [Sports](sports.md) | Pre-game memo | Settles | Scoreline distributions, not moneyline. |
| [Weather](weather.md) | Forecast memo | Settles | Daily settlement. The instrument for debugging the arena. |
| [Essays](essays.md) | Essay | None | Never settles. The cleanest test of a pure-preference loop. |
| [Poker](poker.md) | Action + analysis | **Verifiable** | Solver gives EV. A test harness, not a leaderboard. |
| [Bridge](bridge.md) | Call/card + analysis | **Part verifiable** | Play is double-dummy checkable; bidding is not. |

## Three kinds of truth

The distinction drives what each topic is *for*.

**Verifiable** — a scorer exists at answer time. Poker, and the play half of bridge. Normally this
disqualifies a topic, since you should just use the scorer. They are here as instruments: because
correctness is computable, they are the only place you can measure whether reader preference actually
tracks being right. Run them, compare preference against the scorer, and you learn something about
every other topic. Keep them off the public leaderboard.

**Settles** — no scorer now, but the world answers later. Every market topic and weather. Outcomes are
recorded and given to agents as memory so they can calibrate, and are never a rating input. This is
where the audit lives: months later, did readers prefer the memos that turned out right?

**None** — essays. Nothing ever resolves. If the loop works here it works, and if it degenerates into
longer and more confident writing, that shows up here first with no settlement data to hide behind.

## What every topic inherits

### The comparison

Two agents answer the same question under the same cost ceiling. Both answers are shown blind, side
by side, with author identity and formatting signatures stripped and sides randomised. The reader
picks one, or a tie, then taps one reason.

**Votes are the only supervision signal.** Where a topic settles, that outcome is recorded and made
available to agents as memory so they can calibrate themselves — it is never a rating input, and no
comparison between agents uses it.

### Shared runtime

Fixed infrastructure behind every topic's primitives. Agents call through it and cannot modify or
bypass it, which is the only reason the caps and the provenance rules hold.

| | | |
|---|---|---|
| `R1` | **Clock** | One source of time. Every search, fetch and submission is stamped with it. |
| `R2` | **Governor** | Per-answer hard caps — 40 searches, 120 fetches, 20 code runs, 25 MB, 8 minutes, $2.00 — plus domain denylist, robots rules and per-host rate limits. Calls past a cap are refused, not queued. |
| `R3` | **Archive** | Immutable copy of every fetched page, keyed by URL and fetch time, so citations survive link rot and runs stay reproducible. |
| `R4` | **Provenance** | Checked at submission: every claim must resolve to an archived document. Invented citations are rejected rather than penalised. |
| `R5` | **Budget** | Meters spend in USD and enforces the shared ceiling for both agents answering a question. |
| `R6` | **Submission** | Runs the answer-contract check, then hashes and timestamps. |
| `R7` | **Sandbox** | Backs `run_code`. Fixed library allowlist, no network, no filesystem outside a scratch directory, no state between calls, CPU and memory ceilings. |
| `R8` | **Journal** | Per-agent memory behind `recall` and `remember`. Written every episode; never rewritten by the optimizer. |
| `R9` | **Registry** | The fixed list of data sources a topic may reach. An agent cannot invent a source. |

Topic docs list only the runtime they add on top of these.

### Shared primitives

Most topics reuse this spine. Topic docs mark them as shared and spend their space on what is
domain-specific.

`refine_query` · `search` · `fetch` · `weigh_source` · `extract_claims` · `verify_claim` ·
`compute` · `run_code` · `recall` · `remember` · `cite` · `draft` · `critique`

## Choosing the next topic

A domain is worth building when all four hold. The first two are what make it an arena at all; the
second two are what make it worth the engineering.

1. **Automatic scoring fails.** If a scorer exists, use it — the arena adds nothing but latency.
2. **Competent judges still agree with each other.** Measure this before building. If expert-vs-expert
   agreement is not clearly above chance, there is no objective, only a poll.
3. **Generating is much harder than recognising.** This gap is where harness quality lives. When it
   inverts — hard to produce *and* hard to evaluate — votes are noise with a leaderboard on top.
4. **Some fragment settles later.** Not required, but it is the only way to check whether preference
   tracked quality rather than persuasion.

Weather is the exception worth building early regardless of interest: it settles daily against an
authoritative source, needs no subjective judgment for the settleable part, and has free archived model
data going back decades. If a mechanism in the arena is suspect, weather is where to test it.
