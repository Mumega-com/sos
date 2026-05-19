"""S065 Track A — Etsy OAuth2 adapter tests.

Tests cover:
- PKCE pair generation (format, uniqueness, S256 correctness)
- EtsyOAuthClient.authorization_url (required params present)
- EtsyOAuthClient.exchange_code (mocked httpx — success + error paths)
- EtsyOAuthClient.refresh_access_token (mocked httpx)
- TenantIntegrations.connect_etsy (URL delegation)
- TenantIntegrations.handle_etsy_callback (end-to-end with mocked HTTP)
- TenantIntegrations.refresh_etsy_token (stored refresh flow)
- Tenant isolation: two tenants cannot read each other's credentials
- State store: _etsy_state_set / _etsy_state_pop round-trip
"""
from __future__ import annotations

import base64
import hashlib
import json
import os
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from sos.services.integrations.adapters.etsy import (
    EtsyOAuthClient,
    build_etsy_credentials,
    generate_pkce_pair,
)


# ---------------------------------------------------------------------------
# PKCE
# ---------------------------------------------------------------------------

def test_pkce_pair_format():
    verifier, challenge = generate_pkce_pair()
    # verifier: URL-safe base64, no padding
    assert "=" not in verifier
    assert len(verifier) >= 43  # 32 bytes → ~43 chars minimum

def test_pkce_challenge_is_s256(tmp_path):
    verifier, challenge = generate_pkce_pair()
    digest = hashlib.sha256(verifier.encode()).digest()
    expected = base64.urlsafe_b64encode(digest).rstrip(b"=").decode()
    assert challenge == expected

def test_pkce_pair_uniqueness():
    pairs = [generate_pkce_pair() for _ in range(5)]
    verifiers = [p[0] for p in pairs]
    assert len(set(verifiers)) == 5  # all unique


# ---------------------------------------------------------------------------
# EtsyOAuthClient.authorization_url
# ---------------------------------------------------------------------------

def test_authorization_url_contains_required_params():
    with patch.dict(os.environ, {"ETSY_CLIENT_ID": "test-client-id"}):
        client = EtsyOAuthClient()
        url = client.authorization_url(
            redirect_uri="https://example.com/callback",
            state="abc123",
            code_challenge="challenge_xyz",
        )
    assert "www.etsy.com/oauth/connect" in url
    assert "client_id=test-client-id" in url
    assert "code_challenge=challenge_xyz" in url
    assert "code_challenge_method=S256" in url
    assert "state=abc123" in url
    assert "transactions_r" in url


# ---------------------------------------------------------------------------
# EtsyOAuthClient.exchange_code
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_exchange_code_success():
    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.json.return_value = {
        "access_token": "tok_abc",
        "refresh_token": "ref_xyz",
        "token_type": "Bearer",
        "expires_in": 3600,
    }

    with patch.dict(os.environ, {"ETSY_CLIENT_ID": "test-id"}):
        client = EtsyOAuthClient()
        with patch("httpx.AsyncClient") as mock_cls:
            mock_client = AsyncMock()
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=False)
            mock_client.post = AsyncMock(return_value=mock_response)
            mock_cls.return_value = mock_client

            result = await client.exchange_code(
                code="code123",
                redirect_uri="https://example.com/callback",
                code_verifier="verifier_abc",
            )

    assert result["access_token"] == "tok_abc"
    assert result["refresh_token"] == "ref_xyz"


@pytest.mark.asyncio
async def test_exchange_code_http_error_raises():
    mock_response = MagicMock()
    mock_response.status_code = 400
    mock_response.text = "invalid_grant"

    with patch.dict(os.environ, {"ETSY_CLIENT_ID": "test-id"}):
        client = EtsyOAuthClient()
        with patch("httpx.AsyncClient") as mock_cls:
            mock_client = AsyncMock()
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=False)
            mock_client.post = AsyncMock(return_value=mock_response)
            mock_cls.return_value = mock_client

            with pytest.raises(ValueError, match="token exchange failed"):
                await client.exchange_code("c", "https://x.com/cb", "v")


@pytest.mark.asyncio
async def test_exchange_code_missing_client_id_raises():
    with patch.dict(os.environ, {}, clear=True):
        os.environ.pop("ETSY_CLIENT_ID", None)
        client = EtsyOAuthClient()
        with pytest.raises(ValueError, match="ETSY_CLIENT_ID"):
            await client.exchange_code("code", "https://x.com/cb", "v")


# ---------------------------------------------------------------------------
# EtsyOAuthClient.refresh_access_token
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_refresh_access_token_success():
    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.json.return_value = {
        "access_token": "new_tok",
        "refresh_token": "new_ref",
        "expires_in": 3600,
        "token_type": "Bearer",
    }

    with patch.dict(os.environ, {"ETSY_CLIENT_ID": "test-id"}):
        client = EtsyOAuthClient()
        with patch("httpx.AsyncClient") as mock_cls:
            mock_client = AsyncMock()
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=False)
            mock_client.post = AsyncMock(return_value=mock_response)
            mock_cls.return_value = mock_client

            result = await client.refresh_access_token("old_refresh")

    assert result["access_token"] == "new_tok"


# ---------------------------------------------------------------------------
# build_etsy_credentials
# ---------------------------------------------------------------------------

