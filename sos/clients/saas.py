"""HTTP client for the SaaS service.

Used by:
- billing service (Stripe webhook) — tenant CRUD / lifecycle.
- mcp.sos_mcp_sse — rate-limit check, audit-log writes, marketplace
  browse/subscribe/earnings, notification preferences.

Keeping this client rich lets callers stay R1/R2-clean — they never
import sos.services.saas directly.

The SaaS admin API is admin-key-gated (SOS_SAAS_ADMIN_KEY /
MUMEGA_MASTER_KEY). The client reads the same env vars the server does
and falls back to SOS_SYSTEM_TOKEN.
"""
from __future__ import annotations

import os
from typing import Any, Dict, List, Optional

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

    # --- MCP gateway surface ---------------------------------------------

    def check_rate_limit(
        self, tenant: str, plan: Optional[str] = None
    ) -> Dict[str, Any]:
        """POST /rate-limit/check — returns {allowed, remaining}."""
        resp = self._request(
            "POST", "/rate-limit/check", json={"tenant": tenant, "plan": plan}
        )
        return resp.json()

    def log_tool_call(
        self,
        tenant: str,
        tool: str,
        actor: str = "",
        ip: str = "",
        details: Optional[Dict[str, Any]] = None,
    ) -> None:
        """POST /audit/tool-call — fire-and-forget from caller's POV."""
        self._request(
            "POST",
            "/audit/tool-call",
            json={
                "tenant": tenant,
                "tool": tool,
                "actor": actor,
                "ip": ip,
                "details": details,
            },
        )

    def browse_marketplace(
        self,
        category: Optional[str] = None,
        query: Optional[str] = None,
        limit: int = 20,
    ) -> List[Dict[str, Any]]:
        params: Dict[str, Any] = {"limit": limit}
        if category is not None:
            params["category"] = category
        if query is not None:
            params["query"] = query
        resp = self._request("GET", "/marketplace/listings", params=params)
        return resp.json().get("listings", [])

    def subscribe_marketplace(
        self, tenant: str, listing_id: str
    ) -> Dict[str, Any]:
        resp = self._request(
            "POST",
            "/marketplace/subscriptions",
            json={"tenant": tenant, "listing_id": listing_id},
        )
        return resp.json()

    def my_subscriptions(self, tenant: str) -> List[Dict[str, Any]]:
        resp = self._request(
            "GET", "/marketplace/subscriptions", params={"tenant": tenant}
        )
        return resp.json().get("subscriptions", [])

    def create_listing(
        self,
        seller_tenant: str,
        title: str,
        description: str,
        category: str,
        price_cents: int,
        listing_type: str = "squad",
        price_model: str = "monthly",
        tags: Optional[List[str]] = None,
    ) -> Dict[str, Any]:
        body = {
            "seller_tenant": seller_tenant,
            "title": title,
            "description": description,
            "category": category,
            "listing_type": listing_type,
            "price_cents": price_cents,
            "price_model": price_model,
            "tags": tags or [],
        }
        resp = self._request("POST", "/marketplace/listings", json=body)
        return resp.json()

    def my_earnings(self, tenant: str) -> Dict[str, Any]:
        resp = self._request(
            "GET", "/marketplace/earnings", params={"tenant": tenant}
        )
        return resp.json()

    def get_notification_preferences(self, slug: str) -> Dict[str, Any]:
        resp = self._request("GET", f"/tenants/{slug}/notifications")
        return resp.json()

    def set_notification_preferences(
        self, slug: str, prefs: Dict[str, Any]
    ) -> Dict[str, Any]:
        resp = self._request(
            "POST", f"/tenants/{slug}/notifications", json=prefs
        )
        return resp.json()


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

    # --- MCP gateway surface ---------------------------------------------

    async def check_rate_limit(
        self, tenant: str, plan: Optional[str] = None
    ) -> Dict[str, Any]:
        resp = await self._request(
            "POST", "/rate-limit/check", json={"tenant": tenant, "plan": plan}
        )
        return resp.json()

    async def log_tool_call(
        self,
        tenant: str,
        tool: str,
        actor: str = "",
        ip: str = "",
        details: Optional[Dict[str, Any]] = None,
    ) -> None:
        """Fire-and-forget audit entry. Callers typically wrap in
        ``asyncio.create_task(...)`` so the request path never waits.
        """
        await self._request(
            "POST",
            "/audit/tool-call",
            json={
                "tenant": tenant,
                "tool": tool,
                "actor": actor,
                "ip": ip,
                "details": details,
            },
        )

    async def browse_marketplace(
        self,
        category: Optional[str] = None,
        query: Optional[str] = None,
        limit: int = 20,
    ) -> List[Dict[str, Any]]:
        params: Dict[str, Any] = {"limit": limit}
        if category is not None:
            params["category"] = category
        if query is not None:
            params["query"] = query
        resp = await self._request(
            "GET", "/marketplace/listings", params=params
        )
        return resp.json().get("listings", [])

    async def subscribe_marketplace(
        self, tenant: str, listing_id: str
    ) -> Dict[str, Any]:
        resp = await self._request(
            "POST",
            "/marketplace/subscriptions",
            json={"tenant": tenant, "listing_id": listing_id},
        )
        return resp.json()

    async def my_subscriptions(self, tenant: str) -> List[Dict[str, Any]]:
        resp = await self._request(
            "GET", "/marketplace/subscriptions", params={"tenant": tenant}
        )
        return resp.json().get("subscriptions", [])

    async def create_listing(
        self,
        seller_tenant: str,
        title: str,
        description: str,
        category: str,
        price_cents: int,
        listing_type: str = "squad",
        price_model: str = "monthly",
        tags: Optional[List[str]] = None,
    ) -> Dict[str, Any]:
        body = {
            "seller_tenant": seller_tenant,
            "title": title,
            "description": description,
            "category": category,
            "listing_type": listing_type,
            "price_cents": price_cents,
            "price_model": price_model,
            "tags": tags or [],
        }
        resp = await self._request("POST", "/marketplace/listings", json=body)
        return resp.json()

    async def my_earnings(self, tenant: str) -> Dict[str, Any]:
        resp = await self._request(
            "GET", "/marketplace/earnings", params={"tenant": tenant}
        )
        return resp.json()

    async def get_notification_preferences(self, slug: str) -> Dict[str, Any]:
        resp = await self._request("GET", f"/tenants/{slug}/notifications")
        return resp.json()

    async def set_notification_preferences(
        self, slug: str, prefs: Dict[str, Any]
    ) -> Dict[str, Any]:
        resp = await self._request(
            "POST", f"/tenants/{slug}/notifications", json=prefs
        )
        return resp.json()
