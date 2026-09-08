"""Run the planner against the real model to see what it plans."""

import json

from app.graph.state import BusinessProfile
from app.nodes.planner import plan_queries
from app.observability.logging import configure_logging, run_context
from app.observability.metrics import RunMetrics

configure_logging(level="INFO")

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
    result = plan_queries(
        {
            "run_id": run_id,
            "profile": profile,
            "question": (
                "How does Surfer SEO show up in AI answers and search results "
                "for 'best project management software' style queries in its category?"
            ),
        },
        metrics,
    )

    print("\nstatus:", result["planner_status"].value)
    print("tokens:", metrics.total_tokens)
    print()
    for call in result["planned_calls"]:
        print(f"  {call.tool_name}")
        print(f"    {json.dumps(call.args)}")
    if result["errors"]:
        print("\nrejected:")
        for err in result["errors"]:
            print(" ", err.message)
