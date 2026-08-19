# RSI Arena

A Chatbot-Arena-style leaderboard for **agents** instead of models — plus a background loop that uses the arena's own votes to rewrite the agents that lose.

Chatbot Arena answers "which model is better?" But in practice nobody ships a bare model. They ship a *harness*: a model wired to a set of tools, with a prompt that says how and when to use them. Two harnesses over the same model can be far apart in quality. RSI Arena measures that whole package, and then closes the loop — the winners get mutated into the next generation of contenders.

RSI stands for *recursive self-improvement* — see [below](#why-rsi) for what that means here.

## What's being compared

An **agent** here is:

```
agent = LLM + primitives (tools) + orchestration prompt/plan
```

- **LLM** — the underlying model (and its decoding settings).
- **Primitives** — the typed steps the agent may take. For a web-research task the primitive set might be `rewrite_query`, `search`, `scrape`, `select_docs`, `summarize`, `cite`.
- **Orchestration** — how those primitives get combined. Either a *fixed pipeline* (e.g. `rewrite_query → search → select_docs → scrape → summarize`) or a *free-form* agent where the prompt describes the primitives and their tradeoffs and lets the model decide the order and how many times to loop.

Two agents can share an LLM and a primitive set and still differ entirely, because the orchestration differs. That difference is exactly what the arena is built to measure.

## How it works

### 1. Ask

A user picks a **topic** (web research, coding, data analysis, …) and asks a question. Topics matter: the primitive set and the leaderboard are both scoped per topic, since a good web-research harness tells you nothing about a good SQL harness.

### 2. Battle

Two agents are sampled from the topic's active pool and run on the same question, anonymously and side by side. The user sees the answers — and optionally the trace (which primitives fired, in what order, what came back).

### 3. Vote

The user picks a winner, or calls it a tie. That vote is the only supervision signal in the system. Votes feed an Elo / Bradley–Terry rating per agent per topic.

### 4. Optimize (the background job)

Periodically, an offline job:

1. Takes the **top-K agents** for a topic (K ≈ 10) by rating.
2. Gathers evidence for each: its wins and losses, the questions it lost on, and the traces from those battles.
3. Asks each candidate LLM to **rewrite the harness** — change the prompt, reorder or prune the pipeline, add or drop a primitive, adjust the loop budget — given the winners, the losers, and why.
4. Emits the mutated harnesses as a **new generation** of agents, seeded into the pool at a provisional rating.
5. Retires agents that have been decisively and repeatedly beaten.

The next batch of user questions is the evaluation for that generation. Repeat.

```
   users ask ──▶ battles ──▶ votes ──▶ ratings
                    ▲                     │
                    │                     ▼
              new agents ◀── LLM optimizer over top-K
```

## Why RSI

Because nobody writes the agents. We supply the primitives; the agents do the rest.

A normal agent framework works like this: a human reads the traces, notices the search step fires too early, rewrites the prompt, reorders the pipeline, ships it, repeats. The human is the optimizer, and the whole system improves exactly as fast as that human can read transcripts.

RSI Arena removes the human from that inner loop. What we contribute is fixed and small:

- the **primitives** — the raw capabilities an agent may call (`search`, `scrape`, `summarize`, …)
- the **arena** — a way to run two agents on the same question and record which one won

That's it. Nobody hand-writes an orchestration prompt. Nobody decides that `rewrite_query` should come before `search`. Nobody tunes a loop budget. Those are all *discovered*, by the agents, out of the primitive set they were handed.

The recursion is the part that makes it more than plain search. The optimizer is not a separate fixed system sitting outside the arena — it's the same population of LLMs that competes in it:

- Generation *N+1* is **authored by generation *N***. The winners are handed their own traces, their own losses, and their rivals' answers, and asked to write the next harness.
- As the models improve, so does the optimizer, so the harnesses improve faster.
- As the harnesses improve, the bar for winning a battle rises, so the next round of rewrites is judged against stronger opposition.

Each turn of the loop improves the thing that runs the next turn. That's the recursive part: not a model training itself on its own weights, but a population of agents rewriting the code that makes them agents, evaluated by whether the rewrite actually won.

**Where the recursion stops.** Being precise about the bounds, since "self-improving" is an easy word to oversell:

- Model weights never change. The search is over harnesses — prompts, primitive selection, ordering, loop budgets — not parameters.
- The primitive set is human-supplied. An agent can discover a better *way* to use `scrape`; it cannot invent a primitive it wasn't given. (Letting agents propose new primitives is the obvious next step, and the obvious next safety question.)
- Humans still supply the fitness signal, one vote at a time. The system optimizes toward what arena users prefer — which means it inherits their taste, and their biases, exactly.

## Why this might work

- **The signal is real.** Preference votes on real questions people actually cared about, not a static benchmark that gets memorized and gamed.
- **The search space is the right one.** Prompt-and-pipeline space is where most practical agent quality lives, and it's cheap to search — no training, no gradients, just generation and evaluation.
- **The optimizer is diverse.** Every LLM in the pool proposes mutations, so the population isn't shaped by a single model's blind spots.

## Open questions

Things this design has not settled, listed honestly:

- **Vote sparsity.** Elo over a pool that keeps churning needs a lot of battles per agent. Pool size, generation cadence, and matchmaking (favoring high-uncertainty pairings) all have to be tuned against real traffic.
- **Cost asymmetry.** A harness that calls 40 tools will often beat one that calls 3. Ratings probably need to be reported against a cost/latency budget, or split into tiers.
- **Convergence vs. collapse.** Top-K selection plus LLM mutation can converge on one local style. Some diversity pressure — novelty bonuses, protected niches, occasional random restarts — is likely needed.
- **Adversarial voting.** Anything with a leaderboard attracts vote manipulation.
- **Trace visibility.** Showing traces makes votes better informed but also biases users toward agents that *look* thorough.

## Running it

The [runtime](rsi_arena/) is built: the layer agents are made of. The arena itself — battles,
votes, ratings, the optimizer loop — is not.

### 1. Install

Python 3.10 or newer. Two dependencies, `httpx` and `pydantic`.

```bash
git clone https://github.com/Helpigent/rsi-arena && cd rsi-arena

python3 -m venv .venv && source .venv/bin/activate     # Windows: .venv\Scripts\activate
pip install -e .                       # the runtime
pip install -e ".[server]"             # ...plus the backend, if you want the web app
```

`pip install -r requirements.txt` works too if you would rather not install the package; then
run everything from the repo root, which the example scripts already assume. `make install`
does both halves, Python and npm.

### 2. Check it works, before spending anything

Both test files run against a fake OpenRouter and a fake SearchApi built on
`httpx.MockTransport`. No key, no network, no cost — so run these first and know the failure is
yours rather than ours.

```bash
make test                           # or run them individually:
python tests/test_end_to_end.py     # the runtime
python tests/test_examples.py       # the three sample agents
python tests/test_server.py         # the backend, its SSE routes and blinding
```

```
1. retries+cost           ok (attempts=3, $0.0012)
2. cache + single-flight  ok (1 wire call for 20 parallel, {'entries': 2, 'hits': 19, 'misses': 2})
3. structured output      ok (Answer(answer='42', confidence=0.9))
4. streaming (+replay)    ok ('hello', $0.0003)
5. api + cache + cost     ok ($0.004 then $0.0 cached)
6. agent run              ok
7. agent/trace JSON       ok (agent round-trips, trace serialises)
8. budget ceiling         ok (BudgetExceeded: budget exceeded: llm:test/model would take spend
   to $0.0012 of $0.0001)
all checks passed
```

### 3. Keys

| Variable | Needed for | Where |
|---|---|---|
| `OPENROUTER_API_KEY` | Every model call | [openrouter.ai/keys](https://openrouter.ai/keys) |
| `SEARCHAPI_API_KEY` | `--agent pipeline` and `--agent freeform` | [searchapi.io](https://www.searchapi.io/) |
| `OPENROUTER_APP_URL` | Optional. Sent as `HTTP-Referer`, which lists the app on OpenRouter | — |

```bash
export OPENROUTER_API_KEY=sk-or-...
export SEARCHAPI_API_KEY=...
```

Only `OPENROUTER_API_KEY` is required. `--agent plugin` needs no search key — OpenRouter's `web`
plugin does the retrieval — so it is the one to try if you only have the one key. A missing key
fails immediately with the variable name in the message, rather than as a 401 twenty seconds in.

Two things worth knowing before the first real call:

- **Credits, not a trial.** OpenRouter bills per call against a prepaid balance. With no credits
  every request returns 402, and 402 is not retried — no amount of waiting adds credits. $5 is
  more than enough to work through everything here.
- **The model has to support structured outputs.** Steps with a schema send
  `provider.require_parameters`, so a model whose endpoints do not implement `json_schema`
  routes nowhere and returns 503 rather than quietly ignoring the schema. Anything in the
  suggested list in [`server/app.py`](server/app.py) works; check the *Providers* section of a
  model's page on OpenRouter before picking something else.

Everything is cached, so re-running the same question with the same model costs nothing the
second time. That is usually what you want, and occasionally exactly what you do not — pass
`--max-usd` a fresh value or set `cache=False` on the config when you need a genuinely new call.

### 4. The smoke test agent

Three steps — decompose, compute, write up. A few seconds and well under a cent. Run this before
the research agents; if it works, the plumbing works.

```bash
python examples/smoke_test.py "How many piano tuners are there in Chicago?"
```

```
fermi [anthropic/claude-sonnet-4.5]
tools: calculator
decompose (prompt)
compute (tool)
write_up (prompt)

About 130 piano tuners. Chicago has ~2.7M people, ~1 piano per 40 households…
------------------------------------------------------------
{
  "agent": "fermi",
  "run_id": "9f2c41b7de03",
  "ok": true,
  "error": null,
  "duration_s": 11.4,
  "total_usd": 0.0184,
  "calls": 2,
  "cached_calls": 0,
  "by_kind": { "llm": 0.0184 },
  "by_name": { "anthropic/claude-sonnet-4.5": 0.0184 },
  "usage": { "prompt_tokens": 1832, "completion_tokens": 604, "reasoning_tokens": 0, ... }
}
```

Run it twice and the second run is free: identical requests are served from the cache, and the
ledger shows them at `$0` with `cached_calls` counting them rather than dropping them.

| Flag | Does |
|---|---|
| `--model` | Any OpenRouter slug. Default `anthropic/claude-sonnet-4.5` |
| `--trace` | Print the span tree — every step, its timing and its cost |
| `--json PATH` | Write the whole `AgentResult` (answer, state, trace, ledger) to a file |

### 5. The research agents

Three agents, one question, same model, same tools, same cost ceiling. They differ **only in
orchestration**, which is the comparison the whole project is about:

```bash
python examples/web_research.py "Did the ECB cut rates in July 2026?" --agent all --trace
```

| Agent | Orchestration | Needs |
|---|---|---|
| `pipeline` | Fixed order: plan queries → *(search → take notes)* until sufficient → draft → critique. The plan decides what runs. | Both keys |
| `freeform` | One prompt, the same two search tools, a budget of eight tool calls. The model decides what runs, in what order, and how many times. | Both keys |
| `plugin` | No search tool at all — OpenRouter's `web` plugin retrieves inside the model call. | OpenRouter only |

`--agent all` runs the three concurrently against one shared client, so they share a rate limiter
and a cache and a repeated search is paid for once. That sharing is also what makes an arena
battle fair.

| Flag | Does |
|---|---|
| `--agent {pipeline,freeform,plugin,all}` | Which to run. Default `pipeline` |
| `--model` | Any OpenRouter slug, applied to every agent |
| `--max-usd` | Per-agent ceiling, default `$2.00`. Both sides of a battle get the same one |
| `--trace` | Print each agent's span tree |
| `--stream` | Stream the first step's tokens instead of running the plan — what a live UI renders |
| `--json PATH` | Write every result as JSON |

Expect roughly $0.05–$0.30 per research agent per question, depending on model and how much it
searches. `--max-usd` is enforced: over the ceiling the run stops and returns its partial trace
rather than queueing.

### 6. What comes back

Every run returns an `AgentResult` — the answer, the final state, the full span tree and the cost
ledger — as one JSON object, holding both what a voter needs to see and what the optimizer needs
to read. `--trace` renders the tree:

```
trace 5b33cd140c51 agent='researcher-pipeline' 34.21s $0.19500 (6 calls)
✓ researcher-pipeline (agent) 34.21s
  ✓ plan_queries (step) 3.10s
    ✓ llm (llm) 3.10s $0.00310
  ✓ research (loop) 19.44s
    ✓ iteration 1 (iteration) 19.44s
      ✓ choose_query (step) 1.02s
      ✓ search (step) 0.83s
        ✓ search (tool) 0.83s $0.00400
      ✓ take_notes (step) 8.31s
  ✓ draft (step) 7.20s
  ✓ critique (step) 4.47s
```

### 7. Your own agent

An agent is a context, a plan and a set of tools. In full:

```python
import asyncio
from rsi_arena import Agent, AgentConfig, Plan, PromptStep, ToolStep, Toolbox, api_tool
from rsi_arena.apis import SEARCHAPI

agent = Agent(
    name="researcher",
    context="You are a research agent. Every claim carries the URL it came from.",
    tools=Toolbox([api_tool(SEARCHAPI, "search", name="search")]),
    config=AgentConfig(default_model="anthropic/claude-sonnet-4.5", max_usd=0.50),
    plan=Plan(steps=[
        ToolStep(name="search", tool="search", args={"q": "{{question}}"}, output_key="hits"),
        PromptStep(name="write", prompt="Answer {{question}} from:\n{{hits}}"),
    ]),
)

result = asyncio.run(agent.run("Did the ECB cut rates in July 2026?"))
print(result.output)
print(result.trace.render())
```

Swapping that `ToolStep` for `PromptStep(tools=["*"])` turns the same primitives into a free-form
agent. That one field is the difference the arena measures.

[`rsi_arena/README.md`](rsi_arena/README.md) covers the step types, the loop stopping conditions,
and how to register a new API — which is one `APISpec` literal and no client code.

### Troubleshooting

| Symptom | Cause |
|---|---|
| `OPENROUTER_API_KEY is not set in the environment` | Exported in a different shell, or the venv is not active |
| `searchapi needs SEARCHAPI_API_KEY in the environment` | Use `--agent plugin`, which needs no search key |
| `[402] ...` | Out of OpenRouter credits. Not retried — no amount of waiting adds credits |
| `[503] no available model provider` | Structured-output steps set `provider.require_parameters`, so a model whose endpoints do not support `json_schema` routes nowhere. Pick another `--model` |
| `[429]` in the trace's `retries` | Normal. Backed off and retried; the `Retry-After` header pauses every in-flight call, not just the one that lost |
| `ModuleNotFoundError: rsi_arena` | Run from the repo root, or `pip install -e .` |
| A run costs more than expected | `--trace` breaks cost down per step; `result.trace.costs.summary()` breaks it down by kind and by name |
| The UI says it cannot reach the backend | `python -m server` is not running, or it is on a different port than `NEXT_PUBLIC_API_BASE` |
| The UI says a key is missing but you exported it | It reads the *backend's* environment. Export it in the shell that starts `python -m server`, then restart it |
| A run is still cheap the second time | It is cached. Cached rows are labelled in the trace and priced at $0 |
| The trace arrives all at once at the end | Something is buffering `text/event-stream`. The backend sets `X-Accel-Buffering: no`; a proxy in front may need configuring too |

## The web app

A local UI for running agents against live models and watching what they do. Next.js + Tailwind
+ shadcn on **:8050**, FastAPI on **:3600**.

Two tabs. **Playground** runs one agent and shows its plan, its trace and its bill. **Battle**
runs two agents on the same question, same model, same ceiling, side by side and blind, and asks
you to vote — the arena from the top of this file, minus the ratings.

### Install

```bash
pip install -e ".[server]"          # adds fastapi and uvicorn
cd web && npm install && cd ..
```

### Try it with no keys and no spend

There is a local stand-in for OpenRouter and SearchApi that speaks enough of both protocols to
drive the whole stack — streaming, tool calls, structured outputs, usage and cost. Answers are
canned and say so, so nothing here can be mistaken for a real run.

```bash
make fake      # terminal 1 — the stand-in, on :3601
make demo      # terminal 2 — backend on :3600, wired to it
make web       # terminal 3 — the UI on :8050
```

Open **http://localhost:8050**. Everything works: live traces, streaming answers, blind battles,
voting, the ledger. The only thing that is not real is the model.

### Run it against real API calls

Same three commands, minus the fake, plus your keys:

```bash
export OPENROUTER_API_KEY=sk-or-...
export SEARCHAPI_API_KEY=...        # optional; the `plugin` agent does not need it

make backend                        # terminal 1 — :3600, real calls
make web                            # terminal 2 — :8050
```

Then open **http://localhost:8050** and:

1. Check the banner at the top of the page. It reads the backend's environment, so if it says a
   key is missing, the key is missing *in the shell that started the backend* — exporting it
   somewhere else does not count. Agents needing an absent key are greyed out in the picker.
2. Pick a model. The dropdown is OpenRouter's live catalogue, with known-good ones first.
3. **Set the budget.** It defaults to $1.00 and it is enforced, not advisory — the backend stops
   a run that crosses it and returns the partial trace. This is the field that decides what a
   careless question costs.
4. Ask something, and watch the **Trace** tab rather than the answer. Which primitives fired, in
   what order, how many times the loop went round, and what each step charged, is the entire
   point of the thing.

To confirm calls are genuinely going out rather than coming from the cache: the **Cost** tab
shows `served from cache`, and cached rows are labelled in the trace and priced at $0.

Without the UI, the same calls from a terminal:

```bash
export OPENROUTER_API_KEY=sk-or-...
python examples/web_research.py "Did the ECB cut rates in July 2026?" --agent all --trace
```

### What it costs

Per question, on a mid-tier model:

| | |
|---|---|
| `fermi` | under $0.02 — two model calls, no search |
| `plugin` | $0.02–$0.05 — one call plus OpenRouter's web plugin (Exa is ~$0.007/request) |
| `pipeline` | $0.05–$0.30 — six-plus model calls and up to four searches at ~$0.004 each |
| `freeform` | $0.03–$0.25 — fewer, larger calls; the model decides how much to search |

A battle runs two of these, so budget accordingly. The ceiling is per agent, and both sides of a
battle get the same one.

### Ports and configuration

| | | |
|---|---|---|
| `8050` | Web app | `cd web && npm run dev` |
| `3600` | Backend | `python -m server` — `--port`, `--host`, `--reload` |
| `3601` | Local fake | `python -m tests.fake_openrouter` — only for the no-key demo |

| Variable | Does |
|---|---|
| `OPENROUTER_API_KEY` | Required for any real model call |
| `SEARCHAPI_API_KEY` | Google search for the `pipeline` and `freeform` agents |
| `OPENROUTER_BASE_URL` | Point the runtime at a gateway, proxy, or the local fake |
| `SEARCHAPI_BASE_URL` | Same, for search |
| `RSI_ARENA_DB` | Where battles and votes are stored. Default `./arena.db` |
| `NEXT_PUBLIC_API_BASE` | Where the UI looks for the backend. Default `http://localhost:3600` |

[`server/README.md`](server/README.md) documents the routes and the event stream;
[`web/README.md`](web/README.md) covers the frontend.

## Status

Early, but runnable end to end. The runtime in [`rsi_arena/`](rsi_arena/) works, with sample
agents and offline tests; the [backend](server/) and [web app](web/) run battles live and record
votes. The [topics](topics/) are specified and the Kalshi data layer for the sports and market
topics is built.

What is described above and not built: **ratings** — votes are counted, not turned into Elo,
because an Elo number over a handful of votes looks authoritative and means nothing — and the
**optimizer**, the loop that reads losing traces and writes the next generation of harnesses.
That loop is the whole idea, and it is the part that does not exist yet.

## License

Apache 2.0 — see [LICENSE](LICENSE).
