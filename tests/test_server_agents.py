"""``server.agents`` — every catalogue agent, callable over plain HTTP.

The SSE route is right for a UI watching a trace appear and wrong for anything
else. These are the request/response half: send JSON, get the answer, the
ledger, and the trace if you asked for it.
"""

from __future__ import annotations


# --- describing -------------------------------------------------------------


def test_one_agent_can_be_fetched_by_id(client):
    agent = client.get("/api/agents/fermi").json()
    assert agent["id"] == "fermi" and agent["steps"] == 3
    assert agent["context"] and agent["outline"] and agent["plan"]["steps"]
    assert agent["tools"] == ["calculator"]
    assert agent["missing_keys"] == []


def test_an_unknown_agent_is_a_404(client):
    assert client.get("/api/agents/invented").status_code == 404


def test_the_catalogue_and_the_single_agent_agree(client):
    listed = next(a for a in client.get("/api/agents").json() if a["id"] == "pipeline")
    single = client.get("/api/agents/pipeline").json()
    assert listed["outline"] == single["outline"]


# --- running ----------------------------------------------------------------


def test_running_one_returns_the_answer_the_ledger_and_the_state(client):
    body = client.post("/api/agents/fermi/run", json={
        "question": "How many piano tuners are in Chicago?", "model": "test/model",
        "max_usd": 1.0,
    }).json()

    assert body["ok"] is True and body["error"] is None
    assert body["agent_id"] == "fermi"
    assert body["text"] and body["text"] == body["output"]
    assert body["summary"]["total_usd"] > 0
    assert body["state"]["value"], "the calculator step's output should be in state"
    assert body["error_kind"] is None and body["bailed_out"] is False


def test_the_trace_is_off_by_default_and_available_on_request(client):
    payload = {"question": "q", "model": "test/model", "max_usd": 1.0}
    assert "trace" not in client.post("/api/agents/fermi/run", json=payload).json()

    with_trace = client.post("/api/agents/fermi/run",
                             json={**payload, "include_trace": True}).json()
    assert with_trace["trace"]["root"]["name"] == "fermi"


def test_extra_inputs_reach_the_run(client):
    body = client.post("/api/agents/fermi/run", json={
        "question": "How many piano tuners are in Chicago?", "model": "test/model",
        "inputs": {"audience": "an engineer"},
    }).json()
    assert body["ok"] is True
    assert body["state"]["audience"] == "an engineer", "an input is run state, readable as {{key}}"


def test_a_failed_run_is_a_200_with_the_reason_not_a_500(client):
    # The run happened and cost money; its partial trace is the evidence the
    # arena is collecting. Only an unrunnable request is an error status.
    response = client.post("/api/agents/pipeline/run", json={
        "question": "Did the ECB cut rates?", "model": "test/model",
        "max_usd": 0.002, "cache": False,
    })
    assert response.status_code == 200
    body = response.json()
    assert body["ok"] is False and body["error_kind"] == "budget"


def test_max_spend_mode_answers_from_state_and_says_so(client):
    body = client.post("/api/agents/pipeline/run", json={
        "question": "Did the ECB cut rates?", "model": "test/model",
        "max_usd": 0.002, "cache": False, "max_spend_mode": True,
    }).json()

    assert body["ok"] is False, "a cut-off answer is never counted as a clean one"
    assert body["error_kind"] == "max_spend" and body["bailed_out"] is True
    assert body["text"], "but there is still an answer"
    assert body["summary"]["reserve_used_usd"] > 0


def test_the_bailout_reserve_can_be_set_per_request(client):
    body = client.post("/api/agents/pipeline/run", json={
        "question": "q", "model": "test/model", "max_usd": 0.002, "cache": False,
        "max_spend_mode": True, "bailout_reserve_usd": 0.5,
    }).json()
    assert body["summary"]["reserve_usd"] == 0.5


def test_running_an_unknown_agent_is_a_404(client):
    assert client.post("/api/agents/invented/run",
                       json={"question": "q"}).status_code == 404


def test_an_out_of_range_ceiling_is_rejected(client):
    assert client.post("/api/agents/fermi/run",
                       json={"question": "q", "max_usd": 999}).status_code == 422


def test_an_agent_missing_its_key_is_a_400_naming_the_variable(client, monkeypatch):
    monkeypatch.delenv("SEARCHAPI_API_KEY", raising=False)
    response = client.post("/api/agents/pipeline/run", json={"question": "q"})
    assert response.status_code == 400
    assert "SEARCHAPI_API_KEY" in response.json()["detail"]


# --- answering now ----------------------------------------------------------


def test_answer_now_turns_state_into_an_answer(client):
    body = client.post("/api/agents/fermi/answer", json={
        "question": "How many piano tuners are in Chicago?",
        "state": {"notes": "Chicago has about 2.7M people.", "value": 130},
        "reason": "the run hit its ceiling",
    }).json()
    assert body["agent_id"] == "fermi" and body["text"]


def test_answer_now_works_with_no_state_at_all(client):
    assert client.post("/api/agents/fermi/answer", json={"question": "q"}).json()["text"]


def test_answer_now_on_an_unknown_agent_is_a_404(client):
    assert client.post("/api/agents/invented/answer", json={}).status_code == 404
