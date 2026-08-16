# Weather

Agents answer questions about weather at a specific station and horizon — the daily high, whether it
rains, whether a threshold is crossed — by writing a short forecast memo.

Running example throughout: **"NYC Central Park high temperature on Thursday."**

## Objective

> Given a station and a horizon, produce the forecast memo a competent, skeptical reader would most
> want to have read **before** committing to a decision that depends on the weather.

**Maximize** — which memo a reader would rather have had before deciding.

**Asked of the reader** — *"Which of these would you rather have read before deciding?"* Winner or tie,
then one tap for why: evidence, reasoning, risk framing, or counter-case.

**Equalized** — same cost ceiling for both agents.

## Scoring

| | |
|---|---|
| Stations | A fixed set of 20 US stations with Kalshi temperature ladders |
| Horizon | Same-day and next-day |
| Decision time | Any time before the market closes. Timestamp recorded. |
| Scored by | **CRPS** on the stated distribution, Brier per bracket, and settled P&L where a contract is traded |

CRPS is the right primary metric because the contract requires a full distribution, and it penalises a
confident wrong forecast far harder than a wide honest one.

## Why this topic matters out of proportion to its subject

Weather settles **daily**, against an authoritative source, with no subjective judgment about the
settleable part, and with free archived model output going back decades. Nothing else on the roster
gives feedback that fast or that clean.

That makes weather the natural instrument for debugging the arena itself. Rating convergence, pairing,
voter weighting, canary catch-rates — all of them need many settled questions to evaluate, and here a
week produces what earnings produces in a quarter. Build it early even if nobody finds it interesting.

The information calendar is the other thing weather has that nothing else does: it is fully
deterministic. Model runs land at 00Z, 06Z, 12Z and 18Z, on a schedule published in advance, every day.
Where other topics get scheduled news sparsely — a CPI print, an earnings date — weather gets four
information events a day in perpetuity. That is what makes it possible to ask, precisely and at volume,
whether a market reacted correctly to news.

## Answer contract

| | |
|---|---|
| **Position** | A stated forecast — a value or a threshold call — not a range of possibilities. |
| **Distribution** | A full predictive distribution over the target, not just a point estimate. For a bracketed question, a probability per bracket summing to 1. |
| **Evidence** | Model runs and observations cited by name and run time. "The GFS says" without a run timestamp is not evidence. |
| **Model disagreement** | Where the guidance disagrees, and which way the memo leans. A forecast that hides an ECMWF/GFS split is hiding the whole problem. |
| **Versus market** | The market-implied distribution from the bracket ladder, and where the memo's distribution differs from it. A memo that matches the ladder everywhere is a summary of the market, not a forecast. |
| **Counter-case** | The scenario that would produce a materially different outcome. |
| **Falsifier** | A pre-committed observation — *"if the 12Z run keeps the front west of the city, this is wrong."* |
| **Confidence** | Stated and justified, tied to ensemble spread rather than to tone. |

The **versus market** line does the same job here that *versus implied* does in
[earnings](earnings.md): it forces the memo to say something the market has not already said. Weather
is the better case for it, because a bracket ladder inverts into a whole distribution rather than a
single implied number, and the contract already requires the memo to produce one on the same target.
The two are directly comparable. Where a question is not tied to a listed market, the line does not
apply and is not required.

## Primitive set

Thirty-two steps.

### Question

| | | |
|---|---|---|
| 1 | `parse_settlement(question) → criteria` | Which station, which instrument, which reporting product, what window, how it rounds, and what happens on a late correction. NWS climate reports and raw METAR disagree often enough to decide questions on their own. |
| 2 | `calendar(window) → obs_times` | Observation and model run schedule — when the next 12Z run lands relative to the deadline. |

### Guidance

| | | |
|---|---|---|
| 3 | `model_run(model, run_time, variable, location) → forecast` | Deterministic output from a named run — GFS, ECMWF, HRRR, NAM, NBM. Run time is part of the identity; a stale run is a different forecast. |
| 4 | `ensemble(model, variable, location) → members` | Individual ensemble members. The spread *is* the uncertainty estimate, and it is the only honest way to produce the distribution the contract requires. |
| 5 | `forecast_official(office, location) → text` | The NWS point forecast and, more importantly, the Area Forecast Discussion — a working meteorologist explaining what they believe and what they are unsure about. The highest-value text in the topic. |
| 6 | `observations(station, window) → data` | METAR and ASOS actuals, including what has already happened today. |
| 7 | `climatology(station, date) → normals` | Normals, records and the distribution for this calendar date. |
| 8 | `radar_satellite(region, time) → imagery_summary` | Current features for nowcasting at short horizons. |

### Analysis

| | | |
|---|---|---|
| 9 | `bias_correct(model, station, history) → adjustment` | A model's standing error at this station. Raw model output at a specific gauge is systematically wrong in a learnable direction, and correcting it is most of the skill in short-range forecasting. |
| 10 | `ensemble_stats(members) → P, spread` | Converts members into a calibrated distribution. |
| 11 | `blend(models, weights) → forecast` | Weighted consensus across guidance, which beats any single model on average. |
| 12 | `analog(pattern, history) → cases` | Historical days with a similar synoptic pattern and what actually happened. |
| 13 | `base_rate(reference_class) → frequency` | Climatological baseline for the question as posed. |
| 14 | `decompose(question) → tree` | Cloud cover into insolation into daytime max, rather than jumping to a number. |
| 15 | `compute` · `run_code` | *Shared.* Fitting bias corrections, deriving distributions from members. |
| 16 | `estimate(thesis) → P, σ` | The stated forecast and its distribution. |
| 17 | `calibrate(P, history) → P'` | *Shared.* Weather is the one topic where an agent accumulates enough settled forecasts for this to bite quickly. |
| 18 | `sensitivity(thesis, inputs) → ranking` | Which input the forecast hinges on — usually frontal timing or cloud cover. |
| 19 | `recall(station) → prior` / `remember(note) → ack` | *Shared.* Station-specific model bias is exactly the kind of thing worth remembering. |

