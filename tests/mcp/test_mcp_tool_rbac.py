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

from sos.bus.token_store import hash_token
from sos.mcp.sos_mcp_sse import MCPAuthContext


pytestmark = pytest.mark.asyncio


def _auth(permissions: list[str] | None) -> MCPAuthContext:
    return MCPAuthContext(
        token="sk-test",
        tenant_id="sos",
        is_system=False,
        source="bus_tokens",
        agent_name="gemini-enterprise-sos",
        scope="agent",
        permissions=permissions,
    )


async def test_tools_call_denies_non_customer_bus_token_without_permission(monkeypatch):
    from sos.mcp import sos_mcp_sse as module

    called = False

    async def _fake_handle_tool(*args: Any, **kwargs: Any) -> dict[str, Any]:
        nonlocal called
        called = True
        return {"content": [{"type": "text", "text": "should not run"}]}

    monkeypatch.setattr(module, "handle_tool", _fake_handle_tool)
    monkeypatch.setattr(module, "_audit_tool_call", lambda *args, **kwargs: None)

    response = await module._process_jsonrpc(
        {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "tools/call",
            "params": {"name": "task_create", "arguments": {"title": "blocked"}},
        },
        session_id=None,
        auth=_auth(["send", "inbox", "peers", "health"]),
    )

    assert called is False
    assert response is not None
    assert response["error"]["message"] == "Tool not available: task_create"


async def test_tools_call_allows_exact_permission_before_handler(monkeypatch):
    from sos.mcp import sos_mcp_sse as module

    captured: dict[str, Any] = {}

    async def _fake_handle_tool(name: str, args: dict[str, Any], auth: MCPAuthContext, **kwargs: Any):
        captured["name"] = name
        captured["args"] = args
        return {"content": [{"type": "text", "text": "sent"}]}

    monkeypatch.setattr(module, "handle_tool", _fake_handle_tool)
    monkeypatch.setattr(module, "_append_audit", lambda *args, **kwargs: None)
    monkeypatch.setattr(module, "_audit_tool_call", lambda *args, **kwargs: None)

    response = await module._process_jsonrpc(
        {
            "jsonrpc": "2.0",
            "id": 2,
            "method": "tools/call",
            "params": {"name": "send", "arguments": {"to": "loom", "text": "hi"}},
        },
        session_id=None,
        auth=_auth(["send"]),
    )

    assert response is not None
    assert "error" not in response
    assert captured == {"name": "send", "args": {"to": "loom", "text": "hi"}}


async def test_tools_call_allows_permission_alias(monkeypatch):
    from sos.mcp import sos_mcp_sse as module

    async def _fake_handle_tool(name: str, args: dict[str, Any], auth: MCPAuthContext, **kwargs: Any):
        return {"content": [{"type": "text", "text": name}]}

    monkeypatch.setattr(module, "handle_tool", _fake_handle_tool)
    monkeypatch.setattr(module, "_append_audit", lambda *args, **kwargs: None)
    monkeypatch.setattr(module, "_audit_tool_call", lambda *args, **kwargs: None)

    response = await module._process_jsonrpc(
        {
            "jsonrpc": "2.0",
            "id": 3,
            "method": "tools/call",
            "params": {"name": "status", "arguments": {}},
        },
        session_id=None,
        auth=_auth(["health"]),
    )

    assert response is not None
    assert response["result"]["content"][0]["text"] == "status"


async def test_tools_list_filters_non_customer_bus_token_by_permissions():
    from sos.mcp import sos_mcp_sse as module

    response = await module._process_jsonrpc(
        {"jsonrpc": "2.0", "id": 4, "method": "tools/list", "params": {}},
        session_id=None,
        auth=_auth(["send", "inbox", "health"]),
    )

    names = {tool["name"] for tool in response["result"]["tools"]}
    assert {"send", "inbox", "boot_context", "status", "flow_health"} <= names
    assert "task_create" not in names
    assert "as_agent" not in names


async def test_token_cache_loads_permissions_field(monkeypatch, tmp_path):
    from sos.mcp import sos_mcp_sse as module

    raw_token = "sk-test-rbac"
    token_hash = hash_token(raw_token)
    tokens_path = tmp_path / "tokens.json"
    tokens_path.write_text(
        json.dumps(
            [
                {
                    "active": True,
                    "token_hash": token_hash,
                    "project": "sos",
                    "agent": "gemini-enterprise-sos",
                    "scope": "agent",
                    "permissions": ["send", "inbox", "peers", "health"],
                }
            ]
        )
    )

    monkeypatch.setattr(module, "BUS_TOKENS_PATH", tokens_path)
    cache = module._TokenCacheWithHotReload()

    auth = cache.get()[token_hash]
    assert auth.permissions == ["send", "inbox", "peers", "health"]
