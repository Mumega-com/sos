from __future__ import annotations

import sys
import types
from typing import Any

import pytest

from sos.bus import envelope as bus_envelope

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
        self.xrevrange_calls: list[tuple[str, int]] = []

    async def xrevrange(self, stream: str, count: int = 10):
        self.xrevrange_calls.append((stream, count))
        if stream == "sos:stream:project:acme:agent:alice":
            envelope = bus_envelope.build(
                msg_type="chat",
                source="agent:bob",
                target="agent:alice",
                text="tenant-scoped message",
                project="acme",
                message_id="1-0",
            )
            return [
                (
                    "1-0",
                    envelope,
                )
            ]
        return []

    async def scan(self, cursor: int, match: str | None = None, count: int = 100):
        return 0, []

    async def xadd(self, *args: Any, **kwargs: Any) -> str:
        return "1-0"

    async def publish(self, *args: Any, **kwargs: Any) -> int:
        return 1


class _Response:
    def __init__(self, payload: dict[str, Any]):
        self._payload = payload

    def json(self) -> dict[str, Any]:
        return self._payload


def _auth() -> MCPAuthContext:
    return MCPAuthContext(
        token="test" * 16,
        tenant_id="acme",
        is_system=False,
        source="test",
        agent_name="alice",
    )


async def test_task_list_uses_tenant_scope_without_forcing_agent_filter(monkeypatch):
    from sos.mcp import sos_mcp_sse as module

    redis = _RedisStub()
    captured_urls: list[str] = []

    monkeypatch.setattr(module, "_get_redis", lambda: redis)

    async def _noop_publish_log(*args: Any, **kwargs: Any) -> None:
        return None

    monkeypatch.setattr(module, "_publish_log", _noop_publish_log)

    def _fake_get(url: str, **kwargs: Any):
        captured_urls.append(url)
        return _Response(
            {
                "tasks": [
                    {
                        "title": "acme task",
                        "status": "queued",
                        "project": "acme",
                        "assignee": "alice",
                        "agent": "alice",
                    },
                    {
                        "title": "other tenant task",
                        "status": "queued",
                        "project": "other",
                        "assignee": "alice",
                        "agent": "alice",
                    },
                ]
            }
        )

    monkeypatch.setattr(module.requests, "get", _fake_get)

    result = await handle_tool("task_list", {"limit": 20, "status": "queued"}, _auth())

    assert captured_urls
    url = captured_urls[0]
    assert "project=acme" in url
    assert "agent=" not in url

    text = result["content"][0]["text"]
    assert "acme task" in text
    assert "other tenant task" not in text


async def test_task_list_passes_assignee_and_enforces_limit(monkeypatch):
    from sos.mcp import sos_mcp_sse as module

    redis = _RedisStub()
    captured_urls: list[str] = []

    monkeypatch.setattr(module, "_get_redis", lambda: redis)

    async def _noop_publish_log(*args: Any, **kwargs: Any) -> None:
        return None

    monkeypatch.setattr(module, "_publish_log", _noop_publish_log)

    def _fake_get(url: str, **kwargs: Any):
        captured_urls.append(url)
        return _Response(
            {
                "tasks": [
                    {"title": "alice 1", "status": "queued", "project": "acme", "assignee": "alice"},
                    {"title": "bob", "status": "queued", "project": "acme", "assignee": "bob"},
                    {"title": "alice 2", "status": "queued", "project": "acme", "assignee": "alice"},
                ]
            }
        )

    monkeypatch.setattr(module.requests, "get", _fake_get)

    result = await handle_tool(
        "task_list",
        {"limit": 1, "status": "queued", "assignee": "alice"},
        _auth(),
    )

    assert captured_urls
    url = captured_urls[0]
    assert "limit=1" in url
    assert "status=queued" in url
    assert "assignee=alice" in url

    text = result["content"][0]["text"]
    assert "alice 1" in text
    assert "bob" not in text
    assert "alice 2" not in text


async def test_inbox_reads_only_the_tenant_scoped_stream(monkeypatch):
    from sos.mcp import sos_mcp_sse as module

    redis = _RedisStub()
    monkeypatch.setattr(module, "_get_redis", lambda: redis)

    async def _noop_publish_log(*args: Any, **kwargs: Any) -> None:
        return None

    monkeypatch.setattr(module, "_publish_log", _noop_publish_log)

    result = await handle_tool("inbox", {"agent": "alice", "limit": 5}, _auth())

    assert ("sos:stream:project:acme:agent:alice", 5) in redis.xrevrange_calls
    assert "tenant-scoped message" in result["content"][0]["text"]
