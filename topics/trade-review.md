# Trade review

Given a trader's own settled trade log, categorise what they did and diagnose what is costing them.

Running example: **240 Kalshi positions over six months — up 4% gross, down 2% net.**

## Scope

Settled positions only, on instruments the registry covers. The review analyses decisions that have
already resolved; it does not recommend positions, and no topic on the roster exposes order entry.

A trade log is personal financial data. Logs are de-identified at ingest, and the uploader chooses
whether the question enters the public arena or stays private with a single voter — themselves. That
choice has a cost, and it is discussed in the notes.

## Objective

> Given a settled trade log, produce the review that would most improve the trader's next hundred
> decisions.

**Asked of the reader** — *"Which of these would change how you trade next month?"* Winner or tie, then
one tap for why: diagnosis, evidence, categorisation, or prescription.

## Scoring

| | |
|---|---|
| Source | Uploaded Kalshi and Polymarket exports, plus synthetic logs generated with a known injected behaviour |
| Decision time | Within the answer budget |
| Scored by | **Rule performance out of sample** — held-out portion of the log, then the trader's subsequent trades — plus catch rate on synthetic flaws, and preference |

The synthetic logs are what make this topic verifiable rather than a poll. A log generated to size up
40% after every loss has a known answer, costs nothing to produce, and scales without waiting for
anyone to upload anything. Real logs supply realism; the synthetic corpus supplies the leaderboard.

P&L is already in the log. Restating it is not the task. Two things are: which behaviour produced it,
and what rule would have prevented it using only what was knowable at the time.

## Answer contract

| | |
|---|---|
| **Diagnosis** | The one behaviour costing the most, stated as a mechanism with its cost in dollars. *"Sizes up 40% after a loss; that alone is −$3,100 of the −$4,400."* |
| **Categorisation** | Every position assigned to a stated scheme, with P&L, hit rate and cost drag per group. The scheme is the reviewer's choice and part of what is judged. |
| **Process versus outcome** | Which winners were lucky and which losers were right. Ranking by P&L is a sort, not a review. |
| **Evidence** | Findings bound to position IDs and to the market state at entry, never to the outcome. |
| **Sample adequacy** | Whether the log supports the claim, with position counts per group. |
| **Prescription** | One rule, mechanical enough to follow without judgment, and what it would have done out of sample. |
| **Not the cause** | One thing the log looks like it explains and does not — the plausible story the evidence rules out. |

## Primitives

### Log

| | |
|---|---|
| `parse_log(upload) → fills` | Normalises an exchange export into typed fills. |
| `validate_log(fills) → issues` | Missing legs, impossible timestamps, positions that never close. Logs are dirtier than they look and every number downstream depends on this. |
| `reconcile(fills) → positions` | Fills into round trips, handling partial fills, scale-ins, rolls and expiries. |
| `parse_settlement(ticker) → criteria` | What each contract actually settled on. |

### Reconstruction

| | |
|---|---|
| `market_state(ticker, time) → snapshot` | The book as it stood at entry, from `BookStore`. |
| `fill_quality(position) → assessment` | Price achieved against what was available at that moment and size. Separates a bad decision from a bad execution. |
| `information_set(ticker, time) → available` | What was public at decision time. **The hindsight guard** — a finding resting on information published after entry is rejected, not penalised. |
| `closing_line(ticker) → price` | Final price before settlement. On a short log, CLV is the cleanest skill proxy available. |

### Categorisation

| | |
|---|---|
| `label_position(position) → tags` | Instrument, horizon, size band, session, entry type, and whether it was a re-entry. |
| `cluster_positions(positions, scheme) → groups` | Groups under a scheme the reviewer defines. |
| `tag_sequence(positions) → patterns` | Patterns visible only in order — size up after a loss, cutting winners early, re-entering the same market within an hour. Per-position analysis cannot see these at all. |

### Measurement

| | |
|---|---|
| `pnl_attribution(groups) → decomposition` | Selection, sizing, timing and cost, separated. |
| `cost_drag(positions) → total` | Fees, spread and slippage against gross. Frequently the entire answer on a retail log. |
| `sizing_profile(positions) → curve` | Size against stated conviction where the log carries one, and size against recent results where it does not. |
| `benchmark(positions, baseline) → comparison` | Against the closing line, against holding to settlement, and against random entries at the same timestamps and sizes. |
| `significance(sample, effect) → power` | Whether the log can separate the claimed effect from variance. |

### Prescription

| | |
|---|---|
| `propose_rule(diagnosis) → rule` | A mechanical rule, applicable without judgment. |
| `holdout(log, split) → sets` | Chronological split. A rule fitted to the whole log and tested on the whole log has measured nothing. |
| `apply_rule(rule, set) → effect` | What the rule would have done, in and out of sample. |

### Shared

`compute` · `run_code` · `recall` · `remember` · `counter` · `cite` · `draft` · `critique`

## Runtime additions

| | |
|---|---|
| `LogStore` | Uploaded logs, normalised and de-identified at ingest. The uploader controls whether a question enters the public arena. |
| `BookStore` | Archived order books, shared with [weather](weather.md) and the market topics. Backs `market_state` and `fill_quality`. |
| `FlawedLogSet` | Synthetic logs with known injected behaviours, for scoring. Not readable by agents. |

## Notes

**The reader is the subject.** Every other topic shows two answers to a disinterested reader. Here the
reader is the person being reviewed — the best-informed judge on the roster and the most biased one. A
flattering review is pleasant and useless. The gap is measurable: show the same pair to the trader and
to disinterested readers, and the difference between the two vote distributions is the sycophancy tax.
Instrument that before trusting the leaderboard.

**Privacy costs votes.** A log kept private has exactly one voter, which is too few for a rating to
converge. A log made public gets rated properly and exposes the trader's positions. Most uploaders will
choose privacy, so the public leaderboard will run mostly on synthetic and consented logs — another
reason the synthetic corpus is load-bearing rather than a nicety.

**Hindsight is the other failure.** Knowing the outcome makes every loss look obvious in retrospect.
`information_set` exists to make that constraint mechanical rather than a matter of the reviewer's
discipline, in the same way `R4 Provenance` handles invented citations.

**Most logs are too small to say anything.** Two hundred positions cannot separate a 3% edge from
variance. The honest review often has to say so, and an honest review of that kind is unlikely to beat
a confident one on votes. That makes this the sharpest test on the roster of whether preference tracks
correctness — and the synthetic logs exist so the question has an answer that does not depend on the
vote.
