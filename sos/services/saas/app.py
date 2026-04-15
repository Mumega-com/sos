from __future__ import annotations

import logging
import os
from typing import Optional

from fastapi import FastAPI, HTTPException

from sos.services.saas.models import TenantCreate, TenantStatus, TenantUpdate
from sos.services.saas.registry import TenantRegistry

log = logging.getLogger("sos.saas")

app = FastAPI(title="Mumega SaaS Service", version="0.1.0")
registry = TenantRegistry()


@app.get("/health")
def health():
    return {"status": "ok", "service": "saas", "tenants": len(registry.list())}


@app.post("/tenants")
async def create_tenant(req: TenantCreate):
    existing = registry.get(req.slug)
    if existing:
        raise HTTPException(409, f"Tenant {req.slug} already exists")
    tenant = registry.create(req)
    # TODO: trigger async provisioning (bus token, squad, mirror scope)
    return tenant.model_dump()


@app.get("/tenants")
def list_tenants(status: Optional[str] = None):
    tenants = registry.list(status=status)
    return {"tenants": [t.model_dump() for t in tenants], "count": len(tenants)}


@app.get("/tenants/{slug}")
def get_tenant(slug: str):
    tenant = registry.get(slug)
    if not tenant:
        raise HTTPException(404, f"Tenant {slug} not found")
    return tenant.model_dump()


@app.put("/tenants/{slug}")
def update_tenant(slug: str, req: TenantUpdate):
    tenant = registry.update(slug, req)
    if not tenant:
        raise HTTPException(404, f"Tenant {slug} not found")
    return tenant.model_dump()


@app.post("/tenants/{slug}/activate")
def activate_tenant(slug: str, squad_id: str, bus_token: str):
    tenant = registry.activate(slug, squad_id, bus_token)
    if not tenant:
        raise HTTPException(404, f"Tenant {slug} not found")
    return tenant.model_dump()


@app.post("/tenants/{slug}/suspend")
def suspend_tenant(slug: str):
    tenant = registry.update(slug, TenantUpdate(status=TenantStatus.SUSPENDED))
    if not tenant:
        raise HTTPException(404, f"Tenant {slug} not found")
    return tenant.model_dump()


@app.get("/resolve/{hostname}")
def resolve_hostname(hostname: str):
    """Resolve a hostname to a tenant -- used by Inkwell Worker for multi-tenant routing."""
    tenant = registry.resolve_domain(hostname)
    if not tenant:
        raise HTTPException(404, f"No tenant found for {hostname}")
    return tenant.model_dump()


if __name__ == "__main__":
    import uvicorn

    port = int(os.environ.get("SOS_SAAS_PORT", "8075"))
    uvicorn.run(app, host="0.0.0.0", port=port)
