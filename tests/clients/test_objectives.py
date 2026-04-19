"""Tests for sos.clients.objectives — sync and async HTTP clients.

Mocking strategy: mirror test_registry_client.py.
- Sync  (ObjectivesClient):      inject httpx.MockTransport at construction time.
- Async (AsyncObjectivesClient): patch sos.clients.base.httpx.AsyncClient with a
  factory that injects a MockTransport so the fresh AsyncClient created per _request
  uses our handler.
"""
from __future__ import annotations

import json
import os
from typing import Callable
from unittest.mock import patch

import httpx
import pytest

from sos.clients.base import SOSClientError
from sos.clients.objectives import AsyncObjectivesClient, ObjectivesClient
from sos.contracts.objective import Objective

# ---------------------------------------------------------------------------
# Fixtures / helpers
# ---------------------------------------------------------------------------

_ULID = "01ARZ3NDEKTSV4RRFFQ69G5FAV"  # valid 26-char ULID (Crockford base32)
_NOW = "2026-04-18T12:00:00Z"

_BASE_URL = "http://fake-objectives:6068"
_TOKEN = "test-token"


def _obj_dict(**overrides) -> dict:
    """Return a minimal valid Objective dict."""
    base = {
        "id": _ULID,
        "parent_id": None,
        "title": "Build kernel",
        "description": "",
        "bounty_mind": 0,
        "state": "open",
        "holder_agent": None,
        "holder_heartbeat_at": None,
        "subscribers": [],
        "tags": [],
        "capabilities_required": [],
        "completion_artifact_url": None,
        "completion_notes": "",
        "acks": [],
        "created_by": "agent:codex",
        "created_at": _NOW,
        "updated_at": _NOW,
        "tenant_id": "default",
        "project": None,
    }
    base.update(overrides)
    return base


def _sync_client(handler: Callable[[httpx.Request], httpx.Response]) -> ObjectivesClient:
    transport = httpx.MockTransport(handler)
    return ObjectivesClient(base_url=_BASE_URL, token=_TOKEN, transport=transport)


# ---------------------------------------------------------------------------
# Async helper — same pattern as test_registry_client.py
# ---------------------------------------------------------------------------

_REAL_ASYNC_CLIENT = httpx.AsyncClient


def _mock_async_factory(
    handler: Callable[[httpx.Request], httpx.Response],
) -> Callable[..., httpx.AsyncClient]:
    transport = httpx.MockTransport(handler)

    def _factory(*args, **kwargs) -> httpx.AsyncClient:
        kwargs["transport"] = transport
        return _REAL_ASYNC_CLIENT(*args, **kwargs)

    return _factory


# ---------------------------------------------------------------------------
# 1. create — posts correct body, returns typed Objective
# ---------------------------------------------------------------------------


def test_create_posts_body_returns_objective() -> None:
    obj = _obj_dict(title="New objective")
    captured: dict = {}

    def handler(req: httpx.Request) -> httpx.Response:
        assert req.method == "POST"
        assert req.url.path == "/objectives"
        assert req.headers.get("authorization") == f"Bearer {_TOKEN}"
        captured["body"] = json.loads(req.content)
        return httpx.Response(201, json=obj)

    client = _sync_client(handler)
    result = client.create(title="New objective", created_by="agent:codex")

    assert isinstance(result, Objective)
    assert result.title == "New objective"
    assert captured["body"]["created_by"] == "agent:codex"


# ---------------------------------------------------------------------------
# 2. get — 200 returns Objective, 404 returns None
# ---------------------------------------------------------------------------


def test_get_returns_objective_on_200() -> None:
    obj = _obj_dict()

    def handler(req: httpx.Request) -> httpx.Response:
        assert req.method == "GET"
        assert req.url.path == f"/objectives/{_ULID}"
        return httpx.Response(200, json=obj)

    client = _sync_client(handler)
    result = client.get(_ULID)

    assert isinstance(result, Objective)
    assert result.id == _ULID


def test_get_returns_none_on_404() -> None:
    def handler(req: httpx.Request) -> httpx.Response:
        return httpx.Response(404, content=b'{"detail": "not found"}')

    client = _sync_client(handler)
    result = client.get(_ULID)

    assert result is None


# ---------------------------------------------------------------------------
# 3. tree — returns nested dict
# ---------------------------------------------------------------------------


