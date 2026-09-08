import pytest

from app.tools.schemas import (
    MAX_KEYWORDS,
    KeywordMetricsArgs,
    LLMVisibilityArgs,
    SerpOrganicArgs,
    validate_tool_call,
)


# --- happy path ---

def test_valid_serp_call():
    result = validate_tool_call(
        "serp_organic_lookup",
        {"keyword": "best seo tool", "location": "United Kingdom"},
    )
    assert result.ok
    assert isinstance(result.args, SerpOrganicArgs)
    assert result.args.keyword == "best seo tool"


def test_valid_ai_visibility_call():
    result = validate_tool_call(
        "llm_visibility_lookup",
        {"prompt": "What are the best SEO tools?", "platform": "chat_gpt"},
    )
    assert result.ok
    assert isinstance(result.args, LLMVisibilityArgs)


def test_whitespace_is_stripped():
    result = validate_tool_call(
        "serp_organic_lookup",
        {"keyword": "  best seo tool  ", "location": "United Kingdom"},
    )
    assert result.args.keyword == "best seo tool"


# --- the failures the spec asks about ---

def test_missing_required_field_does_not_crash():
    result = validate_tool_call("serp_organic_lookup", {"keyword": "best seo tool"})

    assert result.ok is False
    assert any("location" in e for e in result.errors)


def test_invalid_enum_value_is_rejected():
    result = validate_tool_call(
        "serp_organic_lookup", {"keyword": "best seo tool", "location": "UK"}
    )

    assert result.ok is False
    assert any("location" in e for e in result.errors)


def test_error_message_lists_the_allowed_values():
    """The planner must be able to self-correct from the message."""
    result = validate_tool_call(
        "serp_organic_lookup", {"keyword": "x y", "location": "Narnia"}
    )
    joined = " ".join(result.errors)

    assert "United Kingdom" in joined


def test_hallucinated_parameter_is_rejected():
    result = validate_tool_call(
        "serp_organic_lookup",
        {"keyword": "best seo tool", "location": "United Kingdom", "depth": 700},
    )

    assert result.ok is False
    assert any("depth" in e for e in result.errors)


def test_unknown_tool_name_lists_known_tools():
    result = validate_tool_call("call_dataforseo", {"anything": 1})

    assert result.ok is False
    assert any("serp_organic_lookup" in e for e in result.errors)


def test_non_dict_arguments_do_not_crash():
    result = validate_tool_call("serp_organic_lookup", "keyword=best seo tool")

    assert result.ok is False
    assert result.args is None


def test_blank_keyword_is_rejected():
    result = validate_tool_call(
        "serp_organic_lookup", {"keyword": "   ", "location": "United Kingdom"}
    )
    assert result.ok is False


# --- cost controls ---

def test_keyword_batch_is_capped():
    too_many = [f"keyword {i}" for i in range(MAX_KEYWORDS + 3)]
    result = validate_tool_call(
        "keyword_metrics_lookup",
        {"keywords": too_many, "location": "United Kingdom"},
    )

    assert result.ok is False
    assert any("keywords" in e for e in result.errors)


def test_duplicate_keywords_are_collapsed():
    result = validate_tool_call(
        "keyword_metrics_lookup",
        {"keywords": ["seo tool", "SEO Tool", "content brief"], "location": "Canada"},
    )

    assert result.ok
    assert len(result.args.keywords) == 2


def test_empty_keyword_list_is_rejected():
    result = validate_tool_call(
        "keyword_metrics_lookup", {"keywords": [], "location": "Canada"}
    )
    assert result.ok is False


# --- schema surface ---

@pytest.mark.parametrize(
    "schema,forbidden",
    [
        (SerpOrganicArgs, "depth"),
        (SerpOrganicArgs, "device"),
        (LLMVisibilityArgs, "api_key"),
        (KeywordMetricsArgs, "limit"),
    ],
)
def test_mechanism_params_are_not_exposed_to_the_llm(schema, forbidden):
    assert forbidden not in schema.model_fields
