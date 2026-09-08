import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.api.main import app
from app.api.routes import get_db
from app.db.models import Base
from tests.fakes import FakeLLM, FakeResponse, tool_call

ANALYSIS_JSON = (
    '{"insights": [{"title": "Absent from the head term", '
    '"detail": "Not in the top 10.", "query_texts": ["best seo tool"]}], '
    '"recommendations": [{"query_text": "best seo tool", '
    '"content_type": "comparison_page", "title": "Surfer SEO vs alternatives", '
    '"rationale": "Comparison intent.", "target_keywords": ["best seo tool"]}]}'
)


def plan_response() -> FakeResponse:
    return FakeResponse(
        tool_calls=[
            tool_call(
                "SerpOrganicArgs",
                {"keyword": "best seo tool", "location": "United Kingdom"},
                "c1",
            ),
            tool_call(
                "KeywordMetricsArgs",
                {"keywords": ["best seo tool"], "location": "United Kingdom"},
                "c2",
            ),
        ]
    )


@pytest.fixture
def client(monkeypatch):
    """In-memory DB, mock DataForSEO, scripted LLM."""
    # Each connection to sqlite:// is a separate empty database, and
    # FastAPI runs endpoints in a threadpool. StaticPool hands every
    # thread the one connection the tables were created on.
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)

    def override_db():
        session = factory()
        try:
            yield session
            session.commit()
        finally:
            session.close()

    app.dependency_overrides[get_db] = override_db

    from app.api import service
    from app.dataforseo.mock import MockDataForSEOClient

    real_run = service.execute_run
    real_recheck = service.execute_recheck

    def run_with_fakes(session, profile, **kwargs):
        kwargs.setdefault("client", MockDataForSEOClient())
        kwargs.setdefault(
            "llm", FakeLLM([plan_response(), FakeResponse(content=ANALYSIS_JSON)])
        )
        return real_run(session, profile, **kwargs)

    def recheck_with_fakes(session, row, **kwargs):
        kwargs.setdefault("client", MockDataForSEOClient())
        kwargs.setdefault("llm", FakeLLM([FakeResponse(content=ANALYSIS_JSON)]))
        return real_recheck(session, row, **kwargs)

    monkeypatch.setattr("app.api.routes.service.execute_run", run_with_fakes)
    monkeypatch.setattr("app.api.routes.service.execute_recheck", recheck_with_fakes)

    with TestClient(app) as c:
        yield c

    app.dependency_overrides.clear()


def make_profile(client, **overrides) -> str:
    payload = {
        "name": "Surfer SEO",
        "domain": "surferseo.com",
        "industry": "SEO Software",
        "competitors": ["clearscope.io"],
        **overrides,
    }
    response = client.post("/api/v1/profiles", json=payload)
    assert response.status_code == 201, response.text
    return response.json()["profile_uuid"]


# --- profiles ---

def test_create_profile_returns_201_and_a_uuid(client):
    response = client.post(
        "/api/v1/profiles",
        json={"name": "Surfer SEO", "domain": "surferseo.com"},
    )

    assert response.status_code == 201
    body = response.json()
    assert body["profile_uuid"]
    assert body["status"] == "created"


def test_url_is_normalized_to_a_bare_domain(client):
    response = client.post(
        "/api/v1/profiles",
        json={"name": "X", "domain": "https://www.surferseo.com/"},
    )

    assert response.json()["domain"] == "surferseo.com"


def test_missing_domain_is_422(client):
    assert client.post("/api/v1/profiles", json={"name": "X"}).status_code == 422


def test_domain_without_a_dot_is_422(client):
    response = client.post("/api/v1/profiles", json={"name": "X", "domain": "localhost"})
    assert response.status_code == 422


def test_unknown_field_is_rejected(client):
    response = client.post(
        "/api/v1/profiles",
        json={"name": "X", "domain": "x.com", "budget": 5000},
    )
    assert response.status_code == 422


def test_get_unknown_profile_is_404(client):
    assert client.get("/api/v1/profiles/nope").status_code == 404


def test_fresh_profile_stats_are_zeroed(client):
    pid = make_profile(client)
    body = client.get(f"/api/v1/profiles/{pid}").json()

    assert body["total_runs"] == 0
    assert body["most_recent_run_status"] is None
    assert body["average_opportunity_score"] is None


# --- run ---

def test_run_returns_the_documented_shape(client):
    pid = make_profile(client)
    response = client.post(f"/api/v1/profiles/{pid}/run", json={})

    assert response.status_code == 200
    body = response.json()
    for field in (
        "pipeline_run_uuid",
        "status",
        "retrieval_calls_planned",
        "records_normalized",
        "top_insights",
        "report",
        "report_summary",
        "total_tokens",
    ):
        assert field in body, field


def test_run_produces_data(client):
    pid = make_profile(client)
    body = client.post(f"/api/v1/profiles/{pid}/run", json={}).json()

    assert body["status"] in ("ok", "partial")
    assert body["retrieval_calls_planned"] == 2
    assert body["records_normalized"] >= 1
    assert body["report_summary"]


def test_run_report_includes_metrics(client):
    pid = make_profile(client)
    body = client.post(f"/api/v1/profiles/{pid}/run", json={}).json()

    assert body["metrics"]["api_calls"] == 2
    assert "node_latency_ms" in body["metrics"]


def test_run_on_unknown_profile_is_404(client):
    assert client.post("/api/v1/profiles/nope/run", json={}).status_code == 404


