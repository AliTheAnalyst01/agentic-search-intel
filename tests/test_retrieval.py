from app.dataforseo.errors import AuthError, RateLimitError, ServerError
from app.dataforseo.mock import MockDataForSEOClient
from app.graph.state import PlannedCall, Status
from app.nodes.retrieval import retrieve
from app.observability.metrics import RunMetrics


def metrics() -> RunMetrics:
    return RunMetrics(run_id="r1")


def serp_call(uuid: str = "q1", keyword: str = "best seo tool") -> PlannedCall:
    return PlannedCall(
        query_uuid=uuid,
        tool_name="serp_organic_lookup",
        args={"keyword": keyword, "location": "United Kingdom"},
        query_text=keyword,
    )


def keywords_call(uuid: str = "q2") -> PlannedCall:
    return PlannedCall(
        query_uuid=uuid,
        tool_name="keyword_metrics_lookup",
        args={"keywords": ["best seo tool"], "location": "United Kingdom"},
    )


def ai_call(uuid: str = "q3") -> PlannedCall:
    return PlannedCall(
        query_uuid=uuid,
        tool_name="llm_visibility_lookup",
        args={"prompt": "What are the best SEO tools?", "platform": "chat_gpt"},
    )


# --- happy path ---

def test_all_calls_succeed():
    state = {"planned_calls": [serp_call(), keywords_call(), ai_call()]}
    result = retrieve(state, metrics(), MockDataForSEOClient())

    assert result["retrieval_status"] == Status.OK
    assert len(result["raw_results"]) == 3
    assert all(r.status == Status.OK for r in result["raw_results"])
    assert result["errors"] == []


def test_raw_response_is_passed_through_unparsed():
    """Parsing belongs to the Extraction node."""
    result = retrieve({"planned_calls": [serp_call()]}, metrics(), MockDataForSEOClient())
    body = result["raw_results"][0].raw_response

    assert body["status_code"] == 20000
    assert "tasks" in body


def test_query_uuid_is_carried_through():
    result = retrieve(
        {"planned_calls": [serp_call("abc-123")]}, metrics(), MockDataForSEOClient()
    )

    assert result["raw_results"][0].query_uuid == "abc-123"


def test_node_writes_only_its_own_keys():
    result = retrieve({"planned_calls": [serp_call()]}, metrics(), MockDataForSEOClient())

    assert set(result) == {"raw_results", "retrieval_status", "errors"}


def test_node_does_not_normalize():
    result = retrieve({"planned_calls": [serp_call()]}, metrics(), MockDataForSEOClient())

    assert "normalized_queries" not in result


# --- failure isolation ---

def test_one_failure_does_not_stop_the_others():
    client = MockDataForSEOClient(fail_script={"serp": [AuthError("401")]})
    state = {"planned_calls": [serp_call(), keywords_call(), ai_call()]}
    result = retrieve(state, metrics(), client)

    statuses = {r.query_uuid: r.status for r in result["raw_results"]}
    assert statuses["q1"] == Status.FAILED
    assert statuses["q2"] == Status.OK
    assert statuses["q3"] == Status.OK
    assert result["retrieval_status"] == Status.PARTIAL


def test_failed_call_still_produces_a_record():
    """Otherwise a failed query is indistinguishable from one never planned."""
    client = MockDataForSEOClient(fail_script={"serp": [AuthError("401")]})
    result = retrieve({"planned_calls": [serp_call()]}, metrics(), client)

    assert len(result["raw_results"]) == 1
    assert result["raw_results"][0].error.startswith("AuthError")


def test_all_calls_failing_is_a_failed_node():
    client = MockDataForSEOClient(
        fail_script={"serp": [AuthError("401")], "keyword_overview": [AuthError("401")]}
    )
    state = {"planned_calls": [serp_call(), keywords_call()]}
    result = retrieve(state, metrics(), client)

    assert result["retrieval_status"] == Status.FAILED
    assert len(result["errors"]) == 2


