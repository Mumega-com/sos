"""GET /sos/brain — observable snapshot of BrainService.

Reads a BrainSnapshot JSON blob that BrainService writes to
``sos:state:brain:snapshot`` (TTL 30s) after every tick. The dashboard
never imports BrainService directly — the redis key is the hand-off.
"""
from __future__ import annotations

from fastapi import APIRouter, Header, HTTPException

from sos.contracts.brain_snapshot import BrainSnapshot
from sos.services.auth import verify_bearer

from ..redis_helper import _get_redis

router = APIRouter(tags=["brain"])

_BRAIN_SNAPSHOT_KEY = "sos:state:brain:snapshot"


@router.get("/sos/brain", response_model=BrainSnapshot)
async def sos_brain(
    authorization: str | None = Header(None),
) -> BrainSnapshot:
    """Return the latest BrainSnapshot persisted by BrainService.

    Auth: any valid Bearer token. Returns 401 on missing/invalid bearer,
    503 when the snapshot key is absent (brain is down or has not ticked
    within the last 30 s).
    """
    if verify_bearer(authorization) is None:
        raise HTTPException(status_code=401, detail="unauthorized")

    try:
        raw = _get_redis().get(_BRAIN_SNAPSHOT_KEY)
    except Exception as exc:
        raise HTTPException(
            status_code=503, detail="brain snapshot unavailable"
        ) from exc

    if not raw:
        raise HTTPException(status_code=503, detail="brain snapshot unavailable")

    if isinstance(raw, bytes):
        raw = raw.decode()

    try:
        return BrainSnapshot.model_validate_json(raw)
    except Exception as exc:
        raise HTTPException(
            status_code=503, detail="brain snapshot unavailable"
        ) from exc
