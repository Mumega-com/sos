from __future__ import annotations

import sys
import types
import json
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
        self.xrange_calls: list[tuple[str, str, int]] = []
        self.duplicate_direct_and_broadcast = False

    async def xrange(self, stream: str, min: str = "-", max: str = "+", count: int = 10):
        self.xrange_calls.append((stream, min, count))
        if stream == "sos:stream:project:acme:agent:alice":
            message_id = "same-id" if self.duplicate_direct_and_broadcast else "direct-1"
            text = "duplicate once" if self.duplicate_direct_and_broadcast else "tenant-scoped message"
            envelope = bus_envelope.build(
                msg_type="chat",
                source="agent:bob",
                target="agent:alice",
                text=text,
                project="acme",
                message_id=message_id,
            )
            return [
                (
                    "1-0",
                    envelope,
                )
            ]
        if stream == "sos:stream:project:acme:broadcast":
            message_id = "same-id" if self.duplicate_direct_and_broadcast else "broadcast-1"
            text = "duplicate once" if self.duplicate_direct_and_broadcast else "tenant broadcast"
            envelope = bus_envelope.build(
                msg_type="send",
                source="agent:bob" if self.duplicate_direct_and_broadcast else "agent:loom",
                target="sos:channel:project:acme:global",
                text=text,
                project="acme",
                message_id=message_id,
            )
            return [("2-0", envelope)]
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


def _auth_with_subscriptions() -> MCPAuthContext:
    auth = _auth()
    auth.subscriptions = [
        "sos:channel:project:acme:global",
        "sos:channel:project:other:global",
    ]
    return auth


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

    assert ("sos:stream:project:acme:agent:alice", "-", 5) in redis.xrange_calls
    assert "tenant-scoped message" in result["content"][0]["text"]


async def test_inbox_reads_subscribed_tenant_broadcast_stream(monkeypatch):
    from sos.mcp import sos_mcp_sse as module

    redis = _RedisStub()
    monkeypatch.setattr(module, "_get_redis", lambda: redis)

    async def _noop_publish_log(*args: Any, **kwargs: Any) -> None:
        return None

    monkeypatch.setattr(module, "_publish_log", _noop_publish_log)

    result = await handle_tool(
        "inbox",
        {"agent": "alice", "limit": 5, "format": "json"},
        _auth_with_subscriptions(),
    )

    payload = json.loads(result["content"][0]["text"])
    texts = [message["text"] for message in payload["messages"]]
    assert "tenant-scoped message" in texts
    assert "tenant broadcast" in texts
    assert ("sos:stream:project:acme:broadcast", "-", 5) in redis.xrange_calls
    assert all(call[0] != "sos:stream:project:other:broadcast" for call in redis.xrange_calls)


async def test_inbox_dedups_direct_and_subscribed_duplicate(monkeypatch):
    from sos.mcp import sos_mcp_sse as module

    redis = _RedisStub()
    redis.duplicate_direct_and_broadcast = True
    monkeypatch.setattr(module, "_get_redis", lambda: redis)

    async def _noop_publish_log(*args: Any, **kwargs: Any) -> None:
        return None

    monkeypatch.setattr(module, "_publish_log", _noop_publish_log)

    result = await handle_tool(
        "inbox",
        {"agent": "alice", "limit": 5, "format": "json"},
        _auth_with_subscriptions(),
    )

    payload = json.loads(result["content"][0]["text"])
    assert [message["text"] for message in payload["messages"]].count("duplicate once") == 1


async def test_bridge_inbox_route_uses_mcp_auth_and_json_shape(monkeypatch):
    from fastapi.testclient import TestClient
    from sos.mcp import sos_mcp_sse as module

    async def _fake_handle_tool(name: str, args: dict[str, Any], auth: MCPAuthContext, **kwargs: Any):
        assert name == "inbox"
        assert args["agent"] == "alice"
        assert args["project"] == "acme"
        assert args["since"] == "1-0"
        assert auth.agent_name == "alice"
        return {
            "structuredContent": {
                "agent": "alice",
                "messages": [{"text": "tenant broadcast"}],
                "cursor": "2-0",
            }
        }

    monkeypatch.setattr(module, "_require_auth", lambda request, token=None: _auth_with_subscriptions())
    monkeypatch.setattr(module, "_enforce_rate_limit", lambda auth: None)
    monkeypatch.setattr(module, "handle_tool", _fake_handle_tool)

    client = TestClient(module.app)
    response = client.get(
        "/bridge/inbox?agent=alice&project=acme&since=1-0",
        headers={"Authorization": "Bearer sk-test"},
    )

    assert response.status_code == 200
    assert response.json()["messages"][0]["text"] == "tenant broadcast"


async def test_token_cache_picks_up_subscription_changes_without_restart(monkeypatch, tmp_path):
    from sos.mcp import sos_mcp_sse as module
    from sos.bus.token_store import hash_token

    tokens_path = tmp_path / "tokens.json"
    raw_token = "sk-test-subscriptions"
    token_hash = hash_token(raw_token)
    tokens_path.write_text(
        json.dumps(
            [
                {
                    "active": True,
                    "token_hash": token_hash,
                    "project": "acme",
                    "agent": "alice",
                    "subscriptions": ["global"],
                }
            ]
        )
    )
    monkeypatch.setattr(module, "BUS_TOKENS_PATH", tokens_path)
    cache = module._TokenCacheWithHotReload()

    first = cache.get()[token_hash]
    assert first.subscriptions == ["sos:channel:global"]

    tokens_path.write_text(
        json.dumps(
            [
                {
                    "active": True,
                    "token_hash": token_hash,
                    "project": "acme",
                    "agent": "alice",
                    "subscriptions": ["sos:channel:project:acme:global"],
                }
            ]
        )
    )
    cache.invalidate()

    second = cache.get()[token_hash]
    assert second.subscriptions == ["sos:channel:project:acme:global"]
