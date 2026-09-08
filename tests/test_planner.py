from app.graph.state import BusinessProfile, Status
from app.nodes.planner import MAX_PLANNED_CALLS, plan_queries
from app.observability.metrics import RunMetrics
from tests.fakes import FakeLLM, FakeResponse, tool_call, usage

PROFILE = BusinessProfile(
    profile_uuid="p1",
    name="Surfer SEO",
    domain="surferseo.com",
    industry="SEO Software",
    description="AI-powered SEO content optimization tool",
    competitors=["clearscope.io", "marketmuse.com"],
)


def state(question: str = "How does Surfer SEO show up in AI answers and search?") -> dict:
    return {"run_id": "r1", "profile": PROFILE, "question": question}


def metrics() -> RunMetrics:
    return RunMetrics(run_id="r1")


def valid_serp(call_id: str = "c1") -> dict:
    return tool_call(
        "SerpOrganicArgs",
        {"keyword": "best seo tool", "location": "United Kingdom"},
        call_id,
    )


def valid_ai(call_id: str = "c2") -> dict:
    return tool_call(
        "LLMVisibilityArgs",
        {"prompt": "What are the best SEO content tools?", "platform": "chat_gpt"},
        call_id,
    )


# --- happy path ---

def test_valid_plan_is_accepted():
    llm = FakeLLM(
        [
            FakeResponse(
                tool_calls=[valid_serp(), valid_ai(), valid_keywords()],
                usage_metadata=usage(),
            )
        ]
    )
    result = plan_queries(state(), metrics(), llm=llm)

    assert result["planner_status"] == Status.OK
    assert len(result["planned_calls"]) == 3
    assert result["errors"] == []


def test_tool_names_are_mapped_to_the_registry():
    llm = FakeLLM([FakeResponse(tool_calls=[valid_serp()])])
    result = plan_queries(state(), metrics(), llm=llm)

    assert result["planned_calls"][0].tool_name == "serp_organic_lookup"


def test_each_planned_call_gets_a_uuid():
    llm = FakeLLM([FakeResponse(tool_calls=[valid_serp(), valid_ai()])])
    result = plan_queries(state(), metrics(), llm=llm)
    uuids = [c.query_uuid for c in result["planned_calls"]]

    assert len(set(uuids)) == 2


def test_query_text_is_derived_per_tool_type():
    llm = FakeLLM([FakeResponse(tool_calls=[valid_serp(), valid_ai()])])
    calls = plan_queries(state(), metrics(), llm=llm)["planned_calls"]

    assert calls[0].query_text == "best seo tool"
    assert calls[1].query_text == "What are the best SEO content tools?"


# --- the node stays in its lane ---

def test_planner_writes_only_its_own_keys():
    llm = FakeLLM([FakeResponse(tool_calls=[valid_serp()])])
    result = plan_queries(state(), metrics(), llm=llm)

    assert set(result) == {"planned_calls", "planner_status", "errors"}


def test_planner_does_not_fetch_anything():
    """No raw_results key: retrieval is a different agent's job."""
    llm = FakeLLM([FakeResponse(tool_calls=[valid_serp()])])
    result = plan_queries(state(), metrics(), llm=llm)

    assert "raw_results" not in result


# --- self-correction ---

def test_invalid_arguments_trigger_one_correction_round():
    bad = tool_call("SerpOrganicArgs", {"keyword": "best seo tool", "location": "UK"})
    llm = FakeLLM(
        [
            FakeResponse(tool_calls=[bad]),
            FakeResponse(tool_calls=[valid_serp(), valid_keywords()]),
        ]
    )
    result = plan_queries(state(), metrics(), llm=llm)

    assert llm.invoke_count == 2
    assert result["planner_status"] == Status.OK
    assert len(result["planned_calls"]) == 2


def test_correction_prompt_contains_the_validator_message():
    bad = tool_call("SerpOrganicArgs", {"keyword": "best seo tool", "location": "UK"})
    llm = FakeLLM([FakeResponse(tool_calls=[bad]), FakeResponse(tool_calls=[valid_serp()])])
    plan_queries(state(), metrics(), llm=llm)

    second_prompt = str(llm.invocations[1])
    assert "United Kingdom" in second_prompt


def test_correction_is_not_attempted_more_than_once():
    bad = tool_call("SerpOrganicArgs", {"keyword": "x y", "location": "Narnia"})
    llm = FakeLLM([FakeResponse(tool_calls=[bad]), FakeResponse(tool_calls=[bad])])
    result = plan_queries(state(), metrics(), llm=llm)

    assert llm.invoke_count == 2, "must not loop indefinitely"
    assert result["planner_status"] == Status.FAILED


# --- degradation ---

def test_no_tool_calls_at_all_is_a_failure():
    llm = FakeLLM([FakeResponse(content="I think you should check Google.")] * 2)
    result = plan_queries(state(), metrics(), llm=llm)

    assert result["planner_status"] == Status.FAILED
    assert result["planned_calls"] == []


