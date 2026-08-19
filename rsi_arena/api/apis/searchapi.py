"""SearchApi.io — Google search, news and scholar.

The first registered API, and the template for every other one: a single
:class:`~rsi_arena.api.APISpec` literal, no client code. Everything below is
data — the endpoints, their parameters, and a ``parse`` function that trims
the response down to what an agent should actually read.

Auth is a bearer token from ``SEARCHAPI_API_KEY`` (SearchApi also accepts
``api_key`` as a query parameter, but a header keeps the key out of logs and
out of our cache keys).

All three endpoints are the same path with a different ``engine`` — which is
exactly the case ``Endpoint.defaults`` exists for.
"""

from __future__ import annotations

import os
from typing import Any

from ...core.ratelimit import RateLimit
from ..auth import BearerAuth
from ..registry import register_api
from ..spec import APISpec, Endpoint, Param

# SearchApi bills per successful search. The paid plans land around $0.004 per
# search; override with ``SEARCHAPI_SPEC.cost_per_call = ...`` if your plan
# differs. It only affects reported cost, never behaviour.
COST_PER_SEARCH_USD = 0.004


def _trim_organic(result: dict[str, Any]) -> dict[str, Any]:
    return {
        "position": result.get("position"),
        "title": result.get("title"),
        "link": result.get("link"),
        "source": result.get("source") or result.get("domain"),
        "date": result.get("date"),
        "snippet": result.get("snippet"),
    }


def parse_search(data: Any) -> dict[str, Any]:
    """Keep the parts an agent can act on; drop the rest.

    A raw Google response is tens of kilobytes of ads, sitelinks and pagination
    that cost tokens to read and never change an answer. The answer box and
    knowledge graph stay because they often contain the answer outright.
    """
    if not isinstance(data, dict):
        return {"results": [], "raw": data}
    out: dict[str, Any] = {
        "query": (data.get("search_parameters") or {}).get("q", ""),
        "total_results": (data.get("search_information") or {}).get("total_results"),
        "results": [_trim_organic(r) for r in (data.get("organic_results") or [])],
    }
    if data.get("answer_box"):
        box = data["answer_box"]
        out["answer_box"] = {
            "type": box.get("type"),
            "answer": box.get("answer") or box.get("snippet"),
            "link": box.get("link"),
        }
    if data.get("knowledge_graph"):
        graph = data["knowledge_graph"]
        out["knowledge_graph"] = {
            "title": graph.get("title"),
            "type": graph.get("type"),
            "description": graph.get("description"),
        }
    if data.get("related_questions"):
        out["related_questions"] = [
            q.get("question") for q in data["related_questions"] if q.get("question")
        ]
    return out


_COMMON = (
    Param("q", "The search query. Supports operators like site:, intitle: and OR.",
          required=True),
    Param("num", "Results to return.", type="integer", default=10),
    Param("page", "1-indexed page of results.", type="integer"),
    Param("gl", "Country code for localisation, e.g. us, gb.", default="us"),
    Param("hl", "Interface language, e.g. en.", default="en"),
    Param("location", "Geographic location to search from, e.g. 'New York,United States'."),
    Param("time_period", "Recency filter.",
          enum=["last_hour", "last_day", "last_week", "last_month", "last_year"]),
)

SEARCHAPI = register_api(
    APISpec(
        name="searchapi",
        # Overridable so the stack can be pointed at the local fake in
        # ``tests/fake_openrouter.py`` and demonstrated without a key.
        base_url=os.environ.get("SEARCHAPI_BASE_URL", "https://www.searchapi.io/api/v1"),
        description="Google search, news and scholar results via SearchApi.io.",
        auth=BearerAuth(env_var="SEARCHAPI_API_KEY"),
        # Their plans are quota-based rather than rate-limited, so this is a
        # politeness ceiling rather than a documented limit.
        rate_limit=RateLimit(per_second=5, burst=10, concurrency=5),
        cost_per_call=COST_PER_SEARCH_USD,
        # Search results for a given query are stable for minutes at a time;
        # an hour of caching turns a loop that re-searches into a free one.
        cache_ttl_s=3600,
        endpoints=[
            Endpoint(
                name="search",
                path="/search",
                description="Run a Google web search and return the organic results.",
                params=_COMMON,
                defaults={"engine": "google"},
                parse=parse_search,
            ),
            Endpoint(
                name="news",
                path="/search",
                description="Run a Google News search. Best for recent events.",
                params=_COMMON,
                defaults={"engine": "google_news"},
                parse=parse_search,
            ),
            Endpoint(
                name="scholar",
                path="/search",
                description="Search Google Scholar for academic papers.",
                params=_COMMON,
                defaults={"engine": "google_scholar"},
                parse=parse_search,
            ),
        ],
    )
)
