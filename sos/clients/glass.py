"""HTTP client for the SOS Glass service (port 8092).

Provides both a sync ``GlassClient`` and an async ``AsyncGlassClient``
mirroring the pattern established in ``sos.clients.economy``.

Auth reads ``SOS_GLASS_SYSTEM_TOKEN`` then falls back to ``SOS_SYSTEM_TOKEN``.
An ``Idempotency-Key`` is auto-generated (UUID4) when not supplied by the caller.
"""
from __future__ import annotations

import os
import uuid
from typing import Any, Dict, List, Optional, Union

from sos.clients.base import AsyncBaseHTTPClient, BaseHTTPClient
from sos.contracts.ports.glass import Tile

_DEFAULT_BASE = "http://localhost:8092"


def _auth_headers(token: Optional[str]) -> Dict[str, str]:
    resolved = token or os.environ.get("SOS_GLASS_SYSTEM_TOKEN") or os.environ.get("SOS_SYSTEM_TOKEN")
    return {"Authorization": f"Bearer {resolved}"} if resolved else {}


def _idempotency_key(key: Optional[str]) -> str:
    return key if key is not None else str(uuid.uuid4())


def _tile_payload(tile: Union[Tile, Dict[str, Any]]) -> Dict[str, Any]:
    if isinstance(tile, Tile):
        return tile.model_dump(mode="json")
    return tile


class GlassClient(BaseHTTPClient):
    """Sync HTTP client for the Glass service."""

    def __init__(
        self,
        base_url: str = _DEFAULT_BASE,
        token: Optional[str] = None,
        **kwargs: Any,
    ) -> None:
        headers = kwargs.pop("headers", None) or {}
        for k, v in _auth_headers(token).items():
            headers.setdefault(k, v)
        super().__init__(base_url, headers=headers, **kwargs)

    def upsert_tile(
        self,
        tenant: str,
        tile: Union[Tile, Dict[str, Any]],
        *,
        idempotency_key: Optional[str] = None,
    ) -> Dict[str, Any]:
        """POST /glass/tiles/{tenant} — create or replace a tile."""
        payload = _tile_payload(tile)
        # TileMintRequest doesn't include tenant — strip it from the body.
        payload.pop("tenant", None)
        idem = _idempotency_key(idempotency_key)
        return self._request(
            "POST",
            f"/glass/tiles/{tenant}",
            json=payload,
            headers={"Idempotency-Key": idem},
        ).json()

    def list_tiles(self, tenant: str) -> List[Dict[str, Any]]:
        """GET /glass/tiles/{tenant} — list tiles for a tenant."""
        return self._request("GET", f"/glass/tiles/{tenant}").json().get("tiles", [])

    def delete_tile(self, tenant: str, tile_id: str) -> bool:
        """DELETE /glass/tiles/{tenant}/{tile_id} — returns True if removed."""
        from sos.clients.base import SOSClientError

        try:
            self._request("DELETE", f"/glass/tiles/{tenant}/{tile_id}")
            return True
        except SOSClientError as exc:
            if exc.status_code == 404:
                return False
            raise

    def get_payload(self, tenant: str, tile_id: str) -> Dict[str, Any]:
        """GET /glass/payload/{tenant}/{tile_id} — resolved tile payload."""
        return self._request("GET", f"/glass/payload/{tenant}/{tile_id}").json()


class AsyncGlassClient(AsyncBaseHTTPClient):
    """Async HTTP client for the Glass service."""

    def __init__(
        self,
        base_url: str = _DEFAULT_BASE,
        token: Optional[str] = None,
        **kwargs: Any,
    ) -> None:
        headers = kwargs.pop("headers", None) or {}
        for k, v in _auth_headers(token).items():
            headers.setdefault(k, v)
        super().__init__(base_url, headers=headers, **kwargs)

    async def upsert_tile(
        self,
        tenant: str,
        tile: Union[Tile, Dict[str, Any]],
        *,
        idempotency_key: Optional[str] = None,
    ) -> Dict[str, Any]:
        """POST /glass/tiles/{tenant} — create or replace a tile."""
        payload = _tile_payload(tile)
        payload.pop("tenant", None)
        idem = _idempotency_key(idempotency_key)
        resp = await self._request(
            "POST",
            f"/glass/tiles/{tenant}",
            json=payload,
            headers={"Idempotency-Key": idem},
        )
        return resp.json()

    async def list_tiles(self, tenant: str) -> List[Dict[str, Any]]:
        """GET /glass/tiles/{tenant} — list tiles for a tenant."""
        resp = await self._request("GET", f"/glass/tiles/{tenant}")
        return resp.json().get("tiles", [])

    async def delete_tile(self, tenant: str, tile_id: str) -> bool:
        """DELETE /glass/tiles/{tenant}/{tile_id} — returns True if removed."""
        from sos.clients.base import SOSClientError

        try:
            await self._request("DELETE", f"/glass/tiles/{tenant}/{tile_id}")
            return True
        except SOSClientError as exc:
            if exc.status_code == 404:
                return False
            raise

    async def get_payload(self, tenant: str, tile_id: str) -> Dict[str, Any]:
        """GET /glass/payload/{tenant}/{tile_id} — resolved tile payload."""
        resp = await self._request("GET", f"/glass/payload/{tenant}/{tile_id}")
        return resp.json()


__all__ = ["GlassClient", "AsyncGlassClient"]
