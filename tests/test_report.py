from app.graph.state import (
    BusinessProfile,
    Insight,
    NormalizedQuery,
    PlannedCall,
    Recommendation,
    Stage,
    StageError,
    Status,
)
from app.nodes.report import build_report
from app.observability.metrics import RunMetrics

PROFILE = BusinessProfile(
    profile_uuid="p1", name="Surfer SEO", domain="surferseo.com", industry="SEO"
)


def metrics() -> RunMetrics:
    return RunMetrics(run_id="r1")


def query(
    uuid: str = "q1",
    text: str = "best seo tool",
    score: float = 0.8,
    status: str = "not_visible",
    position: int | None = None,
) -> NormalizedQuery:
    return NormalizedQuery(
        query_uuid=uuid,
        query_text=text,
        estimated_search_volume=8100,
        competitive_difficulty=74,
        opportunity_score=score,
        visibility_status=status,
        domain_visible=status == "visible",
        visibility_position=position,
    )


def full_state(**overrides) -> dict:
    base = {
        "run_id": "r1",
        "profile": PROFILE,
        "question": "How do we show up?",
        "planned_calls": [
            PlannedCall(query_uuid="q1", tool_name="serp_organic_lookup", args={})
        ],
        "normalized_queries": [query()],
        "insights": [
            Insight(
                title="Absent from the head term",
                detail="Not in the top 10.",
                relevance_score=0.8,
                related_query_uuids=["q1"],
            )
        ],
        "recommendations": [
            Recommendation(
                recommendation_uuid="rec1",
                target_query_uuid="q1",
                content_type="comparison_page",
                title="Surfer SEO vs alternatives",
                rationale="Captures comparison intent.",
                target_keywords=["best seo tool"],
                priority="high",
            )
        ],
        "errors": [],
        "planner_status": Status.OK,
        "retrieval_status": Status.OK,
        "extraction_status": Status.OK,
        "analysis_status": Status.OK,
    }
    return {**base, **overrides}


# --- structure ---

def test_node_writes_only_its_own_keys():
    result = build_report(full_state(), metrics())

    assert set(result) == {
        "report_json",
        "report_summary",
        "report_status",
        "overall_status",
    }


def test_report_contains_the_counts_the_api_promises():
    counts = build_report(full_state(), metrics())["report_json"]["counts"]

    assert counts["retrieval_calls_planned"] == 1
    assert counts["records_normalized"] == 1
    assert counts["insights"] == 1
    assert counts["recommendations"] == 1


def test_report_includes_metrics_for_the_api_response():
    m = metrics()
    m.record_api_call("/v3/serp/google/organic/live/advanced", attempts=2)
    m.record_tokens({"input_tokens": 500, "output_tokens": 100})
    report = build_report(full_state(), m)["report_json"]

    assert report["metrics"]["api_calls"] == 1
    assert report["metrics"]["retries"] == 1
    assert report["metrics"]["total_tokens"] == 600


def test_report_json_is_serializable():
    import json

    report = build_report(full_state(), metrics())["report_json"]

    assert json.dumps(report)


def test_stage_statuses_are_reported():
    statuses = build_report(full_state(), metrics())["report_json"]["stage_statuses"]

    assert statuses["query_planner"] == "ok"
    assert statuses["retrieval"] == "ok"


def test_missing_stage_status_is_pending_not_a_crash():
    state = full_state()
    del state["analysis_status"]
    statuses = build_report(state, metrics())["report_json"]["stage_statuses"]

    assert statuses["analysis"] == "pending"


# --- ordering and content ---

def test_queries_are_sorted_by_opportunity_descending():
    state = full_state(
        normalized_queries=[
            query("q1", "low value", 0.2),
            query("q2", "high value", 0.9),
            query("q3", "mid value", 0.5),
        ]
    )
    rows = build_report(state, metrics())["report_json"]["queries"]

    assert [r["query_text"] for r in rows] == ["high value", "mid value", "low value"]


def test_visibility_breakdown_counts_all_three_states():
    state = full_state(
        normalized_queries=[
            query("q1", "a", status="visible", position=3),
            query("q2", "b", status="not_visible"),
            query("q3", "c", status="unknown"),
            query("q4", "d", status="unknown"),
        ]
    )
    breakdown = build_report(state, metrics())["report_json"]["visibility_breakdown"]

    assert breakdown == {"visible": 1, "not_visible": 1, "unknown": 2}


def test_report_does_not_reason():
    """No new insights are invented here."""
    state = full_state(insights=[])
    report = build_report(state, metrics())["report_json"]

    assert report["insights"] == []


# --- human-readable summary ---

def test_summary_names_the_brand_and_the_numbers():
    summary = build_report(full_state(), metrics())["report_summary"]

    assert "Surfer SEO" in summary
    assert "8100" in summary


def test_summary_lists_recommendations_with_priority():
    summary = build_report(full_state(), metrics())["report_summary"]

    assert "Surfer SEO vs alternatives" in summary
    assert "[high]" in summary


def test_summary_reports_unchecked_queries_honestly():
    state = full_state(normalized_queries=[query(status="unknown")])
    summary = build_report(state, metrics())["report_summary"]

    assert "Could not be checked: 1" in summary


def test_summary_surfaces_caveats_when_errors_occurred():
    state = full_state(
        errors=[
            StageError(
                stage=Stage.RETRIEVAL,
                error_type="RateLimitError",
                message="429 after 3 attempts",
                retryable=True,
            )
        ]
    )
    summary = build_report(state, metrics())["report_summary"]

    assert "Caveats" in summary
    assert "429 after 3 attempts" in summary


def test_summary_agrees_with_the_json():
    """Templating means the two can never diverge."""
    state = full_state(normalized_queries=[query("q1", "a"), query("q2", "b")])
    result = build_report(state, metrics())

    assert "Queries analysed: 2" in result["report_summary"]
    assert result["report_json"]["counts"]["records_normalized"] == 2


# --- overall status ---

def test_a_clean_run_is_ok():
    result = build_report(full_state(), metrics())

    assert result["overall_status"] == Status.OK


def test_a_run_with_a_failed_stage_is_partial():
    state = full_state(retrieval_status=Status.PARTIAL)
    result = build_report(state, metrics())

    assert result["overall_status"] == Status.PARTIAL


def test_a_report_with_no_data_is_partial_not_failed():
    """The report assembled; there was just nothing to report."""
    state = full_state(normalized_queries=[], insights=[], recommendations=[])
    result = build_report(state, metrics())

    assert result["report_status"] == Status.PARTIAL
    assert result["overall_status"] == Status.PARTIAL


def test_skipped_analysis_does_not_degrade_the_run():
    state = full_state(analysis_status=Status.SKIPPED)
    result = build_report(state, metrics())

    assert result["overall_status"] == Status.OK
