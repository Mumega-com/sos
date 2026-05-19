"""FastAPI TestClient tests for the Glass service.

8 test cases:
1. POST happy path — system token, valid body, tile stored and echoed back
2. POST 401 — no auth token
3. POST 403 — non-system (tenant-scoped) token
4. POST 400 — missing Idempotency-Key header
5. GET list empty vs populated
6. DELETE 204 / 404
7. GET payload with http query — mocked httpx, Cache-Control header asserted
8. GET payload with sql query — 501
"""
from __future__ import annotations

import json
from typing import Any
from unittest.mock import AsyncMock, patch

import httpx
import pytest
import fakeredis.aioredis as fakeredis_aio
from fastapi.testclient import TestClient


# ---------------------------------------------------------------------------
# Shared fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def tokens_file(tmp_path, monkeypatch):
    """Patch tokens.json so gate resolves system + tenant tokens."""
    tokens = [
        {"label": "system", "token": "tk_system", "active": True, "is_system": True},
        {"label": "acme-tenant", "token": "tk_acme", "project": "acme", "active": True},
    ]
    p = tmp_path / "tokens.json"
    p.write_text(json.dumps(tokens))
    import sos.kernel.auth as auth_mod

    monkeypatch.setattr(auth_mod, "TOKENS_PATH", p)
    auth_mod._cache.invalidate()
    return p


@pytest.fixture
def fake_redis():
    return fakeredis_aio.FakeRedis(decode_responses=True)


@pytest.fixture
def tile_store(monkeypatch, fake_redis):
    """Patch _tile_store functions to use fakeredis."""
    import sos.services.glass._tile_store as store_mod
    from sos.contracts.ports.glass import Tile

    async def fake_list(tenant: str, *, redis_client: Any = None) -> list[Tile]:
        from sos.services.glass._tile_store import list_tiles as _real_list

        return await _real_list(tenant, redis_client=fake_redis)

    async def fake_upsert(tenant: str, tile: Tile, *, redis_client: Any = None) -> None:
        from sos.services.glass._tile_store import upsert_tile as _real_upsert

        await _real_upsert(tenant, tile, redis_client=fake_redis)

    async def fake_delete(tenant: str, tile_id: str, *, redis_client: Any = None) -> bool:
        from sos.services.glass._tile_store import delete_tile as _real_delete

        return await _real_delete(tenant, tile_id, redis_client=fake_redis)

    monkeypatch.setattr("sos.services.glass.app.list_tiles", fake_list)
    monkeypatch.setattr("sos.services.glass.app.upsert_tile", fake_upsert)
    monkeypatch.setattr("sos.services.glass.app.delete_tile", fake_delete)
    return fake_redis


@pytest.fixture
def client(tokens_file, tile_store):
    from sos.services.glass.app import app

    return TestClient(app)


# ---------------------------------------------------------------------------
# Shared payloads
# ---------------------------------------------------------------------------

_HTTP_TILE_BODY = {
    "id": "health-light",
    "title": "Health",
    "query": {"kind": "http", "service": "registry", "path": "/health"},
    "template": "status_light",
    "refresh_interval_s": 60,
}

_SQL_TILE_BODY = {
    "id": "wallet-balance",
    "title": "Wallet Balance",
    "query": {"kind": "sql", "service": "economy", "statement": "SELECT balance FROM wallet WHERE tenant=:t"},
    "template": "number",
    "refresh_interval_s": 30,
}


# ---------------------------------------------------------------------------
# 1. POST happy path
# ---------------------------------------------------------------------------


def test_post_tile_happy_path(client: TestClient) -> None:
    resp = client.post(
        "/glass/tiles/acme",
        json=_HTTP_TILE_BODY,
        headers={
            "Authorization": "Bearer tk_system",
            "Idempotency-Key": "idem-001",
        },
    )
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["id"] == "health-light"
    assert data["tenant"] == "acme"
    assert data["template"] == "status_light"


# ---------------------------------------------------------------------------
# 2. POST 401 — no token
# ---------------------------------------------------------------------------


def test_post_tile_requires_auth(client: TestClient) -> None:
    resp = client.post(
        "/glass/tiles/acme",
        json=_HTTP_TILE_BODY,
        headers={"Idempotency-Key": "idem-002"},
    )
    assert resp.status_code == 401


# ---------------------------------------------------------------------------
# 3. POST 403 — non-system token
# ---------------------------------------------------------------------------


