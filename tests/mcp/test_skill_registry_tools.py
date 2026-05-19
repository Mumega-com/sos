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
    assert listed["structuredContent"]["count"] == 1
    assert skills[0]["name"] == "blog-draft"
    assert skills[0]["description"] == "Draft a blog post"

    by_peer = await handle_tool("list_skills", {"peer": "calliope-acme"}, _auth("athena-acme"))
    assert by_peer["structuredContent"]["count"] == 1


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
