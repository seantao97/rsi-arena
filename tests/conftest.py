"""Fixtures. Every test gets a fresh fake backend and clients wired to it.

Nothing here touches the network or reads a real key: ``OPENROUTER_API_KEY``
and ``SEARCHAPI_API_KEY`` are set to placeholders at import time so the
credential checks pass, and every request goes to :mod:`tests.fakes` through
``httpx.MockTransport``.

Rate limits are lifted (``per_second=1000``) because the limiter is correct and
slow, and every test that is not *about* the limiter should not pay for it.
"""

from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

os.environ.setdefault("OPENROUTER_API_KEY", "test-key")
os.environ.setdefault("SEARCHAPI_API_KEY", "test-key")
# Set before ``server.store`` is imported, since it reads the path once. The
# ``client`` fixture then hands each test its own file inside this directory.
os.environ["RSI_ARENA_DB"] = str(Path(tempfile.mkdtemp(prefix="rsi-arena-")) / "arena.db")

from rsi_arena import (  # noqa: E402
    APIClient,
    Agent,
    AgentConfig,
    LLMClient,
    LLMConfig,
    MemoryCache,
    Plan,
    PromptStep,
    RateLimit,
    Toolbox,
    tool,
)
from rsi_arena.evals import InMemoryEvalStore, set_default_eval_store  # noqa: E402
from tests.fakes import Fake  # noqa: E402


@pytest.fixture
def fake() -> Fake:
    """The backend every client in a test talks to."""
    return Fake()


@pytest.fixture
async def http(fake: Fake):
    client = fake.client()
    try:
        yield client
    finally:
        await client.aclose()


@pytest.fixture
def cache() -> MemoryCache:
    return MemoryCache()


@pytest.fixture
async def llm(http, cache: MemoryCache) -> LLMClient:
    return LLMClient(
        api_key="test",
        http_client=http,
        cache=cache,
        auto_pricing=False,
        config=LLMConfig(model="test/model"),
        rate_limit=RateLimit(per_second=1000),
    )


@pytest.fixture
async def api(http, cache: MemoryCache) -> APIClient:
    return APIClient(http_client=http, cache=cache)


@pytest.fixture
def config() -> AgentConfig:
    return AgentConfig(default_model="test/model", max_usd=2.0)


@pytest.fixture
def word_count():
    @tool
    async def word_count(text: str) -> int:
        """Count words in a string."""
        return len(text.split())

    return word_count


@pytest.fixture
def toolbox(word_count) -> Toolbox:
    return Toolbox([word_count])


@pytest.fixture
def simple_agent(config: AgentConfig) -> Agent:
    """One prompt step. The smallest thing that is still an agent."""
    return Agent(
        name="simple",
        context="You answer briefly.",
        config=config,
        plan=Plan(steps=[PromptStep(name="answer", prompt="{{question}}", output_key="answer")]),
    )


@pytest.fixture(autouse=True)
def eval_store() -> InMemoryEvalStore:
    """A fresh default eval store per test, so results never leak between them."""
    store = InMemoryEvalStore()
    set_default_eval_store(store)
    return store


@pytest.fixture
def client(fake: Fake, tmp_path: Path):
    """The backend, with its shared clients swapped for mocked ones.

    Deliberately synchronous: ``TestClient`` runs the app on its own event
    loop, and handing it a client built on the test's loop would mix the two.
    ``MockTransport`` dispatches synchronously, so a client built out here is
    safe to use in there.
    """
    from fastapi.testclient import TestClient

    from server import app as server_app
    from server.store import Store

    with TestClient(server_app.app) as test_client:
        # Swap in the mocked clients now that lifespan has built the real ones.
        shared_cache = MemoryCache()
        server_app.state.llm = LLMClient(api_key="test", http_client=fake.client(),
                                         cache=shared_cache, auto_pricing=False,
                                         rate_limit=RateLimit(per_second=1000))
        server_app.state.api = APIClient(cache=shared_cache, http_client=fake.client())
        server_app.state.store = Store(str(tmp_path / "arena.db"))
        server_app.state.evals = InMemoryEvalStore()
        server_app.state.battles = {}
        yield test_client