def test_post_tile_rejects_tenant_token(client: TestClient) -> None:
    resp = client.post(
        "/glass/tiles/acme",
        json=_HTTP_TILE_BODY,
        headers={
            "Authorization": "Bearer tk_acme",
            "Idempotency-Key": "idem-003",
        },
    )
    assert resp.status_code == 403


# ---------------------------------------------------------------------------
# 4. POST 400 — missing Idempotency-Key
# ---------------------------------------------------------------------------


def test_post_tile_requires_idempotency_key(client: TestClient) -> None:
    resp = client.post(
        "/glass/tiles/acme",
        json=_HTTP_TILE_BODY,
        headers={"Authorization": "Bearer tk_system"},
    )
    assert resp.status_code == 400


# ---------------------------------------------------------------------------
# 5. GET list empty vs populated
# ---------------------------------------------------------------------------


def test_get_tiles_empty(client: TestClient) -> None:
    resp = client.get("/glass/tiles/acme", headers={"Authorization": "Bearer tk_system"})
    assert resp.status_code == 200
    data = resp.json()
    assert data["tiles"] == []
    assert data["count"] == 0


def test_get_tiles_populated(client: TestClient) -> None:
    # Seed a tile first.
    client.post(
        "/glass/tiles/acme",
        json=_HTTP_TILE_BODY,
        headers={"Authorization": "Bearer tk_system", "Idempotency-Key": "idem-010"},
    )
    resp = client.get("/glass/tiles/acme", headers={"Authorization": "Bearer tk_system"})
    assert resp.status_code == 200
    data = resp.json()
    assert data["count"] == 1
    assert data["tiles"][0]["id"] == "health-light"


# ---------------------------------------------------------------------------
# 6. DELETE 204 / 404
# ---------------------------------------------------------------------------


def test_delete_tile_204(client: TestClient) -> None:
    client.post(
        "/glass/tiles/acme",
        json=_HTTP_TILE_BODY,
        headers={"Authorization": "Bearer tk_system", "Idempotency-Key": "idem-020"},
    )
    resp = client.delete("/glass/tiles/acme/health-light", headers={"Authorization": "Bearer tk_system"})
    assert resp.status_code == 204


def test_delete_tile_404(client: TestClient) -> None:
    resp = client.delete("/glass/tiles/acme/nonexistent", headers={"Authorization": "Bearer tk_system"})
    assert resp.status_code == 404


# ---------------------------------------------------------------------------
# 7. GET payload with http query — mock httpx, assert Cache-Control header
# ---------------------------------------------------------------------------


def test_get_payload_http_query(client: TestClient, monkeypatch) -> None:
    # Seed the tile.
    client.post(
        "/glass/tiles/acme",
        json=_HTTP_TILE_BODY,
        headers={"Authorization": "Bearer tk_system", "Idempotency-Key": "idem-030"},
    )

    # Mock httpx transport so the downstream call succeeds.
    mock_response = httpx.Response(200, json={"status": "healthy"})
    mock_transport = httpx.MockTransport(lambda req: mock_response)
    mock_client = httpx.AsyncClient(transport=mock_transport)

    monkeypatch.setattr("sos.services.glass.app._httpx_client", mock_client)

    resp = client.get("/glass/payload/acme/health-light", headers={"Authorization": "Bearer tk_system"})
    assert resp.status_code == 200, resp.text

    # Assert Cache-Control header.
    cc = resp.headers.get("cache-control", "")
    assert "max-age=60" in cc, f"Expected max-age=60 in Cache-Control, got: {cc}"

    data = resp.json()
    assert data["tile_id"] == "health-light"
    assert data["cache_ttl_s"] == 60
    assert data["data"]["kind"] == "http"
    assert data["data"]["status"] == 200


# ---------------------------------------------------------------------------
# 8. GET payload with sql query — 501
# ---------------------------------------------------------------------------


def test_get_payload_sql_returns_501(client: TestClient) -> None:
    # Seed a SQL tile.
    client.post(
        "/glass/tiles/acme",
        json=_SQL_TILE_BODY,
        headers={"Authorization": "Bearer tk_system", "Idempotency-Key": "idem-040"},
    )

    resp = client.get("/glass/payload/acme/wallet-balance", headers={"Authorization": "Bearer tk_system"})
    assert resp.status_code == 501
    assert "Phase 7" in resp.json()["detail"]
