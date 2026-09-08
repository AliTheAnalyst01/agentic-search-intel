"""Node wrappers and the fallback node.

LangGraph nodes take state and return a partial state. Our node functions
also need metrics and a client, so these wrappers close over them.
"""

from typing import Any, Callable

from app.graph.state import PipelineState, Stage, StageError, Status
from app.nodes.analysis import analyse
from app.nodes.extraction import extract
from app.nodes.planner import plan_queries
from app.nodes.report import build_report
from app.nodes.retrieval import retrieve
from app.observability.metrics import RunMetrics, node_span
from app.tools.executors import Client


def fallback(state: PipelineState, metrics: RunMetrics) -> PipelineState:
    """Degradation path: record why the pipeline could not continue.

    Reached when an upstream stage produced nothing usable. Its job is to
    make the run describable, so the report explains the gap rather than
    presenting empty results as a finding.
    """
    with node_span(
        "fallback",
        metrics,
        inputs={
            "planner_status": _value(state.get("planner_status")),
            "retrieval_status": _value(state.get("retrieval_status")),
            "extraction_status": _value(state.get("extraction_status")),
        },
    ) as out:
        reason = _degradation_reason(state)
        out["reason"] = reason

        return {
            "errors": [
                StageError(
                    stage=Stage.REPORT,
                    error_type="PipelineDegraded",
                    message=reason,
                    retryable=False,
                )
            ],
            "normalized_queries": state.get("normalized_queries", []),
            "insights": [],
            "recommendations": [],
            "analysis_status": Status.SKIPPED,
        }


def _degradation_reason(state: PipelineState) -> str:
    if state.get("planner_status") == Status.FAILED:
        return "The query planner produced no valid retrieval calls, so no data was gathered."
    if state.get("retrieval_status") == Status.FAILED:
        return "Every retrieval call failed, so there was no data to analyse."
    if state.get("extraction_status") == Status.FAILED:
        return "No usable records could be extracted from the API responses."
    return "The pipeline could not complete a full run."


def _value(status) -> str:
    return status.value if status is not None else "pending"


def make_nodes(
    metrics: RunMetrics, client: Client, llm: Any = None
) -> dict[str, Callable[[PipelineState], PipelineState]]:
    """Bind runtime dependencies into LangGraph-shaped callables.

    Dependencies are injected per run, not captured at import time, so
    the recheck entry point and the tests get correct wiring.
    """
    return {
        Stage.PLANNER.value: lambda s: plan_queries(s, metrics, llm=llm),
        Stage.RETRIEVAL.value: lambda s: retrieve(s, metrics, client),
        Stage.EXTRACTION.value: lambda s: extract(s, metrics),
        Stage.ANALYSIS.value: lambda s: analyse(s, metrics, llm=llm),
        Stage.REPORT.value: lambda s: build_report(s, metrics),
        "fallback": lambda s: fallback(s, metrics),
    }
