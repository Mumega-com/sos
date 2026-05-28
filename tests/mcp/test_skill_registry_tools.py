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
    def __init__(self) -> None:
        self.sets: dict[str, set[str]] = {}
        self.hashes: dict[str, dict[str, str]] = {}
        self.streams: dict[str, list[dict[str, Any]]] = {}
        self.published: list[tuple[str, str]] = []
        self.counters: dict[str, int] = {}

    async def incr(self, key: str) -> int:
        self.counters[key] = self.counters.get(key, 0) + 1
        return self.counters[key]

    async def expire(self, key: str, ttl: int) -> bool:
        return True

    async def hset(self, key: str, mapping: dict[str, Any]) -> int:
        self.hashes[key] = {name: str(value) for name, value in mapping.items()}
        return len(mapping)

    async def hgetall(self, key: str) -> dict[str, str]:
        return dict(self.hashes.get(key, {}))

    async def sadd(self, key: str, member: str) -> int:
        before = len(self.sets.setdefault(key, set()))
        self.sets[key].add(member)
        return len(self.sets[key]) - before

    async def smembers(self, key: str) -> set[str]:
        return set(self.sets.get(key, set()))

    async def xadd(self, stream: str, fields: dict[str, Any]) -> str:
        self.streams.setdefault(stream, []).append(fields)
        return f"{len(self.streams[stream])}-0"

    async def publish(self, channel: str, message: str) -> int:
        self.published.append((channel, message))
        return 1


def _auth(agent: str = "calliope-acme", permissions: list[str] | None = None) -> MCPAuthContext:
    return MCPAuthContext(
        token="sk-test",
        tenant_id="acme",
        is_system=False,
        source="bus_tokens",
        agent_name=agent,
        scope="tenant-agent",
        permissions=permissions or ["skills:*"],
    )


def _customer_auth(permissions: list[str] | None = None) -> MCPAuthContext:
    return MCPAuthContext(
        token="sk-customer",
        tenant_id="acme",
        is_system=False,
        source="bus_tokens",
        agent_name="customer-acme",
        scope="customer",
        permissions=permissions or ["skills:*"],
    )


async def test_register_and_list_skill(monkeypatch):
    from sos.mcp import sos_mcp_sse as module

    redis = _RedisStub()
    monkeypatch.setattr(module, "_get_redis", lambda: redis)

    async def _noop_publish_log(*args: Any, **kwargs: Any) -> None:
        return None

    monkeypatch.setattr(module, "_publish_log", _noop_publish_log)

    registered = await handle_tool(
        "register_skill",
        {"name": "blog-draft", "description": "Draft a blog post"},
        _auth(),
    )
    assert registered["structuredContent"]["skill"]["owner"] == "calliope-acme"

    listed = await handle_tool("list_skills", {}, _auth("athena-acme"))
    skills = listed["structuredContent"]["skills"]
    by_name = {skill["name"]: skill for skill in skills}
    assert listed["structuredContent"]["count"] == 2
    assert by_name["blog-draft"]["description"] == "Draft a blog post"
    assert by_name["inkwell_publish"]["scope"] == "tenant-self"
    assert by_name["inkwell_publish"]["owner_tenant"] is None
    assert "handler" not in by_name["inkwell_publish"]

    by_peer = await handle_tool("list_skills", {"peer": "calliope-acme"}, _auth("athena-acme"))
    assert by_peer["structuredContent"]["count"] == 1


async def test_register_rejects_builtin_skill_shadow(monkeypatch):
    from sos.mcp import sos_mcp_sse as module

    redis = _RedisStub()
    monkeypatch.setattr(module, "_get_redis", lambda: redis)

    registered = await handle_tool(
        "register_skill",
        {"name": "inkwell_publish", "description": "shadow builtin"},
        _auth(),
    )

    assert registered["structuredContent"] == {
        "ok": False,
        "error": "reserved_skill_name",
        "name": "inkwell_publish",
    }

    listed = await handle_tool("list_skills", {}, _auth("athena-acme"))
    skills = listed["structuredContent"]["skills"]
    assert [skill["name"] for skill in skills] == ["inkwell_publish"]


async def test_invoke_skill_routes_structured_request_to_owner(monkeypatch):
    from sos.mcp import sos_mcp_sse as module

    redis = _RedisStub()
    monkeypatch.setattr(module, "_get_redis", lambda: redis)

    async def _noop_publish_log(*args: Any, **kwargs: Any) -> None:
        return None

    monkeypatch.setattr(module, "_publish_log", _noop_publish_log)

    await handle_tool(
        "register_skill",
        {"name": "blog-draft", "description": "Draft a blog post", "handler": "agent:calliope-acme"},
        _auth("calliope-acme"),
    )

    invoked = await handle_tool(
        "invoke_skill",
        {"name": "blog-draft", "input": {"topic": "Mumega"}},
        _auth("athena-acme"),
    )

    assert invoked["structuredContent"]["ok"] is True
    assert invoked["structuredContent"]["owner"] == "calliope-acme"
    stream = redis.streams["sos:stream:project:acme:agent:calliope-acme"]
    message = stream[0]
    assert message["type"] == "skill_invoke"
    assert message["project"] == "acme"
    assert "Mumega" in json.dumps(message)
    assert any(channel == "sos:wake:calliope-acme" for channel, _ in redis.published)


