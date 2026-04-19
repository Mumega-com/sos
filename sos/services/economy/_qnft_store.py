"""Minimal qNFT persistence layer — v0.9.4-alpha.3.

Stores minted qNFTs as JSON-encoded lists in Redis under the key
``sos:qnft:{tenant}``.  The data model is intentionally flat so the
whole thing can be replaced with a D1/Postgres query in the next version
without changing the callers.

The ``redis`` client is injected so tests can pass fakeredis without
touching environment variables.
"""
from __future__ import annotations

import json
import os
from typing import Any

_DEFAULT_TTL_S = 365 * 24 * 3600  # 1 year — seat tokens are long-lived


def _redis_key(tenant: str) -> str:
    return f"sos:qnft:{tenant}"


async def _get_client(redis: Any = None) -> Any:
    """Return an async Redis client, building one from env if not injected."""
    if redis is not None:
        return redis
    import redis.asyncio as aioredis

    url = os.environ.get("SOS_REDIS_URL") or os.environ.get("REDIS_URL", "redis://localhost:6379")
    password = os.environ.get("REDIS_PASSWORD")
    if password:
        return aioredis.from_url(url, password=password, decode_responses=True)
    return aioredis.from_url(url, decode_responses=True)


async def append_qnft(token: dict[str, Any], *, redis: Any = None) -> None:
    """Append one qNFT dict to the tenant's list in Redis."""
    client = await _get_client(redis)
    key = _redis_key(token["tenant"])
    owns = redis is None
    try:
        raw = await client.get(key)
        existing: list[dict[str, Any]] = json.loads(raw) if raw else []
        existing.append(token)
        await client.set(key, json.dumps(existing, default=str), ex=_DEFAULT_TTL_S)
    finally:
        if owns:
            try:
                await client.aclose()
            except Exception:
                pass


async def list_qnfts(tenant: str, *, redis: Any = None) -> list[dict[str, Any]]:
    """Return all qNFTs for a tenant. Empty list if none minted yet."""
    client = await _get_client(redis)
    owns = redis is None
    try:
        raw = await client.get(_redis_key(tenant))
        return json.loads(raw) if raw else []
    finally:
        if owns:
            try:
                await client.aclose()
            except Exception:
                pass


__all__ = ["append_qnft", "list_qnfts"]
