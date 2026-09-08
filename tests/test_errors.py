from app.dataforseo.errors import (
    AuthError,
    DataForSEOError,
    RateLimitError,
    TransportError,
)


def test_transient_errors_are_retryable():
    assert TransportError("timeout").retryable is True
    assert RateLimitError("429").retryable is True


def test_auth_errors_are_not_retryable():
    assert AuthError("bad password").retryable is False


def test_all_errors_share_a_base():
    assert issubclass(AuthError, DataForSEOError)


def test_code_is_preserved():
    err = RateLimitError("slow down", code=40200)
    assert err.code == 40200
