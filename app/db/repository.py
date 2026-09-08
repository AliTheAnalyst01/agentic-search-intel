"""Translation between PipelineState models and database rows.

Every function takes a Session so transaction boundaries stay with the
caller. Nothing here imports from app.graph.build or app.nodes: the
persistence layer knows about data, not about the pipeline.
"""

import uuid
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.db.models import ProfileRow, QueryRow, RecommendationRow, RunRow
from app.graph.state import (
    BusinessProfile,
    NormalizedQuery,
    PipelineState,
    PlannedCall,
    Status,
)

DEFAULT_PER_PAGE = 20
MAX_PER_PAGE = 100


# --- profiles ---

def create_profile(
    session: Session,
    *,
    name: str,
    domain: str,
    industry: str = "",
    description: str = "",
    competitors: list[str] | None = None,
) -> ProfileRow:
    row = ProfileRow(
        profile_uuid=str(uuid.uuid4()),
        name=name,
        domain=domain,
        industry=industry,
        description=description,
        competitors=competitors or [],
    )
    session.add(row)
    session.flush()
    return row


def get_profile(session: Session, profile_uuid: str) -> ProfileRow | None:
    return session.get(ProfileRow, profile_uuid)


def to_business_profile(row: ProfileRow) -> BusinessProfile:
    return BusinessProfile(
        profile_uuid=row.profile_uuid,
        name=row.name,
        domain=row.domain,
        industry=row.industry or "",
        description=row.description or "",
        competitors=list(row.competitors or []),
    )


def profile_stats(session: Session, profile_uuid: str) -> dict[str, Any]:
    """Summary stats for GET /profiles/{uuid}."""
    total_runs = session.scalar(
        select(func.count(RunRow.run_uuid)).where(RunRow.profile_uuid == profile_uuid)
    ) or 0

    latest = session.scalars(
        select(RunRow)
        .where(RunRow.profile_uuid == profile_uuid)
        .order_by(RunRow.created_at.desc())
        .limit(1)
    ).first()

    avg_score = session.scalar(
        select(func.avg(QueryRow.opportunity_score)).where(
            QueryRow.profile_uuid == profile_uuid
        )
    )

    return {
        "total_runs": total_runs,
        "most_recent_run_status": latest.status if latest else None,
        "most_recent_run_uuid": latest.run_uuid if latest else None,
        "average_opportunity_score": round(avg_score, 3) if avg_score is not None else None,
    }


# --- runs ---

def save_run(
    session: Session,
    *,
    profile_uuid: str,
    state: PipelineState,
    metrics_summary: dict[str, Any],
) -> RunRow:
    """Persist a completed run and everything it produced."""
    run_uuid = state.get("run_id") or str(uuid.uuid4())
    queries: list[NormalizedQuery] = state.get("normalized_queries", [])
    planned: list[PlannedCall] = state.get("planned_calls", [])

    # query_uuid -> the call that produced it, so /recheck can replay it.
    call_by_uuid = {c.query_uuid: c for c in planned}

    run = RunRow(
        run_uuid=run_uuid,
        profile_uuid=profile_uuid,
        question=state.get("question", ""),
        status=_status_value(state.get("overall_status")),
        retrieval_calls_planned=len(planned),
        records_normalized=len(queries),
        total_tokens=metrics_summary.get("total_tokens", 0),
        report_json=state.get("report_json", {}),
        report_summary=state.get("report_summary", ""),
        insights=[i.model_dump(mode="json") for i in state.get("insights", [])],
        errors=[e.model_dump(mode="json") for e in state.get("errors", [])],
        metrics=metrics_summary,
    )
    session.add(run)

    for q in queries:
        call = call_by_uuid.get(q.query_uuid)
        session.add(
            QueryRow(
                query_uuid=q.query_uuid,
                run_uuid=run_uuid,
                profile_uuid=profile_uuid,
                query_text=q.query_text,
                tool_name=call.tool_name if call else "",
                tool_args=call.args if call else {},
                estimated_search_volume=q.estimated_search_volume,
                competitive_difficulty=q.competitive_difficulty,
                opportunity_score=q.opportunity_score,
                domain_visible=q.domain_visible,
                visibility_position=q.visibility_position,
                visibility_status=q.visibility_status,
                ai_platforms_checked=list(q.ai_platforms_checked),
                citations=list(q.citations),
                discovered_at=q.discovered_at,
            )
        )

    for r in state.get("recommendations", []):
        session.add(
            RecommendationRow(
                recommendation_uuid=r.recommendation_uuid,
                run_uuid=run_uuid,
                profile_uuid=profile_uuid,
                target_query_uuid=r.target_query_uuid,
                content_type=r.content_type,
                title=r.title,
                rationale=r.rationale,
                target_keywords=list(r.target_keywords),
                priority=r.priority,
            )
        )

    session.flush()
    return run


