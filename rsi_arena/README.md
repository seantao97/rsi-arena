# Runtime

The machinery agents are built out of. Everything a topic's primitive set needs to sit on:
model calls, API calls, tools, plans, traces, cost — and evals, for scoring an agent without
waiting for a human to vote.

Nothing here decides *how* an agent works. That is the orchestration, and the whole point of
the project is that agents discover it. This layer only has to make every orchestration
runnable, comparable and priced.

## Layout

Five packages, layered bottom to top. Read them in this order and the whole thing follows.

| Package | Does |
|---|---|
| [`core/`](core/) | The parts every call goes through, whatever it is calling |
| [`llm/`](llm/) | Talking to models, through OpenRouter |
| [`api/`](api/) | Talking to everything else, declared as data |
| [`agent/`](agent/) | Tools, steps, plans, and the agent that runs them |
| [`evals/`](evals/) | Give an agent a prompt, score what comes back, store it |

Nothing here knows about models or vendors — a cache key, a token bucket, a backoff schedule,
a span and a cost record look the same whether the call underneath was a chat completion or a
weather API, which is why they live below both:

| [`core/`](core/) | |
|---|---|
| [`cache.py`](core/cache.py) | Content-addressed cache plus single-flight. In memory today, Redis later |
| [`costs.py`](core/costs.py) | Usage, cost, the ledger, the budget ceiling and the bail-out reserve |
| [`ratelimit.py`](core/ratelimit.py) · [`retry.py`](core/retry.py) | Token bucket, concurrency ceiling, backoff that respects `Retry-After` |
| [`trace.py`](core/trace.py) | The span tree every run emits |
| [`template.py`](core/template.py) | `{{placeholder}}` rendering and the restricted condition evaluator |

| [`llm/`](llm/) | |
|---|---|
| [`client.py`](llm/client.py) | `LLMClient` — retries, limits, caching, structured outputs, web search, streaming, cost |
| [`messages.py`](llm/messages.py) | `Message`, `ToolCall`, `Citation` — the wire types, with no HTTP near them |
| [`config.py`](llm/config.py) | `LLMConfig` and `WebSearch` |
| [`results.py`](llm/results.py) | `Completion`, `StreamEvent`, and the JSON helpers |
| [`errors.py`](llm/errors.py) | `OpenRouterError`, importable without the client |

| [`api/`](api/) | |
|---|---|
| [`spec.py`](api/spec.py) | `APISpec`, `Endpoint`, `Param` — declarative, serialisable, no connections |
| [`client.py`](api/client.py) | `APIClient` — executes any spec, with the same retries, limits, caching and cost |
| [`auth.py`](api/auth.py) | None / bearer / header / query credentials, read at call time |
| [`registry.py`](api/registry.py) | Name → spec, global and instantiable |
| [`apis/`](api/apis/) | The specs that ship with the runtime. [`searchapi.py`](api/apis/searchapi.py) is the first |

| [`agent/`](agent/) | |
|---|---|
| [`tools.py`](agent/tools.py) | Primitives — from a Python function, an API endpoint, or by hand |
| [`steps.py`](agent/steps.py) | `PromptStep`, `ToolStep`, `LoopStep`, and the `Plan` that holds them |
| [`agent.py`](agent/agent.py) | Context + plan + tools + config, the run, and `immediate_answer` |

| [`evals/`](evals/) | |
|---|---|
| [`eval.py`](evals/eval.py) | `Eval` and `EvalSuite`, and the results they produce |
| [`scoring.py`](evals/scoring.py) | `Score`, the built-in scorers, and the registry the HTTP API selects from |
| [`store.py`](evals/store.py) | Where results go. In memory today; the interface a DB drops into |

Every name a caller normally needs is re-exported from the top, so `from rsi_arena import Agent,
Plan, Eval` works regardless of which package it lives in.

## An agent

