# Backend

FastAPI on **:3600**. Runs the sample agents, streams their traces to the web app on :8050,
and scores them. It is a thin layer: the agents live in [`examples/`](../examples), the runtime
in [`rsi_arena/`](../rsi_arena), and this only exposes them over HTTP.

```bash
pip install -e ".[server]"
export OPENROUTER_API_KEY=sk-or-...
python -m server                # --reload while editing, --port to move it
```

## Routes

### The arena

| | |
|---|---|
| `GET /api/health` | Which keys the process actually has |
| `GET /api/models` | OpenRouter's live model list, structured-output-capable first |
| `POST /api/run` | One agent. **SSE.** |
| `POST /api/battle` | Two agents concurrently, blind by default. **SSE.** |
| `POST /api/vote` | Record a vote; returns the reveal and the running tally |
| `GET /api/leaderboard` | Win/loss/tie counts per agent |

### Agents, callable directly

| | |
|---|---|
| `GET /api/agents` | Catalogue: plan outline, tools, context, and which keys each agent needs |
| `GET /api/agents/{id}` | One agent, same detail |
| `POST /api/agents/{id}/run` | Run it. JSON in, JSON out — no streaming |
| `POST /api/agents/{id}/answer` | Answer now from the state you hand it. One model call, no plan |

### Evals

| | |
|---|---|
| `GET /api/scorers` | The scorers a spec may name, and what each takes |
| `POST /api/evals` | Run one eval: an agent, a prompt, a scorer |
| `POST /api/evals/suite` | Every case against every agent, concurrently |
| `GET /api/evals` | Stored results, newest first. `agent`, `name`, `limit`, `offset` |
| `GET /api/evals/leaderboard` | Mean score and pass rate per agent |
| `GET /api/evals/suites` · `GET /api/evals/suites/{id}` | Stored suite runs |
| `GET /api/evals/{id}` · `DELETE /api/evals/{id}` | One result |

The two streaming routes are POST — a run needs a body — so the client uses `fetch` and reads
the response rather than `EventSource`, which is GET-only. The wire format is ordinary SSE.

```bash
# streamed, for a UI
curl -N -X POST localhost:3600/api/run \
  -H 'content-type: application/json' \
  -d '{"agent":"fermi","question":"How many piano tuners are in Chicago?","max_usd":0.25}'

# plain, for everything else
curl -X POST localhost:3600/api/agents/fermi/run \
  -H 'content-type: application/json' \
  -d '{"question":"How many piano tuners are in Chicago?","max_usd":0.25}'
```

## Running an agent without the stream

`POST /api/agents/{id}/run` takes the same knobs as the SSE route and returns one object:

```json
{
  "agent_id": "fermi",
  "ok": true,
  "error": null,
  "error_kind": null,
  "bailed_out": false,
  "output": "About 130 piano tuners…",
  "text": "About 130 piano tuners…",
  "summary": { "total_usd": 0.0184, "calls": 2, "by_kind": { "llm": 0.0184 } },
  "state": { "decomposition": {}, "value": 128.6 }
}
```

`include_trace: true` adds the full span tree, which is large and therefore off by default.

**A failed run is a 200 with `ok: false`, not a 500.** The run happened, it cost money, and its
partial trace is evidence — throwing that away because the agent gave up would lose exactly the
data the arena is collecting. Only an unrunnable *request* is an error status: an unknown agent
is 404, a missing key is 400 naming the variable, an out-of-range ceiling is 422.

## Max spend

Every route that runs an agent accepts `max_spend_mode`. Off (the default) a run that hits its
ceiling stops with nothing. On, it opens a small reserve, spends it on one call that turns
whatever it gathered into an answer, and reports:

```json
{ "ok": false, "error_kind": "max_spend", "bailed_out": true, "text": "Best available…" }
```

Still `ok: false` — a cut-off answer is never quietly counted as a clean one — but with an
answer attached. `bailout_reserve_usd` sets what that call may spend on top of `max_usd`;
the default is 5% of it, floor $0.02. `summary.reserve_used_usd` reports what it actually cost,
which can exceed the reserve, because a model call is only priced once it has been made.

## Evals

A scorer cannot be sent over HTTP as a function, so it arrives as data — a name, a
`{"type": ...}` spec, or a list of either, scored as a conjunction:

