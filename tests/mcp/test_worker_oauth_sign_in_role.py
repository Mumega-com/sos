"""
R2 privilege-escalation fix — worker_oauth sign_in role derivation tests.

Covers:
  - Role is derived from membership, NOT hardcoded to "owner"
  - Non-owner identity gets its real role (member → member, viewer → viewer)
  - An identity with "owner" membership gets "owner" only if resolve-owner says so
  - sub_matched=false → deny (resolve-owner returned unverified principal)
  - agent_identity_id missing on worker_oauth path → explicit deny (P3-LOW)
  - No role="owner" hardcode remains reachable on worker_oauth path
  - Standard (non-worker_oauth) path is not regressed
"""
from __future__ import annotations

import json
import asyncio
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from sos.mcp.sos_mcp_sse import (
    MCPAuthContext,
    _handle_sign_in,
    _resolve_owner_via_idp,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _worker_oauth_auth(
    *,
    tenant_id: str = "acme",
    agent_identity_id: str | None = "ident-abc123",
    idp_provider: str | None = "google",
    idp_sub: str | None = "sub-google-9999",
    email: str | None = "alice@acme.com",
    email_verified: bool = True,
    role: str = "admin",  # default on fresh context
) -> MCPAuthContext:
    return MCPAuthContext(
        token="stub-hash-00000000000000",
        tenant_id=tenant_id,
        is_system=False,
        source="worker_oauth",
        scope="customer",
        role=role,
        agent_identity_id=agent_identity_id,
        idp_provider=idp_provider,
        idp_sub=idp_sub,
        email=email,
        email_verified=email_verified,
    )


def _resolve_owner_response(
    *,
    sub_matched: bool = True,
    identity_id: str = "ident-abc123",
    memberships: list[dict[str, str]] | None = None,
) -> dict[str, Any]:
    return {
        "identity_id": identity_id,
        "found_via": "idp_sub",
        "sub_matched": sub_matched,
        "person_email": "alice@acme.com",
        "memberships": memberships if memberships is not None else [],
    }


def _run(coro):
    return asyncio.get_event_loop().run_until_complete(coro)


# ---------------------------------------------------------------------------
# Role derivation — core of the R2 fix
# ---------------------------------------------------------------------------

def test_member_role_not_elevated_to_owner():
    """A member identity must get 'member', not 'owner'."""
    auth = _worker_oauth_auth()
    resolve_resp = _resolve_owner_response(
        sub_matched=True,
        memberships=[{"project_id": "acme", "role": "member"}],
    )
    with patch(
        "sos.mcp.sos_mcp_sse._resolve_owner_via_idp",
        new=AsyncMock(return_value=resolve_resp),
    ), patch(
        "sos.mcp.sos_mcp_sse._push_tools_list_changed",
        new=AsyncMock(),
    ):
        result = _run(_handle_sign_in(auth, {"project": "acme"}, session_id=None))

    body = json.loads(result["content"][0]["text"])
    assert body["ok"] is True
    assert body["role"] == "member", (
        f"Expected 'member', got '{body['role']}' — R2 fix broken: "
        "member identity must NOT be elevated to owner"
    )
    assert auth.role == "member"


def test_viewer_role_not_elevated_to_owner():
    """A viewer identity must get 'viewer', not 'owner'."""
    auth = _worker_oauth_auth()
    resolve_resp = _resolve_owner_response(
        sub_matched=True,
        memberships=[{"project_id": "acme", "role": "viewer"}],
    )
    with patch(
        "sos.mcp.sos_mcp_sse._resolve_owner_via_idp",
        new=AsyncMock(return_value=resolve_resp),
    ), patch(
        "sos.mcp.sos_mcp_sse._push_tools_list_changed",
        new=AsyncMock(),
    ):
        result = _run(_handle_sign_in(auth, {"project": "acme"}, session_id=None))

    body = json.loads(result["content"][0]["text"])
    assert body["role"] == "viewer"
    assert auth.role == "viewer"


def test_owner_membership_gets_owner_role():
    """An identity with owner membership must get 'owner' (legitimate path)."""
    auth = _worker_oauth_auth()
    resolve_resp = _resolve_owner_response(
        sub_matched=True,
        memberships=[{"project_id": "acme", "role": "owner"}],
    )
    with patch(
        "sos.mcp.sos_mcp_sse._resolve_owner_via_idp",
        new=AsyncMock(return_value=resolve_resp),
    ), patch(
        "sos.mcp.sos_mcp_sse._push_tools_list_changed",
        new=AsyncMock(),
    ):
        result = _run(_handle_sign_in(auth, {"project": "acme"}, session_id=None))

    body = json.loads(result["content"][0]["text"])
    assert body["ok"] is True
    assert body["role"] == "owner"


# ---------------------------------------------------------------------------
# Negative: sub_matched=false → deny
# ---------------------------------------------------------------------------

def test_sub_matched_false_denies_sign_in():
    """resolve-owner returning sub_matched=false must deny sign_in entirely."""
    auth = _worker_oauth_auth()
    resolve_resp = _resolve_owner_response(sub_matched=False, memberships=[])
    with patch(
        "sos.mcp.sos_mcp_sse._resolve_owner_via_idp",
        new=AsyncMock(return_value=resolve_resp),
    ):
        result = _run(_handle_sign_in(auth, {"project": "acme"}, session_id=None))

    text = result["content"][0]["text"]
    assert "denied" in text.lower()
    assert "sub_matched" in text
    # auth.active_project must NOT be set
    assert auth.active_project is None


def test_resolve_owner_returns_none_denies_sign_in():
    """resolve-owner returning None (network failure) must deny sign_in."""
    auth = _worker_oauth_auth()
    with patch(
        "sos.mcp.sos_mcp_sse._resolve_owner_via_idp",
        new=AsyncMock(return_value=None),
    ):
        result = _run(_handle_sign_in(auth, {"project": "acme"}, session_id=None))

    text = result["content"][0]["text"]
    assert "denied" in text.lower()
    assert auth.active_project is None


# ---------------------------------------------------------------------------
# P3-LOW: agent_identity_id missing → explicit deny
# ---------------------------------------------------------------------------

def test_worker_oauth_missing_agent_identity_id_is_explicit_deny():
    """worker_oauth path with agent_identity_id=None must return explicit deny,
    NOT fall through to the stub-token lookup (_inkwell_lookup_connection)."""
    auth = _worker_oauth_auth(agent_identity_id=None)
    with patch(
        "sos.mcp.sos_mcp_sse._inkwell_lookup_connection",
        new=AsyncMock(return_value=None),
    ) as mock_lookup:
        result = _run(_handle_sign_in(auth, {"project": "acme"}, session_id=None))

    text = result["content"][0]["text"]
    assert "denied" in text.lower()
    # Crucial: stub-token lookup must NOT have been called
    mock_lookup.assert_not_called()
    assert auth.active_project is None


# ---------------------------------------------------------------------------
# No membership for requested project → deny
# ---------------------------------------------------------------------------

def test_no_membership_for_project_denies_sign_in():
    """sub_matched=true but no membership for the requested project → deny."""
    auth = _worker_oauth_auth()
    # Memberships are for a different project
    resolve_resp = _resolve_owner_response(
        sub_matched=True,
        memberships=[{"project_id": "other-project", "role": "owner"}],
    )
    with patch(
        "sos.mcp.sos_mcp_sse._resolve_owner_via_idp",
        new=AsyncMock(return_value=resolve_resp),
    ):
        result = _run(_handle_sign_in(auth, {"project": "acme"}, session_id=None))

    text = result["content"][0]["text"]
    assert "denied" in text.lower()
    assert auth.active_project is None


# ---------------------------------------------------------------------------
# Project scope guard (unchanged from before)
# ---------------------------------------------------------------------------

def test_worker_oauth_cross_project_sign_in_denied():
    """worker_oauth can only sign in to own tenant project."""
    auth = _worker_oauth_auth(tenant_id="acme")
    result = _run(_handle_sign_in(auth, {"project": "other-tenant"}, session_id=None))
    text = result["content"][0]["text"]
    assert "acme" in text
    assert auth.active_project is None


# ---------------------------------------------------------------------------
# Standard (non-worker_oauth) path is not regressed
# ---------------------------------------------------------------------------

def test_standard_path_not_affected():
    """The standard BYOA token path (non-worker_oauth) still resolves role
    from membership, not from worker_oauth logic."""
    auth = MCPAuthContext(
        token="some-byoa-hash",
        tenant_id="acme",
        is_system=False,
        source="byoa",  # NOT worker_oauth
        scope="customer",
        role="admin",
    )
    lookup_resp = {
        "identity": {"id": "ident-byoa-1"},
        "memberships": [{"project_id": "acme", "role": "editor"}],
    }
    with patch(
        "sos.mcp.sos_mcp_sse._inkwell_lookup_connection",
        new=AsyncMock(return_value=lookup_resp),
    ), patch(
        "sos.mcp.sos_mcp_sse._push_tools_list_changed",
        new=AsyncMock(),
    ):
        result = _run(_handle_sign_in(auth, {"project": "acme"}, session_id=None))

    body = json.loads(result["content"][0]["text"])
    assert body["ok"] is True
    assert body["role"] == "editor"


# ---------------------------------------------------------------------------
# _resolve_owner_via_idp unit tests
# ---------------------------------------------------------------------------

def test_resolve_owner_missing_provider_returns_none():
    """Missing idp_provider → None (cannot resolve)."""
    result = _run(
        _resolve_owner_via_idp(
            idp_provider=None,
            idp_sub="sub-9999",
            email="alice@acme.com",
            email_verified=True,
        )
    )
    assert result is None


def test_resolve_owner_missing_sub_returns_none():
    """Missing idp_sub → None (cannot resolve)."""
    result = _run(
        _resolve_owner_via_idp(
            idp_provider="google",
            idp_sub=None,
            email="alice@acme.com",
            email_verified=True,
        )
    )
    assert result is None


def test_resolve_owner_missing_internal_secret_returns_none(monkeypatch):
    """INTERNAL_API_SECRET unset → None (cannot call inkwell-api)."""
    monkeypatch.setattr("sos.mcp.sos_mcp_sse.INTERNAL_API_SECRET", "")
    result = _run(
        _resolve_owner_via_idp(
            idp_provider="google",
            idp_sub="sub-9999",
            email="alice@acme.com",
            email_verified=True,
        )
    )
    assert result is None


def test_resolve_owner_calls_correct_endpoint(monkeypatch):
    """resolve-owner POST hits the right URL with the right payload."""
    monkeypatch.setattr("sos.mcp.sos_mcp_sse.INTERNAL_API_SECRET", "test-secret")
    monkeypatch.setattr("sos.mcp.sos_mcp_sse.INKWELL_API_URL", "https://test.mumega.com")

    import httpx
    from unittest.mock import AsyncMock as AM, MagicMock as MM

    mock_resp = MM()
    mock_resp.status_code = 200
    mock_resp.json.return_value = {
        "identity_id": "ident-x",
        "sub_matched": True,
        "memberships": [{"project_id": "acme", "role": "member"}],
    }

    class _MockAsyncClient:
        async def __aenter__(self):
            return self
        async def __aexit__(self, *a):
            pass
        async def post(self, url, headers=None, json=None):
            self.called_url = url
            self.called_json = json
            self.called_headers = headers
            return mock_resp

    mock_client = _MockAsyncClient()

    with patch("httpx.AsyncClient", return_value=mock_client):
        result = _run(
            _resolve_owner_via_idp(
                idp_provider="google",
                idp_sub="sub-9999",
                email="alice@acme.com",
                email_verified=True,
            )
        )

    assert result is not None
    assert result["sub_matched"] is True
    assert mock_client.called_url == "https://test.mumega.com/api/agents/identities/resolve-owner"
    assert mock_client.called_json["idp_provider"] == "google"
    assert mock_client.called_json["sub"] == "sub-9999"
    assert mock_client.called_json["email"] == "alice@acme.com"
    assert mock_client.called_json["email_verified"] is True
    assert mock_client.called_headers["Authorization"] == "Bearer test-secret"
