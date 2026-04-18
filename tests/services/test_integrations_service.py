"""Tests for the SOS Integrations HTTP service (`sos/services/integrations/app.py`).

Covers:
- GET /health returns 200 + canonical shape
- GET /oauth/credentials/{tenant}/{provider} returns 404 when credentials missing
- GET /oauth/credentials/{tenant}/{provider} returns 200 + dict when credentials present
- Auth behaviour: missing / mismatched tenant scope rejected
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from sos.services.integrations import app as integrations_app_module
from sos.services.integrations.app import app


@pytest.fixture
def client(monkeypatch: pytest.MonkeyPatch) -> TestClient:
    """Provide a TestClient; skip the startup service-registration path
    which would otherwise require Redis."""
    # Neutralise the startup discovery registration
    monkeypatch.setattr(
        "sos.services.integrations.app._startup",
        lambda: None,
        raising=True,
    )
    return TestClient(app)


@pytest.fixture
def system_token(monkeypatch: pytest.MonkeyPatch) -> str:
    """Install a system-scope Bearer token usable for any tenant."""
    token = "test-sys-token-p0-06"
    monkeypatch.setenv("SOS_SYSTEM_TOKEN", token)
    # auth.verify_bearer has a cache; force a reload so the env is picked up.
    from sos.kernel.auth import get_cache

    get_cache().invalidate()
    return token


@pytest.fixture
def isolated_storage(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Point TenantIntegrations storage root at a tmp dir so tests don't
    clobber real creds at ~/.sos/integrations/."""
    from sos.services.integrations import oauth as oauth_module

    monkeypatch.setattr(oauth_module, "STORAGE_ROOT", tmp_path)
    return tmp_path


# ---------------------------------------------------------------------------
# /health
# ---------------------------------------------------------------------------


def test_health_returns_ok(client: TestClient) -> None:
    resp = client.get("/health")
    assert resp.status_code == 200
    body = resp.json()
    assert body["service"] == "integrations"
    assert body["status"] in {"ok", "degraded"}
    assert "version" in body
    assert "uptime_seconds" in body


# ---------------------------------------------------------------------------
# /oauth/credentials/{tenant}/{provider} — auth
# ---------------------------------------------------------------------------


def test_missing_bearer_is_401(client: TestClient) -> None:
    resp = client.get("/oauth/credentials/viamar/google_analytics")
    assert resp.status_code == 401


def test_invalid_bearer_is_401(client: TestClient) -> None:
    resp = client.get(
        "/oauth/credentials/viamar/google_analytics",
        headers={"Authorization": "Bearer not-a-real-token"},
    )
    assert resp.status_code == 401


# ---------------------------------------------------------------------------
# /oauth/credentials/{tenant}/{provider} — 404 + 200 paths
# ---------------------------------------------------------------------------


def test_no_credentials_returns_404(
    client: TestClient,
    system_token: str,
    isolated_storage: Path,
) -> None:
    resp = client.get(
        "/oauth/credentials/viamar/google_analytics",
        headers={"Authorization": f"Bearer {system_token}"},
    )
    assert resp.status_code == 404
    assert "no credentials" in resp.json().get("detail", "")


def test_credentials_present_returns_200_with_dict(
    client: TestClient,
    system_token: str,
    isolated_storage: Path,
) -> None:
    # Seed a credentials file on disk using the same layout TenantIntegrations
    # expects. We do this directly to keep the test focused on the HTTP surface.
    tenant_dir = isolated_storage / "viamar"
    tenant_dir.mkdir(parents=True, exist_ok=True)
    creds: dict[str, Any] = {
        "provider": "google_analytics",
        "access_token": "ya29.test-token",
        "refresh_token": "1//rtok-test",
        "scopes": ["https://www.googleapis.com/auth/analytics.readonly"],
    }
    (tenant_dir / "google_analytics.json").write_text(json.dumps(creds))

    resp = client.get(
        "/oauth/credentials/viamar/google_analytics",
        headers={"Authorization": f"Bearer {system_token}"},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["access_token"] == "ya29.test-token"
    assert body["provider"] == "google_analytics"


def test_patched_get_credentials_is_honoured(
    client: TestClient,
    system_token: str,
) -> None:
    """Independent verification: the service routes via
    TenantIntegrations.get_credentials, so patching it controls the response."""
    with patch(
        "sos.services.integrations.oauth.TenantIntegrations.get_credentials",
        return_value={"access_token": "patched", "provider": "clarity"},
    ):
        resp = client.get(
            "/oauth/credentials/viamar/clarity",
            headers={"Authorization": f"Bearer {system_token}"},
        )
    assert resp.status_code == 200
    assert resp.json()["access_token"] == "patched"
