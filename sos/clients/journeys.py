"""HTTP client for the SOS Journeys Service.

Replaces direct in-process imports of :mod:`sos.services.journeys.tracker`
from callers outside the service boundary (R2 violation P1-05). v0.4.6
Steps 4+5 introduces this client; the journeys service ships an HTTP app
(see :mod:`sos.services.journeys.app`) that exposes the same tracker
surface.
"""

from __future__ import annotations

import os
from typing import Any, Dict, List, Optional

from sos.clients.base import (
    AsyncBaseHTTPClient,
    BaseHTTPClient,
    SOSClientError,
)

DEFAULT_BASE_URL = "http://localhost:6070"
_URL_ENV = "SOS_JOURNEYS_URL"
_TOKEN_ENV = "SOS_JOURNEYS_TOKEN"


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


class JourneysClient(BaseHTTPClient):
    """Synchronous HTTP client for the Journeys service."""

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

    def health(self) -> Dict[str, Any]:
        return self._request("GET", "/health", headers=_auth_headers(self._token)).json()

    def recommend(self, agent: str) -> str:
        resp = self._request(
            "GET", f"/recommend/{agent}", headers=_auth_headers(self._token)
        )
        return str(resp.json().get("path") or "")

    def start(self, agent: str, path: str) -> Dict[str, Any]:
        resp = self._request(
            "POST",
            "/start",
            json={"agent": agent, "path": path},
            headers=_auth_headers(self._token),
        )
        return resp.json()

    def status(self, agent: str) -> List[Dict[str, Any]]:
        resp = self._request(
            "GET", f"/status/{agent}", headers=_auth_headers(self._token)
        )
        body = resp.json()
        return list(body.get("progress", []) if isinstance(body, dict) else [])

    def leaderboard(self, path: Optional[str] = None) -> List[Dict[str, Any]]:
        url = "/leaderboard" + (f"?path={path}" if path else "")
        resp = self._request("GET", url, headers=_auth_headers(self._token))
        body = resp.json()
        return list(body.get("leaders", []) if isinstance(body, dict) else [])


class AsyncJourneysClient(AsyncBaseHTTPClient):
    """Async HTTP client for the Journeys service."""

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

    async def health(self) -> Dict[str, Any]:
        resp = await self._request(
            "GET", "/health", headers=_auth_headers(self._token)
        )
        return resp.json()

    async def recommend(self, agent: str) -> str:
        resp = await self._request(
            "GET", f"/recommend/{agent}", headers=_auth_headers(self._token)
        )
        return str(resp.json().get("path") or "")

    async def start(self, agent: str, path: str) -> Dict[str, Any]:
        resp = await self._request(
            "POST",
            "/start",
            json={"agent": agent, "path": path},
            headers=_auth_headers(self._token),
        )
        return resp.json()

    async def status(self, agent: str) -> List[Dict[str, Any]]:
        resp = await self._request(
            "GET", f"/status/{agent}", headers=_auth_headers(self._token)
        )
        body = resp.json()
        return list(body.get("progress", []) if isinstance(body, dict) else [])

    async def leaderboard(self, path: Optional[str] = None) -> List[Dict[str, Any]]:
        url = "/leaderboard" + (f"?path={path}" if path else "")
        resp = await self._request(
            "GET", url, headers=_auth_headers(self._token)
        )
        body = resp.json()
        return list(body.get("leaders", []) if isinstance(body, dict) else [])


__all__ = ["JourneysClient", "AsyncJourneysClient", "SOSClientError"]
