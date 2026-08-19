"""``rsi_arena.agent.tools`` — the primitives, however they are built."""

from __future__ import annotations

from typing import Annotated

import pytest

from rsi_arena.agent.tools import Tool, Toolbox, api_tool, tool
from rsi_arena.api import APIClient, APISpec, Endpoint, NoAuth, Param
from rsi_arena.core.costs import Cost
from rsi_arena.core.ratelimit import RateLimit
from rsi_arena.core.trace import Tracer


@pytest.fixture
def demo_spec() -> APISpec:
    return APISpec(
        name="demo", base_url="https://demo.test", auth=NoAuth(),
        rate_limit=RateLimit(per_second=1000), cost_per_call=0.004,
        endpoints=[Endpoint("search", "/search",
                            params=(Param("q", "The query.", required=True),
                                    Param("country", "Pin this.", required=True)),
                            parse=lambda d: d["organic_results"])],
    )


# --- from a function --------------------------------------------------------


def test_a_schema_is_built_from_the_signature():
    @tool
    def lookup(city: Annotated[str, "Which city."], days: int = 3) -> str:
        """Look something up."""
        return city

    assert lookup.name == "lookup" and lookup.description == "Look something up."
    schema = lookup.parameters
    assert schema["properties"]["city"]["description"] == "Which city."
    assert schema["properties"]["days"]["type"] == "integer"
    assert schema["required"] == ["city"], "a defaulted parameter is not required"


def test_the_decorator_takes_arguments_too():
    @tool(name="wc", description="Counts.", cost_usd=0.01)
    def word_count(text: str) -> int:
        return len(text.split())

    assert word_count.name == "wc" and word_count.cost_usd == 0.01


def test_openai_schema_shape(word_count):
    schema = word_count.to_openai_schema()
    assert schema["type"] == "function" and schema["function"]["name"] == "word_count"


async def test_calling_a_tool_returns_its_output(word_count):
    result = await word_count(text="one two three")
    assert result.ok and result.output == 3 and result.cost.usd == 0.0


async def test_a_sync_function_works_too():
    @tool
    def double(n: int) -> int:
        """Double it."""
        return n * 2

    assert (await double(n=4)).output == 8


async def test_a_failing_tool_is_information_not_a_crash():
    @tool
    def explode(x: str) -> str:
        """Always fails."""
        raise ValueError("bad argument")

    result = await explode(x="a")
    assert not result.ok and "ValueError: bad argument" in (result.error or "")
    # The model reads this and tries different arguments; only the budget stops it.
    assert result.for_model().startswith("ERROR:")


async def test_a_flat_cost_is_charged_per_call():
    @tool(cost_usd=0.01)
    def priced(x: str) -> str:
        """Costs money."""
        return x

    assert (await priced(x="a")).cost.usd == 0.01


async def test_a_result_that_carries_its_own_cost_is_not_double_charged():
    class Carrier:
        data = "payload"
        cost = Cost(usd=0.004, source="fixed")
        cached = False

    tool_obj = Tool("carrier", "d", {"type": "object"}, lambda: Carrier(), cost_usd=99.0)
    result = await tool_obj()
    assert result.output == "payload" and result.cost.usd == 0.004


async def test_a_traced_call_produces_a_span_and_a_cost(word_count):
    tracer = Tracer(agent="a")
    await word_count(tracer=tracer, text="one two")
    span = tracer.root.children[0]
    assert span.name == "word_count" and span.kind == "tool" and span.status == "ok"


async def test_a_traced_failure_marks_the_span_error():
    @tool
    def explode(x: str) -> str:
        """Fails."""
        raise ValueError("no")

    tracer = Tracer()
    await explode(tracer=tracer, x="a")
    assert tracer.root.children[0].status == "error"


def test_for_model_serialises_non_strings(word_count):
    from rsi_arena.agent.tools import ToolResult

    assert ToolResult(name="t", output={"a": 1}).for_model() == '{"a": 1}'
    assert ToolResult(name="t", output="plain").for_model() == "plain"


# --- from an API endpoint ---------------------------------------------------


async def test_an_api_endpoint_becomes_a_tool(demo_spec: APISpec, api: APIClient):
    search = api_tool(demo_spec, "search", client=api, name="search")
    assert search.name == "search"
    result = await search(q="weather", country="us")
    assert result.ok and result.output[0]["title"] == "T"
    assert result.cost.usd == 0.004, "the API's own cost, not a flat tool cost"


def test_pinned_parameters_are_hidden_from_the_model(demo_spec: APISpec, api: APIClient):
    # Cheaper and safer than asking the model politely not to change them.
    search = api_tool(demo_spec, "search", client=api, fixed={"country": "us"})
    assert "country" not in search.parameters["properties"]
    assert search.parameters["required"] == ["q"]


def test_api_tool_can_name_a_registered_api(demo_spec: APISpec):
    from rsi_arena.api import registry as global_registry

    global_registry.register(demo_spec, replace=True)
    try:
        assert api_tool("demo", "search").name == "demo_search"
    finally:
        global_registry._specs.pop("demo", None)


# --- the toolbox ------------------------------------------------------------


def test_names_and_membership(toolbox: Toolbox, word_count):
    assert toolbox.names() == ["word_count"]
    assert "word_count" in toolbox and len(toolbox) == 1
    assert toolbox.get("word_count") is word_count


def test_an_unknown_tool_lists_what_is_there(toolbox: Toolbox):
    with pytest.raises(KeyError) as exc:
        toolbox.get("nope")
    assert "word_count" in str(exc.value)


def test_schemas_can_be_narrowed(toolbox: Toolbox):
    @tool
    def other(x: str) -> str:
        """Other."""
        return x

    toolbox.add(other)
    assert len(toolbox.schemas()) == 2
    assert [s["function"]["name"] for s in toolbox.schemas(["other"])] == ["other"]


def test_describe_is_a_prompt_catalogue(toolbox: Toolbox):
    assert toolbox.describe() == "- word_count: Count words in a string."


def test_add_api_registers_an_endpoint_as_a_tool(demo_spec: APISpec, api: APIClient):
    box = Toolbox()
    box.add_api(demo_spec, "search", client=api, name="search")
    assert box.names() == ["search"]


async def test_call_many_runs_independent_calls_together(toolbox: Toolbox):
    results = await toolbox.call_many([("word_count", {"text": "a b"}),
                                       ("word_count", {"text": "a b c"})])
    assert [r.output for r in results] == [2, 3]
