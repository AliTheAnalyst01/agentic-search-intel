"""DAG construction.

    query_planner
         |
    [after_planner] ----------------+
         |                          |
    retrieval                       |
         |                          |
    [after_retrieval] --------------+
         |                          |
    extraction                      |
         |                          |
    [after_extraction] -------------+
         |                          |
    analysis                    fallback
         |                          |
         +----------> report <------+
                        |
                       END

The three bracketed decisions are conditional edges. Any of them can
divert to the fallback node, which records why the run degraded before
the report assembles whatever is available.
"""

from typing import Any

from langgraph.graph import END, START, StateGraph

from app.graph.nodes import make_nodes
from app.graph.routing import after_extraction, after_planner, after_retrieval
from app.graph.state import PipelineState, Stage
from app.observability.metrics import RunMetrics
from app.tools.executors import Client


def build_graph(
    metrics: RunMetrics, client: Client, llm: Any = None, entry: str | None = None
):
    """Compile the DAG.

    entry lets /recheck start at retrieval, skipping the planner.
    """
    nodes = make_nodes(metrics, client, llm=llm)
    graph = StateGraph(PipelineState)

    for name, fn in nodes.items():
        graph.add_node(name, fn)

    graph.add_edge(START, entry or Stage.PLANNER.value)

    graph.add_conditional_edges(
        Stage.PLANNER.value,
        after_planner,
        {Stage.RETRIEVAL.value: Stage.RETRIEVAL.value, "fallback": "fallback"},
    )
    graph.add_conditional_edges(
        Stage.RETRIEVAL.value,
        after_retrieval,
        {Stage.EXTRACTION.value: Stage.EXTRACTION.value, "fallback": "fallback"},
    )
    graph.add_conditional_edges(
        Stage.EXTRACTION.value,
        after_extraction,
        {Stage.ANALYSIS.value: Stage.ANALYSIS.value, "fallback": "fallback"},
    )

    graph.add_edge(Stage.ANALYSIS.value, Stage.REPORT.value)
    graph.add_edge("fallback", Stage.REPORT.value)
    graph.add_edge(Stage.REPORT.value, END)

    return graph.compile()
