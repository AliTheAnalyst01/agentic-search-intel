"""Circuit breaker for a repeatedly-failing dependency.

Sits above the transport and below nothing: the retry runner has
already exhausted its attempts by the time a failure reaches here.

Only retryable errors count toward tripping. A 401 or a malformed
request is a problem a breaker cannot help with, and counting our own
bad requests toward dependency health would mean a bug in our request
formatting trips a circuit about someone else's service.

    closed  --N consecutive failures-->  open
    open    --after cooldown-->          half_open
    half_open --success-->               closed
    half_open --failure-->               open (cooldown restarts)
"""

import time
from dataclasses import dataclass, field
from enum import Enum

from app.dataforseo.errors import DataForSEOError


class CircuitOpenError(DataForSEOError):
    """The call was refused without being attempted.

    Not retryable: retrying a call we are deliberately declining to
    make is incoherent, and would defeat the breaker's purpose.
    """


class BreakerState(str, Enum):
    CLOSED = "closed"
    OPEN = "open"
    HALF_OPEN = "half_open"


@dataclass
class CircuitBreaker:
    failure_threshold: int = 3
    cooldown_seconds: float = 30.0
    clock: object = field(default=None)

    _state: BreakerState = field(default=BreakerState.CLOSED, init=False)
    _consecutive_failures: int = field(default=0, init=False)
    _opened_at: float = field(default=0.0, init=False)
    trip_count: int = field(default=0, init=False)
    refused_count: int = field(default=0, init=False)

    def _now(self) -> float:
        return self.clock() if self.clock is not None else time.monotonic()

    @property
    def state(self) -> BreakerState:
        """Current state, promoting open to half_open once cooled down."""
        if (
            self._state is BreakerState.OPEN
            and self._now() - self._opened_at >= self.cooldown_seconds
        ):
            self._state = BreakerState.HALF_OPEN
        return self._state

    def before_call(self) -> None:
        """Raise if the circuit is refusing calls."""
        if self.state is BreakerState.OPEN:
            remaining = self.cooldown_seconds - (self._now() - self._opened_at)
            self.refused_count += 1
            raise CircuitOpenError(
                f"Circuit open after {self._consecutive_failures} consecutive "
                f"failures; retrying in {max(0.0, remaining):.1f}s",
            )

    def record_success(self) -> None:
        self._consecutive_failures = 0
        self._state = BreakerState.CLOSED

    def record_failure(self, error: Exception) -> None:
        """Count a failure, but only if the breaker can help with it."""
        if not getattr(error, "retryable", False):
            return

        self._consecutive_failures += 1
        if self._consecutive_failures >= self.failure_threshold:
            if self._state is not BreakerState.OPEN:
                self.trip_count += 1
            self._state = BreakerState.OPEN
            self._opened_at = self._now()

    def snapshot(self) -> dict[str, object]:
        """For the observability layer."""
        return {
            "state": self.state.value,
            "consecutive_failures": self._consecutive_failures,
            "trips": self.trip_count,
            "calls_refused": self.refused_count,
        }
