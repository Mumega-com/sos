"""HTTP client for the SOS Squad Service.

Replaces in-process imports of :mod:`sos.services.squad.auth` and
:mod:`sos.services.squad.service` from callers outside the service
boundary (R2 violation P1-01 MCP half). v0.4.7 Phase 1 introduces the
two client methods MCP needs — token verification and API-key creation —
without porting the full squad surface.

Most MCP squad operations already flow through raw ``requests`` / ``httpx``
to ``SQUAD_SERVICE_URL`` and only need a token string, not a client
object. This module provides:

- :class:`SquadClient` / :class:`AsyncSquadClient`: thin callers for the
  two endpoints that require first-class parsing (auth verify, key create).
- :func:`_resolve_token` / :func:`_resolve_base_url`: env-driven defaults
  mirroring :mod:`sos.clients.journeys`.
"""

from __future__ import annotations

import os
from typing import Any, Dict, Optional

from sos.clients.base import (
    AsyncBaseHTTPClient,
    BaseHTTPClient,
    SOSClientError,
)

DEFAULT_BASE_URL = "http://localhost:6006"
_URL_ENV = "SOS_SQUAD_URL"
_TOKEN_ENV = "SOS_SQUAD_TOKEN"


def _resolve_base_url(base_url: Optional[str]) -> str:
    return base_url or os.environ.get(_URL_ENV) or DEFAULT_BASE_URL


def _resolve_token(token: Optional[str]) -> Optional[str]:
    return token if token is not None else (
        os.environ.get(_TOKEN_ENV)
        or os.environ.get("SOS_SYSTEM_TOKEN")
        or os.environ.get("MUMEGA_MASTER_KEY")
        or None
    )


def _auth_headers(token: Optional[str]) -> Dict[str, str]:
    return {"Authorization": f"Bearer {token}"} if token else {}


class SquadClient(BaseHTTPClient):
    """Synchronous HTTP client for the Squad service."""

    def __init__(
        self,
        base_url: Optional[str] = None,
        token: Optional[str] = None,
        timeout_seconds: float = 30.0,
    ) -> None:
        super().__init__(
            base_url=_resolve_base_url(base_url),
            timeout_seconds=timeout_seconds,
        )
        self._token = _resolve_token(token)

    def verify_token(self, token: str) -> Optional[Dict[str, Any]]:
        """Resolve a bearer against squad's api_keys table.

        Returns a dict with ``tenant_id``, ``identity_type``, ``is_system``
        on hit, or ``None`` on miss. System-side callers (MCP) pass their
        own token via ``token``; the client's own bearer (``self._token``)
        is the caller identity for the lookup endpoint.
        """
        try:
            resp = self._request(
                "POST",
                "/auth/verify",
                json={"token": token},
                headers=_auth_headers(self._token),
            )
        except SOSClientError as exc:
            if exc.status_code == 401:
                return None
            raise
        body = resp.json()
        return body if isinstance(body, dict) and body.get("ok") else None

    def create_api_key(self, tenant_id: str, role: str = "user") -> Dict[str, Any]:
        """Mint a new squad API key for ``tenant_id`` under ``role``."""
        resp = self._request(
            "POST",
            "/api-keys",
            json={"tenant_id": tenant_id, "identity_type": role},
            headers=_auth_headers(self._token),
        )
        return resp.json()


class AsyncSquadClient(AsyncBaseHTTPClient):
    """Async HTTP client for the Squad service."""

    def __init__(
        self,
        base_url: Optional[str] = None,
        token: Optional[str] = None,
        timeout_seconds: float = 30.0,
    ) -> None:
        super().__init__(
            base_url=_resolve_base_url(base_url),
            timeout_seconds=timeout_seconds,
        )
        self._token = _resolve_token(token)

    async def verify_token(self, token: str) -> Optional[Dict[str, Any]]:
        try:
            resp = await self._request(
                "POST",
                "/auth/verify",
                json={"token": token},
                headers=_auth_headers(self._token),
            )
        except SOSClientError as exc:
            if exc.status_code == 401:
                return None
            raise
        body = resp.json()
        return body if isinstance(body, dict) and body.get("ok") else None

    async def create_api_key(self, tenant_id: str, role: str = "user") -> Dict[str, Any]:
        resp = await self._request(
            "POST",
            "/api-keys",
            json={"tenant_id": tenant_id, "identity_type": role},
            headers=_auth_headers(self._token),
        )
        return resp.json()


__all__ = ["SquadClient", "AsyncSquadClient", "SOSClientError"]
