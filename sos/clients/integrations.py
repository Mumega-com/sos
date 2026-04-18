"""HTTP client for the SOS Integrations Service.

Lets any service / adapter / agent fetch per-tenant OAuth credentials without
importing `sos.services.integrations.oauth`. Required by the v0.4.5 Wave 3
analytics→integrations decoupling (P0-06).
"""

from __future__ import annotations

import os
from typing import Any, Dict, Optional

from sos.clients.base import (
    AsyncBaseHTTPClient,
    BaseHTTPClient,
    SOSClientError,
)

DEFAULT_BASE_URL = "http://localhost:6066"
_TOKEN_ENV = "SOS_INTEGRATIONS_TOKEN"
_URL_ENV = "SOS_INTEGRATIONS_URL"


def _resolve_base_url(base_url: Optional[str]) -> str:
    return base_url or os.environ.get(_URL_ENV) or DEFAULT_BASE_URL


def _resolve_token(token: Optional[str]) -> Optional[str]:
    return token if token is not None else (
        os.environ.get(_TOKEN_ENV) or os.environ.get("SOS_SYSTEM_TOKEN") or None
    )


def _auth_headers(token: Optional[str]) -> Dict[str, str]:
    return {"Authorization": f"Bearer {token}"} if token else {}


class AsyncIntegrationsClient(AsyncBaseHTTPClient):
    """Async HTTP client for the Integrations service."""

    def __init__(
        self,
        base_url: Optional[str] = None,
        token: Optional[str] = None,
        timeout_seconds: float = 10.0,
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

    async def get_credentials(
        self, tenant: str, provider: str
    ) -> Optional[Dict[str, str]]:
        """Return stored credentials for (tenant, provider).

        Returns ``None`` when the service responds with 404 (no credentials
        configured for that pair). Raises :class:`SOSClientError` for other
        non-2xx responses.
        """
        path = f"/oauth/credentials/{tenant}/{provider}"
        try:
            resp = await self._request(
                "GET", path, headers=_auth_headers(self._token)
            )
        except SOSClientError as exc:
            if exc.status_code == 404:
                return None
            raise
        return resp.json()

    async def handle_ghl_callback(
        self, tenant: str, code: str
    ) -> Dict[str, Any]:
        """POST /oauth/ghl/callback/{tenant} — finalize GHL OAuth.

        MCP proxies here after the external GHL redirect lands. The
        integrations service owns the token exchange and storage.
        """
        resp = await self._request(
            "POST",
            f"/oauth/ghl/callback/{tenant}",
            json={"code": code},
            headers=_auth_headers(self._token),
        )
        return resp.json()

    async def handle_google_callback(
        self, tenant: str, code: str, service: str
    ) -> Dict[str, Any]:
        """POST /oauth/google/callback/{tenant} — finalize Google OAuth.

        ``service`` is one of ``analytics``, ``search_console``, ``ads``.
        """
        resp = await self._request(
            "POST",
            f"/oauth/google/callback/{tenant}",
            json={"code": code, "service": service},
            headers=_auth_headers(self._token),
        )
        return resp.json()


class IntegrationsClient(BaseHTTPClient):
    """Synchronous HTTP client for the Integrations service."""

    def __init__(
        self,
        base_url: Optional[str] = None,
        token: Optional[str] = None,
        timeout_seconds: float = 10.0,
    ) -> None:
        super().__init__(
            base_url=_resolve_base_url(base_url),
            timeout_seconds=timeout_seconds,
        )
        self._token = _resolve_token(token)

    def health(self) -> Dict[str, Any]:
        return self._request(
            "GET", "/health", headers=_auth_headers(self._token)
        ).json()

    def get_credentials(
        self, tenant: str, provider: str
    ) -> Optional[Dict[str, str]]:
        path = f"/oauth/credentials/{tenant}/{provider}"
        try:
            resp = self._request("GET", path, headers=_auth_headers(self._token))
        except SOSClientError as exc:
            if exc.status_code == 404:
                return None
            raise
        return resp.json()

    def handle_ghl_callback(
        self, tenant: str, code: str
    ) -> Dict[str, Any]:
        """POST /oauth/ghl/callback/{tenant} — finalize GHL OAuth."""
        resp = self._request(
            "POST",
            f"/oauth/ghl/callback/{tenant}",
            json={"code": code},
            headers=_auth_headers(self._token),
        )
        return resp.json()

    def handle_google_callback(
        self, tenant: str, code: str, service: str
    ) -> Dict[str, Any]:
        """POST /oauth/google/callback/{tenant} — finalize Google OAuth."""
        resp = self._request(
            "POST",
            f"/oauth/google/callback/{tenant}",
            json={"code": code, "service": service},
            headers=_auth_headers(self._token),
        )
        return resp.json()
