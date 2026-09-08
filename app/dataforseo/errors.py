"""Error taxonomy for DataForSEO calls.

Retryability is a property of the error type, not a decision made at the
call site. The retry layer asks the exception, never the HTTP status.
"""


class DataForSEOError(Exception):
    """Base for every DataForSEO failure."""

    retryable: bool = False

    def __init__(self, message: str, *, code: int | None = None) -> None:
        super().__init__(message)
        self.message = message
        self.code = code


# --- Retryable: the request was fine, the world was temporarily not ---

class TransportError(DataForSEOError):
    """Timeout, connection reset, DNS failure."""
    retryable = True


class RateLimitError(DataForSEOError):
    """Too many requests. Back off."""
    retryable = True


class ServerError(DataForSEOError):
    """Their side broke. Might work next time."""
    retryable = True


class TaskError(DataForSEOError):
    """Per-task failure inside an otherwise successful response."""
    retryable = True


# --- Non-retryable: retrying cannot possibly help ---

class AuthError(DataForSEOError):
    """Bad credentials. Retrying just wastes the rate limit."""


class PaymentRequiredError(DataForSEOError):
    """Account out of balance. A human must act."""


class BadRequestError(DataForSEOError):
    """Malformed request or invalid field. Our bug, not theirs."""
