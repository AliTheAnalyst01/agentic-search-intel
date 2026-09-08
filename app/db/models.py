"""SQLAlchemy tables.

Deliberately separate from the PipelineState models. The graph works with
plain Pydantic objects and needs no database, which keeps node tests fast
and makes the persistence layer swappable. Translation lives in
repository.py.
"""

from datetime import datetime, timezone

from sqlalchemy import (
    JSON,
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    String,
    Text,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class Base(DeclarativeBase):
    pass


class ProfileRow(Base):
    __tablename__ = "profiles"

    profile_uuid: Mapped[str] = mapped_column(String(36), primary_key=True)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    domain: Mapped[str] = mapped_column(String(253), nullable=False, index=True)
    industry: Mapped[str] = mapped_column(String(200), default="")
    description: Mapped[str] = mapped_column(Text, default="")
    competitors: Mapped[list] = mapped_column(JSON, default=list)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)

    runs: Mapped[list["RunRow"]] = relationship(
        back_populates="profile", cascade="all, delete-orphan"
    )


class RunRow(Base):
    __tablename__ = "runs"

    run_uuid: Mapped[str] = mapped_column(String(36), primary_key=True)
    profile_uuid: Mapped[str] = mapped_column(
        ForeignKey("profiles.profile_uuid"), nullable=False, index=True
    )
    question: Mapped[str] = mapped_column(Text, default="")
    status: Mapped[str] = mapped_column(String(20), nullable=False)

    retrieval_calls_planned: Mapped[int] = mapped_column(Integer, default=0)
    records_normalized: Mapped[int] = mapped_column(Integer, default=0)
    total_tokens: Mapped[int] = mapped_column(Integer, default=0)

    report_json: Mapped[dict] = mapped_column(JSON, default=dict)
    report_summary: Mapped[str] = mapped_column(Text, default="")
    insights: Mapped[list] = mapped_column(JSON, default=list)
    errors: Mapped[list] = mapped_column(JSON, default=list)
    metrics: Mapped[dict] = mapped_column(JSON, default=dict)

    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, index=True)

    profile: Mapped[ProfileRow] = relationship(back_populates="runs")
    queries: Mapped[list["QueryRow"]] = relationship(
        back_populates="run", cascade="all, delete-orphan"
    )
    recommendations: Mapped[list["RecommendationRow"]] = relationship(
        back_populates="run", cascade="all, delete-orphan"
    )


class QueryRow(Base):
    __tablename__ = "queries"

    query_uuid: Mapped[str] = mapped_column(String(36), primary_key=True)
    run_uuid: Mapped[str] = mapped_column(
        ForeignKey("runs.run_uuid"), nullable=False, index=True
    )
    profile_uuid: Mapped[str] = mapped_column(String(36), nullable=False, index=True)

    query_text: Mapped[str] = mapped_column(Text, nullable=False)

    # Kept so /recheck can re-run this exact call without re-planning.
    tool_name: Mapped[str] = mapped_column(String(60), default="")
    tool_args: Mapped[dict] = mapped_column(JSON, default=dict)

    estimated_search_volume: Mapped[int] = mapped_column(Integer, default=0)
    competitive_difficulty: Mapped[int] = mapped_column(Integer, default=0)
    opportunity_score: Mapped[float] = mapped_column(Float, default=0.0, index=True)
    domain_visible: Mapped[bool] = mapped_column(Boolean, default=False)
    visibility_position: Mapped[int | None] = mapped_column(Integer, nullable=True)
    visibility_status: Mapped[str] = mapped_column(String(20), default="unknown", index=True)
    ai_platforms_checked: Mapped[list] = mapped_column(JSON, default=list)
    citations: Mapped[list] = mapped_column(JSON, default=list)

    discovered_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)

    run: Mapped[RunRow] = relationship(back_populates="queries")


class RecommendationRow(Base):
    __tablename__ = "recommendations"

    recommendation_uuid: Mapped[str] = mapped_column(String(36), primary_key=True)
    run_uuid: Mapped[str] = mapped_column(
        ForeignKey("runs.run_uuid"), nullable=False, index=True
    )
    profile_uuid: Mapped[str] = mapped_column(String(36), nullable=False, index=True)
    target_query_uuid: Mapped[str] = mapped_column(String(36), nullable=False)

    content_type: Mapped[str] = mapped_column(String(40), default="blog_post")
    title: Mapped[str] = mapped_column(Text, nullable=False)
    rationale: Mapped[str] = mapped_column(Text, default="")
    target_keywords: Mapped[list] = mapped_column(JSON, default=list)
    priority: Mapped[str] = mapped_column(String(10), default="medium", index=True)

    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)

    run: Mapped[RunRow] = relationship(back_populates="recommendations")
