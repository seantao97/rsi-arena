"""One fake backend for every test, driven through ``httpx.MockTransport``.

No key, no network, no cost. The tests that used to carry a private copy of a
mock handler now share this, so a change to the wire format is fixed once.

It speaks enough of the real protocols to exercise the whole stack:

* ``POST /chat/completions`` — plain answers, tool calls, ``json_schema``
  responses, ``url_citation`` annotations, usage and cost, streaming SSE
* ``GET /models`` — the price and capability list
* SearchApi's ``/search``
* ``https://demo.test/search`` — a stand-in third-party API for registry tests

Everything a test wants to vary is an attribute: :attr:`Fake.fail_left` makes
the next N calls 429, :attr:`Fake.schema_answers` sets what a structured step
gets back, :attr:`Fake.text` sets the prose. Counters record what was actually
called, which is how the cache and single-flight tests prove anything.
"""

from __future__ import annotations

import json
from typing import Any

import httpx

USAGE = {
    "prompt_tokens": 100,
    "completion_tokens": 20,
    "total_tokens": 120,
    "cost": 0.0012,
    "cost_details": {"upstream_inference_cost": 0.001},
    "completion_tokens_details": {"reasoning_tokens": 5},
    "prompt_tokens_details": {"cached_tokens": 0, "cache_write_tokens": 0},
}

CITATION = {
    "type": "url_citation",
    "url_citation": {
        "url": "https://ecb.europa.eu/x",
        "title": "Monetary policy decisions",
        "content": "The Governing Council decided to leave rates unchanged.",
    },
}

# Keyed by the ``json_schema`` name, which steps set from their own name — so a
# step called ``take_notes`` gets the notes payload without the fake having to
# guess from the prompt.
SCHEMA_ANSWERS: dict[str, Any] = {
    "plan_queries": {
        "queries": ["ecb rate decision july 2026", "ecb press release july 2026"],
        "what_would_settle_it": "The ECB's own press release.",
    },
    "take_notes": {
        "claims": [{"claim": "The Governing Council left rates unchanged.",
                    "url": "https://ecb.europa.eu/x", "date": "2026-07-24", "confidence": 0.9}],
        "still_missing": "nothing",
        "sufficient": True,
    },
    "stop_check": {"done": True, "reason": "the press release is a primary source"},
    "decompose": {
        "expression": "2700000 / 2.5 / 40 * 0.02",
        "weakest_factor": "pianos per household",
        "factors": [{"name": "population", "value": "2700000",
                     "justification": "Chicago city population."}],
    },
    "judgement": {"score": 0.8, "passed": True, "reason": "Sourced and specific."},
}

DEFAULT_TEXT = (
    "The ECB left its three key rates unchanged at the July 2026 meeting "
    "(https://ecb.europa.eu/x, 24 July 2026). What would change this: a correction "
    "to the press release."
)

MODELS = [
    {"id": "fake/sonnet", "name": "Fake Sonnet", "context_length": 200000,
     "supported_parameters": ["tools", "structured_outputs"],
     "pricing": {"prompt": "0.000003", "completion": "0.000015"}},
    {"id": "fake/haiku", "name": "Fake Haiku", "context_length": 200000,
     "supported_parameters": ["tools"],
     "pricing": {"prompt": "0.0000008", "completion": "0.000004"}},
]

SEARCH_RESULTS = {
    "search_information": {"total_results": 12},
    "organic_results": [
        {"position": 1, "title": "ECB press release", "link": "https://ecb.europa.eu/x",
         "source": "ecb.europa.eu", "date": "24 Jul 2026",
         "snippet": "The Governing Council decided to hold rates."},
        {"position": 2, "title": "Euro area policy rates", "link": "https://example.org/rates",
         "source": "example.org", "date": "1 Aug 2026", "snippet": "A table of policy rates."},
    ],
    "answer_box": {"type": "organic", "answer": "Held", "link": "https://ecb.europa.eu/x"},
}


