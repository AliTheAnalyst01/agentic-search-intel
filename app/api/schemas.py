"""HTTP request and response shapes.

Separate from the state models: the wire format is a contract with
clients and should be able to change independently of internal structure.
"""

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator


class ProfileCreate(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    name: str = Field(min_length=1, max_length=200)
    domain: str = Field(min_length=3, max_length=253)
    industry: str = Field(default="", max_length=200)
    description: str = Field(default="", max_length=2000)
    competitors: list[str] = Field(default_factory=list, max_length=20)

    @field_validator("domain")
    @classmethod
    def strip_scheme(cls, v: str) -> str:
        """Accept a URL, store a bare domain."""
        cleaned = v.strip().lower()
        for prefix in ("https://", "http://"):
            cleaned = cleaned.removeprefix(prefix)
        cleaned = cleaned.removeprefix("www.").rstrip("/")
        if "." not in cleaned:
            raise ValueError("domain must contain a dot, e.g. example.com")
        return cleaned


class ProfileCreated(BaseModel):
    profile_uuid: str
    name: str
    domain: str
    status: str = "created"
    created_at: datetime


class ProfileDetail(BaseModel):
    profile_uuid: str
    name: str
    domain: str
    industry: str
    description: str
    competitors: list[str]
    created_at: datetime
    total_runs: int
    most_recent_run_status: str | None
    most_recent_run_uuid: str | None
    average_opportunity_score: float | None


class RunRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    question: str | None = Field(default=None, max_length=500)


class RunResponse(BaseModel):
    pipeline_run_uuid: str
    profile_uuid: str
    status: str
    retrieval_calls_planned: int
    records_normalized: int
    top_insights: list[dict[str, Any]]
    report: dict[str, Any]
    report_summary: str
    total_tokens: int
    errors: list[dict[str, Any]]
    metrics: dict[str, Any]


class QueryOut(BaseModel):
    query_uuid: str
    query_text: str
    estimated_search_volume: int
    competitive_difficulty: int
    opportunity_score: float
    domain_visible: bool
    visibility_position: int | None
    visibility_status: str
    ai_platforms_checked: list[str]
    citations: list[str]
    discovered_at: datetime


class Page(BaseModel):
    page: int
    per_page: int
    total: int


class QueryList(BaseModel):
    queries: list[QueryOut]
    pagination: Page


class RecommendationOut(BaseModel):
    recommendation_uuid: str
    target_query_uuid: str
    content_type: str
    title: str
    rationale: str
    target_keywords: list[str]
    priority: Literal["high", "medium", "low"]


class RecommendationList(BaseModel):
    recommendations: list[RecommendationOut]


class RecheckResponse(BaseModel):
    query: QueryOut | None
    run_id: str
    status: str
    retrieval_status: str
    errors: list[dict[str, Any]]
    metrics: dict[str, Any]


class ErrorResponse(BaseModel):
    detail: str
