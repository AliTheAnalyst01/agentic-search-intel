"""Search / Retrieval node.

Sole responsibility: execute the planned tool calls and return raw API
responses. It does not parse, reshape, or interpret anything.

Failures are isolated per call: one exhausted retry does not kill the
other calls, and the node reports partial rather than failed.
"""

from app.dataforseo.errors import DataForSEOError
from app.graph.state import (
    PipelineState,
    PlannedCall,
    RawResult,
    Stage,
    StageError,
    Status,
)
from app.observability.metrics import RunMetrics, node_span
from app.tools.executors import Client, execute
from app.tools.schemas import validate_tool_call


def _calls_to_run(state: PipelineState) -> list[PlannedCall]:
    """All planned calls, or just one when entering via /recheck."""
    planned = state.get("planned_calls", [])
    target = state.get("recheck_query_uuid")

    if target is None:
        return planned
    return [c for c in planned if c.query_uuid == target]


def _run_one(
    call: PlannedCall, client: Client, metrics: RunMetrics
) -> tuple[RawResult, StageError | None]:
    """Execute one planned call. Never raises."""
    # Re-validate at the boundary: state may have been persisted and
    # reloaded between the planner and here.
    validated = validate_tool_call(call.tool_name, call.args)
    if not validated.ok:
        return (
            RawResult(
                query_uuid=call.query_uuid,
                tool_name=call.tool_name,
                path="",
                status=Status.FAILED,
                error=f"ToolCallValidationError: {'; '.join(validated.errors)}",
            ),
            StageError(
                stage=Stage.RETRIEVAL,
                error_type="ToolCallValidationError",
                message="; ".join(validated.errors),
                retryable=False,
                query_uuid=call.query_uuid,
            ),
        )

    try:
        execution = execute(call.tool_name, validated.args, client)
    except DataForSEOError as err:
        metrics.record_api_call(f"{call.tool_name}:failed", ok=False)
        return (
            RawResult(
                query_uuid=call.query_uuid,
                tool_name=call.tool_name,
                path="",
                status=Status.FAILED,
                error=f"{type(err).__name__}: {err}",
            ),
            StageError(
                stage=Stage.RETRIEVAL,
                error_type=type(err).__name__,
                message=str(err),
                retryable=bool(getattr(err, "retryable", False)),
                query_uuid=call.query_uuid,
            ),
        )

    metrics.record_api_call(execution.path, execution.attempts)
    return (
        RawResult(
            query_uuid=call.query_uuid,
            tool_name=call.tool_name,
            path=execution.path,
            raw_response=execution.raw_response,
            attempts=execution.attempts,
            duration_ms=execution.duration_ms,
            status=Status.OK,
        ),
        None,
    )


def retrieve(
    state: PipelineState, metrics: RunMetrics, client: Client
) -> PipelineState:
    """Node entry point. Returns only the keys this node owns."""
    calls = _calls_to_run(state)

    with node_span(
        Stage.RETRIEVAL.value,
        metrics,
        inputs={
            "calls_to_run": len(calls),
            "recheck_query_uuid": state.get("recheck_query_uuid"),
        },
    ) as out:
        results: list[RawResult] = []
        errors: list[StageError] = []

        for call in calls:
            result, error = _run_one(call, client, metrics)
            results.append(result)
            if error is not None:
                errors.append(error)

        succeeded = [r for r in results if r.status == Status.OK]

        if not calls or not succeeded:
            status = Status.FAILED
        elif len(succeeded) < len(results):
            status = Status.PARTIAL
        else:
            status = Status.OK

        out["calls_succeeded"] = len(succeeded)
        out["calls_failed"] = len(results) - len(succeeded)
        out["status"] = status.value

        return {
            "raw_results": results,
            "retrieval_status": status,
            "errors": errors,
        }
