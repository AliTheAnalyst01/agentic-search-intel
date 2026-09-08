import pytest
from pydantic import ValidationError

from app.graph.state import (
    BusinessProfile,
    NormalizedQuery,
    PlannedCall,
    RawResult,
    Stage,
    StageError,
    Status,
    merge_lists,
)
from app.graph.status import derive_overall_status


# --- schema hygiene ---

def test_profile_requires_identity_fields():
    with pytest.raises(ValidationError):
        BusinessProfile(name="Surfer SEO")


def test_profile_rejects_unknown_fields():
    with pytest.raises(ValidationError):
        BusinessProfile(
            profile_uuid="p1", name="X", domain="x.com", nonsense=True
        )


def test_normalized_query_defaults_are_safe():
    q = NormalizedQuery(query_uuid="q1", query_text="best seo tool")

    assert q.domain_visible is False
    assert q.visibility_position is None
    assert q.opportunity_score == 0.0


def test_visibility_position_is_nullable():
    """Null means not visible; zero would be a real rank."""
    assert NormalizedQuery(query_uuid="q", query_text="t").visibility_position is None


def test_raw_result_can_carry_a_failure():
    r = RawResult(
        query_uuid="q1",
        tool_name="serp_organic_lookup",
        path="/v3/serp",
        status=Status.FAILED,
        error="RateLimitError: exhausted",
    )

    assert r.status == Status.FAILED
    assert r.raw_response == {}


# --- reducer ---

def test_merge_lists_appends():
    assert merge_lists([1, 2], [3]) == [1, 2, 3]


def test_merge_lists_tolerates_empty_sides():
    assert merge_lists(None, [1]) == [1]
    assert merge_lists([1], None) == [1]
    assert merge_lists(None, None) == []


def test_merge_lists_preserves_both_branches():
    """Without a reducer, concurrent branches overwrite each other."""
    a = [RawResult(query_uuid="a", tool_name="t", path="/p")]
    b = [RawResult(query_uuid="b", tool_name="t", path="/p")]

    assert [r.query_uuid for r in merge_lists(a, b)] == ["a", "b"]


# --- overall status ---

def all_ok() -> dict:
    return {
        "planner_status": Status.OK,
        "retrieval_status": Status.OK,
        "extraction_status": Status.OK,
        "analysis_status": Status.OK,
        "report_status": Status.OK,
    }


def test_all_stages_ok_is_ok():
    assert derive_overall_status(all_ok()) == Status.OK


def test_one_failed_stage_with_a_report_is_partial():
    state = all_ok() | {"retrieval_status": Status.FAILED}
    assert derive_overall_status(state) == Status.PARTIAL


def test_a_partial_stage_is_partial():
    state = all_ok() | {"retrieval_status": Status.PARTIAL}
    assert derive_overall_status(state) == Status.PARTIAL


def test_no_report_is_failed():
    state = all_ok() | {"report_status": Status.FAILED}
    assert derive_overall_status(state) == Status.FAILED


def test_empty_state_is_failed():
    assert derive_overall_status({}) == Status.FAILED


def test_skipped_stage_does_not_make_the_run_partial():
    """A deliberately skipped node is not a failure."""
    state = all_ok() | {"analysis_status": Status.SKIPPED}
    assert derive_overall_status(state) == Status.OK


# --- error records ---

def test_stage_error_carries_routing_information():
    err = StageError(
        stage=Stage.RETRIEVAL,
        error_type="RateLimitError",
        message="429 after 3 attempts",
        retryable=True,
        query_uuid="q1",
    )

    assert err.stage == Stage.RETRIEVAL
    assert err.retryable is True


def test_planned_call_records_the_planner_rationale():
    call = PlannedCall(
        query_uuid="q1",
        tool_name="serp_organic_lookup",
        args={"keyword": "best seo tool", "location": "United Kingdom"},
        rationale="Check organic visibility for the head term",
    )

    assert call.rationale
