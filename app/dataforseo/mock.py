"""Scriptable stand-in for DataForSEOClient.

Substitutable for the real client: same post() signature, same return
type, same exception taxonomy. Nodes must not be able to tell them apart.

Failures are scripted per path via fail_script, which is what makes the
simulated-failure tests deterministic.
"""

import json
from pathlib import Path

from app.dataforseo.classify import classify_body
from app.dataforseo.errors import BadRequestError, DataForSEOError
from app.dataforseo.retry import RetryOutcome, RetryPolicy, call_with_retry

FIXTURES = Path(__file__).parent / "fixtures"

# Maps an API path fragment to the fixture that answers it.
ROUTES: dict[str, str] = {
    "serp/google/organic": "serp_organic.json",
    "ai_optimization": "llm_responses.json",
    "keyword_overview": "keyword_data.json",
}


def load_fixture(name: str) -> dict:
    return json.loads((FIXTURES / name).read_text())


class MockDataForSEOClient:
    """Returns fixtures, or raises errors you scripted in advance."""

    def __init__(
        self,
        *,
        policy: RetryPolicy | None = None,
        fail_script: dict[str, list[DataForSEOError | None]] | None = None,
    ) -> None:
        self.policy = policy or RetryPolicy(max_attempts=3, base_delay=0.0, max_delay=0.0)
        self.fail_script = fail_script or {}
        self.call_log: list[str] = []

    def post(
        self, path: str, payload: list[dict], *, on_retry=None
    ) -> tuple[dict, RetryOutcome]:
        return call_with_retry(
            lambda: self._post_once(path, payload),
            policy=self.policy,
            on_retry=on_retry,
        )

    def _post_once(self, path: str, payload: list[dict]) -> dict:
        self.call_log.append(path)

        for key, script in self.fail_script.items():
            if key in path and script:
                error = script.pop(0)
                if error is not None:
                    raise error

        fixture = self._route(path)
        body = load_fixture(fixture)
        classify_body(body)
        return body

    def _route(self, path: str) -> str:
        for fragment, fixture in ROUTES.items():
            if fragment in path:
                return fixture
        raise BadRequestError(f"No mock fixture registered for path: {path}")

    @property
    def call_count(self) -> int:
        return len(self.call_log)

    def close(self) -> None:
        pass
