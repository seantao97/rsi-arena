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

The [runtime](rsi_arena/) is built: the layer agents are made of, laid out bottom to top as
[`core/`](rsi_arena/core) (cache, cost, rate limits, retries, traces), [`llm/`](rsi_arena/llm)
(models), [`api/`](rsi_arena/api) (everything else), [`agent/`](rsi_arena/agent) (tools, steps,
plans) and [`evals/`](rsi_arena/evals) (scoring an agent without a voter). The arena itself —
battles, votes, ratings, the optimizer loop — is not.

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

The whole backend is tested against a fake OpenRouter and a fake SearchApi built on
`httpx.MockTransport`. No key, no network, no cost — so run these first and know the failure is
yours rather than ours.

```bash
make test                     # or: pytest
make coverage                 # the same, with a per-module report
pytest tests/test_llm.py -v   # one file
pytest -k max_spend           # one idea
```

```
395 passed in 0.92s
```

[`tests/README.md`](tests/README.md) lists what is covered where. The short version: the
runtime (`core`, `llm`, `api`, `agent`, `evals`), the sample agents, and every backend route
including the SSE stream and blinding.

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
from rsi_arena.api.apis import SEARCHAPI

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

### 8. Scoring an agent, without waiting for a voter

A battle asks a human which of two answers is better. An **eval** asks a *function* whether one
answer is good — cheaper, repeatable, and runnable with nobody watching, which is what makes it
the thing a nightly job or the optimizer can use. The arena still needs votes for taste; evals
catch the regressions that never should have reached a voter.

An `Eval` is an agent, a prompt, and the scoring function, which the constructor takes and
resolves:

```python
from rsi_arena import Eval, EvalSuite
from rsi_arena.evals import all_of, contains, llm_judge, non_empty

result = await Eval(agent, "Did the ECB cut rates in July 2026?", all_of([
    contains("unchanged"),
    non_empty(200),
    llm_judge("Every factual claim carries the URL it came from."),
])).run()

print(result.score.value, result.score.passed, result.score.notes, result.cost_usd)
```

A scorer can be as small as you like — it takes the output, optionally a context with the run
and the agent, and returns a `Score`, a bool, a number or a dict:

```python
Eval(agent, "When did the ECB last meet?", lambda output: "2026" in output)
```

Built in: `contains`, `not_contains`, `regex`, `non_empty`, `json_valid`, `under_cost`,
`completed`, `llm_judge`, and `all_of` to combine them. `EvalSuite.over(agents, cases)` runs
every case against every agent concurrently against one shared client — the arena comparison,
minus the votes:

```bash
python examples/evals.py                          # every sample agent, every case
python examples/evals.py --agent plugin --judge   # add a model-graded rubric
```

```
eval                   agent                   score     cost  notes
samples:0              researcher-pipeline      1.00   0.1840  non_empty: 2140 characters; …
samples:1              researcher-freeform      0.67   0.0910  regex: no match
samples:2              fermi                    1.00   0.0184  contains: found 1 of 1

3 evals, 2 passed, mean 0.89, $0.2934
```

Results are stored — in the process today, through an interface a database drops straight into.
The backend exposes all of it: `POST /api/evals`, `POST /api/evals/suite`, `GET /api/evals`,
`GET /api/evals/leaderboard`.

### 9. Max spend, and answering anyway

`max_usd` is enforced: over the ceiling the run stops. By default it stops with *nothing*, which
is a bad trade — you paid $2 and got no answer, when the agent had already gathered most of what
it needed.

`max_spend_mode` changes that. At the ceiling, the run opens a small reserve and spends it on
one model call whose input is the run state and whose output is the best answer that state
supports:

```python
config = AgentConfig(max_usd=0.50, max_spend_mode=True)   # reserve: 5% of max_usd, floor $0.02
result = await agent.run(question)

result.output       # the bail-out answer, from whatever it had gathered
result.ok           # False — this is still not a clean run
result.error_kind   # ErrorKind.MAX_SPEND, distinct from ErrorKind.BUDGET
result.bailed_out   # True
```

Recording it as its own kind of error is the point. The arena needs to tell "this harness is
bad" from "this harness ran out of money" from "the provider was down", because only the first
is the agent's fault. `ErrorKind` is `max_spend`, `budget`, `provider`, `api`, `plan` or
`other`, and it rides on every result, every SSE `run_end`, and every eval row.