def latest_run(session: Session, profile_uuid: str) -> RunRow | None:
    return session.scalars(
        select(RunRow)
        .where(RunRow.profile_uuid == profile_uuid)
        .order_by(RunRow.created_at.desc())
        .limit(1)
    ).first()


# --- queries ---

def list_queries(
    session: Session,
    profile_uuid: str,
    *,
    min_score: float | None = None,
    status: str | None = None,
    page: int = 1,
    per_page: int = DEFAULT_PER_PAGE,
) -> tuple[list[QueryRow], int]:
    """Most recent run's queries, highest opportunity first.

    Returns (rows, total_matching) so the caller can build pagination meta.
    """
    run = latest_run(session, profile_uuid)
    if run is None:
        return [], 0

    conditions = [QueryRow.run_uuid == run.run_uuid]
    if min_score is not None:
        conditions.append(QueryRow.opportunity_score >= min_score)
    if status is not None:
        conditions.append(QueryRow.visibility_status == status)

    total = session.scalar(
        select(func.count(QueryRow.query_uuid)).where(*conditions)
    ) or 0

    page = max(1, page)
    per_page = max(1, min(per_page, MAX_PER_PAGE))

    rows = list(
        session.scalars(
            select(QueryRow)
            .where(*conditions)
            .order_by(QueryRow.opportunity_score.desc())
            .offset((page - 1) * per_page)
            .limit(per_page)
        )
    )
    return rows, total


def get_query(session: Session, query_uuid: str) -> QueryRow | None:
    return session.get(QueryRow, query_uuid)


def to_planned_call(row: QueryRow) -> PlannedCall:
    """Rebuild the call so /recheck can replay it."""
    return PlannedCall(
        query_uuid=row.query_uuid,
        tool_name=row.tool_name,
        args=dict(row.tool_args or {}),
        query_text=row.query_text,
    )


def update_query(session: Session, query_uuid: str, q: NormalizedQuery) -> QueryRow | None:
    """Write fresh metrics onto an existing query after a recheck."""
    row = session.get(QueryRow, query_uuid)
    if row is None:
        return None

    row.estimated_search_volume = q.estimated_search_volume
    row.competitive_difficulty = q.competitive_difficulty
    row.opportunity_score = q.opportunity_score
    row.domain_visible = q.domain_visible
    row.visibility_position = q.visibility_position
    row.visibility_status = q.visibility_status
    row.ai_platforms_checked = list(q.ai_platforms_checked)
    row.citations = list(q.citations)
    row.discovered_at = q.discovered_at
    session.flush()
    return row


# --- recommendations ---

def list_recommendations(
    session: Session, profile_uuid: str
) -> list[RecommendationRow]:
    run = latest_run(session, profile_uuid)
    if run is None:
        return []

    priority_rank = {"high": 0, "medium": 1, "low": 2}
    rows = list(
        session.scalars(
            select(RecommendationRow).where(RecommendationRow.run_uuid == run.run_uuid)
        )
    )
    return sorted(rows, key=lambda r: priority_rank.get(r.priority, 3))


def _status_value(status) -> str:
    if status is None:
        return Status.PENDING.value
    return status.value if hasattr(status, "value") else str(status)
