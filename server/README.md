# Backend

FastAPI on **:3600**. Runs the sample agents and streams their traces to the web app on
:8050. It is a thin layer: the agents live in [`examples/`](../examples), the runtime in
[`rsi_arena/`](../rsi_arena), and this only exposes them over HTTP.

```bash
pip install -e ".[server]"
export OPENROUTER_API_KEY=sk-or-...
python -m server                # --reload while editing, --port to move it
```

## Routes

| | |
|---|---|
| `GET /api/health` | Which keys the process actually has |
| `GET /api/agents` | Catalogue: plan outline, tools, context, and which keys each agent needs |
| `GET /api/models` | OpenRouter's live model list, structured-output-capable first |
| `POST /api/run` | One agent. **SSE.** |
| `POST /api/battle` | Two agents concurrently, blind by default. **SSE.** |
| `POST /api/vote` | Record a vote; returns the reveal and the running tally |
| `GET /api/leaderboard` | Win/loss/tie counts per agent |

Both streaming routes are POST — a run needs a body — so the client uses `fetch` and reads the
response rather than `EventSource`, which is GET-only. The wire format is ordinary SSE.

```bash
curl -N -X POST localhost:3600/api/run \
  -H 'content-type: application/json' \
  -d '{"agent":"fermi","question":"How many piano tuners are in Chicago?","max_usd":0.25}'
```

## Events

| Event | Carries |
|---|---|
| `run_start` | The label, and how many steps to expect |
| `battle_start` | `battle_id`, needed to vote |
| `span_start` / `span_end` | One step, tool or model call — id, parent, kind, status, duration, cost |
| `cost` | A billed call, plus the running total |
| `token` | A delta, from whichever step is marked `stream=True` |
| `run_end` | Answer, full trace, ledger |
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

## Shared client

One `LLMClient` and one `APIClient` serve every request, created on startup. That is the point
rather than an optimisation: their rate limiter and cache are shared, so two agents in a battle
cannot outspend each other on retries, and a search both of them run is paid for once.

The cache is in-memory and dies with the process — see [`rsi_arena/cache.py`](../rsi_arena/cache.py)
for the Redis seam.

## Files

| | |
|---|---|
| [`app.py`](app.py) | Routes, request models, the shared clients |
| [`catalogue.py`](catalogue.py) | Which agents the UI can run. Adding one is a single entry |
| [`events.py`](events.py) | Merging concurrent runs into one lossless SSE stream |
| [`store.py`](store.py) | SQLite for battles and votes. Append-only |

## Adding an agent to the UI

One entry in `BUILDERS` in [`catalogue.py`](catalogue.py), plus its key requirements in
`REQUIRES`. It then appears in both pages' pickers with its plan, tools and context.
