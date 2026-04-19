"""Redis-backed tile registry for the Glass layer.

Stores per-tenant tile lists as JSON arrays under ``sos:glass:tiles:{tenant}``.
TTL is 1 year — tiles are long-lived configuration, not ephemeral state.

The Redis client is injectable so tests can pass a fakeredis instance without
touching environment variables.
"""
from __future__ import annotations

import json
import os
from typing import Any

from sos.contracts.ports.glass import Tile

_TILE_KEY_PREFIX = "sos:glass:tiles:"
_DEFAULT_TTL_SECONDS = 365 * 24 * 3600  # 1 year


def _tile_key(tenant: str) -> str:
    return f"{_TILE_KEY_PREFIX}{tenant}"


async def _get_client(redis_client: Any = None) -> Any:
    """Return an async Redis client, building one from env if not injected."""
    if redis_client is not None:
        return redis_client
    import redis.asyncio as aioredis

    url = os.environ.get("SOS_REDIS_URL") or os.environ.get("REDIS_URL", "redis://localhost:6379")
    password = os.environ.get("REDIS_PASSWORD")
    if password:
        return aioredis.from_url(url, password=password, decode_responses=True)
    return aioredis.from_url(url, decode_responses=True)


async def list_tiles(tenant: str, *, redis_client: Any = None) -> list[Tile]:
    """Return all tiles for a tenant. Empty list if key is missing."""
    client = await _get_client(redis_client)
    owns = redis_client is None
    try:
        raw = await client.get(_tile_key(tenant))
        if not raw:
            return []
        records: list[dict[str, Any]] = json.loads(raw)
        return [Tile.model_validate(r) for r in records]
    finally:
        if owns:
            try:
                await client.aclose()
            except Exception:
                pass


async def upsert_tile(tenant: str, tile: Tile, *, redis_client: Any = None) -> None:
    """Insert or replace a tile by id, preserving all others. Resets TTL."""
    client = await _get_client(redis_client)
    owns = redis_client is None
    try:
        raw = await client.get(_tile_key(tenant))
        existing: list[dict[str, Any]] = json.loads(raw) if raw else []
        # Replace by id, or append if new.
        updated = [t for t in existing if t.get("id") != tile.id]
        updated.append(tile.model_dump(mode="json"))
        await client.set(_tile_key(tenant), json.dumps(updated), ex=_DEFAULT_TTL_SECONDS)
    finally:
        if owns:
            try:
                await client.aclose()
            except Exception:
                pass


async def delete_tile(tenant: str, tile_id: str, *, redis_client: Any = None) -> bool:
    """Remove a tile by id. Returns True if the tile was present and removed."""
    client = await _get_client(redis_client)
    owns = redis_client is None
    try:
        raw = await client.get(_tile_key(tenant))
        if not raw:
            return False
        existing: list[dict[str, Any]] = json.loads(raw)
        without = [t for t in existing if t.get("id") != tile_id]
        if len(without) == len(existing):
            return False
        await client.set(_tile_key(tenant), json.dumps(without), ex=_DEFAULT_TTL_SECONDS)
        return True
    finally:
        if owns:
            try:
                await client.aclose()
            except Exception:
                pass


__all__ = ["list_tiles", "upsert_tile", "delete_tile"]
