"""SOS Integrations Service — HTTP surface for per-tenant OAuth credentials.

Exposes the `TenantIntegrations` credential store over HTTP so sibling services
(analytics, autonomy, outreach, ...) can fetch provider tokens without
importing `sos.services.integrations.oauth` directly. That direct-import path
is the P0-06 violation closed by v0.4.5 Wave 3.

Endpoints:
- `GET /health` — canonical SOS health response.
- `GET /oauth/credentials/{tenant}/{provider}` — Bearer-auth'd read-through
  to `TenantIntegrations(tenant).get_credentials(provider)`. Returns 404 if
  the tenant has no credentials for that provider.
- `POST /oauth/ghl/callback/{tenant}` — complete a GHL OAuth round-trip.
  System/admin scope only (MCP proxies external provider callbacks here).
- `POST /oauth/google/callback/{tenant}` — complete a Google OAuth round-trip.
  System/admin scope only.

Auth scope: the Bearer token's `project` must match the `tenant` path param,
OR the token must be system/admin scope. Otherwise 403.
"""

from __future__ import annotations

import os
import time
from typing import Any, Dict, Optional

from fastapi import FastAPI, Header, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

from sos import __version__
from sos.kernel.auth import verify_bearer as _auth_verify_bearer
from sos.kernel.health import health_response
from sos.observability.logging import get_logger

SERVICE_NAME = "integrations"
DEFAULT_PORT = 6066
_START_TIME = time.time()

log = get_logger(SERVICE_NAME, min_level=os.getenv("SOS_LOG_LEVEL", "info"))

app = FastAPI(title="SOS Integrations Service", version=__version__)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.on_event("startup")
async def _startup() -> None:
    """Announce presence to the SOS service registry."""
    try:
        from sos.services.bus.discovery import register_service

        await register_service(SERVICE_NAME, DEFAULT_PORT)
    except Exception as exc:  # pragma: no cover — discovery is best-effort
        log.warning("integrations discovery registration failed", error=str(exc))


# ---------------------------------------------------------------------------
# Auth helper — same pattern as economy/app.py::_verify_bearer
# ---------------------------------------------------------------------------


def _verify_bearer(authorization: Optional[str]) -> Dict[str, Any]:
    """Return a token record dict or raise 401 on failure."""
    ctx = _auth_verify_bearer(authorization)
    if ctx is None:
        if not authorization or not authorization.lower().startswith("bearer "):
            raise HTTPException(status_code=401, detail="missing bearer token")
        raise HTTPException(status_code=401, detail="invalid or inactive token")
    return {
        "project": ctx.project,
        "tenant_slug": ctx.tenant_slug,
        "agent": ctx.agent,
        "label": ctx.label,
        "is_system": ctx.is_system,
        "is_admin": ctx.is_admin,
        "active": True,
    }


def _check_tenant_scope(entry: Dict[str, Any], tenant: str) -> None:
    """Raise 403 unless the token is scoped to *tenant* or is system/admin."""
    if entry.get("is_system") or entry.get("is_admin"):
        return
    scope = entry.get("project") or entry.get("tenant_slug")
    if scope is None:
        # Non-system token with no scope — reject to avoid accidental tenant cross.
        raise HTTPException(
            status_code=403,
            detail="token has no tenant scope",
        )
    if scope != tenant:
        raise HTTPException(
            status_code=403,
            detail=f"token is scoped to tenant '{scope}', cannot read credentials for '{tenant}'",
        )


def _require_system_or_admin(entry: Dict[str, Any]) -> None:
    """Raise 403 unless the caller holds a system or admin token.

    OAuth callbacks arrive at MCP from external providers; MCP proxies them
    here with a system token. No tenant-scoped caller should be completing
    a callback on another tenant's behalf.
    """
    if entry.get("is_system") or entry.get("is_admin"):
        return
    raise HTTPException(
        status_code=403,
        detail="oauth callbacks require system or admin scope",
    )


class GhlCallbackRequest(BaseModel):
    code: str


class GoogleCallbackRequest(BaseModel):
    code: str
    service: str  # "analytics" | "search_console" | "ads"


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------


@app.get("/health")
async def health() -> Dict[str, Any]:
    return health_response(SERVICE_NAME, _START_TIME)


@app.get("/oauth/credentials/{tenant}/{provider}")
async def get_oauth_credentials(
    tenant: str,
    provider: str,
    authorization: Optional[str] = Header(None),
) -> Dict[str, Any]:
    """Return stored credentials for (tenant, provider) or 404.

    Local import of TenantIntegrations is intentional: this service IS the
    owner of that class, so the boundary rule does not apply.
    """
    entry = _verify_bearer(authorization)
    _check_tenant_scope(entry, tenant)

    from sos.services.integrations.oauth import TenantIntegrations

    integrations = TenantIntegrations(tenant)
    creds = integrations.get_credentials(provider)
    if creds is None:
        raise HTTPException(
            status_code=404,
            detail=f"no credentials for {tenant}/{provider}",
        )
    return creds


_GOOGLE_SERVICES = ("analytics", "search_console", "ads")


@app.post("/oauth/ghl/callback/{tenant}")
async def post_ghl_callback(
    tenant: str,
    req: GhlCallbackRequest,
    authorization: Optional[str] = Header(None),
) -> Dict[str, Any]:
    """Complete a GHL OAuth round-trip for *tenant*.

    MCP proxies external GHL redirects here with a system token. Returns
    the stored credentials dict.
    """
    entry = _verify_bearer(authorization)
    _require_system_or_admin(entry)

    from sos.services.integrations.oauth import TenantIntegrations

    integrations = TenantIntegrations(tenant)
    return await integrations.handle_ghl_callback(req.code)


@app.post("/oauth/google/callback/{tenant}")
async def post_google_callback(
    tenant: str,
    req: GoogleCallbackRequest,
    authorization: Optional[str] = Header(None),
) -> Dict[str, Any]:
    """Complete a Google OAuth round-trip for *tenant* + *service*.

    MCP proxies external Google redirects here with a system token.
    ``service`` must be one of analytics, search_console, ads.
    """
    entry = _verify_bearer(authorization)
    _require_system_or_admin(entry)

    if req.service not in _GOOGLE_SERVICES:
        raise HTTPException(
            status_code=400,
            detail=f"unknown google service: {req.service}",
        )

    from sos.services.integrations.oauth import TenantIntegrations

    integrations = TenantIntegrations(tenant)
    return await integrations.handle_google_callback(req.code, req.service)  # type: ignore[arg-type]
