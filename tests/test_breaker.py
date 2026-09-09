import pytest

from app.dataforseo.breaker import BreakerState, CircuitBreaker, CircuitOpenError
from app.dataforseo.errors import AuthError, BadRequestError, RateLimitError


class Clock:
    """Injectable time, so tests never wait for a cooldown."""

    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


def breaker(**kwargs) -> tuple[CircuitBreaker, Clock]:
    clock = Clock()
    return CircuitBreaker(clock=clock, **kwargs), clock


# --- closed state ---

def test_starts_closed_and_allows_calls():
    b, _ = breaker()

    assert b.state is BreakerState.CLOSED
    assert b.before_call() is None


def test_success_resets_the_failure_count():
    b, _ = breaker(failure_threshold=3)
    b.record_failure(RateLimitError("429"))
    b.record_failure(RateLimitError("429"))
    b.record_success()
    b.record_failure(RateLimitError("429"))

    assert b.state is BreakerState.CLOSED, "counter restarted, so no trip"


# --- tripping ---

def test_trips_after_the_threshold():
    b, _ = breaker(failure_threshold=3)
    for _ in range(3):
        b.record_failure(RateLimitError("429"))

    assert b.state is BreakerState.OPEN


def test_open_circuit_refuses_without_attempting():
    b, _ = breaker(failure_threshold=2)
    b.record_failure(RateLimitError("429"))
    b.record_failure(RateLimitError("429"))

    with pytest.raises(CircuitOpenError):
        b.before_call()


def test_refusal_is_not_retryable():
    """Retrying a call we are declining to make is incoherent."""
    assert CircuitOpenError("open").retryable is False


def test_refusal_message_states_the_remaining_cooldown():
    b, _ = breaker(failure_threshold=1, cooldown_seconds=30.0)
    b.record_failure(RateLimitError("429"))

    with pytest.raises(CircuitOpenError, match="30.0s"):
        b.before_call()


# --- what counts as a failure ---

def test_non_retryable_errors_do_not_trip_the_breaker():
    """A 401 is a config problem a breaker cannot help with."""
    b, _ = breaker(failure_threshold=2)
    b.record_failure(AuthError("401"))
    b.record_failure(AuthError("401"))
    b.record_failure(AuthError("401"))

    assert b.state is BreakerState.CLOSED


def test_bad_requests_do_not_trip_the_breaker():
    """Our own malformed request is not dependency ill-health."""
    b, _ = breaker(failure_threshold=2)
    b.record_failure(BadRequestError("40501"))
    b.record_failure(BadRequestError("40501"))

    assert b.state is BreakerState.CLOSED


def test_mixed_failures_only_count_the_retryable_ones():
    b, _ = breaker(failure_threshold=3)
    b.record_failure(RateLimitError("429"))
    b.record_failure(AuthError("401"))
    b.record_failure(RateLimitError("429"))

    assert b.state is BreakerState.CLOSED, "only 2 retryable failures so far"

    b.record_failure(RateLimitError("429"))
    assert b.state is BreakerState.OPEN


# --- recovery ---

def test_cooldown_promotes_open_to_half_open():
    b, clock = breaker(failure_threshold=1, cooldown_seconds=30.0)
    b.record_failure(RateLimitError("429"))
    assert b.state is BreakerState.OPEN

    clock.advance(30.0)
    assert b.state is BreakerState.HALF_OPEN


def test_half_open_allows_a_probe():
    b, clock = breaker(failure_threshold=1, cooldown_seconds=10.0)
    b.record_failure(RateLimitError("429"))
    clock.advance(10.0)

    assert b.before_call() is None, "half-open lets one call through"


def test_successful_probe_closes_the_circuit():
    b, clock = breaker(failure_threshold=1, cooldown_seconds=10.0)
    b.record_failure(RateLimitError("429"))
    clock.advance(10.0)
    b.before_call()
    b.record_success()

    assert b.state is BreakerState.CLOSED


def test_failed_probe_reopens_and_restarts_the_cooldown():
    b, clock = breaker(failure_threshold=1, cooldown_seconds=10.0)
    b.record_failure(RateLimitError("429"))
    clock.advance(10.0)
    assert b.state is BreakerState.HALF_OPEN

    b.record_failure(RateLimitError("429"))
    assert b.state is BreakerState.OPEN

    clock.advance(9.0)
    assert b.state is BreakerState.OPEN, "cooldown restarted from the reopen"


# --- observability ---

def test_snapshot_reports_state_for_logging():
    b, _ = breaker(failure_threshold=2)
    b.record_failure(RateLimitError("429"))
    snap = b.snapshot()

    assert snap["state"] == "closed"
    assert snap["consecutive_failures"] == 1


def test_trips_and_refusals_are_counted():
    b, _ = breaker(failure_threshold=1)
    b.record_failure(RateLimitError("429"))
    for _ in range(3):
        with pytest.raises(CircuitOpenError):
            b.before_call()

    snap = b.snapshot()
    assert snap["trips"] == 1
    assert snap["calls_refused"] == 3
