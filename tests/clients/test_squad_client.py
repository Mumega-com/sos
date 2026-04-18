"""v0.4.7 Phase 1 — P1-01 close (squad half).

Before: sos.mcp.sos_mcp_sse imported sos.services.squad.{auth,service}
in-process for SYSTEM_TOKEN, _lookup_token, SquadDB, create_api_key
(R2 violation).

After: squad ships /auth/verify and POST /api-keys on its existing
FastAPI app (sos/services/squad/app.py). sos.clients.squad provides a
SquadClient + AsyncSquadClient that MCP uses via BaseHTTPClient.
"""
from __future__ import annotations

import ast
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

CLIENTS_DIR = Path(__file__).resolve().parents[2] / "sos" / "clients"
SQUAD_CLIENT = CLIENTS_DIR / "squad.py"


def _imported_modules(file_path: Path) -> set[str]:
    tree = ast.parse(file_path.read_text(encoding="utf-8"))
    mods: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module:
            mods.add(node.module)
            for n in node.names:
                mods.add(f"{node.module}.{n.name}")
    return mods


def test_squad_client_does_not_import_service():
    mods = _imported_modules(SQUAD_CLIENT)
    leaks = [m for m in mods if m.startswith("sos.services.squad")]
    assert leaks == [], f"clients/squad.py still reaches into service: {leaks}"


def test_squad_client_uses_base_http_client():
    src = SQUAD_CLIENT.read_text(encoding="utf-8")
    assert "from sos.clients.base import" in src
    assert "BaseHTTPClient" in src
    assert "AsyncBaseHTTPClient" in src


def test_token_resolves_from_env(monkeypatch):
    from sos.clients.squad import _resolve_token

    monkeypatch.delenv("SOS_SQUAD_TOKEN", raising=False)
    monkeypatch.setenv("SOS_SYSTEM_TOKEN", "sys-abc")
    assert _resolve_token(None) == "sys-abc"
    assert _resolve_token("explicit") == "explicit"


def test_sync_verify_token_hits_endpoint():
    from sos.clients.squad import SquadClient

    client = SquadClient(base_url="http://fake:6006", token="system")
    fake_resp = MagicMock()
    fake_resp.json.return_value = {"ok": True, "tenant_id": "acme", "is_system": False}

    with patch.object(client, "_request", return_value=fake_resp) as mock_req:
        out = client.verify_token("sk-squad-acme-abc")

    assert mock_req.call_args.args == ("POST", "/auth/verify")
    assert mock_req.call_args.kwargs["json"] == {"token": "sk-squad-acme-abc"}
    assert out == {"ok": True, "tenant_id": "acme", "is_system": False}


def test_sync_verify_token_returns_none_on_miss():
    from sos.clients.squad import SquadClient

    client = SquadClient(base_url="http://fake:6006", token="system")
    fake_resp = MagicMock()
    fake_resp.json.return_value = {"ok": False}

    with patch.object(client, "_request", return_value=fake_resp):
        out = client.verify_token("bad-token")

    assert out is None


def test_sync_create_api_key_posts_with_role():
    from sos.clients.squad import SquadClient

    client = SquadClient(base_url="http://fake:6006", token="system")
    fake_resp = MagicMock()
    fake_resp.json.return_value = {
        "token": "sk-squad-acme-deadbeef",
        "tenant_id": "acme",
        "created_at": "2026-04-18T00:00:00Z",
    }

    with patch.object(client, "_request", return_value=fake_resp) as mock_req:
        out = client.create_api_key("acme", role="user")

    assert mock_req.call_args.args == ("POST", "/api-keys")
    assert mock_req.call_args.kwargs["json"] == {"tenant_id": "acme", "identity_type": "user"}
    assert out["token"].startswith("sk-squad-acme-")


@pytest.mark.asyncio
async def test_async_verify_token_hits_endpoint():
    from sos.clients.squad import AsyncSquadClient

    client = AsyncSquadClient(base_url="http://fake:6006", token="system")
    fake_resp = MagicMock()
    fake_resp.json.return_value = {"ok": True, "tenant_id": "acme", "is_system": False}

    with patch.object(client, "_request", AsyncMock(return_value=fake_resp)) as mock_req:
        out = await client.verify_token("sk-squad-acme-abc")

    assert mock_req.call_args.args == ("POST", "/auth/verify")
    assert out == {"ok": True, "tenant_id": "acme", "is_system": False}
