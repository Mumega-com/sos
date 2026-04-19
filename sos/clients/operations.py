"""HTTP client for the SOS Operations Service.

Previously imported :mod:`sos.services.operations.runner` directly (R2 violation
P1-07). v0.4.6 Step 1 swaps that for real HTTP over BaseHTTPClient /
AsyncBaseHTTPClient.
"""

from __future__ import annotations

import os
from typing import Any, Dict, List, Optional

from sos.clients.base import (
    AsyncBaseHTTPClient,
    BaseHTTPClient,
    SOSClientError,
)

DEFAULT_BASE_URL = "http://localhost:6068"
_URL_ENV = "SOS_OPERATIONS_URL"
_TOKEN_ENV = "SOS_OPERATIONS_TOKEN"


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


class OperationsClient(BaseHTTPClient):
    """Synchronous HTTP client for the Operations service."""

    def __init__(
        self,
        base_url: Optional[str] = None,
        token: Optional[str] = None,
        timeout_seconds: float = 60.0,
    ) -> None:
        super().__init__(
            base_url=_resolve_base_url(base_url),
            timeout_seconds=timeout_seconds,
        )
        self._token = _resolve_token(token)

    def health(self) -> Dict[str, Any]:
        return self._request("GET", "/health", headers=_auth_headers(self._token)).json()

    def run(self, customer: str, product: str, dry_run: bool = False) -> Dict[str, Any]:
        resp = self._request(
            "POST",
            "/run",
            json={"customer": customer, "product": product, "dry_run": dry_run},
            headers=_auth_headers(self._token),
        )
        return resp.json()

    def dry_run(self, customer: str, product: str) -> Dict[str, Any]:
        return self.run(customer, product, dry_run=True)

    def list_templates(self) -> List[str]:
        resp = self._request(
            "GET", "/templates", headers=_auth_headers(self._token)
        )
        body = resp.json()
        return list(body.get("templates", []) if isinstance(body, dict) else [])

    def get_template(self, product: str) -> Optional[Dict[str, Any]]:
        try:
            resp = self._request(
                "GET", f"/templates/{product}", headers=_auth_headers(self._token)
            )
        except SOSClientError as exc:
            if exc.status_code == 404:
                return None
            raise
        body = resp.json()
        return body if isinstance(body, dict) else None

    def trigger_pulse(self, tenant: str, project: str) -> Dict[str, Any]:
        resp = self._request(
            "POST",
            "/pulse/trigger",
            json={"tenant": tenant, "project": project},
            headers=_auth_headers(self._token),
        )
        return resp.json()


class AsyncOperationsClient(AsyncBaseHTTPClient):
    """Async HTTP client for the Operations service."""

    def __init__(
        self,
        base_url: Optional[str] = None,
        token: Optional[str] = None,
        timeout_seconds: float = 60.0,
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

    async def run(
        self, customer: str, product: str, dry_run: bool = False
    ) -> Dict[str, Any]:
        resp = await self._request(
            "POST",
            "/run",
            json={"customer": customer, "product": product, "dry_run": dry_run},
            headers=_auth_headers(self._token),
        )
        return resp.json()

    async def dry_run(self, customer: str, product: str) -> Dict[str, Any]:
        return await self.run(customer, product, dry_run=True)

    async def list_templates(self) -> List[str]:
        resp = await self._request(
            "GET", "/templates", headers=_auth_headers(self._token)
        )
        body = resp.json()
        return list(body.get("templates", []) if isinstance(body, dict) else [])

    async def get_template(self, product: str) -> Optional[Dict[str, Any]]:
        try:
            resp = await self._request(
                "GET", f"/templates/{product}", headers=_auth_headers(self._token)
            )
        except SOSClientError as exc:
            if exc.status_code == 404:
                return None
            raise
        body = resp.json()
        return body if isinstance(body, dict) else None

    async def trigger_pulse(self, tenant: str, project: str) -> Dict[str, Any]:
        resp = await self._request(
            "POST",
            "/pulse/trigger",
            json={"tenant": tenant, "project": project},
            headers=_auth_headers(self._token),
        )
        return resp.json()


__all__ = ["OperationsClient", "AsyncOperationsClient"]
