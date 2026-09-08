import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.db.models import Base
from app.db.repository import (
    create_profile,
    get_profile,
    get_query,
    list_queries,
    list_recommendations,
    profile_stats,
    save_run,
    to_business_profile,
    to_planned_call,
    update_query,
)
from app.graph.state import (
    Insight,
    NormalizedQuery,
    PlannedCall,
    Recommendation,
    Status,
)


@pytest.fixture
def session():
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    s = factory()
    yield s
    s.close()


def query(uuid="q1", text="best seo tool", score=0.8, status="not_visible"):
    return NormalizedQuery(
        query_uuid=uuid,
        query_text=text,
        estimated_search_volume=8100,
        competitive_difficulty=74,
        opportunity_score=score,
        visibility_status=status,
        domain_visible=status == "visible",
    )


def run_state(run_id="r1", queries=None, recs=None, planned=None):
    return {
        "run_id": run_id,
        "question": "How do we show up?",
        "overall_status": Status.OK,
        "planned_calls": planned
        if planned is not None
        else [
            PlannedCall(
                query_uuid="q1",
                tool_name="serp_organic_lookup",
                args={"keyword": "best seo tool", "location": "United Kingdom"},
                query_text="best seo tool",
            )
        ],
        "normalized_queries": queries if queries is not None else [query()],
        "insights": [Insight(title="t", detail="d", relevance_score=0.8)],
        "recommendations": recs
        if recs is not None
        else [
            Recommendation(
                recommendation_uuid="rec1",
                target_query_uuid="q1",
                content_type="comparison_page",
                title="X vs Y",
                rationale="why",
                target_keywords=["best seo tool"],
                priority="high",
            )
        ],
        "errors": [],
        "report_json": {"counts": {"records_normalized": 1}},
        "report_summary": "Summary text.",
    }


# --- profiles ---

def test_profile_round_trips(session):
    row = create_profile(
        session,
        name="Surfer SEO",
        domain="surferseo.com",
        industry="SEO Software",
        competitors=["clearscope.io"],
    )
    fetched = get_profile(session, row.profile_uuid)

    assert fetched.name == "Surfer SEO"
    assert fetched.competitors == ["clearscope.io"]


def test_profile_converts_to_a_state_model(session):
    row = create_profile(session, name="X", domain="x.com", competitors=["a.com"])
    profile = to_business_profile(row)

    assert profile.profile_uuid == row.profile_uuid
    assert profile.competitors == ["a.com"]


def test_missing_profile_returns_none(session):
    assert get_profile(session, "nope") is None


# --- runs ---

def test_run_persists_queries_and_recommendations(session):
    profile = create_profile(session, name="X", domain="x.com")
    save_run(
        session,
        profile_uuid=profile.profile_uuid,
        state=run_state(),
        metrics_summary={"total_tokens": 1500, "api_calls": 3},
    )

    rows, total = list_queries(session, profile.profile_uuid)
    assert total == 1
    assert rows[0].query_text == "best seo tool"
    assert len(list_recommendations(session, profile.profile_uuid)) == 1


def test_run_stores_tokens_and_metrics(session):
    profile = create_profile(session, name="X", domain="x.com")
    run = save_run(
        session,
        profile_uuid=profile.profile_uuid,
        state=run_state(),
        metrics_summary={"total_tokens": 1500, "api_calls": 3},
    )

    assert run.total_tokens == 1500
    assert run.metrics["api_calls"] == 3


def test_query_stores_the_call_that_produced_it(session):
    """Needed so /recheck can replay without re-planning."""
    profile = create_profile(session, name="X", domain="x.com")
    save_run(
        session,
        profile_uuid=profile.profile_uuid,
        state=run_state(),
        metrics_summary={},
    )

    row = get_query(session, "q1")
    call = to_planned_call(row)

    assert call.tool_name == "serp_organic_lookup"
    assert call.args["keyword"] == "best seo tool"


def test_query_without_a_matching_plan_still_saves(session):
    """Keyword-batch rows have no PlannedCall of their own."""
    profile = create_profile(session, name="X", domain="x.com")
    save_run(
        session,
        profile_uuid=profile.profile_uuid,
        state=run_state(queries=[query("orphan", "other keyword")], recs=[]),
        metrics_summary={},
    )

    row = get_query(session, "orphan")
    assert row is not None
    assert row.tool_name == ""


# --- stats ---

def test_stats_on_a_fresh_profile(session):
    profile = create_profile(session, name="X", domain="x.com")
    stats = profile_stats(session, profile.profile_uuid)

    assert stats["total_runs"] == 0
    assert stats["most_recent_run_status"] is None
    assert stats["average_opportunity_score"] is None


def test_stats_reflect_runs_and_average_score(session):
    profile = create_profile(session, name="X", domain="x.com")
    save_run(
        session,
        profile_uuid=profile.profile_uuid,
        state=run_state(
            "r1", queries=[query("q1", "a", 0.8), query("q2", "b", 0.4)], recs=[]
        ),
        metrics_summary={},
    )
    stats = profile_stats(session, profile.profile_uuid)

    assert stats["total_runs"] == 1
    assert stats["most_recent_run_status"] == "ok"
    assert stats["average_opportunity_score"] == 0.6