def test_tree_returns_nested_dict() -> None:
    tree_body = {"objective": _obj_dict(), "children": []}

    def handler(req: httpx.Request) -> httpx.Response:
        assert req.url.path == f"/objectives/{_ULID}/tree"
        assert "max_depth" in str(req.url)
        return httpx.Response(200, json=tree_body)

    client = _sync_client(handler)
    result = client.tree(_ULID, max_depth=5)

    assert "objective" in result
    assert result["children"] == []


# ---------------------------------------------------------------------------
# 4. query — empty list + zero count
# ---------------------------------------------------------------------------


def test_query_returns_empty_list() -> None:
    def handler(req: httpx.Request) -> httpx.Response:
        assert req.url.path == "/objectives"
        return httpx.Response(200, json={"objectives": [], "count": 0})

    client = _sync_client(handler)
    result = client.query()

    assert result == []


# ---------------------------------------------------------------------------
# 5. query — tag + min_bounty params forwarded
# ---------------------------------------------------------------------------


def test_query_with_tag_and_min_bounty_passes_params() -> None:
    def handler(req: httpx.Request) -> httpx.Response:
        assert "tag=infra" in str(req.url)
        assert "min_bounty=50" in str(req.url)
        return httpx.Response(200, json={"objectives": [_obj_dict(tags=["infra"])], "count": 1})

    client = _sync_client(handler)
    result = client.query(tag="infra", min_bounty=50)

    assert len(result) == 1
    assert isinstance(result[0], Objective)


# ---------------------------------------------------------------------------
# 6. claim — returns dict with holder_agent (no agent param)
# ---------------------------------------------------------------------------


def test_claim_returns_dict_with_holder_agent() -> None:
    resp_body = {"ok": True, "obj_id": _ULID, "holder_agent": "agent:loom"}

    def handler(req: httpx.Request) -> httpx.Response:
        assert req.url.path == f"/objectives/{_ULID}/claim"
        assert req.method == "POST"
        return httpx.Response(200, json=resp_body)

    client = _sync_client(handler)
    result = client.claim(_ULID)

    assert result["ok"] is True
    assert result["holder_agent"] == "agent:loom"


# ---------------------------------------------------------------------------
# 7. claim — with agent param forwards body
# ---------------------------------------------------------------------------


def test_claim_with_agent_passes_body() -> None:
    resp_body = {"ok": True, "obj_id": _ULID, "holder_agent": "agent:codex"}
    captured: dict = {}

    def handler(req: httpx.Request) -> httpx.Response:
        captured["body"] = json.loads(req.content)
        return httpx.Response(200, json=resp_body)

    client = _sync_client(handler)
    client.claim(_ULID, agent="agent:codex")

    assert captured["body"]["agent"] == "agent:codex"


# ---------------------------------------------------------------------------
# 8. heartbeat — returns True on 200
# ---------------------------------------------------------------------------


def test_heartbeat_returns_true_on_200() -> None:
    def handler(req: httpx.Request) -> httpx.Response:
        assert req.url.path == f"/objectives/{_ULID}/heartbeat"
        return httpx.Response(200, json={"ok": True})

    client = _sync_client(handler)
    assert client.heartbeat(_ULID) is True


# ---------------------------------------------------------------------------
# 9. release — returns True
# ---------------------------------------------------------------------------


def test_release_returns_true() -> None:
    def handler(req: httpx.Request) -> httpx.Response:
        assert req.url.path == f"/objectives/{_ULID}/release"
        return httpx.Response(200, json={"ok": True})

    client = _sync_client(handler)
    assert client.release(_ULID) is True


# ---------------------------------------------------------------------------
# 10. complete — posts artifact_url + notes
# ---------------------------------------------------------------------------


def test_complete_posts_artifact_url_and_notes() -> None:
    captured: dict = {}

    def handler(req: httpx.Request) -> httpx.Response:
        assert req.url.path == f"/objectives/{_ULID}/complete"
        captured["body"] = json.loads(req.content)
        return httpx.Response(200, json={"ok": True, "state": "shipped"})

    client = _sync_client(handler)
    result = client.complete(_ULID, artifact_url="https://example.com/artifact", notes="done")

    assert result["state"] == "shipped"
    assert captured["body"]["artifact_url"] == "https://example.com/artifact"
    assert captured["body"]["notes"] == "done"


