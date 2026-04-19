"""End-to-end test for the Glass dashboard round-trip (Phase 6 §6.9).

Seeds the Phase-6 default tile set through the in-process Glass service,
then hits ``GET /glass/payload/<tenant>/<tile_id>`` for each non-sql tile
and asserts:

- 200 response shape matches ``TilePayload``
- ``Cache-Control: max-age=<refresh_interval_s>`` header is set
- http / bus_tail resolvers are wired up correctly against mocks
- The metabolism tile (sql) still returns 501 (Phase 7 work)

The test uses FastAPI's ``TestClient`` — no real Redis, no real downstream
services. Mirrors the service-level fixtures from
``tests/services/glass/test_app.py``.
"""
from __future__ import annotations

import json
from typing import Any

import fakeredis.aioredis as fakeredis_aio
import httpx
import pytest
from fastapi.testclient import TestClient

from sos.cli._default_tiles import default_tiles
from sos.contracts.ports.glass import TileTemplate


# ---------------------------------------------------------------------------
# Fixtures — mirror tests/services/glass/test_app.py
# ---------------------------------------------------------------------------


@pytest.fixture
def tokens_file(tmp_path, monkeypatch):
    tokens = [
        {"label": "system", "token": "tk_system", "active": True, "is_system": True},
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
# Downstream mocks
# ---------------------------------------------------------------------------


def _mock_httpx_response(request: httpx.Request) -> httpx.Response:
    """Return canned JSON based on the request URL path."""
    url = str(request.url)
    if "/registry/squad/" in url:
        return httpx.Response(200, json={"ok": True, "heartbeat": "2026-04-19T00:00:00Z"})
    if "/objectives/roots/" in url:
        return httpx.Response(200, json={"roots": [{"id": "obj-1", "progress": 0.42}]})
    if "/integrations/ga4/" in url:
        return httpx.Response(200, json={"sessions": [1, 2, 3]})
    return httpx.Response(404, json={"detail": "unexpected URL in mock"})


# ---------------------------------------------------------------------------
# The test
# ---------------------------------------------------------------------------


def test_glass_dashboard_round_trip(client: TestClient, monkeypatch) -> None:
    tenant = "acme"

    # Seed the 5 default tiles via the real POST route (uses the same path
    # ``sos init`` Step F takes in production).
    for tile in default_tiles(tenant):
        body = tile.model_dump(mode="json")
        body.pop("tenant", None)
        resp = client.post(
            f"/glass/tiles/{tenant}",
            json=body,
            headers={
                "Authorization": "Bearer tk_system",
                "Idempotency-Key": f"idem-{tile.id}",
            },
        )
        assert resp.status_code == 200, resp.text

    # Confirm the tile registry reports all 5.
    list_resp = client.get(f"/glass/tiles/{tenant}", headers={"Authorization": "Bearer tk_system"})
    assert list_resp.status_code == 200
    assert list_resp.json()["count"] == 5

    # --- Mock downstream HTTP calls for the 3 http-query tiles ---
    mock_transport = httpx.MockTransport(_mock_httpx_response)
    mock_httpx = httpx.AsyncClient(transport=mock_transport)
    monkeypatch.setattr("sos.services.glass.app._httpx_client", mock_httpx)

    # Mock redis.asyncio.from_url so the bus_tail resolver hits fakeredis.
    import redis.asyncio as aioredis

    fake = fakeredis_aio.FakeRedis(decode_responses=True)
    # Pre-populate the audit stream so XREVRANGE has something to return.
    import asyncio

    async def _seed_bus() -> None:
        await fake.xadd(f"audit:decisions:{tenant}", {"actor": "kernel", "verdict": "allow"})
        await fake.xadd(f"audit:decisions:{tenant}", {"actor": "kernel", "verdict": "deny"})

    asyncio.get_event_loop().run_until_complete(_seed_bus())

    def _fake_from_url(*args, **kwargs):
        return fake

    monkeypatch.setattr(aioredis, "from_url", _fake_from_url)

    # --- Hit /glass/payload for each non-sql tile and validate ---
    expected = {
        "health": {"template": TileTemplate.STATUS_LIGHT, "ttl": 30, "kind": "http"},
        "objectives": {"template": TileTemplate.PROGRESS_BAR, "ttl": 120, "kind": "http"},
        "decisions": {"template": TileTemplate.EVENT_LOG, "ttl": 30, "kind": "bus_tail"},
        "metrics": {"template": TileTemplate.CHART, "ttl": 300, "kind": "http"},
    }

    for tile_id, spec in expected.items():
        resp = client.get(
            f"/glass/payload/{tenant}/{tile_id}",
            headers={"Authorization": "Bearer tk_system"},
        )
        assert resp.status_code == 200, f"{tile_id}: {resp.text}"

        # Cache-Control header matches the tile's refresh_interval_s.
        cc = resp.headers.get("cache-control", "")
        assert f"max-age={spec['ttl']}" in cc, f"{tile_id}: {cc!r}"

        # TilePayload shape.
        body = resp.json()
        assert body["tile_id"] == tile_id
        assert body["cache_ttl_s"] == spec["ttl"]
        assert "rendered_at" in body
        assert isinstance(body["data"], dict)
        assert body["data"]["kind"] == spec["kind"]

    # metabolism (sql) — 501 until Phase 7 ships.
    resp = client.get(
        f"/glass/payload/{tenant}/metabolism",
        headers={"Authorization": "Bearer tk_system"},
    )
    assert resp.status_code == 501
    assert "Phase 7" in resp.json()["detail"]