class Fake:
    """A whole backend, counted and configurable. One per test."""

    def __init__(
        self,
        *,
        text: str = DEFAULT_TEXT,
        fail_left: int = 0,
        cost: float | None = None,
        cite: bool = True,
    ) -> None:
        self.text = text
        self.fail_left = fail_left
        self.cite = cite
        self.usage = dict(USAGE) if cost is None else {**USAGE, "cost": cost}
        self.schema_answers = dict(SCHEMA_ANSWERS)
        # Verbatim bodies, keyed by schema name, for testing what happens when a
        # model ignores the schema it was given. Checked before schema_answers.
        self.raw_answers: dict[str, str] = {}
        self.llm_calls = 0
        self.search_calls = 0
        self.demo_calls = 0
        self.models_calls = 0
        self.bodies: list[dict[str, Any]] = []

    # -- the transport --

    def transport(self) -> httpx.MockTransport:
        return httpx.MockTransport(self.handle)

    def client(self) -> httpx.AsyncClient:
        return httpx.AsyncClient(transport=self.transport())

    def handle(self, request: httpx.Request) -> httpx.Response:
        host, path = request.url.host, request.url.path
        if "openrouter" in host:
            if path.endswith("/models"):
                self.models_calls += 1
                return httpx.Response(200, json={"data": MODELS})
            return self._chat(request)
        if "searchapi" in host:
            self.search_calls += 1
            return httpx.Response(200, json={
                "search_parameters": {"q": request.url.params.get("q")}, **SEARCH_RESULTS})
        self.demo_calls += 1
        return httpx.Response(200, json={
            "query": request.url.params.get("q"),
            "organic_results": [{"position": 1, "title": "T", "link": "https://x",
                                 "snippet": "S"}]})

    # -- chat completions --

    def _chat(self, request: httpx.Request) -> httpx.Response:
        self.llm_calls += 1
        if self.fail_left > 0:
            self.fail_left -= 1
            # Retry-After: 0 so the retry is instant — the test is about the
            # backoff happening at all, not about how long it waits.
            return httpx.Response(429, headers={"Retry-After": "0"},
                                  json={"error": {"code": 429, "message": "slow down"}})

        body = json.loads(request.content)
        self.bodies.append(body)
        payload = self._answer(body)
        return self._sse(payload) if body.get("stream") else httpx.Response(200, json=payload)

    def _answer(self, body: dict[str, Any]) -> dict[str, Any]:
        model = body.get("model", "fake/model")
        messages = body.get("messages") or []

        # First turn of a tool-enabled step: ask for the first tool offered.
        # Generic on purpose, so a test can add a tool without touching this.
        if body.get("tools") and not any(m.get("role") == "tool" for m in messages):
            return {
                "id": "gen-tools", "model": model, "usage": self.usage,
                "choices": [{"finish_reason": "tool_calls", "message": {
                    "role": "assistant", "content": "",
                    "tool_calls": [{"id": "call_1", "type": "function",
                                    "function": self._tool_call(body["tools"][0])}]}}],
            }

        fmt = body.get("response_format")
        if fmt:
            name = fmt.get("json_schema", {}).get("name", "")
            content = self.raw_answers.get(name) or json.dumps(
                self.schema_answers.get(name, {"answer": "42", "confidence": 0.9})
            )
        else:
            content = self.text

        message: dict[str, Any] = {"role": "assistant", "content": content}
        if self.cite and not fmt:
            message["annotations"] = [CITATION]
        return {"id": "gen-1", "model": model, "usage": self.usage,
                "choices": [{"finish_reason": "stop", "message": message}]}

    @staticmethod
    def _tool_call(schema: dict[str, Any]) -> dict[str, str]:
        """Fill the tool's required string parameters with something plausible."""
        function = schema.get("function", {})
        params = function.get("parameters", {})
        args = {
            name: "ecb rate decision july 2026"
            for name in params.get("required", [])
            if params.get("properties", {}).get(name, {}).get("type", "string") == "string"
        }
        return {"name": function.get("name", "unknown"), "arguments": json.dumps(args)}

    def _sse(self, payload: dict[str, Any]) -> httpx.Response:
        """Re-cut a finished response as deltas, the way OpenRouter sends them."""
        content = payload["choices"][0]["message"].get("content") or ""
        pieces = [content[i : i + 12] for i in range(0, len(content), 12)] or [""]
        chunks = [
            {"id": payload["id"], "model": payload["model"],
             "choices": [{"delta": {"content": piece}}]}
            for piece in pieces
        ]
        chunks.append({"id": payload["id"], "model": payload["model"], "usage": self.usage,
                       "choices": [{"delta": {}, "finish_reason": "stop"}]})
        text = "".join(f"data: {json.dumps(c)}\n\n" for c in chunks) + "data: [DONE]\n\n"
        return httpx.Response(200, text=text, headers={"content-type": "text/event-stream"})
