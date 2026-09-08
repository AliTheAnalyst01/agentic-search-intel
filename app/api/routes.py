"""REST endpoints."""

from typing import Annotated, Literal

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.orm import Session

from app.api import service
from app.api.schemas import (
    ProfileCreate,
    ProfileCreated,
    ProfileDetail,
    QueryList,
    QueryOut,
    RecheckResponse,
    RecommendationList,
    RecommendationOut,
    RunRequest,
    RunResponse,
    Page,
)
from app.db import repository as repo
from app.db.session import get_session_factory

router = APIRouter(prefix="/api/v1")


def get_db():
    session = get_session_factory()()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


DB = Annotated[Session, Depends(get_db)]


def _require_profile(session: Session, profile_uuid: str):
    profile = repo.get_profile(session, profile_uuid)
    if profile is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Profile {profile_uuid} not found",
        )
    return profile


# --- profiles ---

@router.post(
    "/profiles", response_model=ProfileCreated, status_code=status.HTTP_201_CREATED
)
def create_profile(payload: ProfileCreate, db: DB) -> ProfileCreated:
    row = repo.create_profile(
        db,
        name=payload.name,
        domain=payload.domain,
        industry=payload.industry,
        description=payload.description,
        competitors=payload.competitors,
    )
    return ProfileCreated(
        profile_uuid=row.profile_uuid,
        name=row.name,
        domain=row.domain,
        created_at=row.created_at,
    )


@router.get("/profiles/{profile_uuid}", response_model=ProfileDetail)
def get_profile(profile_uuid: str, db: DB) -> ProfileDetail:
    row = _require_profile(db, profile_uuid)
    stats = repo.profile_stats(db, profile_uuid)

    return ProfileDetail(
        profile_uuid=row.profile_uuid,
        name=row.name,
        domain=row.domain,
        industry=row.industry or "",
        description=row.description or "",
        competitors=list(row.competitors or []),
        created_at=row.created_at,
        **stats,
    )


# --- pipeline ---

@router.post("/profiles/{profile_uuid}/run", response_model=RunResponse)
def trigger_run(profile_uuid: str, payload: RunRequest | None, db: DB) -> RunResponse:
    profile = _require_profile(db, profile_uuid)
    question = payload.question if payload else None

    run = service.execute_run(db, profile, question=question)

    return RunResponse(
        pipeline_run_uuid=run.run_uuid,
        profile_uuid=profile_uuid,
        status=run.status,
        retrieval_calls_planned=run.retrieval_calls_planned,
        records_normalized=run.records_normalized,
        top_insights=list(run.insights or []),
        report=dict(run.report_json or {}),
        report_summary=run.report_summary or "",
        total_tokens=run.total_tokens,
        errors=list(run.errors or []),
        metrics=dict(run.metrics or {}),
    )


@router.get("/profiles/{profile_uuid}/queries", response_model=QueryList)
def list_queries(
    profile_uuid: str,
    db: DB,
    min_score: Annotated[float | None, Query(ge=0.0, le=1.0)] = None,
    status_filter: Annotated[
        Literal["visible", "not_visible", "unknown"] | None, Query(alias="status")
    ] = None,
    page: Annotated[int, Query(ge=1)] = 1,
    per_page: Annotated[int, Query(ge=1, le=100)] = 20,
) -> QueryList:
    _require_profile(db, profile_uuid)
    rows, total = repo.list_queries(
        db,
        profile_uuid,
        min_score=min_score,
        status=status_filter,
        page=page,
        per_page=per_page,
    )

    return QueryList(
        queries=[QueryOut.model_validate(r, from_attributes=True) for r in rows],
        pagination=Page(page=page, per_page=per_page, total=total),
    )


@router.get(
    "/profiles/{profile_uuid}/recommendations", response_model=RecommendationList
)
def list_recommendations(profile_uuid: str, db: DB) -> RecommendationList:
    _require_profile(db, profile_uuid)
    rows = repo.list_recommendations(db, profile_uuid)

    return RecommendationList(
        recommendations=[
            RecommendationOut.model_validate(r, from_attributes=True) for r in rows
        ]
    )


@router.post("/queries/{query_uuid}/recheck", response_model=RecheckResponse)
def recheck_query(query_uuid: str, db: DB) -> RecheckResponse:
    row = repo.get_query(db, query_uuid)
    if row is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Query {query_uuid} not found",
        )
    if not row.tool_name:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=(
                "This query was derived from a keyword batch and has no "
                "retrieval call of its own, so it cannot be rechecked directly."
            ),
        )

    updated, meta = service.execute_recheck(db, row)

    return RecheckResponse(
        query=QueryOut.model_validate(updated, from_attributes=True)
        if updated is not None
        else None,
        **meta,
    )
