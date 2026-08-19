"""``server.evals`` — the eval endpoint, its suite, and the stored results."""

from __future__ import annotations

import pytest

FERMI = "How many piano tuners are in Chicago?"


def run_eval(client, **overrides) -> dict:
    body = {"agent": "fermi", "prompt": FERMI, "scorer": "non_empty", "model": "test/model",
            "max_usd": 1.0, **overrides}
    response = client.post("/api/evals", json=body)
    assert response.status_code == 200, response.text
    return response.json()


# --- scorers ----------------------------------------------------------------


def test_the_scorer_catalogue_describes_what_a_spec_may_name(client):
    scorers = client.get("/api/scorers").json()
    by_type = {s["type"]: s for s in scorers}
    assert {"contains", "regex", "llm_judge", "non_empty", "completed"} <= set(by_type)
    assert by_type["contains"]["description"]
    assert any(p["name"] == "value" and p["required"] for p in by_type["contains"]["params"])


# --- running one ------------------------------------------------------------


def test_running_an_eval_scores_the_agent_and_returns_the_result(client):
    result = run_eval(client, scorer={"type": "contains", "value": "ECB"})
    assert result["agent"] == "fermi" and result["prompt"] == FERMI
    assert result["output"] and result["ok"] is True
    assert result["score"]["passed"] is True and result["score"]["label"] == "contains"
    assert result["cost_usd"] > 0 and result["id"]


def test_a_scorer_can_be_a_bare_name(client):
    assert run_eval(client, scorer="non_empty")["score"]["passed"] is True


def test_a_list_of_scorers_is_scored_as_a_conjunction(client):
    result = run_eval(client, scorer=["non_empty", {"type": "contains", "value": "ECB"}])
    assert result["score"]["label"] == "all_of"
    assert len(result["score"]["details"]["parts"]) == 2


def test_an_llm_judge_runs_against_the_shared_client(client):
    result = run_eval(client, scorer={"type": "llm_judge",
                                      "rubric": "Every claim carries its URL."})
    assert result["score"]["value"] == 0.8 and result["score"]["notes"]


@pytest.mark.parametrize("scorer", ["invented", {"type": "invented"}, {"value": "x"}, 42])
def test_a_bad_scorer_is_a_400_before_anything_is_spent(client, scorer):
    response = client.post("/api/evals", json={"agent": "fermi", "prompt": "q",
                                               "scorer": scorer})
    assert response.status_code == 400 and "scorer" in response.json()["detail"]


def test_an_unknown_agent_is_a_404(client):
    assert client.post("/api/evals", json={"agent": "invented", "prompt": "q",
                                           "scorer": "non_empty"}).status_code == 404


def test_the_trace_is_off_by_default(client):
    assert "trace" not in run_eval(client)
    assert run_eval(client, include_trace=True)["trace"]["root"]["name"] == "fermi"


def test_the_expected_answer_is_carried_through(client):
    result = run_eval(client, expected="about 130 tuners",
                      scorer={"type": "llm_judge", "rubric": "Is it close?"})
    assert result["score"]["passed"] is True


def test_a_failing_agent_is_still_a_scored_result(client):
    result = run_eval(client, agent="pipeline", prompt="Did the ECB cut rates?",
                      scorer="completed", max_usd=0.002, cache=False)
    assert result["ok"] is False and result["error_kind"] == "budget"
    assert result["score"]["value"] == 0.0


def test_max_spend_mode_scores_the_bailout_answer(client):
    result = run_eval(client, agent="pipeline", prompt="Did the ECB cut rates?",
                      scorer="completed", max_usd=0.002, cache=False, max_spend_mode=True)
    assert result["error_kind"] == "max_spend" and result["bailed_out"] is True
    assert result["output"], "there is an answer to score"
    assert result["score"]["value"] == 0.5, "an answered cut-off beats a dead run"


# --- storing and reading back ------------------------------------------------


def test_a_result_is_stored_and_fetchable(client):
    result = run_eval(client)
    fetched = client.get(f"/api/evals/{result['id']}").json()
    assert fetched["id"] == result["id"] and fetched["output"] == result["output"]


def test_save_false_keeps_it_out_of_the_store(client):
    result = run_eval(client, save=False)
    assert client.get(f"/api/evals/{result['id']}").status_code == 404
    assert client.get("/api/evals").json()["total"] == 0


