import random

import pytest

from app.dataforseo.errors import AuthError, RateLimitError
from app.dataforseo.retry import RetryPolicy, backoff_delay, call_with_retry


class Recorder:
    """Stands in for time.sleep and remembers the delays."""

    def __init__(self) -> None:
        self.delays: list[float] = []

    def __call__(self, delay: float) -> None:
        self.delays.append(delay)


def test_success_on_first_attempt_never_sleeps():
    sleeper = Recorder()
    result, outcome = call_with_retry(lambda: "ok", sleep=sleeper)

    assert result == "ok"
    assert outcome.attempts == 1
    assert sleeper.delays == []


def test_retryable_failure_then_success():
    sleeper = Recorder()
    calls = {"n": 0}

    def flaky() -> str:
        calls["n"] += 1
        if calls["n"] < 3:
            raise RateLimitError("429")
        return "recovered"

    result, outcome = call_with_retry(flaky, sleep=sleeper)

    assert result == "recovered"
    assert outcome.attempts == 3
    assert len(sleeper.delays) == 2


def test_non_retryable_fails_immediately():
    sleeper = Recorder()
    calls = {"n": 0}

    def bad_auth() -> str:
        calls["n"] += 1
        raise AuthError("401")

    with pytest.raises(AuthError):
        call_with_retry(bad_auth, sleep=sleeper)

    assert calls["n"] == 1, "auth failures must not be retried"
    assert sleeper.delays == []


def test_exhausting_attempts_raises_the_last_error():
    sleeper = Recorder()
    calls = {"n": 0}

    def always_429() -> str:
        calls["n"] += 1
        raise RateLimitError("429")

    policy = RetryPolicy(max_attempts=4)
    with pytest.raises(RateLimitError):
        call_with_retry(always_429, policy=policy, sleep=sleeper)

    assert calls["n"] == 4
    assert len(sleeper.delays) == 3, "no sleep after the final attempt"


def test_on_retry_callback_reports_each_retry():
    seen: list[tuple[int, str]] = []
    calls = {"n": 0}

    def flaky() -> str:
        calls["n"] += 1
        if calls["n"] < 3:
            raise RateLimitError("429")
        return "ok"

    call_with_retry(
        flaky,
        sleep=Recorder(),
        on_retry=lambda attempt, err, delay: seen.append((attempt, type(err).__name__)),
    )

    assert seen == [(1, "RateLimitError"), (2, "RateLimitError")]


def test_backoff_window_grows_and_is_capped():
    policy = RetryPolicy(base_delay=1.0, max_delay=4.0)
    rng = random.Random(0)

    for attempt, cap in [(1, 1.0), (2, 2.0), (3, 4.0), (4, 4.0), (10, 4.0)]:
        delay = backoff_delay(attempt, policy, rng)
        assert 0 <= delay <= cap


def test_jitter_produces_different_delays():
    policy = RetryPolicy(base_delay=1.0)
    rng = random.Random(42)
    delays = {backoff_delay(3, policy, rng) for _ in range(20)}

    assert len(delays) > 1, "fixed delays would synchronise all clients"
