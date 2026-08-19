"""``rsi_arena.api`` — specs as data, auth, the registry and the client."""

from __future__ import annotations

import httpx
import pytest

from rsi_arena.api import (
    APIClient,
    APIError,
    APISpec,
    BearerAuth,
    Endpoint,
    HeaderAuth,
    MissingCredential,
    NoAuth,
    Param,
    QueryAuth,
    Registry,
)
from rsi_arena.core.ratelimit import RateLimit
from rsi_arena.core.retry import RetryPolicy


@pytest.fixture
def demo_spec() -> APISpec:
    return APISpec(
        name="demo",
        base_url="https://demo.test",
        auth=NoAuth(),
        rate_limit=RateLimit(per_second=1000),
        cost_per_call=0.004,
        endpoints=[
            Endpoint(
                "search",
                "/search",
                description="Search the demo index.",
                params=(Param("q", "The query.", required=True), Param("n", type="integer")),
                parse=lambda d: d["organic_results"],
            ),
            Endpoint(
                "detail",
                "/items/{item_id}",
                params=(Param("item_id", required=True),),
            ),
        ],
    )


# --- declarations -----------------------------------------------------------


def test_param_becomes_json_schema():
    schema = Param("q", "The query.", required=True, enum=["a", "b"]).to_schema()
    assert schema == {"type": "string", "description": "The query.", "enum": ["a", "b"]}


def test_endpoint_schema_lists_only_required_params_as_required(demo_spec: APISpec):
    schema = demo_spec.endpoint("search").schema()
    assert schema["required"] == ["q"]
    assert set(schema["properties"]) == {"q", "n"}
    assert schema["additionalProperties"] is False


def test_an_unknown_endpoint_names_the_ones_that_exist(demo_spec: APISpec):
    with pytest.raises(KeyError) as exc:
        demo_spec.endpoint("nope")
    assert "search" in str(exc.value) and "detail" in str(exc.value)


def test_spec_serialises_to_data(demo_spec: APISpec):
    blob = demo_spec.to_dict()
    assert blob["name"] == "demo" and blob["cost_per_call"] == 0.004
    assert set(blob["endpoints"]) == {"search", "detail"}


# --- auth -------------------------------------------------------------------


def test_bearer_header(monkeypatch):
    monkeypatch.setenv("DEMO_KEY", "secret")
    headers: dict[str, str] = {}
    BearerAuth(env_var="DEMO_KEY").apply("demo", headers, {})
    assert headers["Authorization"] == "Bearer secret"


def test_custom_header(monkeypatch):
    monkeypatch.setenv("DEMO_KEY", "secret")
    headers: dict[str, str] = {}
    HeaderAuth(env_var="DEMO_KEY", header="X-Token", prefix="tok_").apply("demo", headers, {})
    assert headers["X-Token"] == "tok_secret"


def test_query_auth(monkeypatch):
    monkeypatch.setenv("DEMO_KEY", "secret")
    params: dict[str, str] = {}
    QueryAuth(env_var="DEMO_KEY", param="api_key").apply("demo", {}, params)
    assert params["api_key"] == "secret"


def test_a_missing_credential_names_the_variable(monkeypatch):
    monkeypatch.delenv("DEMO_KEY", raising=False)
    with pytest.raises(MissingCredential) as exc:
        BearerAuth(env_var="DEMO_KEY").apply("demo", {}, {})
    assert "DEMO_KEY" in str(exc.value)


def test_no_auth_needs_nothing():
    headers: dict[str, str] = {}
    NoAuth().apply("demo", headers, {})
    assert headers == {}


# --- registry ---------------------------------------------------------------


def test_register_and_get(demo_spec: APISpec):
    registry = Registry()
    assert registry.register(demo_spec) is demo_spec
    assert registry.get("demo") is demo_spec
    assert registry.names() == ["demo"]


def test_registering_twice_needs_replace(demo_spec: APISpec):
    registry = Registry()
    registry.register(demo_spec)
    with pytest.raises(ValueError):
        registry.register(demo_spec)
    registry.register(demo_spec, replace=True)


def test_an_unknown_api_lists_what_is_registered(demo_spec: APISpec):
    registry = Registry()
    registry.register(demo_spec)
    with pytest.raises(KeyError) as exc:
        registry.get("nope")
    assert "demo" in str(exc.value)


