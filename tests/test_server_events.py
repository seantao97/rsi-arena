"""``server.events`` — merging concurrent runs into one lossless SSE stream.

The merge has to be lossless. A battle runs two agents into one stream, and a
dropped ``span_end`` leaves a row spinning forever in the UI.
"""

from __future__ import annotations

import asyncio
import json

import pytest

from server.events import HEARTBEAT_S, RunStream, encode


def parse(chunks: list[str]) -> list[dict]:
    return [
        json.loads(line[6:])
        for chunk in chunks
        for line in chunk.splitlines()
        if line.startswith("data: ")
    ]


def test_encode_names_the_event_in_both_places():
    # A client that only listens to onmessage still knows what it received.
    wire = encode({"type": "token", "text": "hi"})
    assert wire.startswith("event: token\ndata: ")
    assert json.loads(wire.split("data: ", 1)[1])["type"] == "token"


async def test_one_run_streams_its_events_then_done():
    stream = RunStream()

    async def go() -> dict:
        on_event, on_token = stream.hooks("a")
        on_event({"type": "span_start", "span": {"id": "1"}})
        on_token("hello")
        on_event({"type": "span_end", "span": {"id": "1"}})
        return {"ok": True, "output": "answer"}

    stream.add("a", go)
    events = parse([chunk async for chunk in stream.sse()])
    assert [e["type"] for e in events] == ["span_start", "token", "span_end", "run_end", "done"]
    assert all(e["side"] == "a" for e in events if e["type"] != "done")


async def test_two_sides_interleave_without_losing_either_one():
    stream = RunStream()

    def side(label: str, delay: float):
        async def go() -> dict:
            on_event, _ = stream.hooks(label)
            for index in range(5):
                on_event({"type": "span_start", "span": {"id": f"{label}{index}"}})
                await asyncio.sleep(delay)
                on_event({"type": "span_end", "span": {"id": f"{label}{index}"}})
            return {"ok": True}

        return go

    stream.add("a", side("a", 0.001))
    stream.add("b", side("b", 0.003))
    events = parse([chunk async for chunk in stream.sse()])

    for label in ("a", "b"):
        opened = {e["span"]["id"] for e in events
                  if e["type"] == "span_start" and e["side"] == label}
        closed = {e["span"]["id"] for e in events
                  if e["type"] == "span_end" and e["side"] == label}
        assert opened == closed, f"side {label} dropped {opened - closed}"
    assert len([e for e in events if e["type"] == "run_end"]) == 2
    assert events[-1]["type"] == "done"


async def test_a_raising_run_becomes_a_run_error_not_a_dropped_stream():
    stream = RunStream()

    async def boom() -> dict:
        raise RuntimeError("provider fell over")

    stream.add("a", boom)
    events = parse([chunk async for chunk in stream.sse()])
    assert events[0]["type"] == "run_error"
    assert "RuntimeError: provider fell over" in events[0]["message"]
    assert events[-1]["type"] == "done", "the stream still closes cleanly"


async def test_one_side_failing_does_not_take_the_other_down():
    stream = RunStream()

    async def boom() -> dict:
        raise RuntimeError("no")

    async def fine() -> dict:
        await asyncio.sleep(0.01)
        return {"ok": True}

    stream.add("a", boom)
    stream.add("b", fine)
    events = parse([chunk async for chunk in stream.sse()])
    kinds = {(e["type"], e.get("side")) for e in events}
    assert ("run_error", "a") in kinds and ("run_end", "b") in kinds


async def test_events_queued_after_the_last_read_are_still_drained():
    stream = RunStream()

    async def go() -> dict:
        on_event, _ = stream.hooks("a")
        for index in range(50):
            on_event({"type": "span_end", "span": {"id": str(index)}})
        return {"ok": True}

    stream.add("a", go)
    events = parse([chunk async for chunk in stream.sse()])
    assert len([e for e in events if e["type"] == "span_end"]) == 50


async def test_a_quiet_run_gets_a_keepalive(monkeypatch):
    # Long model calls produce no events; without this an idle timeout kills
    # the connection mid-run.
    monkeypatch.setattr("server.events.HEARTBEAT_S", 0.01)
    stream = RunStream()

    async def slow() -> dict:
        await asyncio.sleep(0.05)
        return {"ok": True}

    stream.add("a", slow)
    chunks = [chunk async for chunk in stream.sse()]
    assert any(chunk.startswith(": keepalive") for chunk in chunks)
    assert parse(chunks)[-1]["type"] == "done"


async def test_hanging_up_cancels_the_run_rather_than_letting_it_spend():
    started = asyncio.Event()
    finished = False

    async def long_run() -> dict:
        nonlocal finished
        started.set()
        await asyncio.sleep(5)
        finished = True
        return {"ok": True}

    stream = RunStream()
    stream.add("a", long_run)

    generator = stream.sse()
    with pytest.raises(asyncio.TimeoutError):
        await asyncio.wait_for(generator.__anext__(), timeout=0.05)
    await started.wait()
    await generator.aclose()
    await asyncio.sleep(0.01)
    assert not finished, "nothing should keep spending on a run nobody is watching"


def test_the_heartbeat_is_short_enough_to_beat_a_typical_idle_timeout():
    assert 0 < HEARTBEAT_S <= 30
