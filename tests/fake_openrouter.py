"""A local stand-in for OpenRouter and SearchApi, so the UI runs with no keys.

Speaks enough of the real protocol to exercise the whole stack end to end —
chat completions, streaming SSE, tool calls, structured outputs, usage and
cost, plus a fake Google result set. Answers are canned and deliberately
obvious, so nobody mistakes a demo run for a real one.

    python -m tests.fake_openrouter                       # terminal 1, :3601
    OPENROUTER_BASE_URL=http://127.0.0.1:3601/api/v1 \\
      SEARCHAPI_BASE_URL=http://127.0.0.1:3601/searchapi/v1 \\
      OPENROUTER_API_KEY=demo SEARCHAPI_API_KEY=demo \\
      python -m server                                    # terminal 2, :3600
    cd web && npm run dev                                 # terminal 3, :8050
"""

from __future__ import annotations

import argparse
import asyncio
import json
from typing import Any

from fastapi import FastAPI, Request
from fastapi.responses import StreamingResponse

app = FastAPI(title="fake-openrouter")

USAGE = {
    "prompt_tokens": 820, "completion_tokens": 210, "total_tokens": 1030,
    "cost": 0.0043, "cost_details": {"upstream_inference_cost": 0.0039},
    "completion_tokens_details": {"reasoning_tokens": 0},
    "prompt_tokens_details": {"cached_tokens": 0, "cache_write_tokens": 0},
}

CANNED = {
    "plan_queries": {
        "queries": ["ecb rate decision july 2026", "ecb press release july 2026",
                    "euro area policy rate history"],
        "what_would_settle_it": "The ECB's own press release for the July meeting.",
    },
    "take_notes": {
        "claims": [{"claim": "The Governing Council left the three key rates unchanged.",
                    "url": "https://www.ecb.europa.eu/press/pr/date/2026/html/demo.en.html",
                    "date": "2026-07-24", "confidence": 0.92}],
        "still_missing": "Nothing material.", "sufficient": True,
    },
    "stop_check": {"done": True, "reason": "The press release is a primary source and it is dated."},
    # For eval scorers of type llm_judge, whose schema is named "judgement".
    "judgement": {"score": 0.75, "passed": True,
                  "reason": "Sourced to the press release and says what would change it. "
                            "(Canned — this is the local fake, not a real judge.)"},
    "decompose": {
        "expression": "2700000 / 2.5 / 40 * 0.02",
        "weakest_factor": "pianos per household",
        "factors": [
            {"name": "population", "value": "2700000", "justification": "Chicago city population."},
            {"name": "household size", "value": "2.5", "justification": "US average."},
            {"name": "pianos per household", "value": "1/40", "justification": "Rough guess."},
            {"name": "tuners per piano", "value": "0.02", "justification": "One tuner per 50 pianos."},
        ],
    },
}

PROSE = (
    "**This is the local fake, not a real model.**\n\n"
    "The ECB left its three key rates unchanged at the July 2026 meeting "
    "([press release](https://www.ecb.europa.eu/press/pr/date/2026/html/demo.en.html), "
    "24 July 2026). The deposit facility rate stayed where it was after the June cut, and the "
    "statement kept the meeting-by-meeting language rather than signalling a path.\n\n"
    "What would change this: a correction to the press release, or a later account of the "
    "meeting showing the decision was closer than the statement implies.\n"
)


def _answer(body: dict[str, Any]) -> dict[str, Any]:
    model = body.get("model", "fake/model")
    messages = body.get("messages") or []

    if body.get("tools") and not any(m.get("role") == "tool" for m in messages):
        return {
            "id": "gen-fake", "model": model, "usage": USAGE,
            "choices": [{"finish_reason": "tool_calls", "message": {
                "role": "assistant", "content": "",
                "tool_calls": [{"id": "call_1", "type": "function", "function": {
                    "name": "search",
                    "arguments": json.dumps({"q": "ecb rate decision july 2026"})}}]}}],
        }

    fmt = body.get("response_format")
    if fmt:
        name = fmt.get("json_schema", {}).get("name", "")
        content = json.dumps(CANNED.get(name, {"answer": "fake", "confidence": 0.5}))
    elif any("Output ONLY the next query" in str(m.get("content", "")) for m in messages):
        content = "ecb press release july 2026"
    else:
        content = PROSE

    return {
        "id": "gen-fake", "model": model, "usage": USAGE,
        "choices": [{"finish_reason": "stop", "message": {
            "role": "assistant", "content": content,
            "annotations": [{"type": "url_citation", "url_citation": {
                "url": "https://www.ecb.europa.eu/press/pr/date/2026/html/demo.en.html",
                "title": "ECB monetary policy decisions (fake)",
                "content": "The Governing Council decided to leave rates unchanged."}}],
        }}],
    }


@app.post("/api/v1/chat/completions")
async def chat(request: Request) -> Any:
    body = await request.json()
    payload = _answer(body)
    if not body.get("stream"):
        # A little latency, so the UI's live trace is actually watchable.
        await asyncio.sleep(0.6)
        return payload

    async def sse():
        content = payload["choices"][0]["message"].get("content") or ""
        for index in range(0, len(content), 8):
            chunk = {"id": payload["id"], "model": payload["model"],
                     "choices": [{"delta": {"content": content[index:index + 8]}}]}
            yield f"data: {json.dumps(chunk)}\n\n"
            await asyncio.sleep(0.02)
        final = {"id": payload["id"], "model": payload["model"], "usage": USAGE,
                 "choices": [{"delta": {}, "finish_reason": "stop"}]}
        yield f"data: {json.dumps(final)}\n\n"
        yield "data: [DONE]\n\n"

    return StreamingResponse(sse(), media_type="text/event-stream")


@app.get("/api/v1/models")
async def models() -> dict[str, Any]:
    return {"data": [
        {"id": "fake/sonnet", "name": "Fake Sonnet", "context_length": 200000,
         "supported_parameters": ["tools", "structured_outputs"],
         "pricing": {"prompt": "0.000003", "completion": "0.000015"}},
        {"id": "fake/haiku", "name": "Fake Haiku", "context_length": 200000,
         "supported_parameters": ["tools", "structured_outputs"],
         "pricing": {"prompt": "0.0000008", "completion": "0.000004"}},
    ]}


@app.get("/searchapi/v1/search")
async def search(q: str = "", engine: str = "google") -> dict[str, Any]:
    await asyncio.sleep(0.4)
    return {
        "search_parameters": {"q": q, "engine": engine},
        "search_information": {"total_results": 4310000},
        "organic_results": [
            {"position": 1, "title": "Monetary policy decisions (fake)",
             "link": "https://www.ecb.europa.eu/press/pr/date/2026/html/demo.en.html",
             "source": "ecb.europa.eu", "date": "24 Jul 2026",
             "snippet": "The Governing Council decided to leave the three key ECB interest "
                        "rates unchanged."},
            {"position": 2, "title": "Euro area policy rate history (fake)",
             "link": "https://example.org/rates", "source": "example.org",
             "date": "1 Aug 2026", "snippet": "A table of policy rates since 2024."},
        ],
        "answer_box": {"type": "organic", "answer": "Unchanged",
                       "link": "https://www.ecb.europa.eu/press/pr/date/2026/html/demo.en.html"},
        "_note": "This is tests/fake_openrouter.py, not a real search.",
    }


def main() -> None:
    import uvicorn

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=int, default=3601)
    args = parser.parse_args()
    uvicorn.run(app, host="127.0.0.1", port=args.port, log_level="warning")


if __name__ == "__main__":
    main()
