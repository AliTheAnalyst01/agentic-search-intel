import pytest

from app.dataforseo.classify import classify_body, classify_http, classify_task
from app.dataforseo.errors import (
    AuthError,
    BadRequestError,
    PaymentRequiredError,
    RateLimitError,
    ServerError,
    TaskError,
)


def test_http_200_passes():
    assert classify_http(200) is None


@pytest.mark.parametrize(
    "status,expected",
    [
        (401, AuthError),
        (402, PaymentRequiredError),
        (404, BadRequestError),
        (429, RateLimitError),
        (503, ServerError),
    ],
)
def test_http_statuses_map_to_types(status, expected):
    with pytest.raises(expected):
        classify_http(status)


def test_body_success_passes():
    assert classify_body({"status_code": 20000, "status_message": "Ok."}) is None


def test_body_client_error_is_not_retryable():
    with pytest.raises(BadRequestError) as exc:
        classify_body({"status_code": 40501, "status_message": "Invalid Field."})
    assert exc.value.retryable is False


def test_body_server_error_is_retryable():
    with pytest.raises(ServerError) as exc:
        classify_body({"status_code": 50000, "status_message": "Internal Error."})
    assert exc.value.retryable is True


def test_missing_status_code_is_a_bad_request():
    with pytest.raises(BadRequestError):
        classify_body({"tasks": []})


def test_known_upstream_task_failure_is_retryable():
    with pytest.raises(TaskError) as exc:
        classify_task({"status_code": 40101, "status_message": "Internal SE Server Error."})
    assert exc.value.retryable is True


def test_task_invalid_field_is_not_retryable():
    with pytest.raises(BadRequestError) as exc:
        classify_task({"status_code": 40501, "status_message": "Invalid Field."})
    assert exc.value.retryable is False
