"""Turn a DataForSEO response into either silence or a typed exception.

DataForSEO returns HTTP 200 for almost everything, including validation
failures, and carries the real outcome in the body's `status_code`.
There are two levels: a top-level code for whether the call was accepted,
and a per-task code for whether that task produced data. Both must be
checked, or failed tasks pass silently into the pipeline as empty results.
"""

from typing import Any

from app.dataforseo.errors import (
    AuthError,
    BadRequestError,
    PaymentRequiredError,
    RateLimitError,
    ServerError,
    TaskError,
)

SUCCESS_MIN = 20000
SUCCESS_MAX = 29999

# Task codes that sit in the client-error range but describe an upstream
# failure, so a retry is worthwhile. Expand as real failures are observed.
RETRYABLE_TASK_CODES = {40101}


def is_success(code: int) -> bool:
    return SUCCESS_MIN <= code <= SUCCESS_MAX


def classify_http(status: int, body_text: str = "") -> None:
    """Raise for the few HTTP statuses DataForSEO actually uses."""
    if status == 200:
        return
    if status == 401:
        raise AuthError("Invalid API credentials", code=status)
    if status == 402:
        raise PaymentRequiredError("Account balance exhausted", code=status)
    if status == 404:
        raise BadRequestError("Endpoint not found", code=status)
    if status == 429:
        raise RateLimitError("Rate limit exceeded", code=status)
    if status >= 500:
        raise ServerError(f"Upstream {status}: {body_text[:200]}", code=status)
    raise BadRequestError(f"Unexpected HTTP {status}", code=status)


def classify_body(body: dict[str, Any]) -> None:
    """Raise on a top-level body status that isn't a success."""
    code = body.get("status_code")
    message = body.get("status_message", "no status_message")

    if code is None:
        raise BadRequestError("Response missing status_code")
    if is_success(code):
        return
    if code >= 50000:
        raise ServerError(f"DataForSEO {code}: {message}", code=code)
    raise BadRequestError(f"DataForSEO {code}: {message}", code=code)


def classify_task(task: dict[str, Any]) -> None:
    """Raise on a per-task status that isn't a success."""
    code = task.get("status_code")
    message = task.get("status_message", "no status_message")

    if code is None:
        raise BadRequestError("Task missing status_code")
    if is_success(code):
        return
    if code >= 50000 or code in RETRYABLE_TASK_CODES:
        raise TaskError(f"Task {code}: {message}", code=code)
    raise BadRequestError(f"Task {code}: {message}", code=code)
