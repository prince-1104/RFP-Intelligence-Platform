from unittest.mock import Mock

import httpx
import pytest
from fastapi.testclient import TestClient
from pydantic import SecretStr, ValidationError

from rfp.api.app import create_app
from rfp.api.health import dependency_checks
from rfp.core.config import Settings


def configure(settings, api_key):
    return Settings(
        _env_file=None,
        **{
            **settings.model_dump(),
            "qdrant_url": "https://cluster.example.com",
            "qdrant_api_key": api_key,
        },
    )


@pytest.mark.parametrize("api_key", ["", "test-private-key"])
def test_qdrant_readiness_auth(settings, monkeypatch, api_key):
    configured = configure(settings, api_key)
    requests = []

    def handle(request):
        requests.append(request)
        assert request.headers.get("api-key", "") == api_key
        return httpx.Response(200, json={"result": {"collections": []}})

    real_client = httpx.Client
    monkeypatch.setattr(
        "rfp.api.health.httpx.Client",
        lambda **kwargs: real_client(transport=httpx.MockTransport(handle), **kwargs),
    )
    dependency_checks(configured, Mock())["qdrant"]()
    assert [request.url.path for request in requests] == (
        ["/readyz", "/collections"] if api_key else ["/readyz"]
    )
    assert "test-private-key" not in repr(configured)


def test_qdrant_requires_https(settings):
    with pytest.raises(ValidationError, match="must use HTTPS"):
        Settings(_env_file=None, **{**settings.model_dump(), "qdrant_api_key": "private-key"})


def test_invalid_cloud_key_reports_unhealthy(settings, monkeypatch):
    configured = configure(settings, SecretStr("test-private-key"))
    real_client = httpx.Client

    def handle(request):
        return httpx.Response(
            200 if request.url.path == "/readyz" else 403, text="provider-secret-detail"
        )

    monkeypatch.setattr(
        "rfp.api.health.httpx.Client",
        lambda **kwargs: real_client(transport=httpx.MockTransport(handle), **kwargs),
    )
    check = dependency_checks(configured, Mock())["qdrant"]
    with TestClient(create_app(configured, {"qdrant": check})) as client:
        response = client.get("/health/ready")
        assert response.status_code == 503
        assert response.json()["dependencies"]["qdrant"] == "unhealthy"
        assert "test-private-key" not in response.text
        assert "provider-secret-detail" not in response.text
