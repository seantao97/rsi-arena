# Scoring a Kalshi harness

Two questions, both answered from public data by replay:

| eval | asks |
| --- | --- |
| `horizon_window` | did it beat no change, five minutes out |
| `settlement_outcome` | did it beat the market on how the match ended |

Five minutes after any past instant the price it predicted is already in
Kalshi's candlestick history; after the whistle the result is on the settled
contract. So a harness is scored by putting it back at that instant with tools
frozen there and reading what actually happened — no data collected, no state on
disk, no scheduled job, no key spent waiting for football.

## What can be replayed

A harness can be replayed only if **every** tool it uses can be frozen. The book,
the price path and the tape can: they are history, and history stops where you
tell it to. Live game state and web research cannot — a story filed after the
instant would be answering with the future.

Binding fails loudly on a tool the frozen box does not have, which is the check
that keeps a replay honest. It is also the constraint that shapes what a
rewritten harness may reach for.

```python
from datetime import datetime, timezone
from topics.kalshi.eval import HorizonWindow

ev = HorizonWindow("KXEPLGAME-26AUG23NEWLFC-NEW",
                   datetime(2026, 8, 23, 15, 30, tzinfo=timezone.utc))
out = await ev.run()
out.score      # 0.5 + skill/2
out.comments   # predicted 0.245, market printed 0.245; no-change missed by ...
```

Or over many windows at once:

```bash
python -m topics.kalshi.eval.run --help
```

## What is in here

Same convention as `tools/`: **no underscore is public, an underscore is
machinery**, and `REGISTRY` is the definition of what an eval is. `run` is
public without being a class because it is invoked with `python -m`.

| | |
| --- | --- |
| `horizon_window.py` | the fast question |
| `settlement_outcome.py` | the slow one |
| `run.py` | the benchmark — one harness over many windows |
| `_load.py` | JSON config to `Agent`, and the short labels the CLI takes |
| `_replay.py` | tools frozen at a past instant, so a replay cannot see ahead |
| `_scorer.py` | the window arithmetic, and the quote a prediction implies |

## The benchmark is no change

Predicting the price stays put is free and nearly always nearly right, so
absolute error rewards it. The score is *skill against no change*: the fraction
of the benchmark's error the forecast removed. Zero means the forecast was worth
exactly as much as saying nothing, and that is where a harness that copies the
current mid lands — which, measured over 586 live windows, is where the first
one did.

Reported as `0.5 + skill/2` so it sits in [0, 1] with half a point for matching
the benchmark. The raw number is in `metadata["skill"]`, unsquashed.

**A market that did not move scores 0.5 however right or wrong the forecast
was.** No change has zero error there, so skill is undefined. Those windows carry
`metadata["unmeasurable"]` so a caller pooling results can drop them rather than
average them in as ties — on a real fixture, three windows in four were
unmeasurable.

## The model predicts, the code decides

The agent returns a *change* and a quote width, never a price level. Two failure
modes measured over 144 live forecasts both came from asking for a level:

- **63% of forecasts returned the current mid exactly.** Copying the visible
  number is the path of least resistance when a level is what is wanted, and it
  scores zero by construction. Asked for a change, doing nothing costs the model
  a deliberate `0`.
- **Both trades that run produced came from misreading the book.** One explained
  a price "already fading back to 0.105" while the market was at 0.295. Because
  the anchor was the model's own reading, a misreading manufactured an edge out
  of nothing — and the further off it was, the larger the fake edge, so the fee
  threshold selected for exactly those. A change is applied by code to the true
  mid, so the anchor can no longer be wrong.

`_scorer.quote_from` is where that anchoring happens.

## Costs are the size of the signal

The taker fee is `ceil(0.07 · P · (1−P) · 100)/100`, peaking at `P = 0.50`. A
round trip is two to three cents — **the same size as the predictions**. A
harness that looks profitable before fees usually is not after them, and the
first one was not: over 211 decisions the best edge after fees was 0.0000.

## What used to be here

A supervisor collecting forecasts overnight, reports and plots over the feed it
wrote, a preflight check and a scheduled GitHub workflow — about two thousand
lines, and three settlement-probability harnesses to feed it.

Removed. The settlement question it existed to answer is answered here by
replay, in a second, because the match it is about has already been played. The
harnesses went with it: they researched the news and read live game state, so
they could not be replayed honestly at all. Three things learned by
running it, worth writing down before they are lost with the code:

- **Scheduled runs fired hours late**, consistently rather than randomly —
  19:09Z from an 18:35Z cron, two days running. Uncorrected that pushed every
  run past the European fixtures into a window where almost nothing is listed.
- **A private repository's Actions allowance is spent in four or five nights**
  by a 285-minute run. Five consecutive scheduled runs once failed in three
  seconds each at the end of a month and recovered by themselves on the first.
  Fifty-seven fixtures went uncollected before anyone noticed.
- **A failing scheduled job tells nobody.** That is what turned a fixable
  problem into a lost weekend.

Both questions are recoverable from a recorded feed if they are ever wanted. The
replay path needs none of it.
