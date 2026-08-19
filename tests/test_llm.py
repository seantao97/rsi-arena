"""``rsi_arena.llm`` — the client, the wire format, and everything on top of it.

Retries, caching, single-flight, structured outputs, streaming, cost and web
search, all against :mod:`tests.fakes` through ``httpx.MockTransport``.
"""

from __future__ import annotations

import asyncio
import json

import pytest
from pydantic import BaseModel

from rsi_arena.core.cache import MemoryCache
from rsi_arena.core.ratelimit import RateLimit
from rsi_arena.llm import (
    Completion,
    LLMClient,
    LLMConfig,
    Message,
    OpenRouterError,
    WebSearch,
    parse_json_loose,
    strict_schema,
    to_messages,
)
from tests.fakes import Fake


class Answer(BaseModel):
    answer: str
    confidence: float


# --- wire types -------------------------------------------------------------


def test_to_messages_accepts_a_string_a_message_or_a_list():
    assert to_messages("hi")[0].content == "hi"
    assert to_messages(Message(role="user", content="hi"))[0].role == "user"
    assert len(to_messages([{"role": "user", "content": "a"}, {"role": "assistant", "content": "b"}])) == 2


def test_system_is_prepended_but_never_duplicated():
    assert to_messages("hi", "be brief")[0].role == "system"
    already = [Message(role="system", content="own"), Message(role="user", content="hi")]
    assert sum(m.role == "system" for m in to_messages(already, "be brief")) == 1


def test_tool_call_arguments_survive_bad_json():
    from rsi_arena.llm.messages import FunctionCall

    assert FunctionCall(name="f", arguments='{"a": 1}').parsed_arguments() == {"a": 1}
    assert FunctionCall(name="f", arguments="not json").parsed_arguments() == {}


def test_parse_json_loose_handles_a_fenced_block_and_prose():
    assert parse_json_loose('```json\n{"a": 1}\n```') == {"a": 1}
    assert parse_json_loose('Here you go: {"a": 1} — hope that helps') == {"a": 1}
    with pytest.raises(ValueError):
        parse_json_loose("no json at all")


def test_strict_schema_closes_every_object():
    schema = strict_schema(Answer)
    assert schema["additionalProperties"] is False
    assert set(schema["required"]) == {"answer", "confidence"}


def test_web_search_becomes_an_explicit_plugin():
    plugin = WebSearch(max_results=3, engine="exa").to_plugin()
    assert plugin == {"id": "web", "max_results": 3, "engine": "exa"}


# --- request assembly -------------------------------------------------------


def test_build_body_carries_config_and_fallbacks(llm: LLMClient):
    config = LLMConfig(model="a", fallback_models=["b"], temperature=0.2, max_tokens=100)
    body = llm.build_body([Message(role="user", content="hi")], config)
    assert body["model"] == "a" and body["models"] == ["a", "b"]
    assert body["temperature"] == 0.2 and body["max_tokens"] == 100


def test_a_schema_forces_provider_require_parameters(llm: LLMClient):
    # Support is per-endpoint, not per-model: without this the same model on a
    # different provider silently ignores the schema.
    body = llm.build_body(
        [Message(role="user", content="hi")],
        LLMConfig(model="a"),
        response_format={"type": "json_schema", "json_schema": {"name": "x"}},
    )
    assert body["provider"]["require_parameters"] is True


def test_a_missing_key_fails_with_the_fix_in_the_message(monkeypatch):
    # Better here than as an opaque 401 from the provider twenty seconds in.
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    with pytest.raises(OpenRouterError) as exc:
        LLMClient()._headers()
    assert "OPENROUTER_API_KEY" in str(exc.value)


# --- calling ----------------------------------------------------------------


async def test_a_plain_completion_carries_text_cost_and_citations(llm: LLMClient):
    completion = await llm.complete("hello")
    assert "ECB" in completion.text
    assert completion.cost.usd == 0.0012 and completion.cost.source == "reported"
    assert completion.usage.prompt_tokens == 100
    assert completion.citations[0].url == "https://ecb.europa.eu/x"


async def test_retries_a_429_then_succeeds(fake: Fake, llm: LLMClient):
    fake.fail_left = 2
    completion = await llm.complete("hello")
    assert completion.attempts == 3, "two retries then success"
    assert fake.llm_calls == 3


