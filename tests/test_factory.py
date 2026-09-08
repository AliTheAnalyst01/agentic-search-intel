import pytest

from app.dataforseo.client import DataForSEOClient
from app.dataforseo.factory import get_client
from app.dataforseo.mock import MockDataForSEOClient


def test_mock_mode_needs_no_credentials():
    assert isinstance(get_client("mock"), MockDataForSEOClient)


def test_live_mode_without_credentials_fails_loudly(monkeypatch):
    monkeypatch.setattr("app.dataforseo.factory.settings.dataforseo_login", "")
    monkeypatch.setattr("app.dataforseo.factory.settings.dataforseo_password", "")

    with pytest.raises(RuntimeError, match="DATAFORSEO_MODE=mock"):
        get_client("live")


def test_sandbox_mode_with_credentials_returns_real_client(monkeypatch):
    monkeypatch.setattr("app.dataforseo.factory.settings.dataforseo_login", "u")
    monkeypatch.setattr("app.dataforseo.factory.settings.dataforseo_password", "p")

    client = get_client("sandbox")
    assert isinstance(client, DataForSEOClient)
    assert "sandbox" in client.base_url