def test_partial_plan_is_marked_partial_and_keeps_the_good_calls():
    bad = tool_call("LLMVisibilityArgs", {"prompt": "hi", "platform": "bing_chat"})
    llm = FakeLLM([FakeResponse(tool_calls=[valid_serp(), bad])] * 2)
    result = plan_queries(state(), metrics(), llm=llm)

    assert result["planner_status"] == Status.PARTIAL
    assert len(result["planned_calls"]) == 1
    rejections = [e for e in result["errors"] if e.error_type == "ToolCallValidationError"]
    assert len(rejections) == 1


def test_validation_errors_are_recorded_as_non_retryable():
    bad = tool_call("SerpOrganicArgs", {"keyword": "x y", "location": "Narnia"})
    llm = FakeLLM([FakeResponse(tool_calls=[valid_serp(), bad])] * 2)
    result = plan_queries(state(), metrics(), llm=llm)

    assert result["errors"][0].retryable is False


def test_unknown_tool_name_is_rejected_not_executed():
    rogue = tool_call("call_dataforseo_directly", {"anything": True})
    llm = FakeLLM([FakeResponse(tool_calls=[rogue])] * 2)
    result = plan_queries(state(), metrics(), llm=llm)

    assert result["planned_calls"] == []
    assert result["planner_status"] == Status.FAILED


# --- cost control ---

def test_plan_is_capped_regardless_of_llm_enthusiasm():
    many = [valid_serp(f"c{i}") for i in range(MAX_PLANNED_CALLS + 4)]
    llm = FakeLLM([FakeResponse(tool_calls=many)])
    result = plan_queries(state(), metrics(), llm=llm)

    assert len(result["planned_calls"]) == MAX_PLANNED_CALLS


# --- observability ---

def test_tokens_are_recorded():
    m = metrics()
    llm = FakeLLM([FakeResponse(tool_calls=[valid_serp()], usage_metadata=usage(200, 40))])
    plan_queries(state(), m, llm=llm)

    assert m.total_tokens == 240


def test_missing_usage_metadata_does_not_break_the_node():
    m = metrics()
    llm = FakeLLM([FakeResponse(tool_calls=[valid_serp()], usage_metadata=None)])
    plan_queries(state(), m, llm=llm)

    assert m.total_tokens == 0


def test_node_span_records_the_plan_size(captured_logs):
    llm = FakeLLM([FakeResponse(tool_calls=[valid_serp(), valid_ai()])])
    plan_queries(state(), metrics(), llm=llm)

    completion = captured_logs[-1]
    assert completion["event"] == "node_completed"
    assert completion["retrieval_calls_planned"] == 2


def test_profile_context_reaches_the_prompt():
    llm = FakeLLM([FakeResponse(tool_calls=[valid_serp()])])
    plan_queries(state(), metrics(), llm=llm)

    prompt = str(llm.invocations[0])
    assert "surferseo.com" in prompt
    assert "clearscope.io" in prompt


# --- coverage gaps ---

def valid_keywords(call_id: str = "c3") -> dict:
    return tool_call(
        "KeywordMetricsArgs",
        {"keywords": ["best seo tool", "seo content brief"], "location": "United Kingdom"},
        call_id,
    )


def test_full_coverage_is_ok():
    llm = FakeLLM([FakeResponse(tool_calls=[valid_serp(), valid_ai(), valid_keywords()])])
    result = plan_queries(state(), metrics(), llm=llm)

    assert result["planner_status"] == Status.OK
    assert result["errors"] == []


def test_missing_keyword_metrics_is_partial_not_failed():
    llm = FakeLLM([FakeResponse(tool_calls=[valid_serp(), valid_ai()])] * 2)
    result = plan_queries(state(), metrics(), llm=llm)

    assert result["planner_status"] == Status.PARTIAL
    assert len(result["planned_calls"]) == 2, "the good calls are kept"


def test_coverage_gap_is_recorded_as_an_error_with_a_reason():
    llm = FakeLLM([FakeResponse(tool_calls=[valid_serp()])] * 2)
    result = plan_queries(state(), metrics(), llm=llm)
    gap = [e for e in result["errors"] if e.error_type == "CoverageGap"]

    assert len(gap) == 1
    assert "opportunity" in gap[0].message.lower()


def test_the_planner_does_not_inject_the_missing_call():
    """Planning stays the planner's job; gaps are reported, not patched."""
    llm = FakeLLM([FakeResponse(tool_calls=[valid_serp()])] * 2)
    result = plan_queries(state(), metrics(), llm=llm)

    assert all(c.tool_name != "keyword_metrics_lookup" for c in result["planned_calls"])


def test_truncation_that_drops_coverage_is_reported():
    """The cap runs first, so a lost metrics call is a real gap."""
    many = [valid_serp(f"c{i}") for i in range(MAX_PLANNED_CALLS)] + [valid_keywords()]
    llm = FakeLLM([FakeResponse(tool_calls=many)] * 2)
    result = plan_queries(state(), metrics(), llm=llm)

    assert len(result["planned_calls"]) == MAX_PLANNED_CALLS
    assert result["planner_status"] == Status.PARTIAL


def test_coverage_gap_count_is_logged(captured_logs):
    llm = FakeLLM([FakeResponse(tool_calls=[valid_serp()])] * 2)
    plan_queries(state(), metrics(), llm=llm)

    assert captured_logs[-1]["coverage_gaps"] == 1
