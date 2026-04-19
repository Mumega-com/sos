"""Glass service — FastAPI app on port 8092.

Tile registry + payload resolver. No LLM in the render path.
Query resolution is dispatched on tile.query.kind:
  - http: proxies GET to a downstream SOS service
  - bus_tail: XREVRANGE on a Redis stream
  - sql: stub — ships in Phase 7 (#212)
"""
from __future__ import annotations

import os
import time
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, Optional

import httpx
from fastapi import FastAPI, Header, HTTPException, Response
from fastapi.middleware.cors import CORSMiddleware

from sos import __version__
from sos.contracts.policy import PolicyDecision
from sos.contracts.ports.glass import (
    Tile,
    TileMintRequest,
    TilePayload,
)
from sos.kernel.health import health_response
from sos.kernel.policy.gate import can_execute
from sos.kernel.telemetry import init_tracing, instrument_fastapi
from sos.observability.logging import get_logger
from sos.services.glass._tile_store import delete_tile, list_tiles, upsert_tile

SERVICE_NAME = "glass"
_START_TIME = time.time()

log = get_logger(SERVICE_NAME, min_level=os.getenv("SOS_LOG_LEVEL", "info"))

init_tracing(SERVICE_NAME)

app = FastAPI(title="SOS Glass Service", version=__version__)
instrument_fastapi(app)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ---------------------------------------------------------------------------
# Module-level httpx client — injectable for tests
# ---------------------------------------------------------------------------

# Tests replace this with an httpx.Client/AsyncClient backed by MockTransport.
_httpx_client: Optional[httpx.AsyncClient] = None


def _get_httpx_client() -> httpx.AsyncClient:
    if _httpx_client is not None:
        return _httpx_client
    return httpx.AsyncClient(timeout=10.0)


# ---------------------------------------------------------------------------
# Gate helper (mirrors economy.app._raise_on_deny)
# ---------------------------------------------------------------------------


def _raise_on_deny(decision: PolicyDecision, *, require_system: bool = False) -> None:
    if not decision.allowed:
        reason = decision.reason or "unauthorized"
        if "bearer" in reason.lower() or "auth" in reason.lower():
            raise HTTPException(status_code=401, detail=reason)
        raise HTTPException(status_code=403, detail=reason)

    if require_system and "system/admin" not in decision.reason:
        raise HTTPException(status_code=403, detail="glass:upsert_tile requires system or admin scope")


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------


@app.get("/health")
async def health() -> Dict[str, Any]:
    return health_response(SERVICE_NAME, _START_TIME)


@app.post("/glass/tiles/{tenant}")
async def upsert_tile_route(
    tenant: str,
    req: TileMintRequest,
    idempotency_key: Optional[str] = Header(None, alias="Idempotency-Key"),
    authorization: Optional[str] = Header(None),
) -> Dict[str, Any]:
    """Upsert a tile definition for a tenant. System/admin scope required."""
    if not authorization:
        raise HTTPException(status_code=401, detail="missing bearer token")
    if idempotency_key is None:
        raise HTTPException(status_code=400, detail="Idempotency-Key header is required")

    decision = await can_execute(
        action="glass:upsert_tile",
        resource=tenant,
        tenant=tenant,
        authorization=authorization,
    )
    _raise_on_deny(decision, require_system=True)

    tile = Tile(
        id=req.id,
        title=req.title,
        query=req.query,
        template=req.template,
        refresh_interval_s=req.refresh_interval_s,
        tenant=tenant,
    )
    await upsert_tile(tenant, tile)
    log.info("tile upserted", tile_id=tile.id, tenant=tenant)
    return tile.model_dump(mode="json")


@app.get("/glass/tiles/{tenant}")
async def list_tiles_route(
    tenant: str,
    authorization: Optional[str] = Header(None),
) -> Dict[str, Any]:
    """List tiles for a tenant. Tenant-or-system scope."""
    if not authorization:
        raise HTTPException(status_code=401, detail="missing bearer token")

    decision = await can_execute(
        action="glass:list_tiles",
        resource=tenant,
        tenant=tenant,
        authorization=authorization,
    )
    _raise_on_deny(decision)

    tiles = await list_tiles(tenant)
    return {"tiles": [t.model_dump(mode="json") for t in tiles], "count": len(tiles)}