def test_listing_is_newest_first_and_filterable(client):
    run_eval(client, name="one")
    run_eval(client, name="two", prompt="a different question")
    listing = client.get("/api/evals").json()
    assert listing["total"] == 2
    assert [r["name"] for r in listing["results"]] == ["two", "one"]
    assert client.get("/api/evals", params={"name": "one"}).json()["total"] == 1
    assert client.get("/api/evals", params={"agent": "nobody"}).json()["total"] == 0


def test_listing_paginates(client):
    for index in range(3):
        run_eval(client, name=f"e{index}", prompt=f"question {index}")
    page = client.get("/api/evals", params={"limit": 2, "offset": 1}).json()
    assert page["total"] == 3 and len(page["results"]) == 2


def test_a_result_can_be_deleted(client):
    result = run_eval(client)
    assert client.delete(f"/api/evals/{result['id']}").status_code == 200
    assert client.get(f"/api/evals/{result['id']}").status_code == 404
    assert client.delete(f"/api/evals/{result['id']}").status_code == 404


def test_an_unknown_result_is_a_404(client):
    assert client.get("/api/evals/nope").status_code == 404


def test_the_leaderboard_aggregates_stored_results(client):
    run_eval(client, scorer={"type": "contains", "value": "ECB"})
    run_eval(client, prompt="another question", scorer={"type": "contains", "value": "nowhere"})
    board = client.get("/api/evals/leaderboard").json()
    assert board[0]["agent"] == "fermi"
    assert board[0]["evals"] == 2 and board[0]["mean_score"] == 0.5


# --- suites -----------------------------------------------------------------


def test_a_suite_runs_every_case_against_every_agent(client):
    body = client.post("/api/evals/suite", json={
        "agents": ["fermi", "plugin"],
        "cases": [{"prompt": FERMI, "scorer": "non_empty", "name": "answers"},
                  {"prompt": "Did the ECB cut rates?", "scorer": {"type": "contains",
                                                                 "value": "ECB"}}],
        "name": "compare", "model": "test/model", "max_usd": 1.0,
    })
    assert body.status_code == 200, body.text
    suite = body.json()
    assert suite["evals"] == 4 and suite["name"] == "compare"
    # The harness's own name, not the catalogue id — "plugin" is
    # "researcher-plugin". The id is kept in metadata.
    assert {r["agent"] for r in suite["results"]} == {"fermi", "researcher-plugin"}
    assert 0.0 <= suite["mean_score"] <= 1.0 and suite["cost_usd"] > 0


def test_the_catalogue_id_is_recorded_alongside_the_harness_name(client):
    result = run_eval(client, agent="plugin", prompt="Did the ECB cut rates?")
    assert result["agent"] == "researcher-plugin"
    assert result["metadata"]["agent_id"] == "plugin"


def test_a_suite_is_stored_and_fetchable(client):
    suite = client.post("/api/evals/suite", json={
        "agents": ["fermi"], "cases": [{"prompt": FERMI, "scorer": "non_empty"}],
        "model": "test/model",
    }).json()
    listed = client.get("/api/evals/suites").json()
    assert [s["id"] for s in listed] == [suite["id"]]
    fetched = client.get(f"/api/evals/suites/{suite['id']}").json()
    assert fetched["evals"] == 1 and fetched["results"][0]["agent"] == "fermi"
    assert client.get("/api/evals").json()["total"] == 1, "its results are stored too"


def test_an_unknown_suite_is_a_404(client):
    assert client.get("/api/evals/suites/nope").status_code == 404


def test_an_oversized_suite_is_refused_before_it_runs(client):
    response = client.post("/api/evals/suite", json={
        "agents": ["fermi", "plugin", "pipeline", "freeform"],
        "cases": [{"prompt": f"q{i}", "scorer": "non_empty"} for i in range(20)],
    })
    assert response.status_code == 400 and "over the limit" in response.json()["detail"]


def test_a_suite_needs_at_least_one_agent_and_one_case(client):
    assert client.post("/api/evals/suite", json={"agents": [], "cases": []}).status_code == 422


def test_a_suite_with_a_bad_scorer_is_a_400(client):
    response = client.post("/api/evals/suite", json={
        "agents": ["fermi"], "cases": [{"prompt": "q", "scorer": "invented"}],
    })
    assert response.status_code == 400
