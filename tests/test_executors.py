import pytest

from app.dataforseo.errors import AuthError, RateLimitError
from app.dataforseo.mock import MockDataForSEOClient
from app.tools.executors import (
    KEYWORD_LIMIT,
    SERP_DEPTH,
    execute,
    keyword_metrics_lookup,
    llm_visibility_lookup,
    serp_organic_lookup,
)
from app.tools.schemas import validate_tool_call


def valid(tool: str, raw: dict):
    result = validate_tool_call(tool, raw)
    assert result.ok, result.errors
    return result.args


# --- payload construction ---

def test_serp_payload_carries_llm_intent():
    args = valid("serp_organic_lookup", {"keyword": "best seo tool", "location": "Canada"})
    execution = serp_organic_lookup(args, MockDataForSEOClient())
    sent = execution.request_payload[0]

    assert sent["keyword"] == "best seo tool"
    assert sent["location_name"] == "Canada"


def test_serp_payload_supplies_mechanism_the_llm_never_saw():
    args = valid(
        "serp_organic_lookup", {"keyword": "best seo tool", "location": "United Kingdom"}
    )
    sent = serp_organic_lookup(args, MockDataForSEOClient()).request_payload[0]

    assert sent["depth"] == SERP_DEPTH
    assert sent["device"] == "desktop"
    assert sent["language_name"] == "English"


def test_keyword_payload_caps_results():
    args = valid(
        "keyword_metrics_lookup",
        {"keywords": ["seo tool", "content brief"], "location": "United States"},
    )
    sent = keyword_metrics_lookup(args, MockDataForSEOClient()).request_payload[0]

    assert sent["keywords"] == ["seo tool", "content brief"]
    assert sent["limit"] == KEYWORD_LIMIT


def test_platform_selects_the_endpoint_path():
    for platform in ("chat_gpt", "gemini", "perplexity"):
        args = valid(
            "llm_visibility_lookup",
            {"prompt": "What are the best SEO tools?", "platform": platform},
        )
        execution = llm_visibility_lookup(args, MockDataForSEOClient())
        assert f"/{platform}/" in execution.path


# --- one tool, one endpoint ---

def test_each_tool_hits_a_distinct_path():
    client = MockDataForSEOClient()

    serp_organic_lookup(
        valid("serp_organic_lookup", {"keyword": "a b", "location": "Canada"}), client
    )
    llm_visibility_lookup(
        valid("llm_visibility_lookup", {"prompt": "best seo tools?", "platform": "claude"}),
        client,
    )
    keyword_metrics_lookup(
        valid("keyword_metrics_lookup", {"keywords": ["a b"], "location": "Canada"}), client
    )

    assert len(set(client.call_log)) == 3


# --- executors do not parse ---

def test_response_is_returned_unmodified():
    """Parsing is the Extraction node's job, not the executor's."""
    args = valid("serp_organic_lookup", {"keyword": "a b", "location": "Canada"})
    execution = serp_organic_lookup(args, MockDataForSEOClient())

    assert execution.raw_response["status_code"] == 20000
    assert "tasks" in execution.raw_response


# --- observability metadata ---

def test_successful_call_reports_one_attempt_and_a_duration():
    args = valid("serp_organic_lookup", {"keyword": "a b", "location": "Canada"})
    execution = serp_organic_lookup(args, MockDataForSEOClient())

    assert execution.attempts == 1
    assert execution.duration_ms >= 0
    assert execution.retry_delays == []


def test_retried_call_reports_attempts_and_delays():
    client = MockDataForSEOClient(fail_script={"serp": [RateLimitError("429"), None]})
    args = valid("serp_organic_lookup", {"keyword": "a b", "location": "Canada"})
    execution = serp_organic_lookup(args, client)

    assert execution.attempts == 2
    assert len(execution.retry_delays) == 1


def test_on_retry_callback_is_forwarded_to_the_client():
    seen: list[int] = []
    client = MockDataForSEOClient(fail_script={"serp": [RateLimitError("429"), None]})
    args = valid("serp_organic_lookup", {"keyword": "a b", "location": "Canada"})

    serp_organic_lookup(
        args, client, on_retry=lambda attempt, err, delay: seen.append(attempt)
    )

    assert seen == [1]


# --- failures propagate as typed errors ---

def test_non_retryable_failure_propagates():
    client = MockDataForSEOClient(fail_script={"serp": [AuthError("401")]})
    args = valid("serp_organic_lookup", {"keyword": "a b", "location": "Canada"})

    with pytest.raises(AuthError):
        serp_organic_lookup(args, client)


# --- dispatch ---

def test_execute_dispatches_by_tool_name():
    args = valid("serp_organic_lookup", {"keyword": "a b", "location": "Canada"})
    execution = execute("serp_organic_lookup", args, MockDataForSEOClient())

    assert execution.tool_name == "serp_organic_lookup"


def test_execute_rejects_an_unregistered_tool():
    with pytest.raises(KeyError):
        execute("call_dataforseo", None, MockDataForSEOClient())


def test_every_schema_has_an_executor():
    """Guards against adding a schema and forgetting to wire it up."""
    from app.tools.executors import EXECUTORS
    from app.tools.schemas import TOOL_SCHEMAS

    assert set(TOOL_SCHEMAS) == set(EXECUTORS)
