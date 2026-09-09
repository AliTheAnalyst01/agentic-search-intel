import httpx
import pytest

from app.dataforseo.client import DataForSEOClient
from app.dataforseo.errors import (
    AuthError,
    BadRequestError,
    RateLimitError,
    ServerError,
    TransportError,
)
from app.dataforseo.retry import RetryPolicy

OK_BODY = {"status_code": 20000, "status_message": "Ok.", "tasks": []}
PAYLOAD = [{"keyword": "best seo tool"}]


def make_client(handler, **kwargs) -> DataForSEOClient:
    transport = httpx.MockTransport(handler)
    return DataForSEOClient(
        base_url="https://sandbox.example.com",
        login="user",
        password="pass",
        client=httpx.Client(transport=transport),
        policy=RetryPolicy(max_attempts=3, base_delay=0.0, max_delay=0.0),
        **kwargs,
    )


def test_happy_path_returns_body():
    client = make_client(lambda req: httpx.Response(200, json=OK_BODY))
    body, outcome = client.post("/v3/serp/google/organic/live/advanced", PAYLOAD)

    assert body["status_code"] == 20000
    assert outcome.attempts == 1


def test_url_is_joined_without_double_slash():
    seen: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(str(request.url))
        return httpx.Response(200, json=OK_BODY)

    make_client(handler).post("/v3/serp/errors", PAYLOAD)
    assert seen == ["https://sandbox.example.com/v3/serp/errors"]


def test_payload_is_sent_as_json():
    seen: list[bytes] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request.content)
        return httpx.Response(200, json=OK_BODY)

    make_client(handler).post("/v3/serp/errors", PAYLOAD)
    assert b"best seo tool" in seen[0]


def test_transient_server_error_is_retried_then_succeeds():
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        if calls["n"] < 3:
            return httpx.Response(503, text="unavailable")
        return httpx.Response(200, json=OK_BODY)

    body, outcome = make_client(handler).post("/v3/serp/errors", PAYLOAD)

    assert body["status_code"] == 20000
    assert outcome.attempts == 3
    assert len(outcome.delays) == 2


def test_auth_failure_is_not_retried():
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        return httpx.Response(401, text="unauthorized")

    with pytest.raises(AuthError):
        make_client(handler).post("/v3/serp/errors", PAYLOAD)

    assert calls["n"] == 1


def test_rate_limit_exhausts_attempts_then_raises():
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        return httpx.Response(429, text="slow down")

    with pytest.raises(RateLimitError):
        make_client(handler).post("/v3/serp/errors", PAYLOAD)

    assert calls["n"] == 3


def test_http_200_with_body_error_still_raises():
    """The trap: a 200 that is actually a failure."""
    body_error = {"status_code": 40501, "status_message": "Invalid Field: 'keyword'."}
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        return httpx.Response(200, json=body_error)

    with pytest.raises(BadRequestError):
        make_client(handler).post("/v3/serp/errors", PAYLOAD)

    assert calls["n"] == 1, "a validation error must not be retried"


def test_http_200_with_body_server_error_is_retried():
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        if calls["n"] < 2:
            return httpx.Response(
                200, json={"status_code": 50000, "status_message": "Internal Error."}
            )
        return httpx.Response(200, json=OK_BODY)

    body, outcome = make_client(handler).post("/v3/serp/errors", PAYLOAD)

    assert body["status_code"] == 20000
    assert outcome.attempts == 2


def test_timeout_becomes_transport_error_and_is_retried():
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        raise httpx.ReadTimeout("too slow", request=request)

    with pytest.raises(TransportError):
        make_client(handler).post("/v3/serp/errors", PAYLOAD)

    assert calls["n"] == 3


def test_non_json_response_becomes_transport_error():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text="<html>gateway page</html>")

    with pytest.raises(TransportError):
        make_client(handler).post("/v3/serp/errors", PAYLOAD)


def test_basic_auth_header_is_sent():
    seen: list[str | None] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request.headers.get("authorization"))
        return httpx.Response(200, json=OK_BODY)

    make_client(handler).post("/v3/serp/errors", PAYLOAD)
    assert seen[0] is not None and seen[0].startswith("Basic ")


# --- circuit breaker integration ---

def test_repeated_exhausted_failures_open_the_circuit():
    from app.dataforseo.breaker import BreakerState, CircuitBreaker, CircuitOpenError

    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        return httpx.Response(503, text="unavailable")

    breaker = CircuitBreaker(failure_threshold=2, cooldown_seconds=30.0)
    client = make_client(handler, breaker=breaker)

    for _ in range(2):
        with pytest.raises(ServerError):
            client.post("/v3/serp/errors", PAYLOAD)

    assert breaker.state is BreakerState.OPEN
    attempts_before = calls["n"]

    with pytest.raises(CircuitOpenError):
        client.post("/v3/serp/errors", PAYLOAD)

    assert calls["n"] == attempts_before, "refused without touching the network"


def test_a_success_keeps_the_circuit_closed():
    from app.dataforseo.breaker import BreakerState, CircuitBreaker

    breaker = CircuitBreaker(failure_threshold=2)
    client = make_client(lambda req: httpx.Response(200, json=OK_BODY), breaker=breaker)
    client.post("/v3/serp/errors", PAYLOAD)

    assert breaker.state is BreakerState.CLOSED


def test_auth_failures_do_not_open_the_circuit():
    from app.dataforseo.breaker import BreakerState, CircuitBreaker

    breaker = CircuitBreaker(failure_threshold=2)
    client = make_client(lambda req: httpx.Response(401, text="nope"), breaker=breaker)

    for _ in range(3):
        with pytest.raises(AuthError):
            client.post("/v3/serp/errors", PAYLOAD)

    assert breaker.state is BreakerState.CLOSED
