# Runtime

The machinery agents are built out of. Everything a topic's primitive set needs to sit on:
model calls, API calls, tools, plans, traces and cost.

Nothing here decides *how* an agent works. That is the orchestration, and the whole point of
the project is that agents discover it. This layer only has to make every orchestration
runnable, comparable and priced.

| Module | Does |
|---|---|
| [`llm.py`](llm.py) | OpenRouter: retries, timeouts, rate limits, caching, structured outputs, web search, streaming, cost |
| [`api.py`](api.py) | Any HTTP API, declared as data. Same retries, limits, caching and cost |
| [`apis/`](apis/) | The registered APIs. [`searchapi.py`](apis/searchapi.py) is the first |
| [`tools.py`](tools.py) | Primitives — from a Python function, an API endpoint, or by hand |
| [`steps.py`](steps.py) | `PromptStep`, `ToolStep`, `LoopStep`, and the `Plan` that holds them |
| [`agent.py`](agent.py) | Context + plan + tools + config, and the run that produces a result |
| [`trace.py`](trace.py) | The span tree every run emits |
| [`costs.py`](costs.py) | Usage, cost and the budget ceiling |
| [`cache.py`](cache.py) | `get`/`set`, in memory today, Redis later |
| [`ratelimit.py`](ratelimit.py) · [`retry.py`](retry.py) | Token bucket, concurrency ceiling, backoff |
| [`template.py`](template.py) | `{{placeholder}}` rendering and the restricted condition evaluator |

## An agent

```python
from rsi_arena import Agent, AgentConfig, Plan, PromptStep, ToolStep, LoopStep, Toolbox, api_tool
from rsi_arena.apis import SEARCHAPI

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

## Caching

`Cache` is `get`, `set`, `clear`. `MemoryCache` is the default; swap it with
`set_default_cache(RedisCache(...))` when there is one. Keys are a SHA-256 of the canonicalised
request, so identical requests hit and changed ones miss.

Concurrent identical requests are also collapsed — `single_flight` means twenty parallel copies
of the same prompt produce one call, not twenty. That is a different problem from caching: a
cache stops the second call once the first has *finished*.

Cache hits are recorded in the trace at $0 rather than dropped, so a replayed run still shows
what it would have cost.

## Cost

Every LLM and API call produces a `Cost` that says where its number came from: `reported`
(OpenRouter's `usage.cost`, the normal case), `estimated` (from the `/models` price table, only
when a response arrives without one), `fixed` (a per-call price an API spec declares), or
`free`.

`AgentConfig.max_usd` and `max_calls` are checked before each step and after each call. Over the
ceiling, the run stops — refused, not queued, matching the arena runtime's Governor.

## Tests

```bash
python tests/test_end_to_end.py     # the runtime, against a fake OpenRouter
python tests/test_examples.py       # the sample agents, against a fake OpenRouter and SearchApi
python tests/test_server.py         # the backend's SSE routes, blinding and voting
```

All three use `httpx.MockTransport`, so they need no key and no network. `make test` runs the set.

For a version you can click on rather than read the output of, `tests/fake_openrouter.py` is a
local stand-in that speaks the real protocols over HTTP — see the web app section of the
[root README](../README.md#try-it-with-no-keys-and-no-spend).
