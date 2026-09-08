"""Full DAG run against the real LLM and the mock DataForSEO client."""

import json

from app.dataforseo.factory import get_client
from app.graph.build import build_graph
from app.graph.state import BusinessProfile
from app.observability.logging import configure_logging, run_context
from app.observability.metrics import RunMetrics

configure_logging(level="INFO", pretty=True)

profile = BusinessProfile(
    profile_uuid="p1",
    name="Surfer SEO",
    domain="surferseo.com",
    industry="SEO Software",
    description="AI-powered SEO content optimization tool",
    competitors=["clearscope.io", "marketmuse.com", "frase.io"],
)

with run_context() as run_id:
    metrics = RunMetrics(run_id=run_id)
    graph = build_graph(metrics, get_client("mock"))

    final = graph.invoke(
        {
            "run_id": run_id,
            "profile": profile,
            "question": "How does Surfer SEO show up in AI answers and search results for its category?",
        }
    )

    metrics.log_summary()

    print("\n" + "=" * 70)
    print(final["report_summary"])
    print("=" * 70)
    print(f"\noverall: {final['overall_status'].value}")
    print(f"queries: {len(final['normalized_queries'])}")
    print(f"tokens:  {metrics.total_tokens}")