# --- listing ---

def test_queries_are_sorted_by_opportunity_descending(session):
    profile = create_profile(session, name="X", domain="x.com")
    save_run(
        session,
        profile_uuid=profile.profile_uuid,
        state=run_state(
            queries=[query("q1", "low", 0.2), query("q2", "high", 0.9)], recs=[]
        ),
        metrics_summary={},
    )
    rows, _ = list_queries(session, profile.profile_uuid)

    assert [r.query_text for r in rows] == ["high", "low"]


def test_min_score_filter(session):
    profile = create_profile(session, name="X", domain="x.com")
    save_run(
        session,
        profile_uuid=profile.profile_uuid,
        state=run_state(
            queries=[query("q1", "low", 0.2), query("q2", "high", 0.9)], recs=[]
        ),
        metrics_summary={},
    )
    rows, total = list_queries(session, profile.profile_uuid, min_score=0.5)

    assert total == 1
    assert rows[0].query_text == "high"


def test_status_filter_distinguishes_unknown(session):
    profile = create_profile(session, name="X", domain="x.com")
    save_run(
        session,
        profile_uuid=profile.profile_uuid,
        state=run_state(
            queries=[
                query("q1", "a", 0.5, "visible"),
                query("q2", "b", 0.5, "unknown"),
                query("q3", "c", 0.5, "not_visible"),
            ],
            recs=[],
        ),
        metrics_summary={},
    )

    for status in ("visible", "unknown", "not_visible"):
        rows, total = list_queries(session, profile.profile_uuid, status=status)
        assert total == 1, status


def test_pagination_returns_the_total_not_just_the_page(session):
    profile = create_profile(session, name="X", domain="x.com")
    save_run(
        session,
        profile_uuid=profile.profile_uuid,
        state=run_state(
            queries=[query(f"q{i}", f"kw {i}", 0.5) for i in range(5)], recs=[]
        ),
        metrics_summary={},
    )
    rows, total = list_queries(session, profile.profile_uuid, page=1, per_page=2)

    assert len(rows) == 2
    assert total == 5


def test_second_page_returns_different_rows(session):
    profile = create_profile(session, name="X", domain="x.com")
    save_run(
        session,
        profile_uuid=profile.profile_uuid,
        state=run_state(
            queries=[query(f"q{i}", f"kw {i}", 1 - i / 10) for i in range(5)], recs=[]
        ),
        metrics_summary={},
    )
    first, _ = list_queries(session, profile.profile_uuid, page=1, per_page=2)
    second, _ = list_queries(session, profile.profile_uuid, page=2, per_page=2)

    assert {r.query_uuid for r in first}.isdisjoint({r.query_uuid for r in second})


def test_only_the_latest_run_is_listed(session):
    profile = create_profile(session, name="X", domain="x.com")
    save_run(
        session,
        profile_uuid=profile.profile_uuid,
        state=run_state("r1", queries=[query("old", "old keyword")], recs=[]),
        metrics_summary={},
    )
    save_run(
        session,
        profile_uuid=profile.profile_uuid,
        state=run_state("r2", queries=[query("new", "new keyword")], recs=[]),
        metrics_summary={},
    )
    rows, total = list_queries(session, profile.profile_uuid)

    assert total == 1
    assert rows[0].query_text == "new keyword"


def test_recommendations_are_sorted_by_priority(session):
    profile = create_profile(session, name="X", domain="x.com")
    recs = [
        Recommendation(
            recommendation_uuid=f"rec{i}",
            target_query_uuid="q1",
            content_type="blog_post",
            title=f"T{i}",
            rationale="r",
            priority=p,
        )
        for i, p in enumerate(["low", "high", "medium"])
    ]
    save_run(
        session,
        profile_uuid=profile.profile_uuid,
        state=run_state(recs=recs),
        metrics_summary={},
    )
    rows = list_recommendations(session, profile.profile_uuid)

    assert [r.priority for r in rows] == ["high", "medium", "low"]


def test_listing_an_unknown_profile_is_empty_not_an_error(session):
    rows, total = list_queries(session, "nope")

    assert rows == []
    assert total == 0


# --- recheck update ---

def test_update_query_writes_fresh_metrics(session):
    profile = create_profile(session, name="X", domain="x.com")
    save_run(
        session,
        profile_uuid=profile.profile_uuid,
        state=run_state(),
        metrics_summary={},
    )

    updated = update_query(
        session, "q1", query("q1", "best seo tool", 0.15, "visible")
    )

    assert updated.opportunity_score == 0.15
    assert updated.visibility_status == "visible"
    assert updated.domain_visible is True


def test_updating_an_unknown_query_returns_none(session):
    assert update_query(session, "nope", query()) is None
