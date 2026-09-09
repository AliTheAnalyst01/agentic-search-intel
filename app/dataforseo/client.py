"""HTTP transport for DataForSEO.

One job: make the call, convert failures into the typed taxonomy, and
report what it took. No knowledge of endpoints, schemas, or agents.
"""

import httpx

from app.config import settings
from app.dataforseo.breaker import CircuitBreaker
from app.dataforseo.classify import classify_body, classify_http
from app.dataforseo.errors import TransportError
from app.dataforseo.retry import RetryOutcome, RetryPolicy, call_with_retry

# DataForSEO wraps every payload in a list of tasks, even for one task.
Payload = list[dict[str, object]]


class DataForSEOClient:
    """Authenticated, retrying, timeout-bounded POST client."""

    def __init__(
        self,
        *,
        base_url: str | None = None,
        login: str | None = None,
        password: str | None = None,
        timeout: float | None = None,
        policy: RetryPolicy | None = None,
        client: httpx.Client | None = None,
        breaker: CircuitBreaker | None = None,
    ) -> None:
        self.base_url = (base_url or settings.dataforseo_base_url).rstrip("/")
        self.login = login if login is not None else settings.dataforseo_login
        self.password = (
            password if password is not None else settings.dataforseo_password
        )
        self.timeout = timeout or settings.dataforseo_timeout
        self.policy = policy or RetryPolicy(
            max_attempts=settings.dataforseo_max_attempts
        )
        self.breaker = breaker or CircuitBreaker()
        self._auth = httpx.BasicAuth(self.login, self.password)
        self._client = client or httpx.Client(
            timeout=httpx.Timeout(self.timeout, connect=5.0)
        )

    def post(
        self, path: str, payload: Payload, *, on_retry=None
    ) -> tuple[dict, RetryOutcome]:
        """POST and return the parsed body, or raise a typed error."""
        url = f"{self.base_url}/{path.lstrip('/')}"

        # Refused before any attempt if the dependency is known bad.
        self.breaker.before_call()

        try:
            result = call_with_retry(
                lambda: self._post_once(url, payload),
                policy=self.policy,
                on_retry=on_retry,
            )
        except Exception as err:
            # Reached only after retries are exhausted, so a counted
            # failure represents a genuinely unavailable dependency.
            self.breaker.record_failure(err)
            raise

        self.breaker.record_success()
        return result

    def _post_once(self, url: str, payload: Payload) -> dict:
        try:
            response = self._client.post(url, json=payload, auth=self._auth)
        except httpx.TimeoutException as err:
            raise TransportError(f"Timeout after {self.timeout}s: {err}") from err
        except httpx.TransportError as err:
            raise TransportError(f"Network failure: {err}") from err

        classify_http(response.status_code, response.text)

        try:
            body = response.json()
        except ValueError as err:
            raise TransportError(f"Non-JSON response: {response.text[:200]}") from err

        classify_body(body)
        return body

    def close(self) -> None:
        self._client.close()
