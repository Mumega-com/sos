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

v0.5.1: Replaced inline `_verify_bearer` / `_check_tenant_scope` /
`_require_system_or_admin` with a single ``sos.kernel.policy.gate.can_execute``
call per route. This is the proof-of-concept migration for the unified
policy gate — see ``docs/kernel/policy.md``. Every authenticated route now
writes exactly one ``AuditEventKind.POLICY_DECISION`` event.
"""

from __future__ import annotations

import os
import time
from typing import Any, Dict, Optional

from fastapi import FastAPI, Header, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

from sos import __version__
from sos.contracts.policy import PolicyDecision
from sos.kernel.health import health_response
from sos.kernel.policy.gate import can_execute
from sos.kernel.telemetry import init_tracing, instrument_fastapi
from sos.observability.logging import get_logger

SERVICE_NAME = "integrations"
DEFAULT_PORT = 6066
_START_TIME = time.time()

log = get_logger(SERVICE_NAME, min_level=os.getenv("SOS_LOG_LEVEL", "info"))

init_tracing("integrations")

app = FastAPI(title="SOS Integrations Service", version=__version__)
instrument_fastapi(app)

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
# Gate helper — turn a PolicyDecision into the appropriate HTTP response
# ---------------------------------------------------------------------------


def _raise_on_deny(decision: PolicyDecision, *, require_system: bool = False) -> None:
    """Map a gate decision to 401/403 if denied.

    When ``require_system`` is True, also enforce that the successful
    decision came via system/admin scope — the gate allows tenant-scoped
    callers into their own tenant, but OAuth callbacks are only meaningful
    from MCP's system token.
    """
    if not decision.allowed:
        reason = decision.reason or "unauthorized"
        if "bearer" in reason.lower() or "auth" in reason.lower():
            raise HTTPException(status_code=401, detail=reason)
        raise HTTPException(status_code=403, detail=reason)

    if require_system:
        pillars = set(decision.pillars_passed)
        # system/admin callers never get 'tenant_scope' added because the
        # gate short-circuits with 'system/admin scope' reason. Check that.
        if "system/admin" not in decision.reason:
            raise HTTPException(
                status_code=403,
                detail="oauth callbacks require system or admin scope",
            )


async def _emit_integrations_deny(
    *,
    action: str,
    target: str,
    reason: str,
    tenant: str,
) -> None:
    """Audit a pre-gate rejection so it hits the unified spine.

    Integrations bails with 401 on missing bearer *before* ``can_execute``
    runs — without an anonymous bearer the gate would silently fall through
    to allow. This helper closes that audit gap, mirroring the v0.5.6.1
    identity fix. Never raises.
    """
    try:
        from sos.contracts.audit import AuditDecision, AuditEventKind
        from sos.kernel.audit import append_event, new_event

        await append_event(
            new_event(
                agent="anonymous",
                tenant=tenant,
                kind=AuditEventKind.POLICY_DECISION,
                action=action,
                target=target,
                decision=AuditDecision.DENY,
                reason=reason,
                policy_tier="integrations_pregate",
            )
        )
    except Exception:
        pass


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
    if not authorization:
        await _emit_integrations_deny(
            action="oauth_credentials_read",
            target=f"{tenant}/{provider}",
            reason="missing bearer token",
            tenant=tenant,
        )
        raise HTTPException(status_code=401, detail="missing bearer token")

    decision = await can_execute(
        action="oauth_credentials_read",
        resource=f"{tenant}/{provider}",
        tenant=tenant,
        authorization=authorization,
    )
    _raise_on_deny(decision)

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
    if not authorization:
        await _emit_integrations_deny(
            action="oauth_ghl_callback",
            target=tenant,
            reason="missing bearer token",
            tenant=tenant,
        )
        raise HTTPException(status_code=401, detail="missing bearer token")

    decision = await can_execute(
        action="oauth_ghl_callback",
        resource=tenant,
        tenant=tenant,
        authorization=authorization,
    )
    _raise_on_deny(decision, require_system=True)

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
    if not authorization:
        await _emit_integrations_deny(
            action="oauth_google_callback",
            target=f"{tenant}/{req.service}",
            reason="missing bearer token",
            tenant=tenant,
        )
        raise HTTPException(status_code=401, detail="missing bearer token")

    if req.service not in _GOOGLE_SERVICES:
        raise HTTPException(
            status_code=400,
            detail=f"unknown google service: {req.service}",
        )

    decision = await can_execute(
        action="oauth_google_callback",
        resource=f"{tenant}/{req.service}",
        tenant=tenant,
        authorization=authorization,
    )
    _raise_on_deny(decision, require_system=True)

    from sos.services.integrations.oauth import TenantIntegrations

    integrations = TenantIntegrations(tenant)
    return await integrations.handle_google_callback(req.code, req.service)  # type: ignore[arg-type]
