"""GET /sos/bus/dlq — dashboard view over the bus dead-letter queue.

Thin read-only surface on top of :mod:`sos.services.bus.dlq`. Two
routes:

* ``GET /sos/bus/dlq`` — list the original streams that currently
  have at least one DLQ entry. Returns ``{"streams": [...]}`` so
  adding per-stream counts later is a non-breaking extension.
* ``GET /sos/bus/dlq/entries?stream=...&limit=...`` — newest-first
  DLQ entries for a single original stream.

Why a query param instead of ``/dlq/{stream}``: stream names contain
colons (``sos:stream:project:...``), and FastAPI path parsing treats
them ambiguously without ``:path`` converters. A query param is
operator-friendlier and unambiguous.

Auth matches the other dashboard observability routes (``/sos/traces``).
This is diagnostic-only — no write verbs; replay lives in a later wave.
"""

from __future__ import annotations

import redis.asyncio as aioredis
from fastapi import APIRouter, Header, HTTPException, Query

from sos.kernel.auth import verify_bearer
from sos.services.bus.dlq import DLQEntry, list_dlq_streams, read_dlq

from ..config import REDIS_PASSWORD

router = APIRouter(prefix="/sos/bus", tags=["bus"])


def _get_async_redis() -> aioredis.Redis:
    """Construct an async Redis client for DLQ reads.

    The dashboard elsewhere uses a sync client (``redis_helper``); we
    use the async one here because :func:`read_dlq` + :func:`list_dlq_streams`
    are async — they share the bus service's ``redis.asyncio`` dependency
    so writer and reader use the same client library and the same
    decode rules (``decode_responses=True``).
    """
    return aioredis.from_url(
        f"redis://:{REDIS_PASSWORD}@localhost:6379/0",
        decode_responses=True,
    )


@router.get("/dlq")
async def list_dlq(
    authorization: str | None = Header(None),
) -> dict[str, list[str]]:
    """Return the list of original streams that currently have DLQ entries."""
    if verify_bearer(authorization) is None:
        raise HTTPException(status_code=401, detail="unauthorized")

    client = _get_async_redis()
    try:
        streams = await list_dlq_streams(client)
    finally:
        await client.aclose()
    return {"streams": streams}


@router.get("/dlq/entries", response_model=list[DLQEntry])
async def get_dlq_entries(
    authorization: str | None = Header(None),
    stream: str = Query(..., description="Original stream name (not the DLQ stream key)"),
    limit: int = Query(100, ge=1, le=1000, description="Max entries returned, newest first"),
) -> list[DLQEntry]:
    """Return up to ``limit`` DLQ entries for ``stream``, newest first."""
    if verify_bearer(authorization) is None:
        raise HTTPException(status_code=401, detail="unauthorized")

    client = _get_async_redis()
    try:
        return await read_dlq(client, stream, limit=limit)
    finally:
        await client.aclose()
