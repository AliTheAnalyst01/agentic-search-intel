"""Extraction / Normalization node.

Sole responsibility: parse raw API responses into clean NormalizedQuery
rows. It merges fragments from different endpoints on the normalized
keyword, because /queries needs volume, difficulty and visibility on a
single row.

It does not interpret the data. Whether a score is good, and what to do
about it, belongs to Analysis.
"""

import uuid
from dataclasses import dataclass, field

from app.graph.state import (
    NormalizedQuery,
    PipelineState,
    RawResult,
    Stage,
    StageError,
    Status,
)
from app.nodes.parsers import (
    normalize_key,
    parse_ai_response,
    parse_keyword_metrics,
    parse_serp,
)
from app.nodes.scoring import opportunity_score
from app.observability.metrics import RunMetrics, node_span


@dataclass
class _Row:
    """Accumulator for one query as fragments arrive."""

    query_uuid: str
    query_text: str
    search_volume: int = 0
    difficulty: int = 0
    visibility_status: str = "unknown"
    visibility_position: int | None = None
    platforms: list[str] = field(default_factory=list)
    citations: list[str] = field(default_factory=list)


def _get_row(rows: dict[str, _Row], text: str, query_uuid: str) -> _Row:
    key = normalize_key(text)
    if key not in rows:
        rows[key] = _Row(query_uuid=query_uuid, query_text=text)
    return rows[key]


def extract(state: PipelineState, metrics: RunMetrics) -> PipelineState:
    """Node entry point. Returns only the keys this node owns."""
    raw_results: list[RawResult] = state.get("raw_results", [])
    profile = state["profile"]

    with node_span(
        Stage.EXTRACTION.value,
        metrics,
        inputs={"raw_results": len(raw_results)},
    ) as out:
        rows: dict[str, _Row] = {}
        errors: list[StageError] = []
        parsed = 0
        unparsable = 0

        for result in raw_results:
            if result.status != Status.OK:
                # A failed retrieval still needs a row, or the query
                # vanishes from /queries as if never planned.
                continue

            try:
                matched = _absorb(result, rows, profile)
            except Exception as err:  # defensive: unknown response shape
                unparsable += 1
                errors.append(
                    StageError(
                        stage=Stage.EXTRACTION,
                        error_type=type(err).__name__,
                        message=f"{result.tool_name}: {err}",
                        retryable=False,
                        query_uuid=result.query_uuid,
                    )
                )
                continue

            if matched:
                parsed += 1
            else:
                unparsable += 1
                errors.append(
                    StageError(
                        stage=Stage.EXTRACTION,
                        error_type="UnparsableResponse",
                        message=f"{result.tool_name} returned no usable records",
                        retryable=False,
                        query_uuid=result.query_uuid,
                    )
                )

        # Rows for queries whose retrieval failed, so the gap is visible.
        for result in raw_results:
            if result.status == Status.OK:
                continue
            text = _fallback_text(state, result.query_uuid)
            row = _get_row(rows, text or result.query_uuid, result.query_uuid)
            row.visibility_status = "unknown"

        normalized = [
            NormalizedQuery(
                query_uuid=row.query_uuid,
                query_text=row.query_text,
                estimated_search_volume=row.search_volume,
                competitive_difficulty=row.difficulty,
                opportunity_score=opportunity_score(
                    search_volume=row.search_volume,
                    difficulty=row.difficulty,
                    visibility_status=row.visibility_status,
                    visibility_position=row.visibility_position,
                ),
                domain_visible=row.visibility_status == "visible",
                visibility_position=row.visibility_position,
                visibility_status=row.visibility_status,
                ai_platforms_checked=sorted(set(row.platforms)),
                citations=sorted(set(row.citations)),
            )
            for row in rows.values()
        ]

        if not normalized:
            status = Status.FAILED
        elif unparsable:
            status = Status.PARTIAL
        else:
            status = Status.OK

        out["records_normalized"] = len(normalized)
        out["responses_parsed"] = parsed
        out["responses_unparsable"] = unparsable
        out["status"] = status.value

        return {
            "normalized_queries": normalized,
            "extraction_status": status,
            "errors": errors,
        }


def _absorb(result: RawResult, rows: dict[str, _Row], profile) -> bool:
    """Fold one raw response into the row accumulator. True if usable."""
    body = result.raw_response

    if result.tool_name == "serp_organic_lookup":
        fragment = parse_serp(body, profile.domain)
        if fragment is None or not fragment.keyword:
            return False
        row = _get_row(rows, fragment.keyword, result.query_uuid)
        row.visibility_status = "visible" if fragment.domain_visible else "not_visible"
        row.visibility_position = fragment.visibility_position
        return True

    if result.tool_name == "keyword_metrics_lookup":
        fragments = parse_keyword_metrics(body)
        if not fragments:
            return False
        for fragment in fragments:
            row = _get_row(rows, fragment.keyword, str(uuid.uuid4()))
            row.search_volume = fragment.search_volume
            row.difficulty = fragment.difficulty
        return True

    if result.tool_name == "llm_visibility_lookup":
        fragment = parse_ai_response(body, profile.name, profile.domain)
        if fragment is None or not fragment.prompt:
            return False
        row = _get_row(rows, fragment.prompt, result.query_uuid)
        row.platforms.append(fragment.platform or "unknown")
        row.citations.extend(fragment.citations)
        if fragment.brand_mentioned:
            row.visibility_status = "visible"
        elif row.visibility_status == "unknown":
            row.visibility_status = "not_visible"
        return True

    return False


def _fallback_text(state: PipelineState, query_uuid: str) -> str:
    for call in state.get("planned_calls", []):
        if call.query_uuid == query_uuid:
            return call.query_text
    return ""
