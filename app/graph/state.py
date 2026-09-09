"""The single state object passed between DAG nodes.

Contracts:
  - Each node reads what it needs and writes only its own keys.
  - Failures are recorded, not raised, so edges can route on them.
  - Every stage has its own status, so a partial run is describable.
"""

from datetime import datetime, timezone
from enum import Enum
from typing import Annotated, Any, Literal, TypedDict

from pydantic import BaseModel, ConfigDict, Field


class Stage(str, Enum):
    PLANNER = "query_planner"
    RETRIEVAL = "retrieval"
    EXTRACTION = "extraction"
    ANALYSIS = "analysis"
    REPORT = "report"


class Status(str, Enum):
    PENDING = "pending"
    OK = "ok"
    PARTIAL = "partial"
    FAILED = "failed"
    SKIPPED = "skipped"


class _Model(BaseModel):
    model_config = ConfigDict(extra="forbid")


class BusinessProfile(_Model):
    """The subject of the run."""

    profile_uuid: str
    name: str
    domain: str
    industry: str = ""
    description: str = ""
    competitors: list[str] = Field(default_factory=list)


class PlannedCall(_Model):
    """One retrieval the planner decided is needed."""

    query_uuid: str
    tool_name: str
    args: dict[str, Any]
    rationale: str = ""
    query_text: str = ""


class RawResult(_Model):
    """One executor's untouched response, plus call metadata."""

    query_uuid: str
    tool_name: str
    path: str
    raw_response: dict[str, Any] = Field(default_factory=dict)
    attempts: int = 1
    duration_ms: float = 0.0
    status: Status = Status.OK
    error: str | None = None


class NormalizedQuery(_Model):
    """Extraction output: one query's cleaned metrics."""

    query_uuid: str
    query_text: str
    # None means the metric was never measured, distinct from a
    # measured zero. Deviates from section 4.2's "integer", for the
    # same reason visibility_status is three-state.
    estimated_search_volume: int | None = None
    competitive_difficulty: int | None = None
    opportunity_score: float = 0.0
    domain_visible: bool = False
    visibility_position: int | None = None
    visibility_status: Literal["visible", "not_visible", "unknown"] = "unknown"
    ai_platforms_checked: list[str] = Field(default_factory=list)
    citations: list[str] = Field(default_factory=list)
    discovered_at: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc)
    )


class Insight(_Model):
    """Analysis output."""

    title: str
    detail: str
    relevance_score: float = 0.0
    related_query_uuids: list[str] = Field(default_factory=list)


class Recommendation(_Model):
    """Analysis output, assembled by the report node."""

    recommendation_uuid: str
    target_query_uuid: str
    content_type: str
    title: str
    rationale: str
    target_keywords: list[str] = Field(default_factory=list)
    priority: str = "medium"


class StageError(_Model):
    """A failure recorded so an edge can route on it."""

    stage: Stage
    error_type: str
    message: str
    retryable: bool = False
    query_uuid: str | None = None


def merge_lists(existing: list, incoming: list) -> list:
    """Reducer so parallel retrieval branches append instead of overwrite."""
    return (existing or []) + (incoming or [])


class PipelineState(TypedDict, total=False):
    """What flows through the graph.

    TypedDict rather than a model because LangGraph merges partial
    dicts returned by nodes; Annotated reducers control how.
    """

    # inputs
    run_id: str
    profile: BusinessProfile
    question: str
    recheck_query_uuid: str | None  # set when entering at retrieval

    # query_planner writes
    planned_calls: list[PlannedCall]
    planner_status: Status

    # retrieval writes
    raw_results: Annotated[list[RawResult], merge_lists]
    retrieval_status: Status

    # extraction writes
    normalized_queries: list[NormalizedQuery]
    extraction_status: Status

    # analysis writes
    insights: list[Insight]
    recommendations: list[Recommendation]
    analysis_status: Status

    # report writes
    report_json: dict[str, Any]
    report_summary: str
    report_status: Status

    # cross-cutting
    errors: Annotated[list[StageError], merge_lists]
    overall_status: Status
