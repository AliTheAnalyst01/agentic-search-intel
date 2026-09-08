"""Conditional edge functions.

Each returns the name of the next node. Kept separate from the graph
construction so the routing rules can be tested without building a graph.
"""

from app.graph.state import PipelineState, Stage, Status


def after_planner(state: PipelineState) -> str:
    """No valid calls means retrieval has nothing to do."""
    if state.get("planner_status") == Status.FAILED:
        return "fallback"
    return Stage.RETRIEVAL.value


def after_retrieval(state: PipelineState) -> str:
    """Validation gate: extraction needs at least one usable response."""
    if state.get("retrieval_status") == Status.FAILED:
        return "fallback"
    return Stage.EXTRACTION.value


def after_extraction(state: PipelineState) -> str:
    """Analysis needs normalized records to reason over."""
    if state.get("extraction_status") == Status.FAILED:
        return "fallback"
    if not state.get("normalized_queries"):
        return "fallback"
    return Stage.ANALYSIS.value
