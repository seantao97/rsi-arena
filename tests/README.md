# Tests

```bash
make test           # or: pytest
make coverage       # the same, with a per-module report
pytest tests/test_max_spend.py -v          # one file
pytest -k "bailout or reserve"             # one idea
```

No key, no network, no cost. Every request goes through `httpx.MockTransport` to
[`fakes.py`](fakes.py), which speaks enough of OpenRouter and SearchApi to exercise the whole
stack — tool calls, `json_schema` responses, streaming SSE, citations, usage and cost, retries.

## Layout

| File | Covers |
|---|---|
| [`conftest.py`](conftest.py) | Fixtures: the fake backend, clients wired to it, a sample agent, the backend under `TestClient` |
| [`fakes.py`](fakes.py) | The fake backend itself. One per test, with counters and knobs |
| [`fake_openrouter.py`](fake_openrouter.py) | Not a test — the same fake served over real HTTP, for `make demo` |
| [`test_core_cache.py`](test_core_cache.py) | Keys, eviction, TTL, single-flight |
| [`test_core_costs.py`](test_core_costs.py) | The ledger, the ceiling, the bail-out reserve |
| [`test_core_ratelimit_retry.py`](test_core_ratelimit_retry.py) | Token bucket, concurrency, backoff, `Retry-After` |
| [`test_core_template.py`](test_core_template.py) | Rendering, and what the condition evaluator refuses |
| [`test_core_trace.py`](test_core_trace.py) | Spans, nesting, live events, serialisation |
| [`test_llm.py`](test_llm.py) | The OpenRouter client, end to end |
| [`test_api.py`](test_api.py) | Specs as data, auth, the registry, the client |
| [`test_agent_tools.py`](test_agent_tools.py) | Tools from a function, an API endpoint, or by hand |
| [`test_agent_steps.py`](test_agent_steps.py) | Prompt, tool and loop steps, and the plan |
| [`test_agent.py`](test_agent.py) | Running one, what comes back, error kinds, serialisation |
| [`test_max_spend.py`](test_max_spend.py) | `max_spend_mode` and `immediate_answer` |
| [`test_evals_scoring.py`](test_evals_scoring.py) | `Score`, the built-in scorers, the registry |
| [`test_evals.py`](test_evals.py) | `Eval`, `EvalSuite`, and the store |
| [`test_examples.py`](test_examples.py) | The sample agents and the eval example, end to end |
| [`test_server.py`](test_server.py) | Health, models, SSE runs, battles, votes, blinding |
| [`test_server_agents.py`](test_server_agents.py) | The non-streaming agent routes |
| [`test_server_evals.py`](test_server_evals.py) | The eval routes and stored results |
| [`test_server_events.py`](test_server_events.py) | Merging concurrent runs into one lossless SSE stream |
| [`test_server_store.py`](test_server_store.py) | The SQLite record of battles and votes |

## Conventions

**One behaviour per test, named as a sentence.** `test_a_blind_battle_leaks_no_identity_to_the_browser`
says what broke without opening the file.

**Async by default.** `asyncio_mode = "auto"` in `pyproject.toml`, so an `async def test_` needs
no decorator.

**A fresh fake per test.** The `fake` fixture is function-scoped and carries counters
(`llm_calls`, `search_calls`) and the request bodies it received (`bodies`) — which is how the
caching and single-flight tests prove anything, and how the prompt-assembly tests check what
actually went out on the wire.

**Rate limits lifted.** Every client fixture uses `per_second=1000`. The limiter is correct and
slow, and tests that are not about the limiter should not pay for it.

**The `client` fixture is synchronous.** `TestClient` runs the app on its own event loop; a
client built on the test's loop would mix the two. `MockTransport` dispatches synchronously, so
one built outside is safe to use inside.
