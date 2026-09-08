"""Derive a run's overall status from its stage statuses.

Isolated from the nodes so the rule is stated once and tested directly.
"""

from app.graph.state import PipelineState, Status

STAGE_STATUS_KEYS = (
    "planner_status",
    "retrieval_status",
    "extraction_status",
    "analysis_status",
    "report_status",
)


def derive_overall_status(state: PipelineState) -> Status:
    """completed / partial / failed, from what the stages reported.

    Rules, in order:
      - no report at all           -> failed
      - every stage ok             -> ok
      - any stage failed or partial-> partial
      - otherwise                  -> ok
    """
    statuses = [state.get(key, Status.PENDING) for key in STAGE_STATUS_KEYS]

    if state.get("report_status") in (None, Status.PENDING, Status.FAILED):
        return Status.FAILED

    if all(s == Status.OK for s in statuses):
        return Status.OK

    if any(s in (Status.FAILED, Status.PARTIAL) for s in statuses):
        return Status.PARTIAL

    return Status.OK