### Market

| | | |
|---|---|---|
| 20 | `read_market(ticker) → book` | The event contract, where the question is tied to one. Current bid, ask, last, depth and volume for a single bracket. |
| 21 | `related_markets(ticker) → [ticker]` | The rest of the temperature ladder. |
| 22 | `check_coherence(tickers) → violations` | Brackets are mutually exclusive and exhaustive, so their prices must sum to 1.00. Ladder violations here are more common than in any other topic. |
| 23 | `implied_distribution(ladder) → density` | Inverts the whole ladder into a de-vigged density over the target. Every other topic gets a single implied number out of its market; a bracket ladder gives back a distribution, on the same target and in the same units as the one the contract already asks the memo to produce. This is what `versus market` rests on. |
| 24 | `external_forecast(question) → [source, P]` | Commercial and consumer forecasts for the same station. |

### Market behaviour

How the ladder got to its current price, rather than what it currently says. All five read from
archived snapshots, never from a live book, so a question replays identically.

| | | |
|---|---|---|
| 25 | `price_path(ticker, window) → series` | Archived bid, ask, last and volume through time, at snapshot resolution rather than daily close. |
| 26 | `run_impact(ticker, run_time) → move` | Price change attributable to a named model run, by joining `price_path` against `ModelStore` release times. Weather is the only topic whose information calendar is published in advance, which is the only reason this attribution is well posed. |
| 27 | `flow(ticker, window) → data` | Volume, open interest change, and trade direction where the print discloses it. Separates a price that moved on conviction from one that drifted on a thin book. |
| 28 | `spread_history(ticker, window) → series` | Spread and depth through time. Tail brackets are thin and wide, and an edge that disappears into the spread was never an edge. |
| 29 | `convergence(ticker, date) → profile` | How brackets collapse toward 0 or 1 as observations land and the day's high gets locked in. By late afternoon the question is often no longer a forecast, and a memo that still treats it as one is answering the wrong question. |

The pairing worth building for is `run_impact` with `ensemble`. The characteristic failure of a
weather market is overreacting to a single deterministic run when the ensemble barely moved — which is
the market's version of the failure this topic already exists to catch. A memo that can show the 12Z
GFS moved the ladder nine cents while the spread across members did not justify it is making exactly
the argument the objective rewards.

### Research, decision and composition

| | | |
|---|---|---|
| 30 | `refine_query` · `search` · `fetch` · `weigh_source` · `extract_claims` · `verify_claim` | *Shared.* Less central here than elsewhere — the good sources are numeric, not textual. |
| 31 | `counter` · `cost_model` · `breakeven` · `size` | *Shared.* Fee drag is smallest in the tails, which is where bracket questions usually sit. |
| 32 | `cite` · `draft` · `critique` | *Shared.* |

## Runtime additions

| | | |
|---|---|---|
| `ModelStore` | NWP archive | Registered model runs and ensemble members, keyed by run time. Archived rather than live, so a question can be replayed exactly. |
| `ObsStore` | Station data | METAR, ASOS and climate reports, with correction history — the original observation *and* the later amendment. |
| `BookStore` | Order book archive | Polled snapshots of every tracked ladder — bid, ask, depth, volume, open interest — keyed by capture time, plus trade prints. Backs the market-behaviour primitives. **Read-only. No order-placement endpoint is exposed; agents write memos, they do not trade.** |
| `Settlement` | History | Verified outcomes per station, readable by `recall`. Agent memory only. |

## Notes

**Skill is bounded and known.** Beyond about seven days, forecast skill collapses toward climatology,
and no harness will change that. Questions should sit inside the horizon where research actually pays,
or the leaderboard measures nothing but noise tolerance.

**The interesting failure is overconfidence.** With ensemble data available, a memo that states a tight
distribution the members do not support is straightforwardly wrong in a way readers can be shown. That
makes weather a good place to test whether the arena's judges reward calibration or confidence — the
question the whole project is ultimately about.

**The book archive is a prerequisite, not a feature.** An exchange serves depth as it stands now;
it does not serve what the book looked like last Tuesday. Trades and candles can be pulled
retroactively, but depth and spread exist only if something was recording them. So `BookStore` has to
be polling before the first question is asked, and densely enough around run releases that
`run_impact` has something to attribute against. This is the one part of the topic with a hard lead
time — everything else can be built in any order, this cannot be backfilled.

**Genre drift is the risk to watch.** [Event markets](event-markets.md) already notes that
coherence-and-disagreement memos are a different genre than the objective describes, and that readers
may not reward them. Market behaviour amplifies that here: a memo that is mostly flow commentary has
stopped being a forecast memo whatever the contract says. `versus market` is deliberately narrow — it
asks where the memo's distribution departs from the ladder, not for an account of how the ladder
traded. If votes show readers rewarding microstructure over forecasting, that is the signal to split
it into a topic of its own rather than to widen this one.