```bash
curl -X POST localhost:3600/api/evals \
  -H 'content-type: application/json' \
  -d '{
    "agent": "pipeline",
    "prompt": "Did the ECB cut rates in July 2026?",
    "scorer": [
      "non_empty",
      {"type": "regex", "pattern": "https?://"},
      {"type": "llm_judge", "rubric": "Every factual claim carries the URL it came from."}
    ],
    "max_usd": 0.50
  }'
```

`GET /api/scorers` lists what is available and what each one takes. Registering a scorer in
Python with `rsi_arena.evals.register_scorer` makes it selectable here immediately — no route
changes.

A suite runs the cross product, capped at `MAX_SUITE_EVALS` (60) so one request cannot start an
unbounded amount of spending:

```bash
curl -X POST localhost:3600/api/evals/suite \
  -H 'content-type: application/json' \
  -d '{
    "agents": ["pipeline", "freeform"],
    "cases": [{"prompt": "Did the ECB cut rates in July 2026?", "scorer": "non_empty"}],
    "name": "nightly"
  }'
```

Results are stored in the process (`InMemoryEvalStore`) and read back through `GET /api/evals`.
They die with the backend; [`rsi_arena/evals/store.py`](../rsi_arena/evals/store.py) is the seam
a database arrives through, and nothing above it changes when it does.

Note that a result's `agent` is the *harness's* name and `metadata.agent_id` is the catalogue
id it was requested by. They differ — `plugin` is `researcher-plugin` — and both are worth
keeping: the first says which harness ran, the second says what was asked for.

## Events

| Event | Carries |
|---|---|
| `run_start` | The label, and how many steps to expect |
| `battle_start` | `battle_id`, needed to vote |
| `span_start` / `span_end` | One step, tool or model call — id, parent, kind, status, duration, cost |
| `cost` | A billed call, plus the running total |
| `token` | A delta, from whichever step is marked `stream=True` |
| `run_end` | Answer, full trace, ledger, `error_kind`, `bailed_out` |
| `run_error` | The run raised before producing anything |
| `done` | Every side has finished |

Every event carries `side` (`"a"` or `"b"`), so a battle is one stream rather than two.

A keepalive comment goes out every 15 seconds. Long model calls produce no events, and without
it an idle timeout kills the connection mid-run.

## Blinding

In a blind battle the agent's name is **absent from the payload**, not merely hidden by the UI —
`run_start`, `summary.agent`, `trace.agent` and the root span all read "Agent A" / "Agent B".
Anything the browser receives, a voter can read out of devtools, and then the arena is measuring
brand recognition instead of the harness. Identities come back from `POST /api/vote`, after the
vote is recorded.

Sides are also shuffled server-side, so position bias does not always land on the same agent.

## Shared clients

One `LLMClient` and one `APIClient` serve every request, created on startup and held in
[`state.py`](state.py). That is the point rather than an optimisation: their rate limiter and
cache are shared, so two agents in a battle cannot outspend each other on retries, a search both
of them run is paid for once, and a suite of thirty evals respects one rate limit.

The cache is in-memory and dies with the process — see
[`rsi_arena/core/cache.py`](../rsi_arena/core/cache.py) for the Redis seam.

## Files

| | |
|---|---|
| [`app.py`](app.py) | The arena routes: health, models, run, battle, vote, leaderboard |
| [`agents.py`](agents.py) | `/api/agents/...` — the catalogue and the non-streaming way to call one |
| [`evals.py`](evals.py) | `/api/evals/...` — run an eval, score it, store it |
| [`state.py`](state.py) | The shared clients, the per-request limits, the key checks |
| [`catalogue.py`](catalogue.py) | Which agents the UI can run. Adding one is a single entry |
| [`events.py`](events.py) | Merging concurrent runs into one lossless SSE stream |
| [`store.py`](store.py) | SQLite for battles and votes. Append-only |

## Adding an agent to the UI

One entry in `BUILDERS` in [`catalogue.py`](catalogue.py), plus its key requirements in
`REQUIRES`. It then appears in both pages' pickers with its plan, tools and context — and is
immediately runnable at `/api/agents/{id}/run` and scorable at `/api/evals`, with no route
changes.
