"""Demonstrate resilience behaviour with scripted DataForSEO failures.

Three scenarios, each printing real log output:

  1. Transient failure recovered by retry with backoff
  2. Non-retryable failure that is NOT retried
  3. Total retrieval failure diverted to the fallback path

Run: uv run python -m scripts.demo_failure
"""

from app.dataforseo.errors import AuthError, RateLimitError, ServerError
from app.dataforseo.mock import MockDataForSEOClient
from app.graph.build import build_graph
from app.graph.state import BusinessProfile
from app.observability.logging import configure_logging, run_context
from app.observability.metrics import RunMetrics

configure_logging(level="INFO", pretty=True)

PROFILE = BusinessProfile(
    profile_uuid="p1",
    name="Surfer SEO",
    domain="surferseo.com",
    industry="SEO Software",
    competitors=["clearscope.io"],
)


def banner(title: str) -> None:
    print("\n" + "=" * 72)
    print(title)
    print("=" * 72)


def run(client: MockDataForSEOClient) -> tuple[dict, RunMetrics]:
    with run_context() as run_id:
        metrics = RunMetrics(run_id=run_id)
        graph = build_graph(metrics, client)
        final = graph.invoke(
            {
                "run_id": run_id,
                "profile": PROFILE,
                "question": "How does Surfer SEO show up in search and AI answers?",
            }
        )
        metrics.log_summary()
        return final, metrics


def scenario_transient_recovery() -> None:
    banner(
        "1. TRANSIENT FAILURE -> RETRY WITH BACKOFF -> RECOVERY\n"
        "   429 twice, then success. Expect attempts=3 and 2 recorded retries."
    )
    client = MockDataForSEOClient(
        fail_script={"serp": [RateLimitError("429 Too Many Requests"), ServerError("503"), None]}
    )
    final, metrics = run(client)

    print(f"\n  retrieval status : {final['retrieval_status'].value}")
    print(f"  overall status   : {final['overall_status'].value}")
    print(f"  retries recorded : {metrics.summary()['retries']}")
    print(f"  attempts on SERP : {[r.attempts for r in final['raw_results']]}")
    print("  -> the run completed normally; the caller never saw the failures")


def scenario_non_retryable() -> None:
    banner(
        "2. NON-RETRYABLE FAILURE -> NO RETRY\n"
        "   A 401 is a configuration error. Retrying wastes the rate limit."
    )
    client = MockDataForSEOClient(fail_script={"serp": [AuthError("401 Unauthorized")] * 5})
    final, _ = run(client)

    failed = [r for r in final["raw_results"] if r.error]
    print(f"\n  SERP calls made  : {sum(1 for p in client.call_log if 'serp' in p)}")
    print(f"  retrieval status : {final['retrieval_status'].value}")
    print(f"  error recorded   : {failed[0].error if failed else 'none'}")
    print("  -> one attempt only, despite max_attempts=3")


def scenario_fallback() -> None:
    banner(
        "3. TOTAL RETRIEVAL FAILURE -> FALLBACK PATH\n"
        "   Every call fails. The DAG routes around extraction and analysis."
    )
    client = MockDataForSEOClient(
        fail_script={
            "serp": [AuthError("401")] * 5,
            "ai_optimization": [AuthError("401")] * 5,
            "keyword_overview": [AuthError("401")] * 5,
        }
    )
    final, metrics = run(client)

    visited = [n.node for n in metrics.nodes]
    degraded = [e.message for e in final["errors"] if e.error_type == "PipelineDegraded"]

    print(f"\n  nodes visited    : {' -> '.join(visited)}")
    print(f"  overall status   : {final['overall_status'].value}")
    print(f"  degradation      : {degraded[0] if degraded else 'none'}")
    print("  -> a report was still produced, explaining the gap")
    print("\n" + "-" * 72)
    print(final["report_summary"])
    print("-" * 72)


if __name__ == "__main__":
    scenario_transient_recovery()
    scenario_non_retryable()
    scenario_fallback()
    print()
