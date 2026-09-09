"""Query Planner node.

Sole responsibility: turn a natural-language question about a brand's
search and AI visibility into a validated list of retrieval calls.

It does not fetch anything. It does not interpret results. Invalid tool
calls get one correction round using the validator's own error text.
"""

import uuid
from typing import Any

from app.graph.state import PipelineState, PlannedCall, Stage, StageError, Status
from app.llm import get_llm
from app.observability.metrics import RunMetrics, node_span
from app.tools.schemas import (
    KeywordMetricsArgs,
    LLMVisibilityArgs,
    SerpOrganicArgs,
    validate_tool_call,
)

MAX_PLANNED_CALLS = 6
CORRECTION_ATTEMPTS = 1

TOOL_MODELS = [SerpOrganicArgs, LLMVisibilityArgs, KeywordMetricsArgs]

# Maps the model class the LLM binds to onto our registry name.
MODEL_TO_TOOL = {
    "SerpOrganicArgs": "serp_organic_lookup",
    "LLMVisibilityArgs": "llm_visibility_lookup",
    "KeywordMetricsArgs": "keyword_metrics_lookup",
}

# Tools whose absence degrades downstream stages. Recorded as a gap
# rather than injected, so planning stays the planner's job and the
# deficiency surfaces in the report instead of hiding as zeroed scores.
COVERAGE_REQUIRED = {
    "keyword_metrics_lookup": (
        "No keyword metrics were planned, so search volume and competitive "
        "difficulty are unavailable and opportunity scores will be unreliable."
    ),
}


def _coverage_gaps(calls: list[PlannedCall]) -> list[str]:
    planned = {c.tool_name for c in calls}
    return [msg for tool, msg in COVERAGE_REQUIRED.items() if tool not in planned]

SYSTEM_PROMPT = """You plan data retrieval for a search-intelligence pipeline.

Given a brand and a research question, decide which lookups are needed to
answer it. You have three tools:

- SerpOrganicArgs: whether a domain ranks in Google organic results for a keyword
- LLMVisibilityArgs: whether a brand is mentioned when an AI platform is asked a question
- KeywordMetricsArgs: search volume and difficulty for a batch of keywords

Rules:
- Plan between 3 and {max_calls} calls total.
- Always include exactly one KeywordMetricsArgs call. Downstream scoring
  needs search volume and difficulty; without it the analysis is unusable.
  Batch several related keywords into that single call.
- Cover DISTINCT queries, not one query rephrased. A category has several
  buying-intent queries: the head term, comparison terms, alternative-to
  terms, and problem-led terms.
- Use ONE location for every call unless the question names multiple markets.
- Vary the AI platform when comparing platforms is useful, but do not repeat
  the same prompt on more than two platforms.
- Phrase AI prompts the way a real user would ask, not as a keyword.
- Use only the allowed values for constrained fields.

Call the tools. Do not reply with prose."""


def _user_prompt(state: PipelineState) -> str:
    profile = state["profile"]
    competitors = ", ".join(profile.competitors) or "none given"

    return (
        f"Brand: {profile.name}\n"
        f"Domain: {profile.domain}\n"
        f"Industry: {profile.industry or 'unspecified'}\n"
        f"Description: {profile.description or 'none'}\n"
        f"Competitors: {competitors}\n\n"
        f"Research question: {state['question']}"
    )


def _query_text(tool_name: str, args: dict[str, Any]) -> str:
    """A human-readable label for this call, for the queries endpoint."""
    if tool_name == "serp_organic_lookup":
        return str(args.get("keyword", ""))
    if tool_name == "llm_visibility_lookup":
        return str(args.get("prompt", ""))
    if tool_name == "keyword_metrics_lookup":
        return ", ".join(args.get("keywords", []))
    return ""


def _to_planned_calls(
    tool_calls: list[dict[str, Any]]
) -> tuple[list[PlannedCall], list[str]]:
    """Validate raw LLM tool calls. Returns (accepted, error messages)."""
    accepted: list[PlannedCall] = []
    problems: list[str] = []

    for call in tool_calls:
        tool_name = MODEL_TO_TOOL.get(call.get("name", ""), call.get("name", ""))
        result = validate_tool_call(tool_name, call.get("args"))

        if not result.ok:
            problems.append(f"{call.get('name')}: {'; '.join(result.errors)}")
            continue

        args = result.args.model_dump()
        accepted.append(
            PlannedCall(
                query_uuid=str(uuid.uuid4()),
                tool_name=tool_name,
                args=args,
                query_text=_query_text(tool_name, args),
            )
        )

    return accepted, problems


def plan_queries(
    state: PipelineState, metrics: RunMetrics, llm: Any = None
) -> PipelineState:
    """Node entry point. Returns only the keys this node owns."""
    llm = llm or get_llm()
    bound = llm.bind_tools(TOOL_MODELS)

    with node_span(
        Stage.PLANNER.value,
        metrics,
        inputs={"question": state.get("question"), "domain": state["profile"].domain},
    ) as out:
        messages: list[Any] = [
            ("system", SYSTEM_PROMPT.format(max_calls=MAX_PLANNED_CALLS)),
            ("user", _user_prompt(state)),
        ]

        accepted: list[PlannedCall] = []
        problems: list[str] = []

        for attempt in range(CORRECTION_ATTEMPTS + 1):
            response = bound.invoke(messages)
            metrics.record_tokens(getattr(response, "usage_metadata", None))

            accepted, problems = _to_planned_calls(response.tool_calls or [])

            if accepted and not problems:
                break
            if attempt == CORRECTION_ATTEMPTS:
                break

            # Feed the validator's own messages back for self-correction.
            #
            # Only a user turn is appended. Replaying the assistant turn
            # would require a matching ToolMessage per tool_call_id, and
            # the model does not need to see its own prior message when
            # the correction turn quotes what was rejected.
            rejected = "\n".join(f"  - {p}" for p in problems)
            messages.append(
                (
                    "user",
                    "Argument validation rejected some of those tool calls:\n"
                    f"{rejected}\n"
                    "Call the tools again, correcting only the invalid "
                    "arguments and using the allowed values shown above.",
                )
            )

        accepted = accepted[:MAX_PLANNED_CALLS]
        gaps = _coverage_gaps(accepted)

        if not accepted:
            status = Status.FAILED
        elif problems or gaps:
            status = Status.PARTIAL
        else:
            status = Status.OK

        out["retrieval_calls_planned"] = len(accepted)
        out["tool_calls_rejected"] = len(problems)
        out["coverage_gaps"] = len(gaps)
        out["status"] = status.value

        errors = [
            StageError(
                stage=Stage.PLANNER,
                error_type="ToolCallValidationError",
                message=p,
                retryable=False,
            )
            for p in problems
        ] + [
            StageError(
                stage=Stage.PLANNER,
                error_type="CoverageGap",
                message=g,
                retryable=False,
            )
            for g in gaps
        ]

        return {
            "planned_calls": accepted,
            "planner_status": status,
            "errors": errors,
        }
