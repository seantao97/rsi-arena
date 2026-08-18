"""End-to-end exercise against a fake OpenRouter and a fake API.

No network and no key: ``httpx.MockTransport`` stands in for both, which is
also how you would test your own agents. Run with ``python -m tests.test_end_to_end``
or ``pytest tests/``.
"""

from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from rsi_arena import (  # noqa: E402
    Agent, AgentConfig, APIClient, APISpec, Endpoint, LLMClient, LLMConfig, LoopStep,
    MemoryCache, Param, Plan, PromptStep, RateLimit, Toolbox, ToolStep, tool,
)
from rsi_arena.api import NoAuth, Registry  # noqa: E402
from rsi_arena.costs import BudgetExceeded  # noqa: E402

CALLS = {"llm": 0, "api": 0, "fail_left": 2}


def openrouter_handler(request: httpx.Request) -> httpx.Response:
    """Fake chat/completions: retries twice, then answers, with tool calls."""
    CALLS["llm"] += 1
    if CALLS["fail_left"] > 0:
        CALLS["fail_left"] -= 1
        return httpx.Response(429, headers={"Retry-After": "0"},
                              json={"error": {"code": 429, "message": "slow down"}})

    body = json.loads(request.content)
    messages = body["messages"]
    usage = {"prompt_tokens": 100, "completion_tokens": 20, "total_tokens": 120,
             "cost": 0.0012, "cost_details": {"upstream_inference_cost": 0.001},
             "completion_tokens_details": {"reasoning_tokens": 5}}

    # First turn of a tool-enabled step: ask for the tool.
    if body.get("tools") and not any(m["role"] == "tool" for m in messages):
        return httpx.Response(200, json={
            "id": "gen-1", "model": body["model"], "usage": usage,
            "choices": [{"finish_reason": "tool_calls", "message": {
                "role": "assistant", "content": "",
                "tool_calls": [{"id": "call_1", "type": "function", "function": {
                    "name": "word_count", "arguments": json.dumps({"text": "one two three"})}}],
            }}]})

    if body.get("response_format"):
        name = body["response_format"]["json_schema"]["name"]
        payload = {"done": True, "reason": "enough"} if name == "stop_check" else \
                  {"answer": "42", "confidence": 0.9}
        return httpx.Response(200, json={
            "id": "gen-2", "model": body["model"], "usage": usage,
            "choices": [{"finish_reason": "stop",
                         "message": {"role": "assistant", "content": json.dumps(payload)}}]})

    return httpx.Response(200, json={
        "id": "gen-3", "model": body["model"], "usage": usage,
        "choices": [{"finish_reason": "stop", "message": {
            "role": "assistant",
            "content": f"answered {len(messages)} messages",
            "annotations": [{"type": "url_citation", "url_citation": {
                "url": "https://example.com/a", "title": "A", "content": "excerpt"}}],
        }}]})


def stream_handler(request: httpx.Request) -> httpx.Response:
    chunks = [
        {"id": "s1", "model": "m", "choices": [{"delta": {"content": "hel"}}]},
        {"id": "s1", "model": "m", "choices": [{"delta": {"content": "lo"}}]},
        {"id": "s1", "model": "m", "choices": [{"delta": {}, "finish_reason": "stop"}],
         "usage": {"prompt_tokens": 5, "completion_tokens": 2, "total_tokens": 7, "cost": 0.0003}},
    ]
    body = "".join(f"data: {json.dumps(c)}\n\n" for c in chunks) + "data: [DONE]\n\n"
    return httpx.Response(200, text=body, headers={"content-type": "text/event-stream"})


def demo_api_handler(request: httpx.Request) -> httpx.Response:
    CALLS["api"] += 1
    return httpx.Response(200, json={"query": request.url.params.get("q"),
                                     "organic_results": [{"position": 1, "title": "T",
                                                          "link": "https://x", "snippet": "S"}]})


def router(request: httpx.Request) -> httpx.Response:
    if "openrouter" in request.url.host:
        if b'"stream": true' in request.content or b'"stream":true' in request.content:
            return stream_handler(request)
        return openrouter_handler(request)
    return demo_api_handler(request)


@tool
async def word_count(text: str) -> int:
    """Count words in a string."""
    return len(text.split())


