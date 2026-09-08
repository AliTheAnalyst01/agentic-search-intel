"""Single decision point for which client the app uses."""

from app.config import settings
from app.dataforseo.client import DataForSEOClient
from app.dataforseo.mock import MockDataForSEOClient

LIVE_BASE_URL = "https://api.dataforseo.com"
SANDBOX_BASE_URL = "https://sandbox.dataforseo.com"


def get_client(mode: str | None = None):
    """Return a client honouring the DataForSEOClient interface.

    mock    - fixtures only, no credentials, no network
    sandbox - real HTTP, sample data, not billed
    live    - real HTTP, real data, billed
    """
    mode = mode or settings.dataforseo_mode

    if mode == "mock":
        return MockDataForSEOClient()

    if not settings.dataforseo_login or not settings.dataforseo_password:
        raise RuntimeError(
            f"DATAFORSEO_MODE={mode} requires DATAFORSEO_LOGIN and "
            "DATAFORSEO_PASSWORD. Use DATAFORSEO_MODE=mock to run without credentials."
        )

    base_url = LIVE_BASE_URL if mode == "live" else SANDBOX_BASE_URL
    return DataForSEOClient(base_url=base_url)