```python
from rsi_arena import Agent, AgentConfig, Plan, PromptStep, ToolStep, LoopStep, Toolbox, api_tool
from rsi_arena.api.apis import SEARCHAPI

agent = Agent(
    name="researcher",
    context="You are a research agent. Every claim carries the URL it came from.",
    tools=Toolbox([api_tool(SEARCHAPI, "search", name="search")]),
    config=AgentConfig(default_model="anthropic/claude-sonnet-4.5", max_usd=2.00),
    plan=Plan(steps=[
        ToolStep(name="search", tool="search", args={"q": "{{question}}"}, output_key="hits"),
        PromptStep(name="write", prompt="Answer {{question}} from:\n{{hits}}", output_key="answer"),
    ]),
)

result = await agent.run("Did the ECB cut rates in July 2026?")
print(result.output, result.cost_usd)
print(result.trace.render())
```

## Steps

A step is simple or it is a loop.

**`PromptStep`** — the LLM does something. `output_schema` makes it return parsed JSON via
OpenRouter's `json_schema` response format. `tools` decides who is driving: leave it empty and
the step is one deterministic call; set it and the model runs its own tool loop up to
`max_tool_iterations`. That switch is the difference between the two orchestrations the arena
compares, and it is one field.

**`ToolStep`** — a tool or API call, with arguments templated from state. Deterministic; the
plan decides.

**`LoopStep`** — inner steps, a `max_loops` ceiling, and a stopping condition. Two kinds:
`until` is a restricted expression over run state (free, deterministic, right for countable
conditions) and `until_prompt` asks a model (the only option for a judgement). Set both and
either one stops the loop; `until` is checked first, so the free one short-circuits the paid one.

Every step writes into one flat state dict under its `output_key`, and later steps read it back
with `{{key}}`. Flat and JSON-serialisable on purpose: the optimizer reads traces and writes
plans, and both ends of that loop are JSON.

## Adding an API

One declaration, no client code:

```python
from rsi_arena import APISpec, Endpoint, Param, NoAuth, RateLimit, register_api

NWS = register_api(APISpec(
    name="nws",
    base_url="https://api.weather.gov",
    auth=NoAuth(),
    rate_limit=RateLimit(per_second=5),
    cache_ttl_s=600,
    endpoints=[
        Endpoint("forecast", "/gridpoints/{office}/{grid_x},{grid_y}/forecast",
                 description="NWS point forecast.",
                 params=(Param("office", "Forecast office, e.g. OKX", required=True),),
                 parse=lambda d: d["properties"]["periods"][:8]),
    ],
))
```

It is now retried, rate limited, cached and costed like everything else, and
`api_tool(NWS, "forecast")` hands it to a model as a callable tool with a generated schema.
`parse` is the hook worth using: raw vendor JSON is enormous, and an agent should not pay
tokens to read pagination metadata.

Anything genuinely custom hooks in through `parse` and `cost_usd` on the endpoint rather than
by subclassing a client — which is what keeps third-party API definitions to one literal.

## Evals

An `Eval` is an agent, a prompt, and a function that scores the text that comes back. The
scoring function is handed to the constructor and resolved there, so a broken eval fails before
it spends anything rather than after.

```python
from rsi_arena import Eval, EvalSuite
from rsi_arena.evals import all_of, contains, llm_judge, non_empty, default_eval_store

ev = Eval(agent, "Did the ECB cut rates in July 2026?", all_of([
    contains("unchanged"),
    non_empty(200),
    llm_judge("Every factual claim carries the URL it came from."),
]))

result = await ev.run()
print(result.score.value, result.score.passed, result.score.notes, result.cost_usd)
print(await default_eval_store().get(result.id))     # stored automatically
```

A scorer takes the output (and optionally an `EvalContext` with the run, the agent and a shared
LLM client), may be sync or async, and may return a `Score`, a bool, a number or a dict —
whatever is least ceremony for the check being written:

