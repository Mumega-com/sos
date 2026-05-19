from __future__ import annotations

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
        self.counters: dict[str, int] = {}

    async def incr(self, key: str) -> int:
        self.counters[key] = self.counters.get(key, 0) + 1
        return self.counters[key]

    async def expire(self, key: str, ttl: int) -> bool:
        return True

    async def sadd(self, key: str, member: str) -> int:
        before = len(self.sets.setdefault(key, set()))
        self.sets[key].add(member)
        return len(self.sets[key]) - before

    async def srem(self, key: str, member: str) -> int:
        existed = member in self.sets.setdefault(key, set())
        self.sets[key].discard(member)
        return int(existed)

    async def smembers(self, key: str) -> set[str]:
        return set(self.sets.get(key, set()))

    async def hset(self, key: str, mapping: dict[str, str]) -> int:
        self.hashes[key] = dict(mapping)
        return len(mapping)

    async def hgetall(self, key: str) -> dict[str, str]:
        return dict(self.hashes.get(key, {}))

    async def delete(self, key: str) -> int:
        existed = key in self.hashes
        self.hashes.pop(key, None)
        return int(existed)


def _auth() -> MCPAuthContext:
    return MCPAuthContext(
        token="sk-test",
        tenant_id="acme",
        is_system=False,
        source="bus_tokens",
        agent_name="alice",
        scope="agent",
        permissions=["workspace:*"],
    )


async def test_workspace_join_members_leave_are_project_scoped(monkeypatch):
    from sos.mcp import sos_mcp_sse as module

    redis = _RedisStub()
    monkeypatch.setattr(module, "_get_redis", lambda: redis)
    monkeypatch.setattr(module, "_publish_log", lambda *args, **kwargs: None)

    async def _noop_publish_log(*args: Any, **kwargs: Any) -> None:
        return None

    monkeypatch.setattr(module, "_publish_log", _noop_publish_log)

    join = await handle_tool(
        "workspace_join",
        {"workspace_id": "Alpha Room", "summary": "planning"},
        _auth(),
        session_id="session-1",
    )
    assert join["structuredContent"]["workspace_id"] == "Alpha-Room"
    assert join["structuredContent"]["project"] == "acme"
    assert join["structuredContent"]["agent"] == "alice"

    members_key = "sos:workspace:acme:Alpha-Room:members"
    member_key = "sos:workspace:acme:Alpha-Room:member:alice"
    assert redis.sets[members_key] == {"alice"}
    assert redis.hashes[member_key]["session_id"] == "session-1"
    assert redis.hashes[member_key]["summary"] == "planning"

    members = await handle_tool("workspace_members", {"workspace_id": "Alpha Room"}, _auth())
    assert members["structuredContent"]["count"] == 1
    assert members["structuredContent"]["members"][0]["agent"] == "alice"
    assert members["structuredContent"]["members"][0]["project"] == "acme"

    left = await handle_tool("workspace_leave", {"workspace_id": "Alpha Room"}, _auth())
    assert left["structuredContent"]["action"] == "left"
    assert redis.sets[members_key] == set()
    assert member_key not in redis.hashes


async def test_workspace_tools_are_visible_with_workspace_permission():
    from sos.mcp import sos_mcp_sse as module

    response = await module._process_jsonrpc(
        {"jsonrpc": "2.0", "id": 1, "method": "tools/list", "params": {}},
        session_id=None,
        auth=_auth(),
    )

    names = {tool["name"] for tool in response["result"]["tools"]}
    assert {"workspace_join", "workspace_leave", "workspace_members"} <= names
    assert "send" not in names