async def test_a_permanent_error_is_not_retried(http, cache):
    import httpx

    calls = {"n": 0}

    def out_of_credits(_request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        return httpx.Response(402, json={"error": {"code": 402, "message": "no credits"}})

    client = LLMClient(api_key="test", cache=cache, auto_pricing=False,
                       http_client=httpx.AsyncClient(transport=httpx.MockTransport(out_of_credits)),
                       rate_limit=RateLimit(per_second=1000))
    with pytest.raises(OpenRouterError) as exc:
        await client.complete("hello")
    assert exc.value.status == 402 and not exc.value.retryable
    assert calls["n"] == 1, "no amount of waiting adds credits"


async def test_identical_calls_are_cached(fake: Fake, llm: LLMClient):
    first = await llm.complete("same")
    second = await llm.complete("same")
    assert fake.llm_calls == 1
    assert second.cached and second.cost.usd == 0.0
    assert second.text == first.text


async def test_a_different_prompt_misses_the_cache(fake: Fake, llm: LLMClient):
    await llm.complete("one")
    await llm.complete("two")
    assert fake.llm_calls == 2


async def test_cache_can_be_turned_off_per_call(fake: Fake, llm: LLMClient):
    await llm.complete("same")
    await llm.complete("same", cache=False)
    assert fake.llm_calls == 2


async def test_twenty_concurrent_identical_calls_make_one_wire_call(fake: Fake, llm: LLMClient):
    results = await llm.complete_many(["same prompt"] * 20)
    assert fake.llm_calls == 1
    assert sum(r.cached for r in results) == 19
    assert len({r.text for r in results}) == 1


async def test_structured_output_validates_into_a_model(llm: LLMClient):
    parsed, completion = await llm.structured("what is it?", Answer)
    assert parsed.answer == "42" and parsed.confidence == 0.9
    assert completion.parsed == {"answer": "42", "confidence": 0.9}


async def test_a_raw_schema_dict_also_works(llm: LLMClient):
    completion = await llm.complete("q", schema={
        "type": "json_schema",
        "json_schema": {"name": "stop_check", "strict": True, "schema": {"type": "object"}},
    })
    assert json.loads(completion.text)["done"] is True


async def test_tool_calls_come_back_parsed(llm: LLMClient, toolbox):
    completion = await llm.complete("count them", tools=toolbox.schemas())
    call = completion.tool_calls[0]
    assert call.function.name == "word_count"
    assert "text" in call.function.parsed_arguments()


async def test_streaming_yields_deltas_then_one_done(llm: LLMClient):
    pieces, final = [], None
    async for event in llm.stream("stream me"):
        if event.type == "delta":
            pieces.append(event.text)
        elif event.type == "done":
            final = event.completion
    assert "".join(pieces) and isinstance(final, Completion)
    assert final.cost.usd == 0.0012, "usage arrives on the final chunk"


async def test_a_replayed_stream_comes_from_the_cache(fake: Fake, llm: LLMClient):
    first = [e.text async for e in llm.stream("same") if e.type == "delta"]
    second = [e.text async for e in llm.stream("same") if e.type == "delta"]
    assert "".join(first) == "".join(second)
    assert fake.llm_calls == 1


async def test_web_search_sends_the_plugin(fake: Fake, llm: LLMClient):
    await llm.complete("q", web_search=WebSearch(max_results=3))
    assert fake.bodies[-1]["plugins"] == [{"id": "web", "max_results": 3}]


async def test_complete_many_runs_concurrently(fake: Fake, llm: LLMClient):
    results = await llm.complete_many([f"prompt {i}" for i in range(8)])
    assert len(results) == 8 and fake.llm_calls == 8


async def test_list_models_returns_the_catalogue(fake: Fake, llm: LLMClient):
    models = await llm.list_models()
    assert {m["id"] for m in models} == {"fake/sonnet", "fake/haiku"}
    assert fake.models_calls == 1


async def test_cost_is_estimated_when_the_response_omits_it(http, cache):
    import httpx

    def no_cost(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/models"):
            return httpx.Response(200, json={"data": [
                {"id": "fake/sonnet", "pricing": {"prompt": "0.000003", "completion": "0.000015"}}
            ]})
        return httpx.Response(200, json={
            "id": "g", "model": "fake/sonnet",
            "usage": {"prompt_tokens": 1000, "completion_tokens": 100, "total_tokens": 1100},
            "choices": [{"finish_reason": "stop",
                         "message": {"role": "assistant", "content": "hi"}}]})

    client = LLMClient(api_key="test", cache=cache, auto_pricing=True,
                       http_client=httpx.AsyncClient(transport=httpx.MockTransport(no_cost)),
                       config=LLMConfig(model="fake/sonnet"),
                       rate_limit=RateLimit(per_second=1000))
    completion = await client.complete("hi")
    assert completion.cost.source == "estimated"
    assert completion.cost.usd == pytest.approx(0.003 + 0.0015)


async def test_the_client_closes_only_what_it_owns(cache: MemoryCache):
    borrowed = Fake().client()
    client = LLMClient(api_key="test", http_client=borrowed, cache=cache)
    await client.close()
    assert not borrowed.is_closed, "a client we did not create is not ours to close"
    await borrowed.aclose()


async def test_used_as_a_context_manager():
    async with LLMClient(api_key="test", http_client=Fake().client()) as client:
        assert isinstance(client, LLMClient)


async def test_a_shared_client_shares_its_rate_limit(fake: Fake, cache):
    # The reason the backend keeps one client: two agents in a battle should
    # not each get their own budget.
    client = LLMClient(api_key="test", http_client=fake.client(), cache=cache,
                       auto_pricing=False, rate_limit=RateLimit(per_second=1000, concurrency=1))
    await asyncio.gather(*(client.complete(f"q{i}") for i in range(4)))
    assert fake.llm_calls == 4
    await client.close()
