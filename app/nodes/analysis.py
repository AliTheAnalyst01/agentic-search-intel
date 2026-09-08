"""Analysis / Synthesis node.

Sole responsibility: reason over normalized data to produce insights and
content recommendations. It performs no retrieval and no parsing.

Division of labour: the LLM writes prose (titles, rationales, angles);
code owns every number (ranking, scores, priority). Letting the model
assign priority would create a second source of truth that contradicts
the opportunity score.
"""

import json
import uuid
from typing import Any

from app.graph.state import (
    Insight,
    NormalizedQuery,
    PipelineState,
    Recommendation,
    Stage,
    StageError,
    Status,
)
from app.llm import get_llm
from app.observability.metrics import RunMetrics, node_span

MAX_INSIGHTS = 4
MAX_RECOMMENDATIONS = 5
TOP_QUERIES_IN_PROMPT = 8

# Priority thresholds on the opportunity score. Code's decision, not the LLM's.
PRIORITY_HIGH = 0.65
PRIORITY_MEDIUM = 0.40

SYSTEM_PROMPT = """You analyse search and AI-visibility data for a brand.

You will be given a brand profile and a table of queries with metrics.
Produce insights about where the brand stands, and content
recommendations that would close the biggest gaps.

Return ONLY a JSON object, no markdown fences, with this shape:

{
  "insights": [
    {"title": "short headline", "detail": "two or three sentences",
     "query_texts": ["the queries this is about"]}
  ],
  "recommendations": [
    {"query_text": "the query this addresses",
     "content_type": "blog_post" | "landing_page" | "faq" | "comparison_page",
     "title": "suggested content title",
     "rationale": "why this closes the gap",
     "target_keywords": ["keyword", "keyword"]}
  ]
}

Rules:
- At most {max_insights} insights and {max_recs} recommendations.
- Ground every claim in the numbers given. Do not invent metrics.
- Where visibility is "unknown", say the data was unavailable rather than
  assuming the brand is absent.
- Do not assign priority or scores; those are computed elsewhere."""


def _system_prompt() -> str:
    """str.replace, not str.format: the prompt contains literal JSON braces."""
    return SYSTEM_PROMPT.replace("{max_insights}", str(MAX_INSIGHTS)).replace(
        "{max_recs}", str(MAX_RECOMMENDATIONS)
    )


def _priority(score: float) -> str:
    if score >= PRIORITY_HIGH:
        return "high"
    if score >= PRIORITY_MEDIUM:
        return "medium"
    return "low"


def _queries_table(queries: list[NormalizedQuery]) -> str:
    ranked = sorted(queries, key=lambda q: q.opportunity_score, reverse=True)
    lines = [
        "query | volume | difficulty | visibility | position | opportunity"
    ]
    for q in ranked[:TOP_QUERIES_IN_PROMPT]:
        # n/a, not 0: an AI prompt has no search volume by nature, and a
        # zero invites the model to reason about data that never existed.
        volume = q.estimated_search_volume or "n/a"
        difficulty = q.competitive_difficulty or "n/a"
        lines.append(
            f"{q.query_text} | {volume} | {difficulty} | {q.visibility_status} | "
            f"{q.visibility_position if q.visibility_position else '-'} | "
            f"{q.opportunity_score}"
        )
    return "\n".join(lines)


def _user_prompt(state: PipelineState, queries: list[NormalizedQuery]) -> str:
    profile = state["profile"]
    return (
        f"Brand: {profile.name} ({profile.domain})\n"
        f"Industry: {profile.industry or 'unspecified'}\n"
        f"Competitors: {', '.join(profile.competitors) or 'none given'}\n\n"
        f"Queries:\n{_queries_table(queries)}\n\n"
        f"Original question: {state.get('question', 'general visibility review')}"
    )


def _parse_json(content: str) -> dict[str, Any]:
    """Models sometimes fence JSON despite instructions."""
    cleaned = content.strip()
    if cleaned.startswith("```"):
        cleaned = cleaned.split("```")[1]
        cleaned = cleaned.removeprefix("json").strip()
    return json.loads(cleaned)


