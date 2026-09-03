# Running the Kalshi agent

Two ways, and they answer different questions.

**Locally**, when you want to watch it work — the log streams, the state
directory is readable while it runs, and stopping it is a Ctrl-C.

**Scheduled**, when you want data from an evening you are not at a desk for.
That needs somewhere to keep an API key, which is why it does not live in this
repository: this one is public.

## Before starting: is there anything to trade?

```bash
python -m topics.kalshi.eval.slate --league EPL,LALIGA,SERIEA,MLS
```

```
league       fixtures  live  tradeable   kick-offs
LALIGA              1     1          1   19:00
EPL                 1     1          0   19:00
MLS                 2     0          0   20:30, 23:00

5 fixtures on the feed, 3 of them under way, 1 with a market to trade.
Worth collecting now.
```

Read `tradeable`, not `fixtures`. They differ constantly: the exchange lists
what it chooses to, and the fixture feed answers with the *next round* when a
league has nothing on today — so seven fixtures across five leagues can mean
one match tonight and four in three days.

An afternoon has been lost to reading that column wrong. It is the whole reason
this command exists.

## Locally

```bash
export OPENROUTER_API_KEY=...

python -m topics.kalshi.eval.supervisor \
    --league EPL,LALIGA,SERIEA,MLS,ARGENTINA,BRASIL \
    --mode horizon --discover \
    --max-contracts 12 --max-per-game 3 \
    --poll 150 --budget 20.00 \
    --state-dir ~/.kalshi-tonight
```

| flag | |
|---|---|
| `--discover` | rescan the leagues, adopt live markets, release them at settlement, refill the slot. Without it you must name tickers |
| `--max-per-game` | slots one fixture may hold. Three is deliberate: sixteen contracts on one match are sixteen correlated observations, and one busy match will otherwise take every slot a later kick-off needs |
| `--poll` | seconds between forecasts on each contract |
| `--budget` | total model spend, enforced across restarts, so a crash loop cannot spend it twice |

Discovery costs nothing while it waits, so starting an hour before kick-off is
free.

### Scoring it

```bash
python -m topics.kalshi.eval.verify --mode horizon \
    --feed ~/.kalshi-tonight/forecasts.jsonl --plots

# several evenings pooled, with what each contributed
python -m topics.kalshi.eval.verify --mode horizon \
    --feed "~/.kalshi-mon/forecasts.jsonl,~/.kalshi-tue/forecasts.jsonl"
```

One evening is not enough to tell a real number from a lucky one — every run so
far has swung tens of percent before settling.

## Scheduled

`workflow.yml` is a GitHub Actions workflow. It wants `OPENROUTER_API_KEY` as
a repository secret, and it checks this repository out at `main` and runs the
agent from it, so the scheduled job never drifts from what is on the branch and
holds no code of its own. As written it lives in a separate repository; see
below for why, and for when it should not.

A separate repository is not the only option, and the reason is narrower than
it looks. What cannot go near a public repository is a key *in the tree* — a
literal in a file, a default in a workflow. A repository **secret** is a
different thing: encrypted, redacted from logs, and withheld from fork pull
requests, so it is as safe in a public repository as a private one.

So the fork in the road is about who owns the repository, not about secrecy:

- **Here, in the arena repository.** Standard runners are free for public
  repositories with no minute allowance to run out, which removes the failure
  described below entirely. Needs Sean to set `OPENROUTER_API_KEY` on his own
  repository — only an admin can — and the run logs become world-readable.
- **A separate private repository**, as set up here: anyone can stand it up
  without waiting on an admin, and the logs stay private. Pays for it in
  Actions minutes.

The workflow checks this repository out either way. Running it from inside the
arena would drop that checkout step, since it would already be there.

### Three things learned by running it

**Scheduled runs fire late — hours late.** Observed at 19:09Z and 19:10Z on
consecutive days from an 18:35Z cron: a consistent offset rather than jitter.
Uncorrected it pushes every run past the European fixtures into a window where
almost nothing is listed. Set each cron ahead by the delay you measure, and
write down why, or the next person reads `07:55` as a typo.

**Private repositories have an Actions minute allowance**, and a 285-minute run
uses it up in four or five nights. Five consecutive scheduled runs failed in
three seconds each with no step executed, at the end of a month, and recovered
by themselves on the first — which is what that failure looks like. Fifty-seven
fixtures went uncollected before anyone noticed.

**A failing scheduled job tells nobody.** That is what turned a fixable problem
into a lost weekend. Whatever you run this on, arrange for a failure to reach a
person.

### Collecting nothing is sometimes correct

`preflight.py` checks both feeds — the exchange *and* the fixture feed — because
losing either produces the same silence: an empty forecasts file that looks
exactly like a night with no football. It counts live fixtures that have a
market and passes that count on, so the guard afterwards can tell the two apart:

- nothing live and nothing collected — a quiet night, exit clean
- markets available and nothing collected — discovery is broken, fail loudly

Without that distinction the guard fired on two consecutive quiet weeknights,
which is how a red build stops meaning anything.

Run it by hand before a long session:

```bash
python topics/kalshi/eval/preflight.py "EPL,LALIGA,SERIEA"
```

## What lands where

```
<state-dir>/forecasts.jsonl   one line per forecast: book, prediction,
                              decision, reasoning, and what was booked
<state-dir>/state.json        positions, budget spent, survives restarts
<state-dir>/plots/            written by verify --plots
```

`forecasts.jsonl` is the record. Every number in any report is recomputed from
it rather than taken from a run's own summary — a report printed inside a
scheduled container is only as good as the code that container checked out, and
one of them reported a return of -280%, which is not a possible number.
