"""``rsi_arena.core.trace`` — spans, nesting, live events and serialisation."""

from __future__ import annotations

import asyncio
import json

import pytest

from rsi_arena.core.costs import BudgetExceeded, Cost, CostTracker
from rsi_arena.core.trace import Tracer, current_span


async def test_spans_nest_and_close_ok():
    tracer = Tracer(agent="a")
    async with tracer.span("outer", "step"):
        async with tracer.span("inner", "llm"):
            pass
    trace = tracer.finish(output="done")
    names = [s.name for s in trace.spans()]
    assert names == ["a", "outer", "inner"]
    assert all(s.status == "ok" for s in trace.spans())
    assert trace.root.children[0].children[0].name == "inner"


async def test_a_raising_body_marks_the_span_error_and_reraises():
    tracer = Tracer()
    with pytest.raises(ValueError):
        async with tracer.span("boom"):
            raise ValueError("no")
    span = tracer.root.children[0]
    assert span.status == "error" and "ValueError: no" in (span.error or "")


async def test_current_span_tracks_the_innermost_open_span():
    tracer = Tracer(agent="a")
    assert current_span() is tracer.root
    async with tracer.span("outer") as outer:
        assert current_span() is outer
    assert current_span() is tracer.root
    tracer.finish()


async def test_cost_lands_on_the_span_and_in_the_ledger():
    tracer = Tracer(costs=CostTracker())
    async with tracer.span("llm", "llm") as span:
        tracer.record_cost("llm", "model-a", Cost(usd=0.01), span)
    assert span.cost is not None and span.cost.usd == 0.01
    assert tracer.costs.total_usd == 0.01
    assert tracer.root.total_usd() == 0.01, "the parent should total its children"


async def test_recording_a_cost_over_the_ceiling_raises_from_the_tracer():
    tracer = Tracer(costs=CostTracker(max_usd=0.005))
    with pytest.raises(BudgetExceeded):
        async with tracer.span("llm", "llm") as span:
            tracer.record_cost("llm", "m", Cost(usd=0.01), span)


async def test_live_events_arrive_in_order():
    events: list[dict] = []
    tracer = Tracer(agent="a", on_event=events.append)
    async with tracer.span("step") as span:
        tracer.record_cost("llm", "m", Cost(usd=0.01), span)
    tracer.finish()
    kinds = [e["type"] for e in events]
    assert kinds == ["span_start", "cost", "span_end", "span_end"]
    assert events[1]["total_usd"] == 0.01
    assert all(e["run_id"] == tracer.trace.run_id for e in events)


async def test_a_broken_listener_never_fails_the_run():
    def explode(_event):
        raise RuntimeError("listener is broken")

    tracer = Tracer(on_event=explode)
    async with tracer.span("step"):
        pass
    assert tracer.finish().root.status == "ok", "telemetry is never load-bearing"


async def test_span_events_carry_no_subtree():
    events: list[dict] = []
    tracer = Tracer(on_event=events.append)
    async with tracer.span("outer"):
        async with tracer.span("inner"):
            pass
    assert all("children" not in e["span"] for e in events)


async def test_concurrent_spans_do_not_steal_each_others_parent():
    tracer = Tracer(agent="a")

    async def side(name: str):
        async with tracer.span(name):
            await asyncio.sleep(0.01)
            async with tracer.span(f"{name}-inner"):
                await asyncio.sleep(0.01)

    async with tracer.span("root-step"):
        await asyncio.gather(side("a"), side("b"))

    step = tracer.root.children[0]
    assert {c.name for c in step.children} == {"a", "b"}
    for child in step.children:
        assert [c.name for c in child.children] == [f"{child.name}-inner"]


def test_long_values_are_truncated_before_they_reach_a_span():
    tracer = Tracer()
    tracer.root.set_output("x" * 100_000)
    assert len(tracer.root.output) < 100_000


async def test_render_and_json_round_trip():
    tracer = Tracer(agent="demo")
    async with tracer.span("step") as span:
        tracer.record_cost("llm", "m", Cost(usd=0.0123), span)
    trace = tracer.finish(output="answer")

    rendered = trace.render()
    assert "demo" in rendered and "step" in rendered and "0.01230" in rendered

    blob = json.loads(trace.to_json())
    assert blob["agent"] == "demo" and blob["root"]["children"][0]["name"] == "step"


async def test_find_locates_spans_by_name():
    tracer = Tracer()
    async with tracer.span("search"):
        pass
    async with tracer.span("search"):
        pass
    assert len(tracer.finish().find("search")) == 2


async def test_skipped_status_survives_the_context_manager():
    tracer = Tracer()
    async with tracer.span("maybe") as span:
        span.status = "skipped"
    assert tracer.root.children[0].status == "skipped"