```python
Eval(agent, "When did the ECB last meet?", lambda output: "2026" in output)
```

Built in: `contains`, `not_contains`, `regex`, `non_empty`, `json_valid`, `under_cost`,
`completed`, `llm_judge`, and `all_of` to combine them. All are registered by name, which is
how the HTTP API selects one — a function cannot travel in a request body, so
`{"type": "contains", "value": "unchanged"}` does. `register_scorer` adds your own to the same
registry, and it becomes selectable over HTTP immediately.

`EvalSuite.over(agents, cases)` runs every case against every agent, concurrently and against
one shared client, and aggregates. That is the arena comparison minus the votes.

Results go to an `EvalStore`. `InMemoryEvalStore` is the default and the only implementation
today; the interface is async throughout so a database is a drop-in:

```python
set_default_eval_store(PostgresEvalStore(dsn))       # later, and nothing else changes
```

## Cost, ceilings and max spend

Every LLM and API call produces a `Cost` that says where its number came from: `reported`
(OpenRouter's `usage.cost`, the normal case), `estimated` (from the `/models` price table, only
when a response arrives without one), `fixed` (a per-call price an API spec declares), or
`free`.

`AgentConfig.max_usd` and `max_calls` are checked before each step and after each call. Over the
ceiling, the run stops — refused, not queued, matching the arena runtime's Governor.

**Max spend mode** changes what happens *at* that ceiling. With `max_spend_mode=True`, a run
that would otherwise stop with nothing instead opens a small reserve and spends it on
`Agent.immediate_answer` — one model call whose input is the run state, whose output is the
best answer that state supports:

```python
config = AgentConfig(max_usd=0.50, max_spend_mode=True)   # reserve defaults to 5%, floor $0.02
result = await agent.run(question)

result.output       # the bail-out answer, from whatever it had gathered
result.ok           # False — this is still not a clean run
result.error_kind   # ErrorKind.MAX_SPEND, distinct from ErrorKind.BUDGET
result.bailed_out   # True
```

`ErrorKind` is the point of recording it separately. The arena needs to tell "this harness is
bad" from "this harness ran out of money" from "the provider was down", because only the first
is the agent's fault: `max_spend`, `budget`, `provider`, `api`, `plan`, `other`.

The reserve is an *allowance*, not a guarantee — a model call's price is only known once it has
been made, so a bail-out can overshoot on its last call. What is guaranteed is that the real
cost is recorded: it lands in the ledger like any other, and `summary()` reports `reserve_usd`
alongside `reserve_used_usd`.

`immediate_answer` is also useful on its own, with no ceiling involved — it is what
`POST /api/agents/{id}/answer` exposes:

```python
text = await agent.immediate_answer(state, question="...", reason="the run was cut short")
```

It is deliberately given no tools. This is the call you make when there is no budget left to
gather anything more.

## Caching

`Cache` is `get`, `set`, `clear`. `MemoryCache` is the default; swap it with
`set_default_cache(RedisCache(...))` when there is one. Keys are a SHA-256 of the canonicalised
request, so identical requests hit and changed ones miss.

Concurrent identical requests are also collapsed — `single_flight` means twenty parallel copies
of the same prompt produce one call, not twenty. That is a different problem from caching: a
cache stops the second call once the first has *finished*.

Cache hits are recorded in the trace at $0 rather than dropped, so a replayed run still shows
what it would have cost.

## Tests

```bash
make test           # or: pytest
make coverage       # the same, with a per-module report
```

Every test runs against `httpx.MockTransport` through [`tests/fakes.py`](../tests/fakes.py), so
no key, no network and no cost. See [`tests/README.md`](../tests/README.md) for what is covered
where.

For a version you can click on rather than read the output of,
[`tests/fake_openrouter.py`](../tests/fake_openrouter.py) is a local stand-in that speaks the
real protocols over HTTP — see the web app section of the
[root README](../README.md#try-it-with-no-keys-and-no-spend).
