"""v0.4.7 Phase 2 — P1-01 close (saas half).

Before: sos.mcp.sos_mcp_sse imported sos.services.saas.{rate_limiter,
marketplace, audit, notifications} in-process for hot-path gating
(R2 violation).

After: saas ships /rate-limit/check, /audit/tool-call, /marketplace/*
on its existing FastAPI app. SaasClient + AsyncSaasClient expose these
so MCP routes everything via HTTP through sos.clients.base.
"""
from __future__ import annotations

import ast
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

MCP_FILE = Path(__file__).resolve().parents[2] / "sos" / "mcp" / "sos_mcp_sse.py"
SAAS_CLIENT = Path(__file__).resolve().parents[2] / "sos" / "clients" / "saas.py"


def _imported_modules(file_path: Path) -> set[str]:
    tree = ast.parse(file_path.read_text(encoding="utf-8"))
    mods: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module:
            mods.add(node.module)
    return mods


def test_mcp_does_not_import_saas():
    leaks = [m for m in _imported_modules(MCP_FILE) if m.startswith("sos.services.saas")]
    assert leaks == [], f"MCP still reaches into saas: {leaks}"


def test_saas_client_exports_mcp_surface():
    from sos.clients.saas import AsyncSaasClient, SaasClient

    for method in (
        "check_rate_limit",
        "log_tool_call",
        "browse_marketplace",
        "subscribe_marketplace",
        "my_subscriptions",
        "create_listing",
        "my_earnings",
        "get_notification_preferences",
        "set_notification_preferences",
    ):
        assert hasattr(SaasClient, method), f"SaasClient missing {method}"
        assert hasattr(AsyncSaasClient, method), f"AsyncSaasClient missing {method}"


def test_sync_check_rate_limit_hits_endpoint():
    from sos.clients.saas import SaasClient

    client = SaasClient(base_url="http://fake:8075", token="admin")
    fake_resp = MagicMock()
    fake_resp.json.return_value = {"allowed": True, "remaining": 99}

    with patch.object(client, "_request", return_value=fake_resp) as mock_req:
        out = client.check_rate_limit("acme", plan="starter")

    assert mock_req.call_args.args == ("POST", "/rate-limit/check")
    assert mock_req.call_args.kwargs["json"] == {"tenant": "acme", "plan": "starter"}
    assert out == {"allowed": True, "remaining": 99}


def test_sync_log_tool_call_posts_entry():
    from sos.clients.saas import SaasClient

    client = SaasClient(base_url="http://fake:8075", token="admin")
    fake_resp = MagicMock()
    fake_resp.json.return_value = {"logged": True}

    with patch.object(client, "_request", return_value=fake_resp) as mock_req:
        client.log_tool_call(
            "acme", "remember", actor="tenant:acme", details={"status": "ok"}
        )

    assert mock_req.call_args.args == ("POST", "/audit/tool-call")
    body = mock_req.call_args.kwargs["json"]
    assert body["tenant"] == "acme"
    assert body["tool"] == "remember"
    assert body["actor"] == "tenant:acme"
    assert body["details"] == {"status": "ok"}


def test_sync_browse_marketplace_returns_listings():
    from sos.clients.saas import SaasClient

    client = SaasClient(base_url="http://fake:8075", token="admin")
    fake_resp = MagicMock()
    fake_resp.json.return_value = {
        "listings": [
            {"id": "lst-1", "title": "SEO", "category": "seo", "price_cents": 4900}
        ]
    }

    with patch.object(client, "_request", return_value=fake_resp) as mock_req:
        out = client.browse_marketplace(category="seo")

    assert mock_req.call_args.args == ("GET", "/marketplace/listings")
    assert mock_req.call_args.kwargs["params"]["category"] == "seo"
    assert len(out) == 1
    assert out[0]["id"] == "lst-1"


def test_sync_subscribe_marketplace_posts():
    from sos.clients.saas import SaasClient

    client = SaasClient(base_url="http://fake:8075", token="admin")
    fake_resp = MagicMock()
    fake_resp.json.return_value = {"success": True, "message": "Subscribed"}

    with patch.object(client, "_request", return_value=fake_resp) as mock_req:
        out = client.subscribe_marketplace("acme", "lst-1")

    assert mock_req.call_args.args == ("POST", "/marketplace/subscriptions")
    assert mock_req.call_args.kwargs["json"] == {
        "tenant": "acme",
        "listing_id": "lst-1",
    }
    assert out["success"] is True


def test_sync_my_earnings():
    from sos.clients.saas import SaasClient

    client = SaasClient(base_url="http://fake:8075", token="admin")
    fake_resp = MagicMock()
    fake_resp.json.return_value = {
        "listings": [],
        "total_mrr_cents": 0,
        "platform_fee_cents": 0,
        "net_earnings_cents": 0,
    }

    with patch.object(client, "_request", return_value=fake_resp) as mock_req:
        out = client.my_earnings("acme")

    assert mock_req.call_args.args == ("GET", "/marketplace/earnings")
    assert mock_req.call_args.kwargs["params"] == {"tenant": "acme"}
    assert out["total_mrr_cents"] == 0


def test_sync_notification_preferences_roundtrip():
    from sos.clients.saas import SaasClient

    client = SaasClient(base_url="http://fake:8075", token="admin")
    get_resp = MagicMock()
    get_resp.json.return_value = {"email": True, "telegram": False}
    set_resp = MagicMock()
    set_resp.json.return_value = {"ok": True, "preferences": {"email": True, "telegram": True}}

    with patch.object(client, "_request", side_effect=[get_resp, set_resp]) as mock_req:
        existing = client.get_notification_preferences("acme")
        existing["telegram"] = True
        out = client.set_notification_preferences("acme", existing)

    calls = mock_req.call_args_list
    assert calls[0].args == ("GET", "/tenants/acme/notifications")
    assert calls[1].args == ("POST", "/tenants/acme/notifications")
    assert out["ok"] is True


@pytest.mark.asyncio
async def test_async_check_rate_limit():
    from sos.clients.saas import AsyncSaasClient

    client = AsyncSaasClient(base_url="http://fake:8075", token="admin")
    fake_resp = MagicMock()
    fake_resp.json.return_value = {"allowed": False, "remaining": 0}

    with patch.object(client, "_request", AsyncMock(return_value=fake_resp)) as mock_req:
        out = await client.check_rate_limit("acme", plan="starter")

    assert mock_req.call_args.args == ("POST", "/rate-limit/check")
    assert out == {"allowed": False, "remaining": 0}


@pytest.mark.asyncio
async def test_async_log_tool_call_fire_and_forget():
    from sos.clients.saas import AsyncSaasClient

    client = AsyncSaasClient(base_url="http://fake:8075", token="admin")
    fake_resp = MagicMock()
    fake_resp.json.return_value = {"logged": True}

    with patch.object(client, "_request", AsyncMock(return_value=fake_resp)) as mock_req:
        await client.log_tool_call("acme", "remember")

    assert mock_req.call_args.args == ("POST", "/audit/tool-call")
