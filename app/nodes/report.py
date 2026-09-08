"""Report node.

Sole responsibility: assemble the final structured output. No reasoning
happens here; insights and recommendations already exist in state.

The human-readable summary is templated from the data rather than
LLM-generated, so it can never contradict the JSON it accompanies.
"""

from datetime import datetime, timezone

from app.graph.state import (
    NormalizedQuery,
    PipelineState,
    Stage,
    Status,
)
from app.graph.status import derive_overall_status
from app.observability.metrics import RunMetrics, node_span

TOP_QUERIES_IN_REPORT = 10


def _visibility_breakdown(queries: list[NormalizedQuery]) -> dict[str, int]:
    counts = {"visible": 0, "not_visible": 0, "unknown": 0}
    for q in queries:
        counts[q.visibility_status] = counts.get(q.visibility_status, 0) + 1
    return counts


def _summary_text(state: PipelineState, queries: list[NormalizedQuery]) -> str:
    profile = state["profile"]
    breakdown = _visibility_breakdown(queries)
    insights = state.get("insights", [])
    recommendations = state.get("recommendations", [])
    errors = state.get("errors", [])

    ranked = sorted(queries, key=lambda q: q.opportunity_score, reverse=True)

    lines = [
        f"Search and AI visibility review for {profile.name} ({profile.domain}).",
        "",
        f"Queries analysed: {len(queries)}. "
        f"Visible: {breakdown['visible']}. "
        f"Not visible: {breakdown['not_visible']}. "
        f"Could not be checked: {breakdown['unknown']}.",
    ]

    if ranked:
        lines += ["", "Highest-opportunity queries:"]
        for q in ranked[:5]:
            position = f"#{q.visibility_position}" if q.visibility_position else "not ranking"
            lines.append(
                f"  - {q.query_text} "
                f"(opportunity {q.opportunity_score}, {q.estimated_search_volume} searches/mo, "
                f"difficulty {q.competitive_difficulty}, {position})"
            )

    if insights:
        lines += ["", "Key findings:"]
        lines += [f"  - {i.title}: {i.detail}" for i in insights]

    if recommendations:
        lines += ["", "Recommended content:"]
        for r in recommendations:
            lines.append(f"  - [{r.priority}] {r.title} ({r.content_type})")
            lines.append(f"      {r.rationale}")

    if errors:
        lines += ["", f"Caveats ({len(errors)}):"]
        lines += [f"  - {e.stage.value}: {e.message}" for e in errors[:5]]

    return "\n".join(lines)


def build_report(state: PipelineState, metrics: RunMetrics) -> PipelineState:
    """Node entry point. Returns only the keys this node owns."""
    queries: list[NormalizedQuery] = state.get("normalized_queries", [])

    with node_span(
        Stage.REPORT.value,
        metrics,
        inputs={
            "queries": len(queries),
            "insights": len(state.get("insights", [])),
            "recommendations": len(state.get("recommendations", [])),
        },
    ) as out:
        ranked = sorted(queries, key=lambda q: q.opportunity_score, reverse=True)
        profile = state["profile"]

        report_json = {
            "run_id": state.get("run_id", ""),
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "profile": {
                "profile_uuid": profile.profile_uuid,
                "name": profile.name,
                "domain": profile.domain,
            },
            "question": state.get("question", ""),
            "stage_statuses": {
                "query_planner": _status_value(state.get("planner_status")),
                "retrieval": _status_value(state.get("retrieval_status")),
                "extraction": _status_value(state.get("extraction_status")),
                "analysis": _status_value(state.get("analysis_status")),
            },
            "counts": {
                "retrieval_calls_planned": len(state.get("planned_calls", [])),
                "records_normalized": len(queries),
                "insights": len(state.get("insights", [])),
                "recommendations": len(state.get("recommendations", [])),
            },
            "visibility_breakdown": _visibility_breakdown(queries),
            "queries": [
                q.model_dump(mode="json") for q in ranked[:TOP_QUERIES_IN_REPORT]
            ],
            "insights": [i.model_dump(mode="json") for i in state.get("insights", [])],
            "recommendations": [
                r.model_dump(mode="json") for r in state.get("recommendations", [])
            ],
            "errors": [e.model_dump(mode="json") for e in state.get("errors", [])],
            "metrics": metrics.summary(),
        }

        summary = _summary_text(state, queries)

        # A report always assembles; its status reflects whether it had
        # anything to report on.
        status = Status.OK if queries else Status.PARTIAL

        out["report_bytes"] = len(summary)
        out["status"] = status.value

        interim = {**state, "report_status": status}

        return {
            "report_json": report_json,
            "report_summary": summary,
            "report_status": status,
            "overall_status": derive_overall_status(interim),
        }


def _status_value(status) -> str:
    return status.value if status is not None else Status.PENDING.value
