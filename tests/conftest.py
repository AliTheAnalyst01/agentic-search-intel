"""Capture log lines through the production processor chain.

structlog.testing.capture_logs() replaces the whole chain, which
silently drops contextvars and redaction. This fixture swaps only
the final renderer, so tests see exactly what production emits.
"""

import logging

import pytest
import structlog

from app.observability.logging import build_processors


@pytest.fixture
def captured_logs():
    entries: list[dict] = []

    def capture(logger, method_name, event_dict):
        entries.append(dict(event_dict))
        raise structlog.DropEvent

    structlog.configure(
        processors=build_processors(capture),
        wrapper_class=structlog.make_filtering_bound_logger(logging.INFO),
        logger_factory=structlog.PrintLoggerFactory(),
        cache_logger_on_first_use=False,
    )

    yield entries

    structlog.reset_defaults()
