import json

from app.graph.state import BusinessProfile, NormalizedQuery, Status
from app.nodes.analysis import PRIORITY_HIGH, analyse
from app.observability.metrics import RunMetrics
from tests.fakes import FakeLLM, FakeResponse, usage

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
) -> NormalizedQuery:
    return NormalizedQuery(
        query_uuid=uuid,
        query_text=text,
        estimated_search_volume=8100,
        competitive_difficulty=74,
        opportunity_score=score,
        visibility_status=status,
    )


def state(queries: list[NormalizedQuery]) -> dict:
    return {"profile": PROFILE, "normalized_queries": queries, "question": "How do we show up?"}


def payload(insights=None, recs=None) -> str:
    return json.dumps(
        {
            "insights": insights
            if insights is not None
            else [
                {
                    "title": "Not ranking for the head term",
                    "detail": "The brand is absent from the top 10.",
                    "query_texts": ["best seo tool"],
                }
            ],
            "recommendations": recs
            if recs is not None
            else [
                {
                    "query_text": "best seo tool",
                    "content_type": "comparison_page",
                    "title": "Surfer SEO vs the alternatives",
                    "rationale": "Captures comparison intent.",
                    "target_keywords": ["best seo tool", "seo tool comparison"],
                }
            ],
        }
    )


# --- happy path ---

def test_insights_and_recommendations_are_produced():
    llm = FakeLLM([FakeResponse(content=payload(), usage_metadata=usage())])
    result = analyse(state([query()]), metrics(), llm=llm)

    assert result["analysis_status"] == Status.OK
    assert len(result["insights"]) == 1
    assert len(result["recommendations"]) == 1


def test_node_writes_only_its_own_keys():
    llm = FakeLLM([FakeResponse(content=payload())])
    result = analyse(state([query()]), metrics(), llm=llm)

    assert set(result) == {"insights", "recommendations", "analysis_status", "errors"}


def test_fenced_json_is_tolerated():
    llm = FakeLLM([FakeResponse(content=f"```json\n{payload()}\n```")])
    result = analyse(state([query()]), metrics(), llm=llm)

    assert result["analysis_status"] == Status.OK


# --- code owns the numbers ---

def test_priority_is_derived_from_the_opportunity_score():
    llm = FakeLLM([FakeResponse(content=payload())])
    high = analyse(state([query(score=0.9)]), metrics(), llm=llm)

    assert high["recommendations"][0].priority == "high"


def test_low_score_yields_low_priority():
    llm = FakeLLM([FakeResponse(content=payload())])
    low = analyse(state([query(score=0.1)]), metrics(), llm=llm)

    assert low["recommendations"][0].priority == "low"


def test_model_supplied_priority_is_ignored():
    """Two sources of truth would contradict each other."""
    recs = [
        {
            "query_text": "best seo tool",
            "content_type": "blog_post",
            "title": "X",
            "rationale": "y",
            "target_keywords": [],
            "priority": "high",
        }
    ]
    llm = FakeLLM([FakeResponse(content=payload(recs=recs))])
    result = analyse(state([query(score=0.05)]), metrics(), llm=llm)

    assert result["recommendations"][0].priority == "low"


def test_insight_relevance_comes_from_the_related_query():
    llm = FakeLLM([FakeResponse(content=payload())])
    result = analyse(state([query(score=0.77)]), metrics(), llm=llm)

    assert result["insights"][0].relevance_score == 0.77


# --- linkage integrity ---

def test_recommendation_links_to_a_real_query_uuid():
    llm = FakeLLM([FakeResponse(content=payload())])
    result = analyse(state([query("abc-123")]), metrics(), llm=llm)

    assert result["recommendations"][0].target_query_uuid == "abc-123"


def test_recommendation_for_an_invented_query_is_dropped():
    recs = [
        {
            "query_text": "a query nobody looked up",
            "content_type": "blog_post",
            "title": "X",
            "rationale": "y",
            "target_keywords": [],
        }
    ]
    llm = FakeLLM([FakeResponse(content=payload(recs=recs))])
    result = analyse(state([query()]), metrics(), llm=llm)

    assert result["recommendations"] == []
    assert result["errors"][0].error_type == "UnmatchedRecommendation"


def test_query_matching_ignores_case_and_spacing():
    recs = [
        {
            "query_text": "  Best SEO Tool ",
            "content_type": "blog_post",
            "title": "X",
            "rationale": "y",
            "target_keywords": [],
        }
    ]
    llm = FakeLLM([FakeResponse(content=payload(recs=recs))])
    result = analyse(state([query()]), metrics(), llm=llm)

    assert len(result["recommendations"]) == 1


# --- degradation ---

def test_no_queries_is_skipped_not_failed():
    """Nothing to analyse is not the same as analysis breaking."""
    result = analyse(state([]), metrics(), llm=FakeLLM([]))

    assert result["analysis_status"] == Status.SKIPPED
    assert result["insights"] == []


def test_unparsable_llm_output_does_not_crash():
    llm = FakeLLM([FakeResponse(content="I'm afraid I can't do that.")])
    result = analyse(state([query()]), metrics(), llm=llm)

    assert result["analysis_status"] == Status.FAILED
    assert result["errors"][0].error_type == "UnparsableLLMOutput"


def test_llm_exception_is_recorded_not_raised():
    class Exploding:
        def invoke(self, messages):
            raise RuntimeError("upstream 500")

    result = analyse(state([query()]), metrics(), llm=Exploding())

    assert result["analysis_status"] == Status.FAILED
    assert result["errors"][0].error_type == "RuntimeError"


def test_partial_output_keeps_what_parsed():
    llm = FakeLLM([FakeResponse(content=payload(recs=[{"no_title": True}]))])
    result = analyse(state([query()]), metrics(), llm=llm)

    assert len(result["insights"]) == 1
    assert result["recommendations"] == []


def test_output_is_capped():
    many = [
        {"title": f"insight {i}", "detail": "d", "query_texts": ["best seo tool"]}
        for i in range(10)
    ]
    llm = FakeLLM([FakeResponse(content=payload(insights=many))])
    result = analyse(state([query()]), metrics(), llm=llm)

    assert len(result["insights"]) <= 4


# --- prompt content ---

def test_metrics_reach_the_prompt():
    llm = FakeLLM([FakeResponse(content=payload())])
    analyse(state([query()]), metrics(), llm=llm)
    prompt = str(llm.invocations[0])

    assert "8100" in prompt
    assert "surferseo.com" in prompt


def test_unknown_visibility_is_visible_in_the_prompt():
    llm = FakeLLM([FakeResponse(content=payload())])
    analyse(state([query(status="unknown")]), metrics(), llm=llm)

    assert "unknown" in str(llm.invocations[0])


def test_tokens_are_recorded():
    m = metrics()
    llm = FakeLLM([FakeResponse(content=payload(), usage_metadata=usage(300, 80))])
    analyse(state([query()]), m, llm=llm)

    assert m.total_tokens == 380
