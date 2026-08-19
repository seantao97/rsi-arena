"""Run the sample agents against a fake OpenRouter and a fake SearchApi.

Catches the failure mode that only shows up on a real run and costs money to
find: a ``{{placeholder}}`` that never resolves, a loop condition that names
something not in state, a schema a step cannot fill.
"""

from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from examples import smoke_test, web_research  # noqa: E402
from rsi_arena import APIClient, AgentConfig, LLMClient, LLMConfig, MemoryCache, RateLimit  # noqa: E402

USAGE = {"prompt_tokens": 800, "completion_tokens": 150, "total_tokens": 950, "cost": 0.0031}
CALLS = {"search": 0}


def _reply(model: str, content: str) -> httpx.Response:
    return httpx.Response(200, json={
        "id": "gen", "model": model, "usage": USAGE,
        "choices": [{"finish_reason": "stop",
                     "message": {"role": "assistant", "content": content}}]})


def openrouter(request: httpx.Request) -> httpx.Response:
    """Answer a chat completion, in SSE form when the request asked to stream."""
    body = json.loads(request.content)
    response = _answer(body)
    return _as_sse(response, body) if body.get("stream") else response


def _as_sse(response: httpx.Response, body: dict) -> httpx.Response:
    """Re-cut a finished response as deltas, the way OpenRouter sends them."""
    payload = response.json()
    content = payload["choices"][0]["message"].get("content") or ""
    pieces = [content[i:i + 12] for i in range(0, len(content), 12)] or [""]
    chunks = [{"id": payload["id"], "model": payload["model"],
               "choices": [{"delta": {"content": piece}}]} for piece in pieces]
    chunks.append({"id": payload["id"], "model": payload["model"], "usage": payload["usage"],
                   "choices": [{"delta": {}, "finish_reason": "stop"}]})
    text = "".join(f"data: {json.dumps(c)}\n\n" for c in chunks) + "data: [DONE]\n\n"
    return httpx.Response(200, text=text, headers={"content-type": "text/event-stream"})


def _answer(body: dict) -> httpx.Response:
    model = body["model"]
    fmt = body.get("response_format")

    if body.get("tools") and not any(m["role"] == "tool" for m in body["messages"]):
        return httpx.Response(200, json={
            "id": "gen", "model": model, "usage": USAGE,
            "choices": [{"finish_reason": "tool_calls", "message": {
                "role": "assistant", "content": "",
                "tool_calls": [{"id": "c1", "type": "function", "function": {
                    "name": "search", "arguments": json.dumps({"q": "ecb july 2026"})}}]}}]})

    if fmt:
        name = fmt["json_schema"]["name"]
        payloads = {
            "plan_queries": {"queries": ["ecb rate decision july 2026", "ecb press release"],
                             "what_would_settle_it": "The ECB press release."},
            "take_notes": {"claims": [{"claim": "The ECB held rates.",
                                       "url": "https://ecb.europa.eu/x", "date": "2026-07-24",
                                       "confidence": 0.9}],
                           "still_missing": "nothing", "sufficient": True},
            "stop_check": {"done": True, "reason": "sourced"},
            "decompose": {"expression": "2.7e6 / 4 * 0.02", "weakest_factor": "tuners per piano",
                          "factors": [{"name": "population", "value": "2.7e6",
                                       "justification": "Chicago city population."}]},
        }
        return _reply(model, json.dumps(payloads.get(name, {})))

    return _reply(model, "The ECB held rates at its July 2026 meeting "
                         "(https://ecb.europa.eu/x). This changes if the press release is revised.")


def searchapi(request: httpx.Request) -> httpx.Response:
    CALLS["search"] += 1
    return httpx.Response(200, json={
        "search_parameters": {"q": request.url.params.get("q")},
        "search_information": {"total_results": 12},
        "organic_results": [{"position": 1, "title": "ECB press release",
                             "link": "https://ecb.europa.eu/x", "date": "24 Jul 2026",
                             "snippet": "The Governing Council decided to hold rates."}],
        "answer_box": {"type": "organic", "answer": "Held", "link": "https://ecb.europa.eu/x"},
    })


def router(request: httpx.Request) -> httpx.Response:
    return openrouter(request) if "openrouter" in request.url.host else searchapi(request)


async def main() -> None:
    import os

    os.environ.setdefault("SEARCHAPI_API_KEY", "test-key")
    transport = httpx.MockTransport(router)
    http = httpx.AsyncClient(transport=transport)
    cache = MemoryCache()
    config = AgentConfig(default_model="test/model", max_usd=2.0)
    llm = LLMClient(api_key="test", cache=cache, http_client=http, auto_pricing=False,
                    config=config.to_llm_config(), rate_limit=RateLimit(per_second=1000))
    api = APIClient(cache=cache, http_client=http)
    tools = web_research.search_tools(api)

    agents = [
        web_research.pipeline_agent(config, tools),
        web_research.freeform_agent(config, tools),
        web_research.plugin_agent(config),
        smoke_test.build_agent("test/model"),
    ]
    questions = ["Did the ECB cut rates in July 2026?"] * 3 + ["How many piano tuners in Chicago?"]

    results = await asyncio.gather(*(a.run(q, llm=llm) for a, q in zip(agents, questions)))
    for agent, result in zip(agents, results):
        assert result.ok, f"{agent.name} failed: {result.error}\n{result.trace.render()}"
        assert result.output, f"{agent.name} produced nothing"
        print(f"{agent.name:24s} ok  ${result.cost_usd:.4f}  "
              f"{result.trace.costs.calls} calls  {len(list(result.trace.spans()))} spans")

    pipeline = results[0]
    assert pipeline.state["evidence"], "loop collected nothing"
    assert CALLS["search"] >= 1, "the pipeline never searched"
    print(f"\nsearch API called {CALLS['search']}x (rest served from cache)")
    print(pipeline.trace.render())
    await http.aclose()
    print("\nall sample agents ran")


if __name__ == "__main__":
    asyncio.run(main())
