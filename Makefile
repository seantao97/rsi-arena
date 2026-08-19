# Three processes, three terminals. `make demo` needs no API keys; `make backend`
# uses the real ones from your environment.

# python3 rather than python: it resolves both inside a venv and on a bare
# macOS/Linux box, where `python` often does not exist at all.
PYTHON ?= python3

.PHONY: help install test backend web demo fake clean

help:
	@echo "make install    install python deps (runtime + backend)"
	@echo "make test       run every offline test suite (no keys, no network)"
	@echo "make backend    FastAPI on :3600, real API calls, needs OPENROUTER_API_KEY"
	@echo "make web        Next.js on :8050"
	@echo "make fake       local stand-in for OpenRouter and SearchApi on :3601"
	@echo "make demo       backend on :3600 wired to the fake — no keys, no spend"

install:
	$(PYTHON) -m pip install -e ".[server]"
	cd web && npm install

test:
	$(PYTHON) tests/test_end_to_end.py
	$(PYTHON) tests/test_examples.py
	$(PYTHON) tests/test_server.py

backend:
	$(PYTHON) -m server --reload

web:
	cd web && npm run dev

fake:
	$(PYTHON) -m tests.fake_openrouter

demo:
	OPENROUTER_BASE_URL=http://127.0.0.1:3601/api/v1 \
	SEARCHAPI_BASE_URL=http://127.0.0.1:3601/searchapi/v1 \
	OPENROUTER_API_KEY=demo SEARCHAPI_API_KEY=demo \
	$(PYTHON) -m server

clean:
	rm -rf web/.next arena.db arena.db-wal arena.db-shm
	find . -name __pycache__ -type d -prune -exec rm -rf {} +
