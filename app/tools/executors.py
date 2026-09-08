"""Tool executors: validated args in, raw API response out.

Each executor owns exactly one logical DataForSEO call. It supplies all
the mechanism the LLM was not allowed to choose (depth, device, language,
result limits) and returns the response untouched.

Parsing belongs to the Extraction node. An executor that reshapes data
has taken over another agent's responsibility.
"""

import time
from dataclasses import dataclass, field
from typing import Any, Callable, Protocol

from app.dataforseo.retry import RetryOutcome
from app.tools.schemas import (
    KeywordMetricsArgs,
    LLMVisibilityArgs,
    SerpOrganicArgs,
)

# --- mechanism: fixed by us, never LLM-chosen ---

SERP_DEPTH = 10
SERP_DEVICE = "desktop"
SERP_OS = "windows"
LANGUAGE_NAME = "English"
KEYWORD_LIMIT = 20


class Client(Protocol):
    """Both the real and mock clients satisfy this."""

    def post(
        self, path: str, payload: list[dict], *, on_retry: Any = None
    ) -> tuple[dict, RetryOutcome]: ...


@dataclass
class ToolExecution:
    """One tool call and everything the observability layer needs about it."""

    tool_name: str
    path: str
    request_payload: list[dict[str, Any]]
    raw_response: dict[str, Any]
    attempts: int
    duration_ms: float
    retry_delays: list[float] = field(default_factory=list)


def _timed(
    tool_name: str,
    path: str,
    payload: list[dict[str, Any]],
    client: Client,
    on_retry: Any = None,
) -> ToolExecution:
    started = time.perf_counter()
    body, outcome = client.post(path, payload, on_retry=on_retry)
    elapsed_ms = (time.perf_counter() - started) * 1000

    return ToolExecution(
        tool_name=tool_name,
        path=path,
        request_payload=payload,
        raw_response=body,
        attempts=outcome.attempts,
        duration_ms=round(elapsed_ms, 2),
        retry_delays=list(outcome.delays),
    )


def serp_organic_lookup(
    args: SerpOrganicArgs, client: Client, *, on_retry: Any = None
) -> ToolExecution:
    """Google organic results for one keyword."""
    payload = [
        {
            "keyword": args.keyword,
            "location_name": args.location,
            "language_name": LANGUAGE_NAME,
            "device": SERP_DEVICE,
            "os": SERP_OS,
            "depth": SERP_DEPTH,
        }
    ]
    return _timed(
        "serp_organic_lookup",
        "/v3/serp/google/organic/live/advanced",
        payload,
        client,
        on_retry,
    )


def llm_visibility_lookup(
    args: LLMVisibilityArgs, client: Client, *, on_retry: Any = None
) -> ToolExecution:
    """One AI platform's answer to one prompt, with citations."""
    payload = [
        {
            "user_prompt": args.prompt,
            "web_search": True,
        }
    ]
    return _timed(
        "llm_visibility_lookup",
        f"/v3/ai_optimization/{args.platform}/llm_responses/live",
        payload,
        client,
        on_retry,
    )


def keyword_metrics_lookup(
    args: KeywordMetricsArgs, client: Client, *, on_retry: Any = None
) -> ToolExecution:
    """Search volume and difficulty for a small keyword batch."""
    payload = [
        {
            "keywords": args.keywords,
            "location_name": args.location,
            "language_name": LANGUAGE_NAME,
            "limit": KEYWORD_LIMIT,
        }
    ]
    return _timed(
        "keyword_metrics_lookup",
        "/v3/dataforseo_labs/google/keyword_overview/live",
        payload,
        client,
        on_retry,
    )


EXECUTORS: dict[str, Callable[..., ToolExecution]] = {
    "serp_organic_lookup": serp_organic_lookup,
    "llm_visibility_lookup": llm_visibility_lookup,
    "keyword_metrics_lookup": keyword_metrics_lookup,
}


def execute(tool_name: str, args: Any, client: Client, *, on_retry: Any = None):
    """Dispatch a validated tool call to its executor."""
    executor = EXECUTORS.get(tool_name)
    if executor is None:
        raise KeyError(f"No executor registered for tool '{tool_name}'")
    return executor(args, client, on_retry=on_retry)