def test_empty_plan_is_a_failed_node():
    result = retrieve({"planned_calls": []}, metrics(), MockDataForSEOClient())

    assert result["retrieval_status"] == Status.FAILED
    assert result["raw_results"] == []


def test_error_retryability_is_preserved_for_routing():
    client = MockDataForSEOClient(fail_script={"serp": [ServerError("500")] * 5})
    result = retrieve({"planned_calls": [serp_call()]}, metrics(), client)

    assert result["errors"][0].retryable is True
    assert result["errors"][0].query_uuid == "q1"


def test_non_retryable_error_is_marked_as_such():
    client = MockDataForSEOClient(fail_script={"serp": [AuthError("401")]})
    result = retrieve({"planned_calls": [serp_call()]}, metrics(), client)

    assert result["errors"][0].retryable is False


# --- retries happen below this node ---

def test_transient_failure_recovers_and_is_reported_as_ok():
    client = MockDataForSEOClient(fail_script={"serp": [RateLimitError("429"), None]})
    result = retrieve({"planned_calls": [serp_call()]}, metrics(), client)

    assert result["retrieval_status"] == Status.OK
    assert result["raw_results"][0].attempts == 2


def test_retry_count_reaches_the_metrics():
    m = metrics()
    client = MockDataForSEOClient(fail_script={"serp": [RateLimitError("429"), None]})
    retrieve({"planned_calls": [serp_call()]}, m, client)

    assert m.api_calls == 1
    assert m.retries == 1


# --- boundary re-validation ---

def test_corrupted_args_are_caught_without_calling_the_api():
    """State may have been persisted and reloaded; trust nothing."""
    client = MockDataForSEOClient()
    bad = PlannedCall(
        query_uuid="q1",
        tool_name="serp_organic_lookup",
        args={"keyword": "best seo tool", "location": "Narnia"},
    )
    result = retrieve({"planned_calls": [bad]}, metrics(), client)

    assert result["raw_results"][0].status == Status.FAILED
    assert client.call_count == 0


def test_unknown_tool_name_is_rejected_at_the_boundary():
    client = MockDataForSEOClient()
    rogue = PlannedCall(query_uuid="q1", tool_name="call_dataforseo", args={})
    result = retrieve({"planned_calls": [rogue]}, metrics(), client)

    assert result["retrieval_status"] == Status.FAILED
    assert client.call_count == 0


# --- recheck entry point ---

def test_recheck_runs_only_the_targeted_query():
    state = {
        "planned_calls": [serp_call("q1"), keywords_call("q2"), ai_call("q3")],
        "recheck_query_uuid": "q2",
    }
    client = MockDataForSEOClient()
    result = retrieve(state, metrics(), client)

    assert len(result["raw_results"]) == 1
    assert result["raw_results"][0].query_uuid == "q2"
    assert client.call_count == 1


def test_recheck_with_an_unknown_uuid_fails_cleanly():
    state = {"planned_calls": [serp_call("q1")], "recheck_query_uuid": "nope"}
    result = retrieve(state, metrics(), MockDataForSEOClient())

    assert result["retrieval_status"] == Status.FAILED
    assert result["raw_results"] == []


# --- observability ---

def test_span_reports_the_success_and_failure_split(captured_logs):
    client = MockDataForSEOClient(fail_script={"serp": [AuthError("401")]})
    state = {"planned_calls": [serp_call(), keywords_call()]}
    retrieve(state, metrics(), client)

    completion = captured_logs[-1]
    assert completion["calls_succeeded"] == 1
    assert completion["calls_failed"] == 1


def test_api_call_paths_are_recorded_for_the_metrics_summary():
    m = metrics()
    retrieve({"planned_calls": [serp_call(), ai_call()]}, m, MockDataForSEOClient())

    assert len(m.api_call_paths) == 2
    assert any("serp" in p for p in m.api_call_paths)
