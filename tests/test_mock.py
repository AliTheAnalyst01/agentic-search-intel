import pytest

from app.dataforseo.errors import (
    AuthError,
    BadRequestError,
    RateLimitError,
    ServerError,
)
from app.dataforseo.mock import MockDataForSEOClient

SERP_PATH = "/v3/serp/google/organic/live/advanced"
AI_PATH = "/v3/ai_optimization/chat_gpt/llm_responses/live"
KW_PATH = "/v3/dataforseo_labs/google/keyword_overview/live"


def test_serp_fixture_has_the_expected_shape():
    body, outcome = MockDataForSEOClient().post(SERP_PATH, [{}])
    items = body["tasks"][0]["result"][0]["items"]

    assert body["status_code"] == 20000
    assert outcome.attempts == 1
    assert len(items) == 5
    assert items[2]["domain"] == "surferseo.com"
    assert items[2]["rank_absolute"] == 3


def test_ai_fixture_carries_citations():
    body, _ = MockDataForSEOClient().post(AI_PATH, [{}])
    sections = body["tasks"][0]["result"][0]["items"][0]["sections"]
    citations = sections[0]["annotations"]

    assert any("surferseo.com" in c["url"] for c in citations)


def test_keyword_fixture_has_varied_volumes():
    body, _ = MockDataForSEOClient().post(KW_PATH, [{}])
    results = body["tasks"][0]["result"]
    volumes = [r["keyword_info"]["search_volume"] for r in results]

    assert len(set(volumes)) == len(volumes), "ties make scoring untestable"


def test_unknown_path_raises_rather_than_returning_junk():
    with pytest.raises(BadRequestError):
        MockDataForSEOClient().post("/v3/backlinks/summary/live", [{}])


def test_scripted_transient_failure_then_recovery():
    client = MockDataForSEOClient(
        fail_script={"serp": [RateLimitError("429"), None]}
    )
    body, outcome = client.post(SERP_PATH, [{}])

    assert body["status_code"] == 20000
    assert outcome.attempts == 2
    assert client.call_count == 2


def test_scripted_non_retryable_failure_stops_immediately():
    client = MockDataForSEOClient(fail_script={"serp": [AuthError("401")]})

    with pytest.raises(AuthError):
        client.post(SERP_PATH, [{}])

    assert client.call_count == 1


def test_scripted_exhaustion_raises_after_max_attempts():
    client = MockDataForSEOClient(
        fail_script={"serp": [ServerError("500")] * 5}
    )

    with pytest.raises(ServerError):
        client.post(SERP_PATH, [{}])

    assert client.call_count == 3


def test_fail_script_is_scoped_to_matching_paths():
    client = MockDataForSEOClient(fail_script={"serp": [AuthError("401")]})
    body, _ = client.post(KW_PATH, [{}])

    assert body["status_code"] == 20000


def test_call_log_records_paths_in_order():
    client = MockDataForSEOClient()
    client.post(SERP_PATH, [{}])
    client.post(KW_PATH, [{}])

    assert client.call_log == [SERP_PATH, KW_PATH]
