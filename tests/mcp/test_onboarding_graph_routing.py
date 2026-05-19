from __future__ import annotations

import hashlib
import json
import sys
import types
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

_mirror_db_stub = types.ModuleType("mirror.kernel.db")
_mirror_db_stub.get_db = lambda: None
_mirror_embeddings_stub = types.ModuleType("mirror.kernel.embeddings")
_mirror_embeddings_stub.get_embedding = lambda text: []
sys.modules.setdefault("mirror.kernel.db", _mirror_db_stub)
sys.modules.setdefault("mirror.kernel.embeddings", _mirror_embeddings_stub)

import sos.mcp.sos_mcp_sse as sse
from sos.mcp.sos_mcp_sse import MCPAuthContext, handle_tool


pytestmark = pytest.mark.asyncio


class _RedisStub:
    def __init__(self) -> None:
        self.sets: dict[str, set[str]] = {}
        self.hashes: dict[str, dict[str, str]] = {}
        self.streams: dict[str, list[dict[str, Any]]] = {}
        self.published: list[tuple[str, str]] = []

    async def xadd(self, stream: str, fields: dict[str, Any]) -> str:
        self.streams.setdefault(stream, []).append(fields)
        return f"{len(self.streams[stream])}-0"

    async def publish(self, channel: str, message: str) -> int:
        self.published.append((channel, message))
        return 1

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


def _client(tmp_path: Path, monkeypatch) -> TestClient:
    tokens = tmp_path / "tokens.json"
    tokens.write_text("[]\n")
    invites = tmp_path / "invites.json"
    invites.write_text("[]\n")
    requests = tmp_path / "requests.json"
    requests.write_text("[]\n")
    monkeypatch.setattr(sse, "BUS_TOKENS_PATH", tokens)
    monkeypatch.setattr(sse, "ONBOARDING_INVITES_PATH", invites)
    monkeypatch.setattr(sse, "ONBOARDING_REQUESTS_PATH", requests)
    sse._local_token_cache.invalidate()
    return TestClient(sse.app)


async def test_check_in_routes_agent_and_confirms_over_bus(monkeypatch):
    redis = _RedisStub()
    monkeypatch.setattr(sse, "_get_redis", lambda: redis)

    async def _noop_publish_log(*args: Any, **kwargs: Any) -> None:
        return None

    monkeypatch.setattr(sse, "_publish_log", _noop_publish_log)

    auth = MCPAuthContext(
        token="sk-test",
        tenant_id="acme",
        is_system=False,
        source="bus_tokens",
        agent_name="athena-acme",
        scope="tenant-agent",
        permissions=["check_in"],
    )

    result = await handle_tool("check_in", {"model": "reviewer"}, auth)
    route = result["structuredContent"]["onboarding_route"]

    assert route["agent"] == "athena-acme"
    assert route["project"] == "acme"
    assert route["squad_id"] == "acme-review"
    assert redis.hashes["sos:onboarding:acme:agent:athena-acme"]["squad_id"] == "acme-review"
    assert "sos:onboarding:acme:events" in redis.streams
    assert "sos:stream:project:acme:agent:athena-acme" in redis.streams
    assert any(channel == "sos:wake:athena-acme" for channel, _ in redis.published)


async def test_join_with_invite_routes_and_graph_exposes_assignment(
    tmp_path: Path, monkeypatch
) -> None:
    redis = _RedisStub()
    client = _client(tmp_path, monkeypatch)
    monkeypatch.setattr(sse, "_get_redis", lambda: redis)
    monkeypatch.setattr(sse.requests, "put", lambda *a, **k: None)
    code = "invite-acme-route"
    (tmp_path / "invites.json").write_text(
        json.dumps(
            [
                {
                    "id": "inv-route",
                    "code_hash": hashlib.sha256(code.encode()).hexdigest(),
                    "tenant_id": "acme",
                    "role": "member",
                    "scopes": ["bus:send", "tasks:*"],
                    "active": True,
                    "max_uses": 1,
                    "uses": 0,
                }
            ]
        )
        + "\n"
    )

    join = client.post(
        "/api/v1/onboarding/join-with-invite",
        json={"invite_code": code, "agent_name": "calliope-acme", "model": "writer"},
    )

    assert join.status_code == 200
    body = join.json()
    assert body["onboarding_route"]["squad_id"] == "acme-content"
    token = body["token"]

    graph = client.get("/api/v1/onboarding/graph", headers={"Authorization": f"Bearer {token}"})
    assert graph.status_code == 200
    payload = graph.json()
    assert payload["assignments"][0]["agent"] == "calliope-acme"
    assert payload["assignments"][0]["squad_id"] == "acme-content"
    assert any(node["id"] == "squad:acme-content" for node in payload["nodes"])
    assert any(edge["type"] == "assigned_to" for edge in payload["edges"])
