"""HTTP client for the SaaS service — tenant CRUD + lifecycle.

Used by the billing service (Stripe webhook) to register, activate, and
cancel tenants without importing sos.services.saas directly, preserving
the R1 contract (services don't import other services).

The SaaS admin API is admin-key-gated (SOS_SAAS_ADMIN_KEY /
MUMEGA_MASTER_KEY). The client reads the same env vars the server does
and falls back to SOS_SYSTEM_TOKEN.
"""
from __future__ import annotations

import os
from typing import Any, Dict, Optional

from sos.clients.base import AsyncBaseHTTPClient, BaseHTTPClient

_DEFAULT_BASE = "http://localhost:8075"


def _admin_headers(token: Optional[str]) -> Dict[str, str]:
    key = (
        token
        or os.environ.get("SOS_SAAS_ADMIN_KEY")
        or os.environ.get("MUMEGA_MASTER_KEY")
        or os.environ.get("SOS_SYSTEM_TOKEN")
    )
    return {"Authorization": f"Bearer {key}"} if key else {}


class SaasClient(BaseHTTPClient):
    """Sync client for billing webhook paths (stripe → saas.tenant CRUD)."""

    def __init__(
        self,
        base_url: str = _DEFAULT_BASE,
        token: Optional[str] = None,
        **kwargs: Any,
    ):
        headers = kwargs.pop("headers", None) or {}
        for k, v in _admin_headers(token).items():
            headers.setdefault(k, v)
        super().__init__(base_url, headers=headers, **kwargs)

    def health(self) -> Dict[str, Any]:
        return self._request("GET", "/health").json()

    def create_tenant(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        """POST /tenants — register a new tenant.

        ``payload`` matches ``sos.contracts.tenant.TenantCreate`` (slug,
        label, email, plan, optionally domain/industry/tagline).
        """
        return self._request("POST", "/tenants", json=payload).json()

    def activate_tenant(self, slug: str, squad_id: str, bus_token: str) -> Dict[str, Any]:
        """POST /tenants/{slug}/activate — flip status to active after
        provisioning finishes and bus token/squad are wired.
        """
        body = {"squad_id": squad_id, "bus_token": bus_token}
        return self._request("POST", f"/tenants/{slug}/activate", json=body).json()

    def update_tenant(self, slug: str, update: Dict[str, Any]) -> Dict[str, Any]:
        """PUT /tenants/{slug} — partial update (status, domain, plan, etc.)."""
        return self._request("PUT", f"/tenants/{slug}", json=update).json()

    def cancel_tenant(self, slug: str) -> Dict[str, Any]:
        """Convenience: set status to cancelled. Used by the Stripe
        subscription.deleted webhook.
        """
        return self.update_tenant(slug, {"status": "cancelled"})


class AsyncSaasClient(AsyncBaseHTTPClient):
    """Async variant — for callers already inside async request handlers."""

    def __init__(
        self,
        base_url: str = _DEFAULT_BASE,
        token: Optional[str] = None,
        **kwargs: Any,
    ):
        headers = kwargs.pop("headers", None) or {}
        for k, v in _admin_headers(token).items():
            headers.setdefault(k, v)
        super().__init__(base_url, headers=headers, **kwargs)

    async def health(self) -> Dict[str, Any]:
        resp = await self._request("GET", "/health")
        return resp.json()

    async def create_tenant(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        resp = await self._request("POST", "/tenants", json=payload)
        return resp.json()

    async def activate_tenant(
        self, slug: str, squad_id: str, bus_token: str
    ) -> Dict[str, Any]:
        body = {"squad_id": squad_id, "bus_token": bus_token}
        resp = await self._request("POST", f"/tenants/{slug}/activate", json=body)
        return resp.json()

    async def update_tenant(self, slug: str, update: Dict[str, Any]) -> Dict[str, Any]:
        resp = await self._request("PUT", f"/tenants/{slug}", json=update)
        return resp.json()

    async def cancel_tenant(self, slug: str) -> Dict[str, Any]:
        return await self.update_tenant(slug, {"status": "cancelled"})
