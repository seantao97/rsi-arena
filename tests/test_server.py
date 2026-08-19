"""The backend's arena routes: health, models, SSE runs, battles, votes.

The shared clients are swapped for mocked ones after lifespan has run, so
nothing here touches the network. The eval and agent routes are next door in
``test_server_agents.py`` and ``test_server_evals.py``.
"""

from __future__ import annotations

import json

import httpx
import pytest


def sse_events(response: httpx.Response) -> list[dict]:
    return [json.loads(line[6:]) for line in response.iter_lines() if line.startswith("data: ")]


# --- the plain routes -------------------------------------------------------


def test_health_reports_which_keys_the_process_has(client):
    health = client.get("/api/health").json()
    assert health["ok"] and health["keys"]["OPENROUTER_API_KEY"] is True


def test_the_catalogue_lists_every_agent_with_its_plan(client):
    agents = client.get("/api/agents").json()
    assert [a["id"] for a in agents] == ["pipeline", "freeform", "plugin", "fermi"]
    assert all(a["outline"] and "plan" in a and "requires" in a for a in agents)
    assert all("missing_keys" in a for a in agents)


def test_models_falls_back_to_the_shortlist_when_the_catalogue_is_unreachable(client):
    models = client.get("/api/models").json()
    assert models and models[0]["id"]


# --- one run, streamed ------------------------------------------------------


def test_a_run_streams_spans_tokens_and_a_final_answer(client):
    with client.stream("POST", "/api/run", json={
        "agent": "fermi", "question": "piano tuners in Chicago", "model": "test/model",
        "max_usd": 1.0,
    }) as response:
        assert response.status_code == 200
        assert response.headers["content-type"].startswith("text/event-stream")
        events = sse_events(response)

    kinds = [e["type"] for e in events]
    assert kinds[0] == "run_start" and kinds[-1] == "done"

    end = next(e for e in events if e["type"] == "run_end")
    assert end["ok"], end.get("error")
    assert end["summary"]["total_usd"] > 0
    assert end["error_kind"] is None and end["bailed_out"] is False

    opened = {e["span"]["id"] for e in events if e["type"] == "span_start"}
    closed = {e["span"]["id"] for e in events if e["type"] == "span_end"}
    assert opened <= closed, f"spans opened but never closed: {opened - closed}"
    assert "".join(e["text"] for e in events if e["type"] == "token"), "the last step should stream"


def test_a_run_over_its_ceiling_reports_the_budget_kind(client):
    with client.stream("POST", "/api/run", json={
        "agent": "pipeline", "question": "Did the ECB cut rates?", "model": "test/model",
        "max_usd": 0.002, "cache": False,
    }) as response:
        events = sse_events(response)
    end = next(e for e in events if e["type"] == "run_end")
    assert end["ok"] is False and end["error_kind"] == "budget"
    assert end["bailed_out"] is False


def test_max_spend_mode_over_sse_bails_out_instead_of_returning_nothing(client):
    with client.stream("POST", "/api/run", json={
        "agent": "pipeline", "question": "Did the ECB cut rates?", "model": "test/model",
        "max_usd": 0.002, "cache": False, "max_spend_mode": True,
    }) as response:
        events = sse_events(response)
    end = next(e for e in events if e["type"] == "run_end")
    assert end["error_kind"] == "max_spend" and end["bailed_out"] is True
    assert end["output"], "there should be an answer to show"


def test_an_unknown_agent_is_a_404(client):
    assert client.post("/api/run", json={"agent": "nope", "question": "x"}).status_code == 404


# --- battles ----------------------------------------------------------------


def test_a_battle_runs_both_sides_without_dropping_either_one(client):
    with client.stream("POST", "/api/battle", json={
        "agent_a": "pipeline", "agent_b": "freeform",
        "question": "Did the ECB cut rates in July 2026?", "model": "test/model", "blind": True,
    }) as response:
        events = sse_events(response)

    ends = {e["side"]: e for e in events if e["type"] == "run_end"}
    assert set(ends) == {"a", "b"}, f"both sides must finish, got {set(ends)}"
    assert all(e["ok"] for e in ends.values())
    for side in ("a", "b"):
        opened = {e["span"]["id"] for e in events
                  if e["type"] == "span_start" and e["side"] == side}
        closed = {e["span"]["id"] for e in events
                  if e["type"] == "span_end" and e["side"] == side}
        assert opened <= closed, f"side {side} dropped span_end for {opened - closed}"


def test_a_blind_battle_leaks_no_identity_to_the_browser(client):
    with client.stream("POST", "/api/battle", json={
        "agent_a": "pipeline", "agent_b": "freeform",
        "question": "Did the ECB cut rates in July 2026?", "model": "test/model", "blind": True,
    }) as response:
        events = sse_events(response)

    starts = {e["side"]: e for e in events if e["type"] == "run_start"}
    assert {e["agent"] for e in starts.values()} == {"Agent A", "Agent B"}
    assert all(e["agent_id"] is None for e in events if e["type"] == "run_end")
    # The whole payload, every event: anything the browser received, a voter
    # can read out of devtools, and then the arena measures brand recognition.
    blob = json.dumps(events)
    for name in ("researcher-pipeline", "researcher-freeform", "pipeline", "freeform"):
        assert name not in blob, f"blind battle leaked {name!r} to the client"


def test_a_non_blind_battle_names_both_sides(client):
    with client.stream("POST", "/api/battle", json={
        "agent_a": "pipeline", "agent_b": "freeform", "question": "q",
        "model": "test/model", "blind": False, "shuffle": False,
    }) as response:
        events = sse_events(response)
    assert {e["agent_id"] for e in events if e["type"] == "run_end"} == {"pipeline", "freeform"}


# --- voting -----------------------------------------------------------------


def test_a_vote_reveals_the_pairing_and_updates_the_tally(client):
    with client.stream("POST", "/api/battle", json={
        "agent_a": "pipeline", "agent_b": "freeform", "question": "q", "model": "test/model",
    }) as response:
        events = sse_events(response)
    battle_id = next(e for e in events if e["type"] == "battle_start")["battle_id"]

    voted = client.post("/api/vote", json={"battle_id": battle_id, "winner": "a",
                                           "reason": "evidence"}).json()
    assert set(voted["reveal"]) == {"a", "b"}
    assert voted["leaderboard"][0]["wins"] == 1
    assert client.get("/api/leaderboard").json()[0]["wins"] == 1


def test_voting_on_an_unknown_battle_is_a_404(client):
    assert client.post("/api/vote", json={"battle_id": "nope", "winner": "a"}).status_code == 404


@pytest.mark.parametrize("winner", ["a", "b", "tie", "both_bad"])
def test_every_verdict_is_accepted(client, winner):
    with client.stream("POST", "/api/battle", json={
        "agent_a": "pipeline", "agent_b": "freeform", "question": "q", "model": "test/model",
    }) as response:
        events = sse_events(response)
    battle_id = next(e for e in events if e["type"] == "battle_start")["battle_id"]
    assert client.post("/api/vote", json={"battle_id": battle_id,
                                          "winner": winner}).status_code == 200


def test_an_invalid_verdict_is_rejected(client):
    assert client.post("/api/vote", json={"battle_id": "x", "winner": "maybe"}).status_code == 422
