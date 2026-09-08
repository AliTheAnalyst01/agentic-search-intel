"""Pipeline execution and persistence.

Sits between the HTTP routes and the graph so both /run and /recheck
share one definition of "execute and persist". Routes stay about HTTP;
this module stays about orchestration.
"""

from typing import Any

from sqlalchemy.orm import Session

from app.dataforseo.factory import get_client
from app.db import repository as repo
from app.db.models import ProfileRow, QueryRow, RunRow
from app.graph.build import build_graph
from app.graph.state import Stage
from app.observability.logging import new_run_id, run_context
from app.observability.metrics import RunMetrics

DEFAULT_QUESTION_TEMPLATE = (
    "How does {name} show up in AI answers and search results for queries "
    "in the {industry} category?"
)


def default_question(profile: ProfileRow) -> str:
    return DEFAULT_QUESTION_TEMPLATE.format(
        name=profile.name, industry=profile.industry or "its"
    )


def execute_run(
    session: Session,
    profile: ProfileRow,
    *,
    question: str | None = None,
    client: Any = None,
    llm: Any = None,
) -> RunRow:
    """Run the full DAG for a profile and persist the result."""
    client = client or get_client()

    with run_context() as run_id:
        metrics = RunMetrics(run_id=run_id)
        graph = build_graph(metrics, client, llm=llm)

        final = graph.invoke(
            {
                "run_id": run_id,
                "profile": repo.to_business_profile(profile),
                "question": question or default_question(profile),
            }
        )

        metrics.log_summary()

        return repo.save_run(
            session,
            profile_uuid=profile.profile_uuid,
            state=final,
            metrics_summary=metrics.summary(),
        )


def execute_recheck(
    session: Session,
    query_row: QueryRow,
    *,
    client: Any = None,
    llm: Any = None,
) -> tuple[QueryRow | None, dict[str, Any]]:
    """Re-run retrieval and downstream nodes for a single query.

    Enters the graph at retrieval, skipping the planner, using the tool
    call stored alongside the query. Updates the existing row rather than
    creating a new run.
    """
    client = client or get_client()
    profile = repo.get_profile(session, query_row.profile_uuid)
    planned = repo.to_planned_call(query_row)

    with run_context() as run_id:
        metrics = RunMetrics(run_id=run_id)
        graph = build_graph(metrics, client, llm=llm, entry=Stage.RETRIEVAL.value)

        final = graph.invoke(
            {
                "run_id": run_id,
                "profile": repo.to_business_profile(profile),
                "question": f"Recheck: {query_row.query_text}",
                "planned_calls": [planned],
                "recheck_query_uuid": query_row.query_uuid,
            }
        )

        metrics.log_summary()

        refreshed = next(
            (
                q
                for q in final.get("normalized_queries", [])
                if q.query_uuid == query_row.query_uuid
            ),
            None,
        )

        updated = (
            repo.update_query(session, query_row.query_uuid, refreshed)
            if refreshed is not None
            else None
        )

        return updated, {
            "run_id": run_id,
            "status": _value(final.get("overall_status")),
            "retrieval_status": _value(final.get("retrieval_status")),
            "errors": [e.model_dump(mode="json") for e in final.get("errors", [])],
            "metrics": metrics.summary(),
        }


def _value(status) -> str:
    return status.value if hasattr(status, "value") else "unknown"