async def main() -> None:
    transport = httpx.MockTransport(router)
    http = httpx.AsyncClient(transport=transport)
    cache = MemoryCache()

    llm = LLMClient(api_key="test", cache=cache, http_client=http,
                    config=LLMConfig(model="test/model"), rate_limit=RateLimit(per_second=1000),
                    auto_pricing=False)

    # 1. retries, cost, citations
    first = await llm.complete("hello")
    assert first.text.startswith("answered"), first
    assert first.attempts == 3, f"expected 2 retries then success, got {first.attempts}"
    assert first.cost.usd == 0.0012 and first.cost.source == "reported"
    assert first.citations[0].url == "https://example.com/a"
    print(f"1. retries+cost           ok (attempts={first.attempts}, ${first.cost.usd})")

    # 2. caching + single flight: 20 identical parallel calls, one wire call
    before = CALLS["llm"]
    results = await llm.complete_many(["same prompt"] * 20)
    assert CALLS["llm"] - before == 1, f"expected 1 wire call, got {CALLS['llm'] - before}"
    assert sum(r.cached for r in results) == 19
    print(f"2. cache + single-flight  ok (1 wire call for 20 parallel, {cache.stats()})")

    # 3. structured outputs
    from pydantic import BaseModel

    class Answer(BaseModel):
        answer: str
        confidence: float

    parsed, completion = await llm.structured("what is it?", Answer)
    assert parsed.answer == "42" and parsed.confidence == 0.9
    print(f"3. structured output      ok ({parsed!r})")

    # 4. streaming
    pieces, final = [], None
    async for event in llm.stream("stream me"):
        if event.type == "delta":
            pieces.append(event.text)
        elif event.type == "done":
            final = event.completion
    assert "".join(pieces) == "hello" and final is not None and final.cost.usd == 0.0003
    replayed = [e.text async for e in llm.stream("stream me") if e.type == "delta"]
    assert "".join(replayed) == "hello"
    print(f"4. streaming (+replay)    ok ({''.join(pieces)!r}, ${final.cost.usd})")

    # 5. API registry: a whole API in one declaration
    local = Registry()
    spec = local.register(APISpec(
        name="demo", base_url="https://demo.test", auth=NoAuth(),
        rate_limit=RateLimit(per_second=1000), cost_per_call=0.004,
        endpoints=[Endpoint("search", "/search", params=(Param("q", required=True),),
                            parse=lambda d: d["organic_results"])]))
    api = APIClient(registry=local, cache=cache, http_client=http)
    hit = await api.call("demo", "search", q="weather")
    again = await api.call("demo", "search", q="weather")
    assert hit.data[0]["title"] == "T" and again.cached and again.cost.usd == 0.0
    print(f"5. api + cache + cost     ok (${hit.cost.usd} then ${again.cost.usd} cached)")

    # 6. full agent: tool step, tool-calling prompt step, loop with a condition
    tools = Toolbox([word_count])
    tools.add_api(spec, "search", client=api, name="search")
    agent = Agent(
        name="demo-agent",
        context="You are a careful research agent.",
        tools=tools,
        config=AgentConfig(default_model="test/model", max_usd=1.0, cache=True),
        plan=Plan(steps=[
            ToolStep(name="search", tool="search", args={"q": "{{question}}"}, output_key="hits"),
            LoopStep(name="refine", max_loops=3, until="len(hits) >= 1", steps=[
                PromptStep(name="think", prompt="Refine: {{question}}", output_key="thought"),
            ]),
            PromptStep(name="use_tools", prompt="Count words in {{thought}}", tools=["*"],
                       output_key="counted"),
            PromptStep(name="write", prompt="Write up {{hits}} and {{counted}}",
                       output_schema={"type": "object",
                                      "properties": {"answer": {"type": "string"},
                                                     "confidence": {"type": "number"}},
                                      "required": ["answer", "confidence"],
                                      "additionalProperties": False},
                       output_key="memo"),
        ]),
    )
    result = await agent.run("what is the weather", llm=llm)
    assert result.ok, result.error
    assert result.output == {"answer": "42", "confidence": 0.9}
    assert any(s.kind == "tool" and s.name == "word_count" for s in result.trace.spans())
    print("6. agent run              ok")
    print(result.trace.render())
    print("   costs:", json.dumps(result.trace.costs.summary()))

    # 7. serialisation round-trip
    blob = json.dumps(agent.to_dict())
    rebuilt = Agent.from_dict(json.loads(blob), tools=tools)
    assert json.dumps(rebuilt.to_dict()) == blob
    assert len(result.trace.to_json()) > 1000
    print("7. agent/trace JSON       ok (agent round-trips, trace serialises)")

    # 8. budget ceiling refuses rather than queues
    broke = Agent(name="broke", context="", tools=tools,
                  config=AgentConfig(default_model="test/model", max_usd=0.0001, cache=False),
                  plan=Plan(steps=[PromptStep(name="a", prompt="hi"),
                                   PromptStep(name="b", prompt="hi again")]))
    out = await broke.run("q", llm=llm)
    assert not out.ok and "BudgetExceeded" in (out.error or "")
    print(f"8. budget ceiling         ok ({out.error})")

    await http.aclose()
    print("\nall checks passed")


if __name__ == "__main__":
    asyncio.run(main())