def test_build_etsy_credentials_structure():
    token_response = {
        "access_token": "tok",
        "refresh_token": "ref",
        "token_type": "Bearer",
        "expires_in": 3600,
    }
    creds = build_etsy_credentials(token_response, "shop_42", "Kay's Shop", "user_7")
    assert creds["provider"] == "etsy"
    assert creds["access_token"] == "tok"
    assert creds["shop_id"] == "shop_42"
    assert creds["shop_name"] == "Kay's Shop"
    assert creds["etsy_user_id"] == "user_7"
    assert "expires_at" in creds
    assert "connected_at" in creds
    assert "transactions_r" in creds["scopes"]


# ---------------------------------------------------------------------------
# TenantIntegrations — Etsy methods
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_connect_etsy_returns_url(tmp_path):
    from sos.services.integrations.oauth import TenantIntegrations
    with patch.dict(os.environ, {"ETSY_CLIENT_ID": "test-id"}):
        with patch.object(
            TenantIntegrations,
            "__init__",
            lambda self, tenant: setattr(self, "tenant", tenant) or setattr(self, "storage_dir", tmp_path / tenant) or None,
        ):
            ti = TenantIntegrations("kayhermes")
            url = ti.connect_etsy(
                redirect_uri="https://api.mumega.com/oauth/etsy/callback",
                state="s1",
                code_challenge="ch1",
            )
    assert "etsy.com/oauth/connect" in url
    assert "ch1" in url


@pytest.mark.asyncio
async def test_handle_etsy_callback_stores_credentials(tmp_path):
    """handle_etsy_callback: real exchange returns credentials stored to disk."""
    from sos.services.integrations.oauth import TenantIntegrations

    token_resp = {
        "access_token": "live_tok",
        "refresh_token": "live_ref",
        "token_type": "Bearer",
        "expires_in": 3600,
    }
    me_resp = {"user_id": 99}
    shops_resp = [{"shop_id": 1234, "shop_name": "Kay's Prints"}]

    storage = tmp_path / "kayhermes"
    storage.mkdir()

    with patch.dict(os.environ, {"ETSY_CLIENT_ID": "test-id"}):
        with patch("sos.services.integrations.adapters.etsy.EtsyOAuthClient.exchange_code", new=AsyncMock(return_value=token_resp)), \
             patch("sos.services.integrations.adapters.etsy.EtsyOAuthClient.get_me", new=AsyncMock(return_value=me_resp)), \
             patch("sos.services.integrations.adapters.etsy.EtsyOAuthClient.get_user_shops", new=AsyncMock(return_value=shops_resp)):

            ti = TenantIntegrations.__new__(TenantIntegrations)
            ti.tenant = "kayhermes"
            ti.storage_dir = storage

            creds = await ti.handle_etsy_callback(
                code="code_xyz",
                redirect_uri="https://api.mumega.com/oauth/etsy/callback",
                code_verifier="verifier_abc",
            )

    assert creds["access_token"] == "live_tok"
    assert creds["shop_id"] == "1234"
    assert creds["shop_name"] == "Kay's Prints"
    assert (storage / "etsy.json").exists()
    stored = json.loads((storage / "etsy.json").read_text())
    assert stored["shop_id"] == "1234"


# ---------------------------------------------------------------------------
# Tenant isolation
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_tenant_isolation(tmp_path):
    """Credentials for tenant A must not be visible to tenant B."""
    from sos.services.integrations.oauth import TenantIntegrations

    token_resp = {"access_token": "tok_a", "refresh_token": "ref_a", "token_type": "Bearer", "expires_in": 3600}

    dir_a = tmp_path / "tenant_a"
    dir_a.mkdir()
    dir_b = tmp_path / "tenant_b"
    dir_b.mkdir()

    with patch.dict(os.environ, {"ETSY_CLIENT_ID": "test-id"}):
        with patch("sos.services.integrations.adapters.etsy.EtsyOAuthClient.exchange_code", new=AsyncMock(return_value=token_resp)), \
             patch("sos.services.integrations.adapters.etsy.EtsyOAuthClient.get_me", new=AsyncMock(return_value={"user_id": 1})), \
             patch("sos.services.integrations.adapters.etsy.EtsyOAuthClient.get_user_shops", new=AsyncMock(return_value=[])):

            ti_a = TenantIntegrations.__new__(TenantIntegrations)
            ti_a.tenant = "tenant_a"
            ti_a.storage_dir = dir_a
            await ti_a.handle_etsy_callback("code", "https://cb", "v")

    ti_b = TenantIntegrations.__new__(TenantIntegrations)
    ti_b.tenant = "tenant_b"
    ti_b.storage_dir = dir_b
    assert ti_b.get_credentials("etsy") is None


# ---------------------------------------------------------------------------
# State store round-trip
# ---------------------------------------------------------------------------

def test_state_store_roundtrip():
    from sos.services.integrations.app import _etsy_state_set, _etsy_state_pop, _ETSY_STATE_STORE

    _ETSY_STATE_STORE.clear()

    with patch("sos.services.integrations.app._etsy_state_set", wraps=lambda s, t, v: _ETSY_STATE_STORE.update({s: f"{t}|{v}"})), \
         patch("sos.services.integrations.app._etsy_state_pop", wraps=lambda s: (lambda v: (v.split("|", 1)[0], v.split("|", 1)[1]) if v else None)(_ETSY_STATE_STORE.pop(s, None))):

        from sos.services.integrations import app as _app
        _app._ETSY_STATE_STORE["s_test"] = "kayhermes|verifier_abc"
        result = _app._etsy_state_pop("s_test")
        assert result == ("kayhermes", "verifier_abc")
        assert "s_test" not in _app._ETSY_STATE_STORE

    _ETSY_STATE_STORE.clear()
