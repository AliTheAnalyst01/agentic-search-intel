.PHONY: help install run test demo demo-failure lint clean

help:
	@echo "make install       - install dependencies"
	@echo "make run           - start the API on :8000"
	@echo "make test          - run the test suite"
	@echo "make demo          - full DAG run, mock DataForSEO, real LLM"
	@echo "make demo-failure  - retry, backoff and fallback demonstrations"
	@echo "make clean         - remove the local database and caches"

install:
	uv sync

run:
	uv run uvicorn app.api.main:app --reload --port 8000

test:
	uv run pytest -q

demo:
	uv run python -m scripts.demo_run

demo-failure:
	uv run python -m scripts.demo_failure

lint:
	uv run ruff check app tests || true

clean:
	rm -f agentic_search.db
	find . -type d -name __pycache__ -prune -exec rm -rf {} +
	rm -rf .pytest_cache