def _match_query(
    text: str, queries: list[NormalizedQuery]
) -> NormalizedQuery | None:
    target = " ".join(str(text).lower().split())
    for q in queries:
        if " ".join(q.query_text.lower().split()) == target:
            return q
    return None


def analyse(
    state: PipelineState, metrics: RunMetrics, llm: Any = None
) -> PipelineState:
    """Node entry point. Returns only the keys this node owns."""
    queries: list[NormalizedQuery] = state.get("normalized_queries", [])
    llm = llm or get_llm()

    with node_span(
        Stage.ANALYSIS.value, metrics, inputs={"queries": len(queries)}
    ) as out:
        if not queries:
            out["status"] = Status.SKIPPED.value
            out["insights"] = 0
            return {
                "insights": [],
                "recommendations": [],
                "analysis_status": Status.SKIPPED,
                "errors": [
                    StageError(
                        stage=Stage.ANALYSIS,
                        error_type="NoData",
                        message="No normalized queries to analyse",
                        retryable=False,
                    )
                ],
            }

        errors: list[StageError] = []
        insights: list[Insight] = []
        recommendations: list[Recommendation] = []

        try:
            response = llm.invoke(
                [
                    ("system", _system_prompt()),
                    ("user", _user_prompt(state, queries)),
                ]
            )
            metrics.record_tokens(getattr(response, "usage_metadata", None))
            payload = _parse_json(response.content)
        except (json.JSONDecodeError, ValueError, IndexError) as err:
            errors.append(
                StageError(
                    stage=Stage.ANALYSIS,
                    error_type="UnparsableLLMOutput",
                    message=str(err),
                    retryable=False,
                )
            )
            payload = {}
        except Exception as err:
            errors.append(
                StageError(
                    stage=Stage.ANALYSIS,
                    error_type=type(err).__name__,
                    message=str(err),
                    retryable=bool(getattr(err, "retryable", False)),
                )
            )
            payload = {}

        for item in (payload.get("insights") or [])[:MAX_INSIGHTS]:
            if not isinstance(item, dict) or not item.get("title"):
                continue
            related = [
                q.query_uuid
                for text in item.get("query_texts") or []
                if (q := _match_query(text, queries)) is not None
            ]
            scores = [
                q.opportunity_score
                for q in queries
                if q.query_uuid in related
            ]
            insights.append(
                Insight(
                    title=str(item["title"]),
                    detail=str(item.get("detail", "")),
                    # Code owns the number, not the model.
                    relevance_score=round(max(scores), 3) if scores else 0.0,
                    related_query_uuids=related,
                )
            )

        for item in (payload.get("recommendations") or [])[:MAX_RECOMMENDATIONS]:
            if not isinstance(item, dict) or not item.get("title"):
                continue
            target = _match_query(item.get("query_text", ""), queries)
            if target is None:
                errors.append(
                    StageError(
                        stage=Stage.ANALYSIS,
                        error_type="UnmatchedRecommendation",
                        message=f"Recommendation references unknown query: {item.get('query_text')!r}",
                        retryable=False,
                    )
                )
                continue
            recommendations.append(
                Recommendation(
                    recommendation_uuid=str(uuid.uuid4()),
                    target_query_uuid=target.query_uuid,
                    content_type=str(item.get("content_type", "blog_post")),
                    title=str(item["title"]),
                    rationale=str(item.get("rationale", "")),
                    target_keywords=[str(k) for k in item.get("target_keywords") or []],
                    # Derived from the score, never from the model.
                    priority=_priority(target.opportunity_score),
                )
            )

        if not insights and not recommendations:
            status = Status.FAILED
        elif errors:
            status = Status.PARTIAL
        else:
            status = Status.OK

        out["insights"] = len(insights)
        out["recommendations"] = len(recommendations)
        out["status"] = status.value

        return {
            "insights": insights,
            "recommendations": recommendations,
            "analysis_status": status,
            "errors": errors,
        }
