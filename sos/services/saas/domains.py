"""Custom domain provisioning via Cloudflare API.

When a tenant sets a custom domain:
1. Add the domain to Cloudflare (custom hostname)
2. Store the mapping in the tenant registry
3. Set the KV domain mapping for edge resolution
"""
from __future__ import annotations

import logging
import os
from typing import Optional

import httpx

from sos.services.saas.registry import TenantRegistry

log = logging.getLogger("sos.saas.domains")

CF_ACCOUNT_ID = os.environ.get("CF_ACCOUNT_ID", "")
CF_API_TOKEN = os.environ.get("CF_API_TOKEN", "")
CF_ZONE_ID = os.environ.get("CF_ZONE_ID", "")  # mumega.com zone


class DomainManager:
    def __init__(self, registry: Optional[TenantRegistry] = None) -> None:
        self.registry = registry or TenantRegistry()

    async def provision_custom_domain(self, tenant_slug: str, domain: str) -> dict:
        """Provision a custom domain for a tenant."""
        tenant = self.registry.get(tenant_slug)
        if not tenant:
            return {"error": f"Tenant {tenant_slug} not found", "success": False}

        result: dict = {"domain": domain, "tenant": tenant_slug, "steps": []}

        # 1. Add custom hostname to Cloudflare (for SSL)
        if CF_API_TOKEN and CF_ZONE_ID:
            cf_result = await self._add_cloudflare_hostname(domain)
            result["steps"].append({"action": "cloudflare_hostname", "result": cf_result})
        else:
            result["steps"].append({"action": "cloudflare_hostname", "result": "skipped (no credentials)"})

        # 2. Update tenant registry
        from sos.services.saas.models import TenantUpdate

        self.registry.update(tenant_slug, TenantUpdate(domain=domain))
        result["steps"].append({"action": "registry_update", "result": "ok"})

        # 3. Set KV domain mapping (for edge resolution)
        # This is done by the SaaS service calling the Worker API
        # or by setting it directly in KV via Cloudflare API
        kv_result = await self._set_kv_domain_mapping(domain, tenant_slug)
        result["steps"].append({"action": "kv_mapping", "result": kv_result})

        result["success"] = True
        result["dns_instructions"] = {
            "type": "CNAME",
            "name": domain,
            "value": "mumega-sites.pages.dev",
            "note": "Customer must add this DNS record to their domain provider",
        }

        log.info("Custom domain %s provisioned for %s", domain, tenant_slug)
        return result

    async def _add_cloudflare_hostname(self, domain: str) -> str:
        """Add a custom hostname to Cloudflare for SaaS SSL."""
        if not CF_API_TOKEN or not CF_ZONE_ID:
            return "skipped"
        try:
            async with httpx.AsyncClient(timeout=15) as client:
                resp = await client.post(
                    f"https://api.cloudflare.com/client/v4/zones/{CF_ZONE_ID}/custom_hostnames",
                    headers={"Authorization": f"Bearer {CF_API_TOKEN}", "Content-Type": "application/json"},
                    json={
                        "hostname": domain,
                        "ssl": {"method": "http", "type": "dv", "settings": {"min_tls_version": "1.2"}},
                    },
                )
                data = resp.json()
                if data.get("success"):
                    return "ok"
                return f"error: {data.get('errors', [])}"
        except Exception as exc:
            return f"error: {exc}"

    async def _set_kv_domain_mapping(self, domain: str, tenant_slug: str) -> str:
        """Set domain -> tenant mapping in Cloudflare KV for edge resolution."""
        kv_namespace_id = os.environ.get("CF_KV_SESSIONS_ID", "")
        if not CF_API_TOKEN or not CF_ACCOUNT_ID or not kv_namespace_id:
            return "skipped (no KV credentials)"
        try:
            async with httpx.AsyncClient(timeout=15) as client:
                resp = await client.put(
                    f"https://api.cloudflare.com/client/v4/accounts/{CF_ACCOUNT_ID}/storage/kv/namespaces/{kv_namespace_id}/values/domain:{domain}",
                    headers={"Authorization": f"Bearer {CF_API_TOKEN}"},
                    content=tenant_slug,
                )
                return "ok" if resp.status_code == 200 else f"error: {resp.status_code}"
        except Exception as exc:
            return f"error: {exc}"

    async def remove_custom_domain(self, tenant_slug: str) -> dict:
        """Remove custom domain from a tenant."""
        tenant = self.registry.get(tenant_slug)
        if not tenant or not tenant.domain:
            return {"error": "No custom domain set", "success": False}

        domain = tenant.domain
        from sos.services.saas.models import TenantUpdate

        self.registry.update(tenant_slug, TenantUpdate(domain=None))

        # Remove KV mapping
        await self._remove_kv_mapping(domain)

        return {"success": True, "removed": domain}

    async def _remove_kv_mapping(self, domain: str) -> None:
        kv_namespace_id = os.environ.get("CF_KV_SESSIONS_ID", "")
        if not CF_API_TOKEN or not CF_ACCOUNT_ID or not kv_namespace_id:
            return
        try:
            async with httpx.AsyncClient(timeout=15) as client:
                await client.delete(
                    f"https://api.cloudflare.com/client/v4/accounts/{CF_ACCOUNT_ID}/storage/kv/namespaces/{kv_namespace_id}/values/domain:{domain}",
                    headers={"Authorization": f"Bearer {CF_API_TOKEN}"},
                )
        except Exception:
            pass