def test_the_builtin_searchapi_spec_is_registered():
    from rsi_arena import get_api
    from rsi_arena.api.apis import SEARCHAPI  # noqa: F401 - importing is what registers it

    assert get_api("searchapi").endpoint("search").name == "search"


# --- calling ----------------------------------------------------------------


async def test_a_call_parses_and_prices(api: APIClient, demo_spec: APISpec):
    response = await api.call(demo_spec, "search", q="weather")
    assert response.data[0]["title"] == "T", "parse should have unwrapped organic_results"
    assert response.cost.usd == 0.004 and response.cost.source == "fixed"
    assert response.status == 200


async def test_the_second_identical_call_is_free(api: APIClient, demo_spec: APISpec, fake):
    await api.call(demo_spec, "search", q="weather")
    again = await api.call(demo_spec, "search", q="weather")
    assert fake.demo_calls == 1
    assert again.cached and again.cost.usd == 0.0


async def test_a_different_query_is_a_different_call(api: APIClient, demo_spec: APISpec, fake):
    await api.call(demo_spec, "search", q="one")
    await api.call(demo_spec, "search", q="two")
    assert fake.demo_calls == 2


async def test_path_placeholders_are_filled_and_dropped_from_the_query(
    api: APIClient, demo_spec: APISpec
):
    response = await api.call(demo_spec, "detail", item_id="abc")
    assert response.url.endswith("/items/abc"), response.url
    assert "item_id=" not in response.url


async def test_a_missing_required_parameter_raises_before_any_request(
    api: APIClient, demo_spec: APISpec, fake
):
    with pytest.raises(ValueError) as exc:
        await api.call(demo_spec, "search")
    assert "q" in str(exc.value)
    assert fake.demo_calls == 0


async def test_call_many_runs_a_batch(api: APIClient, demo_spec: APISpec):
    responses = await api.call_many(
        [{"api": demo_spec, "endpoint": "search", "q": f"query {i}"} for i in range(3)]
    )
    assert len(responses) == 3 and all(r.status == 200 for r in responses)


async def test_a_4xx_becomes_an_api_error_and_is_not_retried(cache):
    calls = {"n": 0}

    def bad_request(_request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        return httpx.Response(400, text="bad query")

    spec = APISpec(name="broken", base_url="https://broken.test", auth=NoAuth(),
                   rate_limit=RateLimit(per_second=1000),
                   retry=RetryPolicy(max_attempts=3, initial_backoff=0.0, jitter=False),
                   endpoints=[Endpoint("go", "/go")])
    client = APIClient(cache=cache,
                       http_client=httpx.AsyncClient(transport=httpx.MockTransport(bad_request)))
    with pytest.raises(APIError) as exc:
        await client.call(spec, "go")
    assert exc.value.status == 400 and not exc.value.retryable
    assert calls["n"] == 1


async def test_a_429_is_retried_then_succeeds(cache):
    calls = {"n": 0}

    def flaky(_request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        if calls["n"] < 3:
            return httpx.Response(429, headers={"Retry-After": "0"}, text="slow down")
        return httpx.Response(200, json={"ok": True})

    spec = APISpec(name="flaky", base_url="https://flaky.test", auth=NoAuth(),
                   rate_limit=RateLimit(per_second=1000),
                   retry=RetryPolicy(max_attempts=4, initial_backoff=0.0, jitter=False),
                   endpoints=[Endpoint("go", "/go")])
    client = APIClient(cache=cache,
                       http_client=httpx.AsyncClient(transport=httpx.MockTransport(flaky)))
    response = await client.call(spec, "go")
    assert response.data == {"ok": True} and response.attempts == 3


async def test_limiters_are_per_api_and_reused(api: APIClient, demo_spec: APISpec):
    await api.call(demo_spec, "search", q="a")
    first = api._limiter(demo_spec)
    await api.call(demo_spec, "search", q="b")
    assert api._limiter(demo_spec) is first, "one limiter per API, not one per call"


async def test_the_client_closes_only_what_it_owns(cache, fake):
    borrowed = fake.client()
    client = APIClient(cache=cache, http_client=borrowed)
    await client.close()
    assert not borrowed.is_closed
    await borrowed.aclose()