def test_stats_update_after_a_run(client):
    pid = make_profile(client)
    client.post(f"/api/v1/profiles/{pid}/run", json={})
    body = client.get(f"/api/v1/profiles/{pid}").json()

    assert body["total_runs"] == 1
    assert body["most_recent_run_status"] in ("ok", "partial")
    assert body["average_opportunity_score"] is not None


# --- queries ---

def test_queries_are_sorted_by_opportunity(client):
    pid = make_profile(client)
    client.post(f"/api/v1/profiles/{pid}/run", json={})
    queries = client.get(f"/api/v1/profiles/{pid}/queries").json()["queries"]

    scores = [q["opportunity_score"] for q in queries]
    assert scores == sorted(scores, reverse=True)


def test_query_objects_carry_every_documented_field(client):
    pid = make_profile(client)
    client.post(f"/api/v1/profiles/{pid}/run", json={})
    query = client.get(f"/api/v1/profiles/{pid}/queries").json()["queries"][0]

    for field in (
        "query_text",
        "estimated_search_volume",
        "competitive_difficulty",
        "opportunity_score",
        "domain_visible",
        "visibility_position",
        "discovered_at",
    ):
        assert field in query, field


def test_min_score_filter_applies(client):
    pid = make_profile(client)
    client.post(f"/api/v1/profiles/{pid}/run", json={})
    body = client.get(f"/api/v1/profiles/{pid}/queries?min_score=0.99").json()

    assert body["pagination"]["total"] == 0


def test_pagination_reports_the_full_total(client):
    pid = make_profile(client)
    client.post(f"/api/v1/profiles/{pid}/run", json={})
    all_body = client.get(f"/api/v1/profiles/{pid}/queries").json()
    paged = client.get(f"/api/v1/profiles/{pid}/queries?page=1&per_page=1").json()

    assert len(paged["queries"]) == 1
    assert paged["pagination"]["total"] == all_body["pagination"]["total"]


def test_invalid_status_filter_is_422(client):
    pid = make_profile(client)
    response = client.get(f"/api/v1/profiles/{pid}/queries?status=maybe")

    assert response.status_code == 422


def test_out_of_range_min_score_is_422(client):
    pid = make_profile(client)
    assert client.get(f"/api/v1/profiles/{pid}/queries?min_score=5").status_code == 422


def test_queries_before_any_run_are_empty(client):
    pid = make_profile(client)
    body = client.get(f"/api/v1/profiles/{pid}/queries").json()

    assert body["queries"] == []
    assert body["pagination"]["total"] == 0


# --- recommendations ---

def test_recommendations_carry_every_documented_field(client):
    pid = make_profile(client)
    client.post(f"/api/v1/profiles/{pid}/run", json={})
    recs = client.get(f"/api/v1/profiles/{pid}/recommendations").json()["recommendations"]

    assert recs
    for field in (
        "recommendation_uuid",
        "target_query_uuid",
        "content_type",
        "title",
        "rationale",
        "target_keywords",
        "priority",
    ):
        assert field in recs[0], field


def test_recommendation_targets_a_real_query(client):
    pid = make_profile(client)
    client.post(f"/api/v1/profiles/{pid}/run", json={})
    recs = client.get(f"/api/v1/profiles/{pid}/recommendations").json()["recommendations"]
    queries = client.get(f"/api/v1/profiles/{pid}/queries").json()["queries"]
    known = {q["query_uuid"] for q in queries}

    assert recs[0]["target_query_uuid"] in known


# --- recheck ---

def test_recheck_skips_the_planner(client):
    pid = make_profile(client)
    client.post(f"/api/v1/profiles/{pid}/run", json={})
    queries = client.get(f"/api/v1/profiles/{pid}/queries").json()["queries"]
    target = next(q for q in queries if q["visibility_status"] != "unknown")

    body = client.post(f"/api/v1/queries/{target['query_uuid']}/recheck").json()

    assert "query_planner" not in body["metrics"]["node_latency_ms"]
    assert "retrieval" in body["metrics"]["node_latency_ms"]


def test_recheck_returns_the_updated_query(client):
    pid = make_profile(client)
    client.post(f"/api/v1/profiles/{pid}/run", json={})
    queries = client.get(f"/api/v1/profiles/{pid}/queries").json()["queries"]
    target = next(q for q in queries if q["visibility_status"] != "unknown")

    response = client.post(f"/api/v1/queries/{target['query_uuid']}/recheck")

    assert response.status_code == 200
    assert response.json()["query"]["query_uuid"] == target["query_uuid"]


def test_recheck_unknown_query_is_404(client):
    assert client.post("/api/v1/queries/nope/recheck").status_code == 404


def test_recheck_a_keyword_batch_row_is_409(client):
    """Those rows have no retrieval call of their own to replay."""
    pid = make_profile(client)
    client.post(f"/api/v1/profiles/{pid}/run", json={})
    queries = client.get(f"/api/v1/profiles/{pid}/queries").json()["queries"]
    orphans = [q for q in queries if q["visibility_status"] == "unknown"]

    if not orphans:
        pytest.skip("no keyword-batch rows in this fixture run")

    response = client.post(f"/api/v1/queries/{orphans[0]['query_uuid']}/recheck")
    assert response.status_code == 409


# --- health ---

def test_health_reports_the_dataforseo_mode(client):
    body = client.get("/health").json()

    assert body["status"] == "ok"
    assert "dataforseo_mode" in body
