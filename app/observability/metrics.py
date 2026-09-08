"""Per-run metrics and the node span that records them.

Section 3.6 asks for per-node latency, success/failure rate and API call
counts. Collecting them as the run proceeds is the only way they exist
at the end of it.
"""

import time
from contextlib import contextmanager
from dataclasses import dataclass, field
from typing import Any, Iterator

from app.observability.logging import get_logger, redact

log = get_logger("pipeline")


@dataclass
class NodeMetric:
    node: str
    status: str  # ok | failed
    duration_ms: float
    attempts: int = 1
    error_type: str | None = None
    retryable: bool | None = None


@dataclass
class RunMetrics:
    """Everything measured during one DAG run."""

    run_id: str
    nodes: list[NodeMetric] = field(default_factory=list)
    api_calls: int = 0
    api_calls_failed: int = 0
    api_call_paths: list[str] = field(default_factory=list)
    retries: int = 0
    input_tokens: int = 0
    output_tokens: int = 0

    def record_node(self, metric: NodeMetric) -> None:
        self.nodes.append(metric)

    def record_api_call(
        self, path: str, attempts: int = 1, *, ok: bool = True
    ) -> None:
        self.api_calls += 1
        if not ok:
            self.api_calls_failed += 1
        self.api_call_paths.append(path)
        self.retries += max(0, attempts - 1)

    def record_tokens(self, usage: dict[str, Any] | None) -> None:
        if not usage:
            return
        self.input_tokens += usage.get("input_tokens", 0)
        self.output_tokens += usage.get("output_tokens", 0)

    @property
    def total_tokens(self) -> int:
        return self.input_tokens + self.output_tokens

    def summary(self) -> dict[str, Any]:
        """The end-of-run report."""
        ok = [n for n in self.nodes if n.status == "ok"]
        failed = [n for n in self.nodes if n.status == "failed"]

        return {
            "run_id": self.run_id,
            "nodes_executed": len(self.nodes),
            "nodes_ok": len(ok),
            "nodes_failed": len(failed),
            "success_rate": round(len(ok) / len(self.nodes), 3) if self.nodes else 0.0,
            "total_duration_ms": round(sum(n.duration_ms for n in self.nodes), 2),
            "node_latency_ms": {n.node: n.duration_ms for n in self.nodes},
            "failed_nodes": [
                {"node": n.node, "error": n.error_type, "retryable": n.retryable}
                for n in failed
            ],
            "api_calls": self.api_calls,
            "api_calls_succeeded": self.api_calls - self.api_calls_failed,
            "api_calls_failed": self.api_calls_failed,
            "api_call_paths": self.api_call_paths,
            "retries": self.retries,
            "total_tokens": self.total_tokens,
        }

    def log_summary(self) -> None:
        log.info("run_summary", **self.summary())


@contextmanager
def node_span(
    node: str,
    metrics: RunMetrics,
    *,
    inputs: dict[str, Any] | None = None,
) -> Iterator[dict[str, Any]]:
    """Time a node, log its start and outcome, record its metric.

    Yields a mutable dict the node can add to for the completion log
    (record counts, decisions taken, and so on).
    """
    log.info("node_started", node=node, inputs=redact(inputs or {}))
    started = time.perf_counter()
    extras: dict[str, Any] = {}

    try:
        yield extras
    except Exception as err:
        duration_ms = round((time.perf_counter() - started) * 1000, 2)
        retryable = bool(getattr(err, "retryable", False))

        metrics.record_node(
            NodeMetric(
                node=node,
                status="failed",
                duration_ms=duration_ms,
                error_type=type(err).__name__,
                retryable=retryable,
            )
        )
        log.error(
            "node_failed",
            node=node,
            duration_ms=duration_ms,
            error_type=type(err).__name__,
            error=str(err),
            retryable=retryable,
            **extras,
        )
        raise
    else:
        duration_ms = round((time.perf_counter() - started) * 1000, 2)
        metrics.record_node(
            NodeMetric(
                node=node,
                status="ok",
                duration_ms=duration_ms,
                attempts=extras.get("attempts", 1),
            )
        )
        log.info("node_completed", node=node, duration_ms=duration_ms, **extras)
