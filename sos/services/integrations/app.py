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

Auth scope: the Bearer token's `project` must match the `tenant` path param,
OR the token must be system/admin scope. Otherwise 403.
"""

from __future__ import annotations

import os
import time
from typing import Any, Dict, Optional

from fastapi import FastAPI, Header, HTTPException
from fastapi.middleware.cors import CORSMiddleware

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