# ---------------------------------------------------------------------------
# 11. ack — appends acker
# ---------------------------------------------------------------------------


def test_ack_passes_acker() -> None:
    captured: dict = {}

    def handler(req: httpx.Request) -> httpx.Response:
        assert req.url.path == f"/objectives/{_ULID}/ack"
        captured["body"] = json.loads(req.content)
        return httpx.Response(200, json={"ok": True, "acks": ["agent:loom"]})

    client = _sync_client(handler)
    result = client.ack(_ULID, acker="agent:loom")

    assert captured["body"]["acker"] == "agent:loom"
    assert "agent:loom" in result["acks"]


# ---------------------------------------------------------------------------
# 12. env_token fallback — SOS_OBJECTIVES_TOKEN → SOS_SYSTEM_TOKEN
# ---------------------------------------------------------------------------


def test_env_token_falls_back_to_system_token(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("SOS_OBJECTIVES_TOKEN", raising=False)
    monkeypatch.setenv("SOS_SYSTEM_TOKEN", "fallback-token")

    captured: dict = {}

    def handler(req: httpx.Request) -> httpx.Response:
        captured["auth"] = req.headers.get("authorization", "")
        return httpx.Response(200, json={"objectives": [], "count": 0})

    transport = httpx.MockTransport(handler)
    client = ObjectivesClient(base_url=_BASE_URL, transport=transport)
    client.query()

    assert captured["auth"] == "Bearer fallback-token"


# ---------------------------------------------------------------------------
# 13a. async create
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_async_create_returns_objective() -> None:
    obj = _obj_dict(title="Async obj")

    def handler(req: httpx.Request) -> httpx.Response:
        assert req.method == "POST"
        assert req.url.path == "/objectives"
        return httpx.Response(201, json=obj)

    with patch("sos.clients.base.httpx.AsyncClient", _mock_async_factory(handler)):
        client = AsyncObjectivesClient(base_url=_BASE_URL, token=_TOKEN)
        result = await client.create(title="Async obj", created_by="agent:codex")

    assert isinstance(result, Objective)
    assert result.title == "Async obj"


# ---------------------------------------------------------------------------
# 13b. async get — 200 + 404
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_async_get_returns_objective_on_200() -> None:
    obj = _obj_dict()

    def handler(req: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=obj)

    with patch("sos.clients.base.httpx.AsyncClient", _mock_async_factory(handler)):
        client = AsyncObjectivesClient(base_url=_BASE_URL, token=_TOKEN)
        result = await client.get(_ULID)

    assert isinstance(result, Objective)


@pytest.mark.asyncio
async def test_async_get_returns_none_on_404() -> None:
    def handler(req: httpx.Request) -> httpx.Response:
        return httpx.Response(404, content=b'{"detail":"not found"}')

    with patch("sos.clients.base.httpx.AsyncClient", _mock_async_factory(handler)):
        client = AsyncObjectivesClient(base_url=_BASE_URL, token=_TOKEN)
        result = await client.get(_ULID)

    assert result is None


# ---------------------------------------------------------------------------
# 13c. async claim
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_async_claim_returns_dict() -> None:
    resp_body = {"ok": True, "obj_id": _ULID, "holder_agent": "agent:async-loom"}

    def handler(req: httpx.Request) -> httpx.Response:
        assert req.url.path == f"/objectives/{_ULID}/claim"
        return httpx.Response(200, json=resp_body)

    with patch("sos.clients.base.httpx.AsyncClient", _mock_async_factory(handler)):
        client = AsyncObjectivesClient(base_url=_BASE_URL, token=_TOKEN)
        result = await client.claim(_ULID, agent="agent:async-loom")

    assert result["ok"] is True
    assert result["holder_agent"] == "agent:async-loom"


# ---------------------------------------------------------------------------
# 14. non-404 error raises SOSClientError
# ---------------------------------------------------------------------------


def test_get_raises_on_500() -> None:
    def handler(req: httpx.Request) -> httpx.Response:
        return httpx.Response(500, content=b"internal error")

    client = _sync_client(handler)
    with pytest.raises(SOSClientError) as exc_info:
        client.get(_ULID)

    assert exc_info.value.status_code == 500
