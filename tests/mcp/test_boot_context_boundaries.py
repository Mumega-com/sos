from __future__ import annotations

import json
import sys
import types
from typing import Any

import pytest

_mirror_db_stub = types.ModuleType("mirror.kernel.db")
_mirror_db_stub.get_db = lambda: None
_mirror_embeddings_stub = types.ModuleType("mirror.kernel.embeddings")
_mirror_embeddings_stub.get_embedding = lambda text: []
sys.modules.setdefault("mirror.kernel.db", _mirror_db_stub)
sys.modules.setdefault("mirror.kernel.embeddings", _mirror_embeddings_stub)

from sos.mcp.sos_mcp_sse import MCPAuthContext, handle_tool


pytestmark = pytest.mark.asyncio


class _RedisStub:
    async def scan(self, cursor: int, match: str | None = None, count: int = 100):
        if cursor != 0:
            return 0, []
        if match == "sos:stream:project:acme:agent:*":
            return 0, ["sos:stream:project:acme:agent:alice", "sos:stream:project:acme:agent:bob"]
        if match == "sos:registry:*":
            return 0, ["sos:registry:charlie"]
        return 0, []

    async def hgetall(self, key: str) -> dict[str, str]:
        if key == "sos:registry:charlie":
            return {"project": "acme"}
        return {}


def _payload(result: dict[str, Any]) -> dict[str, Any]:
    return json.loads(result["content"][0]["text"])


async def test_boot_context_returns_permissions_and_peers(monkeypatch):
    from sos.mcp import sos_mcp_sse as module

    monkeypatch.setattr(module, "_get_redis", lambda: _RedisStub())
    monkeypatch.setattr(module, "_tenant_is_active_mcp", lambda tenant_slug: True)

    auth = MCPAuthContext(
        token="sk-test",
        tenant_id="acme",
        is_system=False,
        source="bus_tokens",
        agent_name="alice",
        scope="agent",
        permissions=["send", "inbox", "workspace:*"],
    )

    result = await handle_tool("boot_context", {"project": "acme"}, auth)
    body = _payload(result)

    assert body["identity"]["project"] == "acme"
    assert body["identity"]["tenant_id"] == "acme"
    assert body["identity"]["permissions"] == ["send", "inbox", "workspace:*"]
    assert body["peers"]["project"] == "acme"
    assert body["peers"]["agents"] == ["alice", "bob", "charlie"]


async def test_boot_context_cross_project_request_returns_scoped_error(monkeypatch):
    from sos.mcp import sos_mcp_sse as module

    monkeypatch.setattr(module, "_get_redis", lambda: _RedisStub())

    auth = MCPAuthContext(
        token="sk-test",
        tenant_id="acme",
        is_system=False,
        source="bus_tokens",
        agent_name="alice",
        scope="agent",
        permissions=["boot_context"],
    )

    result = await handle_tool("boot_context", {"project": "other"}, auth)
    body = _payload(result)

    assert body["error"] == "project_scope_denied"
    assert body["requested_project"] == "other"
    assert body["allowed_project"] == "acme"
    assert "peers" not in body
    assert "memory" not in body
