from app.dataforseo.errors import AuthError
from app.dataforseo.mock import MockDataForSEOClient
from app.graph.build import build_graph
from app.graph.routing import after_extraction, after_planner, after_retrieval
from app.graph.state import (
    BusinessProfile,
    NormalizedQuery,
    PlannedCall,
    Stage,
    Status,
)
from app.observability.metrics import RunMetrics
from tests.fakes import FakeLLM, FakeResponse, tool_call

PROFILE = BusinessProfile(
    profile_uuid="p1",
    name="Surfer SEO",
    domain="surferseo.com",
    industry="SEO Software",
    competitors=["clearscope.io"],
)

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


def working_llm() -> FakeLLM:
    return FakeLLM([plan_response(), FakeResponse(content=ANALYSIS_JSON)])


def initial_state() -> dict:
    return {
        "run_id": "r1",
        "profile": PROFILE,
        "question": "How does Surfer SEO show up in search and AI answers?",
    }


# --- routing rules in isolation ---

def test_failed_planner_routes_to_fallback():
    assert after_planner({"planner_status": Status.FAILED}) == "fallback"


def test_partial_planner_continues_to_retrieval():
    assert after_planner({"planner_status": Status.PARTIAL}) == Stage.RETRIEVAL.value


def test_failed_retrieval_routes_to_fallback():
    assert after_retrieval({"retrieval_status": Status.FAILED}) == "fallback"


def test_partial_retrieval_continues():
    """Some data is still worth extracting."""
    assert after_retrieval({"retrieval_status": Status.PARTIAL}) == Stage.EXTRACTION.value


def test_empty_extraction_routes_to_fallback():
    state = {"extraction_status": Status.OK, "normalized_queries": []}
    assert after_extraction(state) == "fallback"


def test_populated_extraction_continues_to_analysis():
    state = {
        "extraction_status": Status.OK,
        "normalized_queries": [NormalizedQuery(query_uuid="q1", query_text="x")],
    }
    assert after_extraction(state) == Stage.ANALYSIS.value


# --- happy path end to end ---

def test_full_run_completes():
    metrics = RunMetrics(run_id="r1")
    graph = build_graph(metrics, MockDataForSEOClient(), llm=working_llm())
    final = graph.invoke(initial_state())

    assert final["overall_status"] in (Status.OK, Status.PARTIAL)
    assert final["report_json"]
    assert final["report_summary"]


def test_full_run_visits_every_agent():
    metrics = RunMetrics(run_id="r1")
    graph = build_graph(metrics, MockDataForSEOClient(), llm=working_llm())
    graph.invoke(initial_state())
    visited = [n.node for n in metrics.nodes]

    assert visited == [
        "query_planner",
        "retrieval",
        "extraction",
        "analysis",
        "report",
    ]


def test_full_run_produces_recommendations():
    metrics = RunMetrics(run_id="r1")
    graph = build_graph(metrics, MockDataForSEOClient(), llm=working_llm())
    final = graph.invoke(initial_state())

    assert len(final["recommendations"]) == 1
    assert final["recommendations"][0].target_query_uuid


def test_full_run_records_api_calls():
    metrics = RunMetrics(run_id="r1")
    graph = build_graph(metrics, MockDataForSEOClient(), llm=working_llm())
    graph.invoke(initial_state())

    assert metrics.api_calls == 2


# --- the fallback path ---

def test_total_retrieval_failure_diverts_to_fallback():
    metrics = RunMetrics(run_id="r1")
    client = MockDataForSEOClient(
        fail_script={
            "serp": [AuthError("401")],
            "keyword_overview": [AuthError("401")],
        }
    )
    graph = build_graph(metrics, client, llm=working_llm())
    final = graph.invoke(initial_state())
    visited = [n.node for n in metrics.nodes]

    assert "fallback" in visited
    assert "analysis" not in visited
    assert final["report_json"], "a report is still produced"


def test_fallback_explains_the_degradation():
    metrics = RunMetrics(run_id="r1")
    client = MockDataForSEOClient(
        fail_script={
            "serp": [AuthError("401")],
            "keyword_overview": [AuthError("401")],
        }
    )
    graph = build_graph(metrics, client, llm=working_llm())
    final = graph.invoke(initial_state())
    reasons = [e.message for e in final["errors"] if e.error_type == "PipelineDegraded"]

    assert reasons
    assert "retrieval" in reasons[0].lower() or "failed" in reasons[0].lower()


def test_failed_planner_skips_retrieval_entirely():
    metrics = RunMetrics(run_id="r1")
    client = MockDataForSEOClient()
    bad_plan = FakeResponse(content="I cannot help with that.")
    graph = build_graph(metrics, client, llm=FakeLLM([bad_plan, bad_plan]))
    final = graph.invoke(initial_state())
    visited = [n.node for n in metrics.nodes]

    assert visited == ["query_planner", "fallback", "report"]
    assert client.call_count == 0
    assert final["overall_status"] == Status.PARTIAL


def test_partial_retrieval_still_reaches_analysis():
    metrics = RunMetrics(run_id="r1")
    client = MockDataForSEOClient(fail_script={"serp": [AuthError("401")]})
    graph = build_graph(metrics, client, llm=working_llm())
    graph.invoke(initial_state())
    visited = [n.node for n in metrics.nodes]

    assert "analysis" in visited
    assert "fallback" not in visited


# --- recheck entry point ---

def test_recheck_enters_at_retrieval():
    metrics = RunMetrics(run_id="r1")
    client = MockDataForSEOClient()
    graph = build_graph(
        metrics, client, llm=working_llm(), entry=Stage.RETRIEVAL.value
    )
    state = {
        **initial_state(),
        "planned_calls": [
            PlannedCall(
                query_uuid="q1",
                tool_name="serp_organic_lookup",
                args={"keyword": "best seo tool", "location": "United Kingdom"},
                query_text="best seo tool",
            )
        ],
        "recheck_query_uuid": "q1",
    }
    final = graph.invoke(state)
    visited = [n.node for n in metrics.nodes]

    assert "query_planner" not in visited
    assert visited[0] == "retrieval"
    assert client.call_count == 1
    assert final["report_json"]


# --- structure ---

def test_graph_is_not_a_linear_chain():
    """Every stage but the report has somewhere else it can go."""
    metrics = RunMetrics(run_id="r1")
    graph = build_graph(metrics, MockDataForSEOClient(), llm=working_llm())
    nodes = graph.get_graph().nodes

    assert "fallback" in nodes
    assert len(nodes) >= 7
