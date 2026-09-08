import pytest
import structlog

from app.dataforseo.errors import AuthError, RateLimitError
from app.observability.logging import REDACTED, redact, run_context
from app.observability.metrics import RunMetrics, node_span


# --- redaction ---

def test_sensitive_keys_are_redacted():
    out = redact({"login": "me@example.com", "password": "hunter2", "keyword": "seo"})

    assert out["password"] == REDACTED
    assert out["login"] == REDACTED
    assert out["keyword"] == "seo"


def test_a_sensitive_block_is_redacted_wholesale():
    """An auth object is replaced entirely, not walked into."""
    out = redact({"payload": [{"auth": {"api_key": "abc"}, "depth": 10}]})

    assert out["payload"][0]["auth"] == REDACTED
    assert out["payload"][0]["depth"] == 10


def test_redaction_recurses_through_safe_containers():
    out = redact({"tasks": [{"data": {"password": "hunter2", "keyword": "seo"}}]})

    assert out["tasks"][0]["data"]["password"] == REDACTED
    assert out["tasks"][0]["data"]["keyword"] == "seo"


def test_redaction_preserves_keys():
    """Knowing the field was present helps debug auth failures."""
    assert "password" in redact({"password": "x"})


def test_redaction_is_case_insensitive():
    assert redact({"API_Key": "x"})["API_Key"] == REDACTED


# --- correlation id ---

def test_run_context_binds_a_run_id(captured_logs):
    with run_context() as run_id:
        structlog.get_logger().info("something_happened")

    assert captured_logs[0]["run_id"] == run_id


def test_run_context_unbinds_on_exit(captured_logs):
    with run_context():
        pass
    structlog.get_logger().info("outside")

    assert "run_id" not in captured_logs[-1]


def test_all_lines_in_a_run_share_one_id(captured_logs):
    with run_context("fixed-run-id"):
        m = RunMetrics(run_id="fixed-run-id")
        with node_span("a", m):
            pass
        with node_span("b", m):
            pass

    assert {line["run_id"] for line in captured_logs} == {"fixed-run-id"}


# --- node spans ---

def test_successful_span_logs_start_and_completion(captured_logs):
    m = RunMetrics(run_id="r")
    with node_span("query_planner", m, inputs={"q": "x"}):
        pass

    events = [line["event"] for line in captured_logs]
    assert events == ["node_started", "node_completed"]


def test_span_records_duration_and_status():
    m = RunMetrics(run_id="r")
    with node_span("planner", m):
        pass

    assert m.nodes[0].node == "planner"
    assert m.nodes[0].status == "ok"
    assert m.nodes[0].duration_ms >= 0


def test_span_extras_reach_the_completion_log(captured_logs):
    m = RunMetrics(run_id="r")
    with node_span("planner", m) as out:
        out["retrieval_calls_planned"] = 3

    assert captured_logs[-1]["retrieval_calls_planned"] == 3


def test_failed_span_logs_error_type_and_retryability(captured_logs):
    m = RunMetrics(run_id="r")
    with pytest.raises(RateLimitError):
        with node_span("retrieval", m):
            raise RateLimitError("429")

    failure = captured_logs[-1]
    assert failure["event"] == "node_failed"
    assert failure["error_type"] == "RateLimitError"
    assert failure["retryable"] is True


def test_non_retryable_failure_is_marked_as_such(captured_logs):
    m = RunMetrics(run_id="r")
    with pytest.raises(AuthError):
        with node_span("retrieval", m):
            raise AuthError("401")

    assert captured_logs[-1]["retryable"] is False


def test_span_reraises_so_the_graph_can_route():
    """Logging is not error handling."""
    m = RunMetrics(run_id="r")
    with pytest.raises(AuthError):
        with node_span("retrieval", m):
            raise AuthError("401")

    assert m.nodes[0].status == "failed"


def test_span_redacts_inputs(captured_logs):
    m = RunMetrics(run_id="r")
    with node_span("retrieval", m, inputs={"password": "hunter2", "keyword": "seo"}):
        pass

    assert captured_logs[0]["inputs"]["password"] == REDACTED
    assert captured_logs[0]["inputs"]["keyword"] == "seo"


# --- run metrics ---

def test_api_calls_and_retries_are_counted():
    m = RunMetrics(run_id="r")
    m.record_api_call("/v3/serp/google/organic/live/advanced", attempts=3)
    m.record_api_call("/v3/dataforseo_labs/google/keyword_overview/live", attempts=1)

    assert m.api_calls == 2
    assert m.retries == 2


def test_tokens_accumulate_across_calls():
    m = RunMetrics(run_id="r")
    m.record_tokens({"input_tokens": 100, "output_tokens": 20})
    m.record_tokens({"input_tokens": 50, "output_tokens": 10})

    assert m.total_tokens == 180


def test_missing_token_usage_is_tolerated():
    m = RunMetrics(run_id="r")
    m.record_tokens(None)

    assert m.total_tokens == 0


def test_summary_reports_success_rate_and_failures():
    m = RunMetrics(run_id="r")
    with node_span("planner", m):
        pass
    with pytest.raises(RateLimitError):
        with node_span("retrieval", m):
            raise RateLimitError("429")

    summary = m.summary()
    assert summary["nodes_executed"] == 2
    assert summary["success_rate"] == 0.5
    assert summary["failed_nodes"][0]["node"] == "retrieval"
    assert summary["failed_nodes"][0]["retryable"] is True


def test_summary_of_an_empty_run_does_not_divide_by_zero():
    assert RunMetrics(run_id="r").summary()["success_rate"] == 0.0


def test_summary_includes_per_node_latency():
    m = RunMetrics(run_id="r")
    with node_span("planner", m):
        pass

    assert "planner" in m.summary()["node_latency_ms"]