async def test_skill_tools_visible_with_skill_permission():
    from sos.mcp import sos_mcp_sse as module

    response = await module._process_jsonrpc(
        {"jsonrpc": "2.0", "id": 1, "method": "tools/list", "params": {}},
        session_id=None,
        auth=_auth(permissions=["skills:read"]),
    )

    names = {tool["name"] for tool in response["result"]["tools"]}
    assert "list_skills" in names
    assert "register_skill" not in names
    assert "invoke_skill" not in names


async def test_invoke_inkwell_publish_uses_auth_tenant_and_forwards_token(monkeypatch):
    from sos.mcp import sos_mcp_sse as module

    calls: list[dict[str, Any]] = []

    async def _fake_post(tenant_slug: str, token: str, payload: dict[str, str]) -> tuple[int, dict[str, Any]]:
        calls.append({"tenant_slug": tenant_slug, "token": token, "payload": payload})
        return 409, {"error": "approval_required", "approval_id": "appr_123"}

    monkeypatch.setattr(module, "_post_inkwell_publish", _fake_post)
    monkeypatch.setattr(module, "_schedule_audit_event", lambda event: None)

    invoked = await handle_tool(
        "invoke_skill",
        {
            "name": "inkwell_publish",
            "input": {
                "title": "S164 smoke",
                "slug": "s164-smoke",
                "content_md": "test",
                "type": "topic",
                "visibility": "draft",
            },
        },
        _auth(agent="hermes-aionboard", permissions=["skills:invoke"]),
    )

    assert calls == [
        {
            "tenant_slug": "acme",
            "token": "sk-test",
            "payload": {
                "title": "S164 smoke",
                "slug": "s164-smoke",
                "content_md": "test",
                "type": "topic",
                "visibility": "draft",
            },
        }
    ]
    assert invoked["structuredContent"]["error"] == "approval_required"
    assert invoked["structuredContent"]["approval_id"] == "appr_123"
    assert invoked["structuredContent"]["http_status"] == 409


async def test_invoke_inkwell_publish_rejects_customer_token(monkeypatch):
    from sos.mcp import sos_mcp_sse as module

    async def _unexpected_post(*args: Any, **kwargs: Any) -> tuple[int, dict[str, Any]]:
        raise AssertionError("customer token must fail before substrate POST")

    monkeypatch.setattr(module, "_post_inkwell_publish", _unexpected_post)

    invoked = await handle_tool(
        "invoke_skill",
        {
            "name": "inkwell_publish",
            "input": {
                "title": "S164 smoke",
                "slug": "s164-smoke",
                "content_md": "test",
                "type": "topic",
                "visibility": "draft",
            },
        },
        _customer_auth(permissions=["skills:invoke"]),
    )

    assert invoked["structuredContent"]["ok"] is False
    assert invoked["structuredContent"]["error"] == "tenant_scope_required"


async def test_invoke_inkwell_publish_rejects_cross_tenant_override(monkeypatch):
    from sos.mcp import sos_mcp_sse as module

    async def _unexpected_post(*args: Any, **kwargs: Any) -> tuple[int, dict[str, Any]]:
        raise AssertionError("cross-tenant override must fail before substrate POST")

    monkeypatch.setattr(module, "_post_inkwell_publish", _unexpected_post)

    invoked = await handle_tool(
        "invoke_skill",
        {
            "name": "inkwell_publish",
            "input": {
                "tenant_slug": "other-tenant",
                "title": "S164 smoke",
                "slug": "s164-smoke",
                "content_md": "test",
                "type": "topic",
                "visibility": "draft",
            },
        },
        _auth(agent="hermes-aionboard", permissions=["skills:invoke"]),
    )

    assert invoked["structuredContent"]["ok"] is False
    assert invoked["structuredContent"]["error"] == "tenant_override_forbidden"
    assert invoked["structuredContent"]["tenant_slug"] == "acme"


async def test_invoke_inkwell_publish_rejects_same_tenant_override(monkeypatch):
    from sos.mcp import sos_mcp_sse as module

    async def _unexpected_post(*args: Any, **kwargs: Any) -> tuple[int, dict[str, Any]]:
        raise AssertionError("tenant override must fail before substrate POST")

    monkeypatch.setattr(module, "_post_inkwell_publish", _unexpected_post)

    invoked = await handle_tool(
        "invoke_skill",
        {
            "name": "inkwell_publish",
            "input": {
                "tenant_slug": "acme",
                "title": "S164 smoke",
                "slug": "s164-smoke",
                "content_md": "test",
                "type": "topic",
                "visibility": "draft",
            },
        },
        _auth(agent="hermes-aionboard", permissions=["skills:invoke"]),
    )

    assert invoked["structuredContent"]["ok"] is False
    assert invoked["structuredContent"]["error"] == "tenant_override_forbidden"
    assert invoked["structuredContent"]["tenant_slug"] == "acme"


async def test_invoke_inkwell_publish_rejects_invalid_payload(monkeypatch):
    from sos.mcp import sos_mcp_sse as module

    async def _unexpected_post(*args: Any, **kwargs: Any) -> tuple[int, dict[str, Any]]:
        raise AssertionError("invalid payload must fail before substrate POST")

    monkeypatch.setattr(module, "_post_inkwell_publish", _unexpected_post)

    invoked = await handle_tool(
        "invoke_skill",
        {
            "name": "inkwell_publish",
            "input": {
                "title": "S164 smoke",
                "slug": "../bad",
                "content_md": "test",
                "type": "topic",
                "visibility": "draft",
            },
        },
        _auth(agent="hermes-aionboard", permissions=["skills:invoke"]),
    )

    assert invoked["structuredContent"]["ok"] is False
    assert invoked["structuredContent"]["error"] == "invalid_input"
