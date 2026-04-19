"""Unit tests for sos.services.glass._tile_store.

Uses fakeredis so no real Redis instance is needed.
"""
from __future__ import annotations

import pytest
import fakeredis.aioredis as fakeredis_aio

from sos.contracts.ports.glass import HttpQuery, Tile, TileTemplate
from sos.services.glass._tile_store import delete_tile, list_tiles, upsert_tile


def _make_tile(tile_id: str = "health-light", tenant: str = "acme") -> Tile:
    return Tile(
        id=tile_id,
        title="Health",
        query=HttpQuery(kind="http", service="registry", path="/health"),
        template=TileTemplate.STATUS_LIGHT,
        refresh_interval_s=60,
        tenant=tenant,
    )


@pytest.fixture
def redis():
    """Synchronous-style fixture that returns a fakeredis async client."""
    return fakeredis_aio.FakeRedis(decode_responses=True)


# ---------------------------------------------------------------------------
# 1. Empty list
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_list_tiles_empty(redis) -> None:
    result = await list_tiles("acme", redis_client=redis)
    assert result == []


# ---------------------------------------------------------------------------
# 2. Upsert new tile
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_upsert_new_tile(redis) -> None:
    tile = _make_tile("health-light")
    await upsert_tile("acme", tile, redis_client=redis)

    tiles = await list_tiles("acme", redis_client=redis)
    assert len(tiles) == 1
    assert tiles[0].id == "health-light"
    assert tiles[0].title == "Health"
    assert tiles[0].tenant == "acme"


# ---------------------------------------------------------------------------
# 3. Upsert overwrites same id
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_upsert_overwrites_same_id(redis) -> None:
    original = _make_tile("health-light")
    await upsert_tile("acme", original, redis_client=redis)

    updated = Tile(
        id="health-light",
        title="Health Updated",
        query=HttpQuery(kind="http", service="registry", path="/health"),
        template=TileTemplate.STATUS_LIGHT,
        refresh_interval_s=120,
        tenant="acme",
    )
    await upsert_tile("acme", updated, redis_client=redis)

    tiles = await list_tiles("acme", redis_client=redis)
    assert len(tiles) == 1
    assert tiles[0].title == "Health Updated"
    assert tiles[0].refresh_interval_s == 120


# ---------------------------------------------------------------------------
# 4. Delete
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_delete_existing_tile(redis) -> None:
    tile = _make_tile("health-light")
    await upsert_tile("acme", tile, redis_client=redis)

    removed = await delete_tile("acme", "health-light", redis_client=redis)
    assert removed is True

    tiles = await list_tiles("acme", redis_client=redis)
    assert tiles == []


@pytest.mark.asyncio
async def test_delete_nonexistent_tile_returns_false(redis) -> None:
    removed = await delete_tile("acme", "does-not-exist", redis_client=redis)
    assert removed is False


@pytest.mark.asyncio
async def test_upsert_multiple_tiles_preserves_others(redis) -> None:
    t1 = _make_tile("health-light")
    t2 = _make_tile("wallet-balance")
    await upsert_tile("acme", t1, redis_client=redis)
    await upsert_tile("acme", t2, redis_client=redis)

    await delete_tile("acme", "health-light", redis_client=redis)
    tiles = await list_tiles("acme", redis_client=redis)
    assert len(tiles) == 1
    assert tiles[0].id == "wallet-balance"
