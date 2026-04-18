"""Tests for the v0.5.1 unified-policy-gate migration in the integrations service.

Validates that route-level auth behaviour is preserved after replacing
inline _verify_bearer / _check_tenant_scope / _require_system_or_admin
with a single can_execute + _raise_on_deny call per route.
"""
from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from sos.contracts.policy import PolicyDecision
from sos.kernel.auth import AuthContext
from sos.services.integrations.app import app

client = TestClient(app)


# ---------------------------------------------------------------------------
# Shared fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def redirect_audit(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Redirect all audit disk writes to a temp directory."""
    audit_dir = tmp_path / "audit"
    audit_dir.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr("sos.kernel.audit._audit_dir", lambda: audit_dir)


def _system_decision(reason: str = "system/admin scope granted") -> PolicyDecision:
    """Return an allowed PolicyDecision that satisfies the require_system check."""
    return PolicyDecision(
        allowed=True,
        reason=reason,
        tier="act_freely",
        action="test_action",
        resource="test_resource",
        pillars_passed=["tenant_scope"],
        pillars_failed=[],
    )


def _tenant_decision(reason: str = "scope matches tenant 'acme'") -> PolicyDecision:
    """Return an allowed PolicyDecision for a tenant-scoped caller."""
    return PolicyDecision(
        allowed=True,
        reason=reason,
        tier="act_freely",
        action="test_action",
        resource="test_resource",
        pillars_passed=["tenant_scope"],
        pillars_failed=[],
    )


def _denied_auth_decision() -> PolicyDecision:
    """Return a denied PolicyDecision for invalid bearer tokens."""
    return PolicyDecision(
        allowed=False,
        reason="invalid or inactive bearer token",
        tier="denied",
        action="test_action",
        resource="test_resource",
        pillars_passed=[],
        pillars_failed=["auth"],
    )


def _denied_scope_decision(scope: str = "acme", tenant: str = "othertenant") -> PolicyDecision:
    """Return a denied PolicyDecision for a tenant scope mismatch."""
    return PolicyDecision(
        allowed=False,
        reason=f"token scoped to '{scope}', not '{tenant}'",
        tier="denied",
        action="test_action",
        resource="test_resource",
        pillars_passed=[],
        pillars_failed=["tenant_scope"],
    )


def _denied_system_required_decision() -> PolicyDecision:
    """Return an allowed-but-not-system decision (triggers require_system 403)."""
    return PolicyDecision(
        allowed=True,
        reason="scope matches tenant 'acme'",  # No 'system/admin' in reason
        tier="act_freely",
        action="test_action",
        resource="acme",
        pillars_passed=["tenant_scope"],
        pillars_failed=[],
    )


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_health_no_auth() -> None:
    """GET /health returns 200 with no Authorization header."""
    response = client.get("/health")
    assert response.status_code == 200


def test_credentials_missing_bearer_returns_401() -> None:
    """GET /oauth/credentials without Authorization header → 401."""
    response = client.get("/oauth/credentials/acme/ghl")
    assert response.status_code == 401


def test_credentials_invalid_bearer_returns_401() -> None:
    """GET /oauth/credentials with invalid bearer token → 401."""
    with patch(
        "sos.services.integrations.app.can_execute",
        new_callable=AsyncMock,
        return_value=_denied_auth_decision(),
    ):
        response = client.get(
            "/oauth/credentials/acme/ghl",
            headers={"Authorization": "Bearer nope"},
        )
    assert response.status_code == 401


def test_credentials_wrong_tenant_returns_403() -> None:
    """GET /oauth/credentials with a token scoped to wrong tenant → 403."""
    acme_ctx = AuthContext(
        agent=None,
        project="acme",
        tenant_slug="acme",
        is_system=False,
        is_admin=False,
        label="test-acme",
    )
    # Patch verify_bearer in the gate's namespace (it's imported there at module level)
    with patch("sos.kernel.policy.gate.verify_bearer", return_value=acme_ctx):
        response = client.get(
            "/oauth/credentials/othertenant/ghl",
            headers={"Authorization": "Bearer acme-token"},
        )
    assert response.status_code == 403


