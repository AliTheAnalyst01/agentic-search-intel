"""Retry with exponential backoff and full jitter.

Deliberately knows nothing about DataForSEO: it asks the exception whether
it is retryable. `sleep` is injected so tests can assert on the backoff
curve without waiting for it.
"""

import random
import time
from dataclasses import dataclass
from typing import Callable, Protocol, TypeVar

T = TypeVar("T")


class Retryable(Protocol):
    retryable: bool


@dataclass(frozen=True)
class RetryPolicy:
    max_attempts: int = 3
    base_delay: float = 0.5
    max_delay: float = 8.0


@dataclass
class RetryOutcome:
    """What happened, for the observability layer to log."""
    attempts: int = 0
    delays: list[float] = None  # type: ignore[assignment]

    def __post_init__(self) -> None:
        if self.delays is None:
            self.delays = []


def backoff_delay(attempt: int, policy: RetryPolicy, rng: random.Random) -> float:
    """Full jitter: uniform across the whole exponential window.

    attempt is 1-based, so the first retry draws from [0, base_delay].
    """
    window = min(policy.max_delay, policy.base_delay * (2 ** (attempt - 1)))
    return rng.uniform(0, window)


def call_with_retry(
    fn: Callable[[], T],
    *,
    policy: RetryPolicy | None = None,
    on_retry: Callable[[int, Exception, float], None] | None = None,
    sleep: Callable[[float], None] = time.sleep,
    rng: random.Random | None = None,
) -> tuple[T, RetryOutcome]:
    """Call fn, retrying only errors that declare themselves retryable.

    Returns the result plus a record of what it took to get it.
    Raises the last exception if attempts are exhausted, or immediately
    on a non-retryable error.
    """
    policy = policy or RetryPolicy()
    rng = rng or random.Random()
    outcome = RetryOutcome()

    for attempt in range(1, policy.max_attempts + 1):
        outcome.attempts = attempt
        try:
            return fn(), outcome
        except Exception as err:
            if not getattr(err, "retryable", False):
                raise
            if attempt == policy.max_attempts:
                raise
            delay = backoff_delay(attempt, policy, rng)
            outcome.delays.append(delay)
            if on_retry is not None:
                on_retry(attempt, err, delay)
            sleep(delay)

    raise AssertionError("unreachable")