@app.delete("/glass/tiles/{tenant}/{tile_id}", status_code=204)
async def delete_tile_route(
    tenant: str,
    tile_id: str,
    authorization: Optional[str] = Header(None),
) -> Response:
    """Remove a tile. System/admin scope required."""
    if not authorization:
        raise HTTPException(status_code=401, detail="missing bearer token")

    decision = await can_execute(
        action="glass:delete_tile",
        resource=tenant,
        tenant=tenant,
        authorization=authorization,
    )
    _raise_on_deny(decision, require_system=True)

    removed = await delete_tile(tenant, tile_id)
    if not removed:
        raise HTTPException(status_code=404, detail=f"tile '{tile_id}' not found for tenant '{tenant}'")
    log.info("tile deleted", tile_id=tile_id, tenant=tenant)
    return Response(status_code=204)


@app.get("/glass/payload/{tenant}/{tile_id}")
async def get_payload_route(
    tenant: str,
    tile_id: str,
    authorization: Optional[str] = Header(None),
) -> Response:
    """Hot path — resolve a tile's query and return a TilePayload.

    Sets Cache-Control: max-age=<refresh_interval_s> on 200 responses.
    """
    if not authorization:
        raise HTTPException(status_code=401, detail="missing bearer token")

    decision = await can_execute(
        action="glass:get_payload",
        resource=tenant,
        tenant=tenant,
        authorization=authorization,
    )
    _raise_on_deny(decision)

    tiles = await list_tiles(tenant)
    match = next((t for t in tiles if t.id == tile_id), None)
    if match is None:
        raise HTTPException(status_code=404, detail=f"tile '{tile_id}' not found for tenant '{tenant}'")

    data = await _resolve_query(match)

    payload = TilePayload(
        tile_id=tile_id,
        rendered_at=datetime.now(timezone.utc),
        data=data,
        cache_ttl_s=match.refresh_interval_s,
    )

    import json as _json

    return Response(
        content=_json.dumps(payload.model_dump(mode="json"), default=str),
        media_type="application/json",
        headers={"Cache-Control": f"max-age={match.refresh_interval_s}"},
    )


# ---------------------------------------------------------------------------
# Query resolvers
# ---------------------------------------------------------------------------


async def _resolve_query(tile: Tile) -> Dict[str, Any]:
    kind = tile.query.kind
    if kind == "http":
        return await _resolve_http(tile)
    if kind == "bus_tail":
        return await _resolve_bus_tail(tile)
    if kind == "sql":
        # TODO: sql query kind ships in Phase 7 — task #212
        raise HTTPException(status_code=501, detail="sql query kind ships in Phase 7")
    raise HTTPException(status_code=500, detail=f"unknown query kind: {kind}")


async def _resolve_http(tile: Tile) -> Dict[str, Any]:
    """GET http://<service>.internal<path> and return the parsed JSON."""
    query = tile.query  # type: ignore[union-attr]
    url = f"http://{query.service}.internal{query.path}"
    client = _get_httpx_client()
    owns = _httpx_client is None
    try:
        resp = await client.get(url)
        body: Any = None
        try:
            body = resp.json()
        except Exception:
            body = resp.text
        return {"kind": "http", "status": resp.status_code, "body": body}
    finally:
        if owns:
            await client.aclose()


async def _resolve_bus_tail(tile: Tile) -> Dict[str, Any]:
    """XREVRANGE <stream> + - COUNT <limit> via redis.asyncio."""
    import redis.asyncio as aioredis

    query = tile.query  # type: ignore[union-attr]
    redis_url = os.environ.get("SOS_REDIS_URL") or os.environ.get("REDIS_URL", "redis://localhost:6379")
    password = os.environ.get("REDIS_PASSWORD")
    if password:
        client = aioredis.from_url(redis_url, password=password, decode_responses=True)
    else:
        client = aioredis.from_url(redis_url, decode_responses=True)

    try:
        raw = await client.xrevrange(query.stream, count=query.limit)
        entries = [{"id": entry_id, "fields": fields} for entry_id, fields in raw]
        return {"kind": "bus_tail", "entries": entries}
    finally:
        try:
            await client.aclose()
        except Exception:
            pass
