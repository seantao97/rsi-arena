"""``rsi_arena.agent.tools`` — the primitives, however they are built."""

from __future__ import annotations

import json

import pytest

from rsi_arena.agent.tools import Tool, ToolOutput, Toolbox, api_tool
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


# --- declared -----------------------------------------------------------------


class Double(Tool):
    name = "double"
    description = "Double it."
    parameters = {"type": "object", "properties": {"n": {"type": "integer"}},
                  "required": ["n"]}

    def get_tool_output(self, input):
        return ToolOutput(response=str(input["n"] * 2),
                          raw_output={"value": input["n"] * 2})


def test_a_tool_declares_its_own_schema():
    lookup = Double()
    assert lookup.name == "double" and lookup.description == "Double it."
    schema = lookup.parameters
    assert schema["properties"]["n"]["type"] == "integer"
    assert schema["required"] == ["n"]
    # The declared schemas also come back as JSON, which is what a config
    # written by a model reads.
    assert json.loads(lookup.get_tool_input_schema()) == schema
    assert json.loads(lookup.get_tool_output_schema())["type"] == "object"


def test_the_version_reaches_the_model():
    """A harness names a tool and the revision it was written against, so a
    later change of output shape does not silently alter an older harness."""
    class V2(Double):
        version = 2

    assert V2().described().endswith("(v2)")
    assert "(v2)" in V2().to_openai_schema()["function"]["description"]


def test_openai_schema_shape(word_count):
    schema = word_count.to_openai_schema()
    assert schema["type"] == "function" and schema["function"]["name"] == "word_count"


async def test_calling_a_tool_returns_its_output(word_count):
    result = await word_count(text="one two three")
    assert result.ok and result.output == {"count": 3} and result.cost.usd == 0.0


async def test_the_model_reads_the_sentence_and_code_reads_the_structure():
    """One body, two callers. Sending the payload to the model as well would
    spend tokens on data the sentence already summarises."""
    double = Double()
    result = await double(n=4)
    assert result.output == {"value": 8}, "a pipeline step reads the structure"
    assert result.for_model() == "8", "the model reads the sentence"
    assert (await double.aget_tool_output(n=4)).raw_output == {"value": 8}


async def test_a_failing_tool_is_information_not_a_crash():
    class Explode(Tool):
        name = "explode"
        description = "Always fails."
        parameters = {"type": "object", "properties": {"x": {"type": "string"}}}

        def get_tool_output(self, input):
            raise ValueError("bad argument")

    result = await Explode()(x="a")
    assert not result.ok and "ValueError: bad argument" in (result.error or "")
    # The model reads this and tries different arguments; only the budget stops it.
    assert result.for_model().startswith("ERROR:")


async def test_a_tool_can_refuse_without_raising():
    """A refusal is a sentence the model can act on; a traceback is not."""
    class Refuses(Tool):
        name = "refuses"
        description = "Declines."
        parameters = {"type": "object", "properties": {}}

        def get_tool_output(self, input):
            return ToolOutput.failed("no market at that price")

    out = Refuses().get_tool_output({})
    assert not out.ok and out.response == "unavailable: no market at that price"
    assert not (await Refuses()()).ok


async def test_a_flat_cost_is_charged_per_call():
    class Priced(Double):
        name = "priced"
        cost_usd = 0.01

    assert (await Priced()(n=1)).cost.usd == 0.01


async def test_a_result_that_carries_its_own_cost_is_not_double_charged():
    class Carrier:
        data = "payload"
        cost = Cost(usd=0.004, source="fixed")
        cached = False

    class Carries(Tool):
        name = "carrier"
        description = "d"
        cost_usd = 99.0

        def get_tool_output(self, input):
            return ToolOutput(response=Carrier())   # type: ignore[arg-type]

    result = await Carries()()
    assert result.output == "payload" and result.cost.usd == 0.004


async def test_a_traced_call_produces_a_span_and_a_cost(word_count):
    tracer = Tracer(agent="a")
    await word_count(tracer=tracer, text="one two")
    span = tracer.root.children[0]
    assert span.name == "word_count" and span.kind == "tool" and span.status == "ok"


async def test_a_traced_failure_marks_the_span_error():
    class Explode(Tool):
        name = "explode"
        description = "Fails."
        parameters = {"type": "object", "properties": {"x": {"type": "string"}}}

        def get_tool_output(self, input):
            raise ValueError("no")

    tracer = Tracer()
    await Explode()(tracer=tracer, x="a")
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
    class Other(Double):
        name = "other"
        description = "Other."

    toolbox.add(Other())
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
    assert [r.output for r in results] == [{"count": 2}, {"count": 3}]
