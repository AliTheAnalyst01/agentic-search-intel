from app.dataforseo.mock import load_fixture
from app.graph.state import BusinessProfile, PlannedCall, RawResult, Status
from app.nodes.extraction import extract
from app.observability.metrics import RunMetrics

PROFILE = BusinessProfile(
    profile_uuid="p1", name="Surfer SEO", domain="surferseo.com", industry="SEO"
)


def metrics() -> RunMetrics:
    return RunMetrics(run_id="r1")


def raw(tool: str, fixture: str, uuid: str, status: Status = Status.OK) -> RawResult:
    return RawResult(
        query_uuid=uuid,
        tool_name=tool,
        path=f"/v3/{tool}",
        raw_response=load_fixture(fixture) if status == Status.OK else {},
        status=status,
        error=None if status == Status.OK else "AuthError: 401",
    )


def serp() -> RawResult:
    return raw("serp_organic_lookup", "serp_organic.json", "q1")


def keywords() -> RawResult:
    return raw("keyword_metrics_lookup", "keyword_data.json", "q2")


def ai() -> RawResult:
    return raw("llm_visibility_lookup", "llm_responses.json", "q3")


def state(results: list[RawResult], planned: list[PlannedCall] | None = None) -> dict:
    return {
        "profile": PROFILE,
        "raw_results": results,
        "planned_calls": planned or [],
    }


# --- basics ---

def test_serp_result_becomes_a_visible_row():
    result = extract(state([serp()]), metrics())
    row = result["normalized_queries"][0]

    assert row.query_text == "best seo tool"
    assert row.domain_visible is True
    assert row.visibility_position == 3
    assert row.visibility_status == "visible"


def test_keyword_batch_becomes_one_row_per_keyword():
    result = extract(state([keywords()]), metrics())

    assert len(result["normalized_queries"]) == 3


def test_node_writes_only_its_own_keys():
    result = extract(state([serp()]), metrics())

    assert set(result) == {"normalized_queries", "extraction_status", "errors"}


def test_node_produces_no_insights():
    """Interpretation belongs to Analysis."""
    result = extract(state([serp()]), metrics())

    assert "insights" not in result


# --- the merge ---

def test_serp_and_keyword_data_merge_onto_one_row():
    result = extract(state([serp(), keywords()]), metrics())
    rows = {r.query_text.lower(): r for r in result["normalized_queries"]}
    merged = rows["best seo tool"]

    assert merged.visibility_position == 3
    assert merged.estimated_search_volume == 8100
    assert merged.competitive_difficulty == 74


def test_merge_is_case_insensitive():
    body = load_fixture("serp_organic.json")
    body["tasks"][0]["result"][0]["keyword"] = "Best SEO Tool"
    shouty = RawResult(
        query_uuid="q1", tool_name="serp_organic_lookup", path="/p", raw_response=body
    )
    result = extract(state([shouty, keywords()]), metrics())
    texts = [r.query_text.lower() for r in result["normalized_queries"]]

    assert texts.count("best seo tool") == 1


def test_merged_row_gets_a_real_opportunity_score():
    result = extract(state([serp(), keywords()]), metrics())
    merged = next(
        r for r in result["normalized_queries"] if r.query_text.lower() == "best seo tool"
    )

    assert 0 < merged.opportunity_score < 1


# --- ai visibility ---

def test_ai_result_records_platform_and_citations():
    result = extract(state([ai()]), metrics())
    row = result["normalized_queries"][0]

    assert row.ai_platforms_checked
    assert any("surferseo.com" in c for c in row.citations)
    assert row.visibility_status == "visible"


# --- degradation ---

def test_failed_retrieval_still_produces_a_row_marked_unknown():
    failed = raw("serp_organic_lookup", "serp_organic.json", "q9", Status.FAILED)
    planned = [
        PlannedCall(
            query_uuid="q9",
            tool_name="serp_organic_lookup",
            args={},
            query_text="seo audit tool",
        )
    ]
    result = extract(state([failed], planned), metrics())
    row = result["normalized_queries"][0]

    assert row.query_text == "seo audit tool"
    assert row.visibility_status == "unknown"
    assert row.domain_visible is False


def test_unknown_visibility_scores_lower_than_a_confirmed_gap():
    """A failed check must not look like a top opportunity."""
    failed = raw("serp_organic_lookup", "serp_organic.json", "q9", Status.FAILED)
    unknown = extract(state([failed]), metrics())["normalized_queries"][0]
    known = extract(state([serp()]), metrics())["normalized_queries"][0]

    assert unknown.opportunity_score < 1.0
    assert known.visibility_status != "unknown"


def test_empty_response_is_recorded_as_unparsable():
    empty = RawResult(
        query_uuid="q1",
        tool_name="serp_organic_lookup",
        path="/p",
        raw_response={"status_code": 20000, "tasks": []},
    )
    result = extract(state([empty]), metrics())

    assert result["extraction_status"] == Status.FAILED
    assert result["errors"][0].error_type == "UnparsableResponse"


def test_mixed_parsable_and_unparsable_is_partial():
    empty = RawResult(
        query_uuid="q9", tool_name="serp_organic_lookup", path="/p", raw_response={}
    )
    result = extract(state([serp(), empty]), metrics())

    assert result["extraction_status"] == Status.PARTIAL
    assert len(result["normalized_queries"]) == 1


def test_no_raw_results_is_a_failed_node():
    result = extract(state([]), metrics())

    assert result["extraction_status"] == Status.FAILED
    assert result["normalized_queries"] == []


def test_unknown_tool_name_is_recorded_not_crashed():
    rogue = RawResult(
        query_uuid="q1", tool_name="mystery_tool", path="/p", raw_response={"a": 1}
    )
    result = extract(state([rogue]), metrics())

    assert result["extraction_status"] == Status.FAILED
    assert len(result["errors"]) == 1


# --- observability ---

def test_span_reports_record_counts(captured_logs):
    extract(state([serp(), keywords()]), metrics())
    completion = captured_logs[-1]

    assert completion["records_normalized"] == 3
    assert completion["responses_parsed"] == 2
