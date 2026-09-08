"""Produces the sample log excerpt the README asks for."""

from app.dataforseo.errors import RateLimitError
from app.dataforseo.mock import MockDataForSEOClient
from app.observability.logging import configure_logging, run_context
from app.observability.metrics import RunMetrics, node_span
from app.tools.executors import serp_organic_lookup
from app.tools.schemas import validate_tool_call

configure_logging(level="INFO")

with run_context() as run_id:
    metrics = RunMetrics(run_id=run_id)

    with node_span("query_planner", metrics, inputs={"question": "How does Surfer SEO show up?"}) as out:
        out["retrieval_calls_planned"] = 1

    client = MockDataForSEOClient(fail_script={"serp": [RateLimitError("429"), None]})
    args = validate_tool_call(
        "serp_organic_lookup", {"keyword": "best seo tool", "location": "United Kingdom"}
    ).args

    with node_span("retrieval", metrics, inputs={"keyword": args.keyword, "password": "hunter2"}) as out:
        execution = serp_organic_lookup(args, client)
        metrics.record_api_call(execution.path, execution.attempts)
        out["attempts"] = execution.attempts
        out["items_returned"] = len(execution.raw_response["tasks"][0]["result"][0]["items"])

    try:
        with node_span("extraction", metrics, inputs={"records": 5}):
            raise RateLimitError("simulated downstream failure")
    except RateLimitError:
        pass

    metrics.log_summary()
