"""Structured JSON logging with a per-run correlation ID.

Every log line in a run carries the same run_id, so a single grep
reconstructs the whole pipeline execution in order.
"""

import logging
import sys
import uuid
from contextlib import contextmanager
from typing import Any, Iterator

import structlog

# Keys whose values must never reach a log sink.
SENSITIVE_KEYS = frozenset(
    {
        "password",
        "login",
        "api_key",
        "apikey",
        "credential",
        "authorization",
        "auth",
        "token",
        "secret",
    }
)

REDACTED = "***redacted***"


def redact(value: Any) -> Any:
    """Recursively replace sensitive values, preserving structure.

    Structure is kept on purpose: knowing a password field was present
    and non-empty is useful when debugging an auth failure.
    """
    if isinstance(value, dict):
        return {
            k: REDACTED if k.lower() in SENSITIVE_KEYS else redact(v)
            for k, v in value.items()
        }
    if isinstance(value, (list, tuple)):
        return [redact(v) for v in value]
    return value


def _redact_processor(logger, method_name, event_dict):
    return redact(event_dict)


def build_processors(final_processor) -> list:
    """The processor chain, with a swappable final step.

    Shared by production and tests so both exercise the same
    redaction and contextvar merging.
    """
    return [
        structlog.contextvars.merge_contextvars,
        _redact_processor,
        structlog.processors.add_log_level,
        structlog.processors.TimeStamper(fmt="iso", utc=True),
        structlog.processors.format_exc_info,
        final_processor,
    ]


def configure_logging(
    level: str = "INFO", pretty: bool = False, cache: bool = True
) -> None:
    """Install the processor chain. Call once at startup."""
    renderer = (
        structlog.dev.ConsoleRenderer()
        if pretty
        else structlog.processors.JSONRenderer()
    )

    structlog.configure(
        processors=build_processors(renderer),
        wrapper_class=structlog.make_filtering_bound_logger(
            getattr(logging, level.upper())
        ),
        logger_factory=structlog.PrintLoggerFactory(file=sys.stdout),
        cache_logger_on_first_use=cache,
    )


def new_run_id() -> str:
    return str(uuid.uuid4())


@contextmanager
def run_context(run_id: str | None = None, **extra: Any) -> Iterator[str]:
    """Bind a run_id (and any extra fields) to every log line inside."""
    run_id = run_id or new_run_id()
    structlog.contextvars.bind_contextvars(run_id=run_id, **extra)
    try:
        yield run_id
    finally:
        structlog.contextvars.unbind_contextvars("run_id", *extra.keys())


def get_logger(name: str = "app"):
    return structlog.get_logger(name)
