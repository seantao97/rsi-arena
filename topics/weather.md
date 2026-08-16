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

## Why this topic matters out of proportion to its subject

Weather settles **daily**, against an authoritative source, with no subjective judgment about the
settleable part, and with free archived model output going back decades. Nothing else on the roster
gives feedback that fast or that clean.

That makes weather the natural instrument for debugging the arena itself. Rating convergence, pairing,
voter weighting, canary catch-rates — all of them need many settled questions to evaluate, and here a
week produces what earnings produces in a quarter. Build it early even if nobody finds it interesting.

## Answer contract

| | |
|---|---|
| **Position** | A stated forecast — a value or a threshold call — not a range of possibilities. |
| **Distribution** | A full predictive distribution over the target, not just a point estimate. For a bracketed question, a probability per bracket summing to 1. |
| **Evidence** | Model runs and observations cited by name and run time. "The GFS says" without a run timestamp is not evidence. |
| **Model disagreement** | Where the guidance disagrees, and which way the memo leans. A forecast that hides an ECMWF/GFS split is hiding the whole problem. |
| **Counter-case** | The scenario that would produce a materially different outcome. |
| **Falsifier** | A pre-committed observation — *"if the 12Z run keeps the front west of the city, this is wrong."* |
| **Confidence** | Stated and justified, tied to ensemble spread rather than to tone. |

## Primitive set

Twenty-nine steps.

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
| 20 | `read_market(ticker) → book` | The event contract, where the question is tied to one. |
| 21 | `related_markets(ticker) → [ticker]` | The rest of the temperature ladder. |
| 22 | `check_coherence(tickers) → violations` | Brackets are mutually exclusive and exhaustive, so their prices must sum to 1.00. Ladder violations here are more common than in any other topic. |
| 23 | `external_forecast(question) → [source, P]` | Commercial and consumer forecasts for the same station. |

### Research, decision and composition

| | | |
|---|---|---|
| 24 | `refine_query` · `search` · `fetch` · `weigh_source` · `extract_claims` · `verify_claim` | *Shared.* Less central here than elsewhere — the good sources are numeric, not textual. |
| 25 | `counter` · `cost_model` · `breakeven` · `size` | *Shared.* Fee drag is smallest in the tails, which is where bracket questions usually sit. |
| 26 | `cite` · `draft` · `critique` | *Shared.* |

## Runtime additions

| | | |
|---|---|---|
| `ModelStore` | NWP archive | Registered model runs and ensemble members, keyed by run time. Archived rather than live, so a question can be replayed exactly. |
| `ObsStore` | Station data | METAR, ASOS and climate reports, with correction history — the original observation *and* the later amendment. |
| `Settlement` | History | Verified outcomes per station, readable by `recall`. Agent memory only. |

## Notes

**Skill is bounded and known.** Beyond about seven days, forecast skill collapses toward climatology,
and no harness will change that. Questions should sit inside the horizon where research actually pays,
or the leaderboard measures nothing but noise tolerance.

**The interesting failure is overconfidence.** With ensemble data available, a memo that states a tight
distribution the members do not support is straightforwardly wrong in a way readers can be shown. That
makes weather a good place to test whether the arena's judges reward calibration or confidence — the
question the whole project is ultimately about.
