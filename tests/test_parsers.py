from app.dataforseo.mock import load_fixture
from app.nodes.parsers import (
    normalize_key,
    parse_ai_response,
    parse_keyword_metrics,
    parse_serp,
)

SERP = load_fixture("serp_organic.json")
KEYWORDS = load_fixture("keyword_data.json")
AI = load_fixture("llm_responses.json")


# --- serp ---

def test_visible_domain_gets_its_rank():
    fragment = parse_serp(SERP, "surferseo.com")

    assert fragment.domain_visible is True
    assert fragment.visibility_position == 3
    assert fragment.keyword == "best seo tool"


def test_absent_domain_is_not_visible():
    fragment = parse_serp(SERP, "example.com")

    assert fragment.domain_visible is False
    assert fragment.visibility_position is None


def test_www_prefix_is_ignored():
    assert parse_serp(SERP, "www.surferseo.com").visibility_position == 3


def test_empty_serp_body_returns_none():
    assert parse_serp({}, "surferseo.com") is None
    assert parse_serp({"tasks": [{"result": []}]}, "surferseo.com") is None


# --- keyword metrics ---

def test_every_keyword_in_the_batch_is_parsed():
    fragments = parse_keyword_metrics(KEYWORDS)

    assert len(fragments) == 3
    assert fragments[0].search_volume == 8100
    assert fragments[0].difficulty == 74


def test_missing_metrics_default_to_zero():
    body = {"tasks": [{"result": [{"keyword": "x y"}]}]}
    fragment = parse_keyword_metrics(body)[0]

    assert fragment.search_volume == 0
    assert fragment.difficulty == 0


def test_results_without_a_keyword_are_skipped():
    assert parse_keyword_metrics({"tasks": [{"result": [{"keyword_info": {}}]}]}) == []


# --- ai responses ---

def test_brand_cited_in_an_ai_answer_is_detected():
    fragment = parse_ai_response(AI, "Surfer SEO", "surferseo.com")

    assert fragment.brand_mentioned is True
    assert any("surferseo.com" in c for c in fragment.citations)


def test_unrelated_brand_is_not_detected():
    fragment = parse_ai_response(AI, "Nonexistent Co", "nonexistent.example")

    assert fragment.brand_mentioned is False


def test_platform_and_prompt_are_captured():
    fragment = parse_ai_response(AI, "Surfer SEO", "surferseo.com")

    assert fragment.platform
    assert "SEO" in fragment.prompt


def test_malformed_ai_body_returns_none():
    assert parse_ai_response({"tasks": []}, "X", "x.com") is None


# --- merge key ---

def test_case_and_whitespace_are_normalized():
    assert normalize_key("  Best   SEO Tool ") == normalize_key("best seo tool")