def test_credentials_right_tenant_reaches_handler() -> None:
    """GET /oauth/credentials with matching tenant scope → 200 with creds."""
    acme_ctx = AuthContext(
        agent=None,
        project="acme",
        tenant_slug="acme",
        is_system=False,
        is_admin=False,
        label="test-acme",
    )
    mock_integrations = MagicMock()
    mock_integrations.get_credentials.return_value = {"access_token": "t"}

    with patch("sos.kernel.policy.gate.verify_bearer", return_value=acme_ctx), \
         patch("sos.services.integrations.oauth.TenantIntegrations") as MockCls:
        MockCls.return_value = mock_integrations
        response = client.get(
            "/oauth/credentials/acme/ghl",
            headers={"Authorization": "Bearer acme-token"},
        )

    assert response.status_code == 200
    assert response.json().get("access_token") == "t"


def test_credentials_not_found_returns_404() -> None:
    """GET /oauth/credentials when get_credentials returns None → 404."""
    acme_ctx = AuthContext(
        agent=None,
        project="acme",
        tenant_slug="acme",
        is_system=False,
        is_admin=False,
        label="test-acme",
    )
    mock_integrations = MagicMock()
    mock_integrations.get_credentials.return_value = None

    with patch("sos.kernel.policy.gate.verify_bearer", return_value=acme_ctx), \
         patch("sos.services.integrations.oauth.TenantIntegrations") as MockCls:
        MockCls.return_value = mock_integrations
        response = client.get(
            "/oauth/credentials/acme/ghl",
            headers={"Authorization": "Bearer acme-token"},
        )

    assert response.status_code == 404


def test_ghl_callback_system_token_succeeds(monkeypatch: pytest.MonkeyPatch) -> None:
    """POST /oauth/ghl/callback with system token → 200."""
    monkeypatch.setenv("SOS_SYSTEM_TOKEN", "sys-x")
    # Invalidate the auth cache so the env var is picked up
    from sos.kernel.auth import get_cache
    get_cache().invalidate()

    mock_integrations = MagicMock()
    mock_integrations.handle_ghl_callback = AsyncMock(return_value={"ok": True})

    # Mock can_execute to return a system/admin decision (reason must contain "system/admin")
    system_decision = PolicyDecision(
        allowed=True,
        reason="system/admin scope",
        tier="act_freely",
        action="oauth_ghl_callback",
        resource="acme",
        pillars_passed=["tenant_scope"],
        pillars_failed=[],
    )
    with patch(
        "sos.services.integrations.app.can_execute",
        new_callable=AsyncMock,
        return_value=system_decision,
    ), patch("sos.services.integrations.oauth.TenantIntegrations") as MockCls:
        MockCls.return_value = mock_integrations
        response = client.post(
            "/oauth/ghl/callback/acme",
            json={"code": "abc"},
            headers={"Authorization": "Bearer sys-x"},
        )

    assert response.status_code == 200
    assert response.json().get("ok") is True


def test_ghl_callback_tenant_token_forbidden() -> None:
    """POST /oauth/ghl/callback with tenant-scoped token → 403 with system or admin in detail."""
    acme_ctx = AuthContext(
        agent=None,
        project="acme",
        tenant_slug="acme",
        is_system=False,
        is_admin=False,
        label="test-acme",
    )
    # Gate uses its own imported verify_bearer; patch there so it returns a
    # valid (but tenant-scoped) context. The gate then allows the call, but
    # _raise_on_deny(require_system=True) should reject it with 403.
    with patch("sos.kernel.policy.gate.verify_bearer", return_value=acme_ctx):
        response = client.post(
            "/oauth/ghl/callback/acme",
            json={"code": "abc"},
            headers={"Authorization": "Bearer acme-token"},
        )

    assert response.status_code == 403
    assert "system or admin" in response.json().get("detail", "").lower()


def test_google_callback_bad_service_returns_400(monkeypatch: pytest.MonkeyPatch) -> None:
    """POST /oauth/google/callback with unknown service → 400 before auth check."""
    monkeypatch.setenv("SOS_SYSTEM_TOKEN", "sys-x")
    from sos.kernel.auth import get_cache
    get_cache().invalidate()

    # The service validation happens before can_execute, so no need to mock gate
    response = client.post(
        "/oauth/google/callback/acme",
        json={"code": "abc", "service": "bogus"},
        headers={"Authorization": "Bearer sys-x"},
    )

    assert response.status_code == 400
