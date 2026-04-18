"""v0.4.5 Wave 9 — billing → saas HTTP decoupling (P0-01).

billing.webhook previously imported TenantRegistry from
sos.services.saas.registry and mutated tenant state in-process.
Wave 9 replaces the direct import with AsyncSaasClient over HTTP.
"""
from __future__ import annotations

import ast
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

BILLING_DIR = Path(__file__).resolve().parents[2] / "sos" / "services" / "billing"


def _imported_modules(root: Path) -> set[str]:
    mods: set[str] = set()
    for py in root.rglob("*.py"):
        try:
            tree = ast.parse(py.read_text(encoding="utf-8"))
        except SyntaxError:
            continue
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and node.module:
                mods.add(node.module)
                for n in node.names:
                    mods.add(f"{node.module}.{n.name}")
    return mods


def test_billing_does_not_import_service_saas():
    mods = _imported_modules(BILLING_DIR)
    leaks = [m for m in mods if m.startswith("sos.services.saas")]
    assert leaks == [], f"billing still imports sos.services.saas: {leaks}"


def test_billing_does_not_import_service_economy():
    mods = _imported_modules(BILLING_DIR)
    leaks = [m for m in mods if m.startswith("sos.services.economy")]
    assert leaks == [], f"billing still imports sos.services.economy: {leaks}"


def test_billing_uses_saas_client():
    webhook_src = (BILLING_DIR / "webhook.py").read_text(encoding="utf-8")
    assert "from sos.clients.saas import AsyncSaasClient" in webhook_src
    assert "TenantRegistry" not in webhook_src


def test_saas_client_create_tenant_posts_correct_payload():
    """SaasClient.create_tenant POSTs /tenants with the given JSON body."""
    from sos.clients.saas import SaasClient

    client = SaasClient(base_url="http://fake-saas:8075", token="admin-key")
    fake_response = MagicMock()
    fake_response.json.return_value = {"slug": "acme", "status": "provisioning"}
    with patch.object(client, "_request", return_value=fake_response) as mock_req:
        out = client.create_tenant({"slug": "acme", "label": "Acme", "email": "a@b.co"})

    mock_req.assert_called_once()
    assert mock_req.call_args.args[0] == "POST"
    assert mock_req.call_args.args[1] == "/tenants"
    assert mock_req.call_args.kwargs["json"]["slug"] == "acme"
    assert out["slug"] == "acme"


def test_saas_client_activate_posts_bus_token():
    from sos.clients.saas import SaasClient

    client = SaasClient(base_url="http://fake-saas:8075", token="admin")
    fake_response = MagicMock()
    fake_response.json.return_value = {"slug": "acme", "status": "active"}
    with patch.object(client, "_request", return_value=fake_response) as mock_req:
        client.activate_tenant("acme", squad_id="acme", bus_token="tok123")

    assert mock_req.call_args.args == ("POST", "/tenants/acme/activate")
    assert mock_req.call_args.kwargs["json"] == {"squad_id": "acme", "bus_token": "tok123"}


def test_saas_client_cancel_delegates_to_update():
    from sos.clients.saas import SaasClient

    client = SaasClient(base_url="http://fake-saas:8075", token="admin")
    fake_response = MagicMock()
    fake_response.json.return_value = {"slug": "acme", "status": "cancelled"}
    with patch.object(client, "_request", return_value=fake_response) as mock_req:
        client.cancel_tenant("acme")

    assert mock_req.call_args.args == ("PUT", "/tenants/acme")
    assert mock_req.call_args.kwargs["json"] == {"status": "cancelled"}


def test_saas_client_sets_admin_bearer_from_env(monkeypatch):
    from sos.clients.saas import SaasClient

    monkeypatch.setenv("SOS_SAAS_ADMIN_KEY", "env-admin-key")
    monkeypatch.delenv("MUMEGA_MASTER_KEY", raising=False)
    client = SaasClient(base_url="http://fake-saas:8075")
    auth = client._client.headers.get("Authorization")
    assert auth == "Bearer env-admin-key"


@pytest.mark.asyncio
async def test_webhook_handle_checkout_calls_saas_client(monkeypatch):
    """handle_checkout_completed routes tenant create+activate through the client."""
    from sos.services.billing import webhook as wh

    async def _fake_provision(slug, label, email):
        return {"status": "provisioned", "bus_token": "tok-abc"}

    monkeypatch.setattr(wh, "provision_tenant", _fake_provision)

    mock_client = MagicMock()
    mock_client.create_tenant = AsyncMock(return_value={"slug": "new-tenant"})
    mock_client.activate_tenant = AsyncMock(return_value={"status": "active"})

    with patch.object(wh, "_saas_client", return_value=mock_client):
        session = {
            "customer_email": "new@example.com",
            "customer_details": {"name": "New Co"},
            "metadata": {"slug": "new-tenant", "plan": "seo"},
            "amount_total": 0,
            "id": "cs_test",
        }
        result = await wh.handle_checkout_completed(session)

    mock_client.create_tenant.assert_awaited_once()
    mock_client.activate_tenant.assert_awaited_once_with(
        "new-tenant", squad_id="new-tenant", bus_token="tok-abc"
    )
    assert result.get("status") == "provisioned"
