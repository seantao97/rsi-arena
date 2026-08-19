"""Backend tests against a fake OpenRouter and a fake SearchApi.

Exercises the SSE routes the UI depends on: that a run streams span events in
order, that a battle interleaves two sides without dropping either one's
``span_end``, and that a blind battle withholds identities until the vote.

No key, no network — the same ``MockTransport`` the other tests use.
"""

from __future__ import annotations

import json
import os
import sys
import tempfile
from pathlib import Path

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

os.environ["OPENROUTER_API_KEY"] = "test-key"
os.environ["SEARCHAPI_API_KEY"] = "test-key"
os.environ["RSI_ARENA_DB"] = str(Path(tempfile.gettempdir()) / "rsi_arena_test.db")
Path(os.environ["RSI_ARENA_DB"]).unlink(missing_ok=True)

from fastapi.testclient import TestClient  # noqa: E402

from rsi_arena import APIClient, LLMClient, MemoryCache, RateLimit  # noqa: E402
from server import app as server_app  # noqa: E402
from tests.test_examples import router  # noqa: E402


def sse_events(response: httpx.Response) -> list[dict]:
    events = []
    for line in response.iter_lines():
        if line.startswith("data: "):
            events.append(json.loads(line[6:]))
    return events


def main() -> None:
    with TestClient(server_app.app) as client:
        # Swap the live clients for mocked ones now that lifespan has run.
        http = httpx.AsyncClient(transport=httpx.MockTransport(router))
        cache = MemoryCache()
        server_app.state.llm = LLMClient(api_key="test", http_client=http, cache=cache,
                                         auto_pricing=False,
                                         rate_limit=RateLimit(per_second=1000))
        server_app.state.api = APIClient(cache=cache, http_client=http)

        health = client.get("/api/health").json()
        assert health["ok"] and health["keys"]["OPENROUTER_API_KEY"]
        print(f"health          ok  {health['keys']}")

        agents = client.get("/api/agents").json()
        ids = [a["id"] for a in agents]
        assert ids == ["pipeline", "freeform", "plugin", "fermi"], ids
        assert all(a["outline"] and "plan" in a for a in agents)
        print(f"agents          ok  {ids}")

        models = client.get("/api/models").json()
        assert models and models[0]["id"]
        print(f"models          ok  {len(models)} listed, first={models[0]['id']}")

        # --- single run ---
        with client.stream("POST", "/api/run",
                           json={"agent": "fermi", "question": "piano tuners in Chicago",
                                 "model": "test/model", "max_usd": 1.0}) as response:
            assert response.status_code == 200
            assert response.headers["content-type"].startswith("text/event-stream")
            events = sse_events(response)

        kinds = [e["type"] for e in events]
        assert kinds[0] == "run_start" and kinds[-1] == "done", kinds[:3]
        end = next(e for e in events if e["type"] == "run_end")
        assert end["ok"], end.get("error")
        assert end["summary"]["total_usd"] > 0
        opened = {e["span"]["id"] for e in events if e["type"] == "span_start"}
        closed = {e["span"]["id"] for e in events if e["type"] == "span_end"}
        assert opened <= closed, f"spans opened but never closed: {opened - closed}"
        tokens = "".join(e["text"] for e in events if e["type"] == "token")
        assert tokens, "the final step should have streamed"
        print(f"run (sse)       ok  {len(events)} events, {len(opened)} spans, "
              f"{len(tokens)} streamed chars, ${end['summary']['total_usd']:.4f}")

        # --- battle ---
        with client.stream("POST", "/api/battle",
                           json={"agent_a": "pipeline", "agent_b": "freeform",
                                 "question": "Did the ECB cut rates in July 2026?",
                                 "model": "test/model", "blind": True}) as response:
            events = sse_events(response)

        start = next(e for e in events if e["type"] == "battle_start")
        battle_id = start["battle_id"]
        ends = {e["side"]: e for e in events if e["type"] == "run_end"}
        assert set(ends) == {"a", "b"}, f"both sides must finish, got {set(ends)}"
        assert all(e["ok"] for e in ends.values())
        assert all(e["agent_id"] is None for e in ends.values()), "blind battle leaked identities"
        # The name must be absent from the payload, not merely hidden by the UI:
        # anything the browser received, a voter can read out of devtools.
        starts = {e["side"]: e for e in events if e["type"] == "run_start"}
        assert {e["agent"] for e in starts.values()} == {"Agent A", "Agent B"}, starts
        # The whole payload, every event: an id the browser received is an id a
        # voter can read out of devtools.
        blob = json.dumps(events)
        for name in ("researcher-pipeline", "researcher-freeform", "pipeline", "freeform"):
            assert name not in blob, f"blind battle leaked {name!r} to the client"
        for side in ("a", "b"):
            opened = {e["span"]["id"] for e in events
                      if e["type"] == "span_start" and e["side"] == side}
            closed = {e["span"]["id"] for e in events
                      if e["type"] == "span_end" and e["side"] == side}
            assert opened <= closed, f"side {side} dropped span_end for {opened - closed}"
        interleaved = [e["side"] for e in events if e["type"] == "span_start"]
        print(f"battle (sse)    ok  {len(events)} events, both sides blind, "
              f"a={interleaved.count('a')} b={interleaved.count('b')} spans")

        # --- vote and reveal ---
        voted = client.post("/api/vote", json={"battle_id": battle_id, "winner": "a",
                                               "reason": "evidence"}).json()
        assert set(voted["reveal"]) == {"a", "b"}
        assert voted["leaderboard"][0]["wins"] == 1
        print(f"vote + reveal   ok  {voted['reveal']}, leaderboard={voted['leaderboard']}")

        assert client.post("/api/vote", json={"battle_id": "nope", "winner": "a"}).status_code == 404
        assert client.post("/api/run", json={"agent": "nope", "question": "x"}).status_code == 404
        print("errors          ok  unknown battle and unknown agent both 404")

    print("\nbackend ok")


if __name__ == "__main__":
    main()