The reserve is an allowance, not a guarantee — a model call's price is only known once it has
been made, so a bail-out can overshoot on its last call. What is guaranteed is that the real
cost is recorded; `summary()` reports `reserve_usd` next to `reserve_used_usd`.

The same call is available on its own, with no ceiling involved. Every agent has it:

```python
text = await agent.immediate_answer(state, question="…", reason="the run was cut short")
```

It gets no tools, deliberately: this is the call you make when there is no budget left to gather
anything more.

### 10. Calling an agent from anything

The SSE route is right for a UI watching a trace appear and wrong for everything else. Every
agent in the catalogue is also callable as plain JSON:

```bash
curl -X POST localhost:3600/api/agents/fermi/run \
  -H 'content-type: application/json' \
  -d '{"question":"How many piano tuners are in Chicago?","max_usd":0.25,"max_spend_mode":true}'
```

```json
{ "agent_id": "fermi", "ok": true, "error_kind": null, "bailed_out": false,
  "text": "About 130 piano tuners…",
  "summary": { "total_usd": 0.0184, "calls": 2 } }
```

`GET /api/agents/{id}` describes one, and `POST /api/agents/{id}/answer` is `immediate_answer`
over HTTP. A failed run is a 200 with `ok: false` — the run happened and its partial trace is
evidence; only an unrunnable request is an error status.
[`server/README.md`](server/README.md) has every route and its body.

### Troubleshooting

| Symptom | Cause |
|---|---|
| `OPENROUTER_API_KEY is not set in the environment` | Exported in a different shell, or the venv is not active |
| `searchapi needs SEARCHAPI_API_KEY in the environment` | Use `--agent plugin`, which needs no search key |
| `[402] ...` | Out of OpenRouter credits. Not retried — no amount of waiting adds credits |
| `[503] no available model provider` | Structured-output steps set `provider.require_parameters`, so a model whose endpoints do not support `json_schema` routes nowhere. Pick another `--model` |
| `[429]` in the trace's `retries` | Normal. Backed off and retried; the `Retry-After` header pauses every in-flight call, not just the one that lost |
| `ModuleNotFoundError: rsi_arena` | Run from the repo root, or `pip install -e .` |
| `ModuleNotFoundError: rsi_arena.llm` after an upgrade | The runtime is packages now: `rsi_arena.llm.client`, `rsi_arena.api.spec`, `rsi_arena.core.costs`, `rsi_arena.agent.steps`. Every common name is still re-exported from `rsi_arena` itself |
| An eval returns `scorer_error` | The scorer raised. Its exception is in `score.notes` — a broken scorer is a failed row, not a lost run |
| `bad scorer: unknown scorer ...` | `GET /api/scorers` lists what a spec may name; `register_scorer` adds your own |
| A run reports `error_kind: "max_spend"` | It hit its ceiling and answered from state instead of stopping with nothing. `bailed_out` is `true`, and the answer is in `output` |
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
| `RSI_ARENA_DB` | Where battles and votes are stored. Default `./arena.db`. Eval results are in-process and do not use it |
| `NEXT_PUBLIC_API_BASE` | Where the UI looks for the backend. Default `http://localhost:3600` |

[`server/README.md`](server/README.md) documents the routes and the event stream;
[`web/README.md`](web/README.md) covers the frontend.

## Status

Early, but runnable end to end. The runtime in [`rsi_arena/`](rsi_arena/) works, with sample
agents and an offline test suite; the [backend](server/) and [web app](web/) run battles live
and record votes, every agent is callable as plain JSON, and [evals](rsi_arena/evals) score them
without a human in the loop. The [topics](topics/) are specified and the Kalshi data layer for
the sports and market topics is built.

What is described above and not built: **ratings** — votes are counted, not turned into Elo,
because an Elo number over a handful of votes looks authoritative and means nothing — and the
**optimizer**, the loop that reads losing traces and writes the next generation of harnesses.
That loop is the whole idea, and it is the part that does not exist yet. Evals are half of what
it will need: a fitness signal it can run itself, between the human votes.

Eval results live in the process and die with it. The store interface is async and swappable
precisely so that stops being true without anything above it changing.

## License

Apache 2.0 — see [LICENSE](LICENSE).
