from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import os
import re
import secrets
import sqlite3
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

import httpx
from fastapi import Depends, FastAPI, Header, HTTPException, Query, Request
from fastapi.responses import HTMLResponse
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pydantic import BaseModel

from sos.services.saas.audit import get_audit, log_admin, log_tool_call as _audit_log_tool_call
from sos.services.saas.billing import SaaSBilling
from sos.services.saas.logging_config import setup_logging
from sos.contracts.tenant import TenantCreate, TenantPlan, TenantStatus, TenantUpdate
from sos.services.saas.marketplace import Marketplace
from sos.services.saas.notifications import get_router as get_notification_router
from sos.services.saas.pairing import router as pairing_router
from sos.services.saas.sso_routes import router as sso_router
from sos.services.saas.rate_limiter import check_rate_limit as _check_rate_limit
from sos.services.saas.registry import TenantRegistry
from sos.kernel.health import health_response
from sos.kernel.telemetry import init_tracing, instrument_fastapi

# Configure structured logging
setup_logging("saas")
# OTEL tracing: idempotent, no-ops cleanly if packages not installed.
# Must run before FastAPI/httpx auto-instrumentation attaches.
init_tracing("saas")

log = logging.getLogger("sos.saas")

app = FastAPI(title="Mumega SaaS Service", version="0.1.0")
instrument_fastapi(app)
app.include_router(pairing_router)
app.include_router(sso_router)
_START_TIME = time.time()
registry = TenantRegistry()
billing = SaaSBilling(registry)

# --- Auth ---

_bearer = HTTPBearer(auto_error=False)

def _get_admin_key() -> str:
    """Return the expected admin API key from environment."""
    key = os.environ.get("SOS_SAAS_ADMIN_KEY") or os.environ.get("MUMEGA_MASTER_KEY", "")
    if not key:
        log.warning("No admin key configured (SOS_SAAS_ADMIN_KEY / MUMEGA_MASTER_KEY)")
    return key

def require_admin(credentials: HTTPAuthorizationCredentials | None = Depends(_bearer)) -> None:
    """FastAPI dependency — enforce Bearer token on admin endpoints."""
    expected = _get_admin_key()
    if not expected:
        raise HTTPException(503, detail="Admin key not configured on server")
    if credentials is None or credentials.credentials != expected:
        raise HTTPException(401, detail="unauthorized")


_TOKENS_PATH = Path.home() / "SOS" / "sos" / "bus" / "tokens.json"


def require_customer(credentials: HTTPAuthorizationCredentials | None = Depends(_bearer)) -> str:
    """FastAPI dependency — validate tenant MCP token, return tenant_slug.

    Accepts: Authorization: Bearer sk-{slug}-{hex}
    Strategy:
      1. Hash the token and look it up in tokens.json (scope=customer).
         The project field gives the slug candidate.
      2. If registry.get(slug_candidate) exists, return it.
      3. Otherwise fall back: find tenant whose bus_token == submitted token directly.
         This handles cases where tokens.json project != tenant slug.
    """
    if credentials is None:
        raise HTTPException(401, detail="unauthorized")

    token = credentials.credentials
    token_hash = hashlib.sha256(token.encode()).hexdigest()

    if not _TOKENS_PATH.exists():
        raise HTTPException(401, detail="unauthorized")

    try:
        tokens: list[dict] = json.loads(_TOKENS_PATH.read_text())
    except Exception:
        raise HTTPException(401, detail="unauthorized")

    slug_candidate: str | None = None
    for entry in tokens:
        if (
            entry.get("token_hash") == token_hash
            and entry.get("active", True)
            and entry.get("scope") == "customer"
        ):
            slug_candidate = entry.get("project")
            break

    if slug_candidate is None:
        raise HTTPException(401, detail="unauthorized")

    # Fast path: project field matches tenant slug directly
    if registry.get(slug_candidate):
        return slug_candidate

    # Fallback: find tenant whose bus_token matches the submitted token
    # (handles tokens.json project field being the full name slug, not the short slug)
    try:
        conn = sqlite3.connect(
            f"file:{Path.home() / '.sos' / 'data' / 'squads.db'}?mode=ro", uri=True
        )
        conn.row_factory = sqlite3.Row
        row = conn.execute(
            "SELECT slug FROM tenants WHERE bus_token = ?", (token,)
        ).fetchone()
        conn.close()
        if row:
            return str(row["slug"])
    except Exception:
        pass

    raise HTTPException(401, detail="unauthorized")


# --- Audit-wrapper helpers ---


async def _emit_policy(
    *, agent: str, action: str, target: str, tenant: str,
    allowed: bool, reason: str, tier: str,
) -> None:
    """Emit one POLICY_DECISION to the audit spine. Never raises."""
    from sos.kernel.audit import append_event, new_event
    from sos.contracts.audit import AuditDecision, AuditEventKind
    try:
        await append_event(new_event(
            agent=agent,
            tenant=tenant,
            kind=AuditEventKind.POLICY_DECISION,
            action=action,
            target=target,
            decision=AuditDecision.ALLOW if allowed else AuditDecision.DENY,
            reason=reason,
            policy_tier=tier,
        ))
    except Exception as exc:
        log.debug("audit emit failed (non-fatal): %s", exc)


def audited_admin(action: str):
    """Factory: FastAPI dep that enforces admin auth AND audits the decision."""
    async def _dep(credentials: HTTPAuthorizationCredentials | None = Depends(_bearer)) -> None:
        try:
            require_admin(credentials)
        except HTTPException as exc:
            await _emit_policy(
                agent="anonymous", action=action, target="admin", tenant="mumega",
                allowed=False, reason=str(exc.detail), tier="saas_admin",
            )
            raise
        await _emit_policy(
            agent="admin", action=action, target="admin", tenant="mumega",
            allowed=True, reason="master_key", tier="saas_admin",
        )
    return _dep


def audited_customer(action: str):
    """Factory: FastAPI dep that enforces customer auth, audits, returns tenant_slug."""
    async def _dep(credentials: HTTPAuthorizationCredentials | None = Depends(_bearer)) -> str:
        try:
            tenant_slug = require_customer(credentials)
        except HTTPException as exc:
            await _emit_policy(
                agent="anonymous", action=action, target="customer", tenant="unknown",
                allowed=False, reason=str(exc.detail), tier="saas_customer",
            )
            raise
        await _emit_policy(
            agent="customer", action=action, target=tenant_slug, tenant=tenant_slug,
            allowed=True, reason="tenant_token", tier="saas_customer",
        )
        return tenant_slug
    return _dep


@app.get("/", response_class=HTMLResponse)
def landing():
    """Self-serve signup page."""
    from sos.services.saas.signup_page import SIGNUP_HTML

    return SIGNUP_HTML


@app.get("/health")
def health():
    return health_response("saas", _START_TIME)


@app.post("/tenants")
async def create_tenant(
    req: TenantCreate,
    _: None = Depends(audited_admin("saas:tenant_create")),
    idempotency_key: Optional[str] = Header(default=None, alias="Idempotency-Key"),
):
    from sos.kernel.idempotency import with_idempotency

    async def _do() -> dict:
        existing = registry.get(req.slug)
        if existing:
            raise HTTPException(409, f"Tenant {req.slug} already exists")
        tenant = registry.create(req)
        log_admin("tenant.created", tenant=req.slug, details={"plan": req.plan.value if hasattr(req.plan, "value") else str(req.plan)})
        # TODO: trigger async provisioning (bus token, squad, mirror scope)
        return tenant.model_dump()

    return await with_idempotency(
        key=idempotency_key,
        tenant=req.slug,
        request_body=req.model_dump(),
        fn=_do,
    )


@app.get("/tenants")
def list_tenants(status: Optional[str] = None, _: None = Depends(audited_admin("saas:tenants_list"))):
    tenants = registry.list(status=status)
    return {"tenants": [t.model_dump() for t in tenants], "count": len(tenants)}


@app.get("/tenants/{slug}")
def get_tenant(slug: str, _: None = Depends(audited_admin("saas:tenant_read"))):
    tenant = registry.get(slug)
    if not tenant:
        raise HTTPException(404, f"Tenant {slug} not found")
    return tenant.model_dump()


@app.put("/tenants/{slug}")
def update_tenant(slug: str, req: TenantUpdate, _: None = Depends(audited_admin("saas:tenant_update"))):
    tenant = registry.update(slug, req)
    if not tenant:
        raise HTTPException(404, f"Tenant {slug} not found")
    return tenant.model_dump()


class ActivateRequest(BaseModel):
    squad_id: str
    bus_token: str


@app.post("/tenants/{slug}/activate")
def activate_tenant(slug: str, req: ActivateRequest, _: None = Depends(audited_admin("saas:tenant_activate"))):
    tenant = registry.activate(slug, req.squad_id, req.bus_token)
    if not tenant:
        raise HTTPException(404, f"Tenant {slug} not found")
    log_admin("tenant.activated", tenant=slug)
    return tenant.model_dump()


@app.post("/tenants/{slug}/suspend")
def suspend_tenant(slug: str, _: None = Depends(audited_admin("saas:tenant_suspend"))):
    tenant = registry.update(slug, TenantUpdate(status=TenantStatus.SUSPENDED))
    if not tenant:
        raise HTTPException(404, f"Tenant {slug} not found")
    log_admin("tenant.suspended", tenant=slug)
    return tenant.model_dump()


# --- Multi-seat token management ---


@app.post("/tenants/{slug}/seats")
async def create_seat(
    slug: str,
    req: CreateSeatRequest,
    _: None = Depends(audited_admin("saas:seat_create")),
    idempotency_key: Optional[str] = Header(default=None, alias="Idempotency-Key"),
):
    """Create a new seat (additional MCP token) for a tenant."""
    from sos.kernel.idempotency import with_idempotency

    async def _do() -> dict:
        tenant = registry.get(slug)
        if not tenant:
            raise HTTPException(404, f"Tenant {slug} not found")

        # Check seat limits by plan
        seat_limits = {"starter": 1, "growth": 5, "scale": -1}  # -1 = unlimited
        plan_key = tenant.plan.value.lower() if hasattr(tenant.plan, "value") else str(tenant.plan).lower()
        limit = seat_limits.get(plan_key, 1)

        current_seats = _count_seats(slug)
        if limit != -1 and current_seats >= limit:
            raise HTTPException(
                403,
                f"Plan {plan_key} allows {limit} seat(s). Current: {current_seats}. Upgrade to add more.",
            )

        # Generate new token for this seat
        token = f"sk-{slug}-{secrets.token_hex(16)}"
        _register_bus_token_with_label(slug, token, req.label, role=req.role, plan=plan_key)
        log_admin("seat.created", tenant=slug, details={"label": req.label, "role": req.role})

        mcp_url = f"https://mcp.mumega.com/sse/{token}"

        return {
            "seat": req.label,
            "token": token,
            "mcp_url": mcp_url,
            "connect": {
                "claude_code": f'claude mcp add mumega --transport sse --url "{mcp_url}"',
                "claude_desktop": {"mcpServers": {"mumega": {"url": mcp_url}}},
            },
            "seats_used": current_seats + 1,
            "seats_limit": limit if limit != -1 else "unlimited",
        }

    body = {"slug": slug, "payload": req.model_dump()}
    return await with_idempotency(
        key=idempotency_key,
        tenant=slug,
        request_body=body,
        fn=_do,
    )


@app.get("/tenants/{slug}/seats")
def list_seats(slug: str, _: None = Depends(audited_admin("saas:seats_list"))):
    """List all seats (team members) for a tenant."""
    tenant = registry.get(slug)
    if not tenant:
        raise HTTPException(404, f"Tenant {slug} not found")
    seats = _get_seats(slug)
    plan_key = tenant.plan.value.lower() if hasattr(tenant.plan, "value") else str(tenant.plan).lower()
    seat_limits = {"starter": 1, "growth": 5, "scale": -1}
    limit = seat_limits.get(plan_key, 1)
    return {
        "seats": seats,
        "count": len(seats),
        "plan": plan_key,
        "limit": limit if limit != -1 else "unlimited",
    }


@app.delete("/tenants/{slug}/seats/{token_id}")
def revoke_seat(slug: str, token_id: str, _: None = Depends(audited_admin("saas:seat_revoke"))):
    """Revoke a seat by revoking its MCP token."""
    tenant = registry.get(slug)
    if not tenant:
        raise HTTPException(404, f"Tenant {slug} not found")
    _revoke_seat(slug, token_id)
    log_admin("seat.revoked", tenant=slug)
    return {"revoked": True, "token_id": token_id}


@app.get("/resolve/{hostname}")
def resolve_hostname(hostname: str):
    """Resolve a hostname to a tenant -- used by Inkwell Worker for multi-tenant routing."""
    tenant = registry.resolve_domain(hostname)
    if not tenant:
        raise HTTPException(404, f"No tenant found for {hostname}")
    return tenant.model_dump()


# --- Billing endpoints ---


@app.post("/tenants/{slug}/usage")
def record_usage(slug: str, metric: str, quantity: int, _: None = Depends(audited_admin("saas:usage_record"))):
    billing.record_usage(slug, metric, quantity)
    return {"ok": True}


@app.get("/tenants/{slug}/usage")
def get_usage(slug: str, period: Optional[str] = None, _: None = Depends(audited_admin("saas:usage_read"))):
    return billing.get_usage(slug, period)


@app.post("/tenants/{slug}/transaction")
def record_transaction(
    slug: str,
    tx_type: str,
    amount_cents: int,
    description: str = "",
    stripe_id: str = "",
    _: None = Depends(audited_admin("saas:transaction_record")),
):
    tx_id = billing.record_transaction(
        slug, tx_type, amount_cents, description, stripe_id
    )
    return {"ok": True, "transaction_id": tx_id}


@app.get("/tenants/{slug}/invoice")
def get_invoice(slug: str, _: None = Depends(audited_admin("saas:invoice_read"))):
    return billing.get_tenant_invoice(slug)


@app.get("/revenue")
def platform_revenue(period: Optional[str] = None, _: None = Depends(audited_admin("saas:revenue_read"))):
    return billing.get_revenue(period=period)


@app.get("/tenants/{slug}/audit")
def get_audit_log(slug: str, event_type: Optional[str] = None, limit: int = 100, _: None = Depends(audited_admin("saas:audit_read"))):
    """Query audit log for a tenant."""
    return {"events": get_audit().query(tenant_slug=slug, event_type=event_type, limit=limit)}


# ---------------------------------------------------------------------------
# MCP gateway surface — rate-limit, audit, marketplace.
# The MCP SSE server calls these over HTTP via SaasClient so it never
# imports sos.services.saas directly (R2 contract).
# ---------------------------------------------------------------------------


class RateLimitCheckRequest(BaseModel):
    tenant: str
    plan: Optional[str] = None


@app.post("/rate-limit/check")
def rate_limit_check(req: RateLimitCheckRequest, _: None = Depends(audited_admin("saas:rate_limit_check"))):
    """Sliding-window rate-limit check for a tenant."""
    allowed, remaining = _check_rate_limit(req.tenant, req.plan)
    return {"allowed": allowed, "remaining": remaining}


class AuditToolCallRequest(BaseModel):
    tenant: str
    tool: str
    actor: str = ""
    ip: str = ""
    details: Optional[dict] = None


@app.post("/audit/tool-call")
def audit_tool_call(req: AuditToolCallRequest, _: None = Depends(audited_admin("saas:audit_tool_call"))):
    """Append a tool-call entry to the audit log."""
    _audit_log_tool_call(
        req.tenant, req.tool, actor=req.actor, ip=req.ip, details=req.details
    )
    return {"logged": True}


_marketplace = Marketplace()


@app.get("/marketplace/listings")
def marketplace_browse(
    category: Optional[str] = None,
    query: Optional[str] = None,
    limit: int = 20,
    _: None = Depends(audited_admin("saas:marketplace_listings_read")),
):
    """Browse marketplace listings."""
    return {"listings": _marketplace.browse(category=category, query=query, limit=limit)}


class MarketplaceSubscribeRequest(BaseModel):
    tenant: str
    listing_id: str


@app.post("/marketplace/subscriptions")
def marketplace_subscribe(
    req: MarketplaceSubscribeRequest, _: None = Depends(audited_admin("saas:marketplace_subscribe"))
):
    """Subscribe a tenant to a listing."""
    return _marketplace.subscribe(req.tenant, req.listing_id)


@app.get("/marketplace/subscriptions")
def marketplace_my_subscriptions(
    tenant: str = Query(...), _: None = Depends(audited_admin("saas:marketplace_subscriptions_read"))
):
    """List active subscriptions for a tenant."""
    return {"subscriptions": _marketplace.my_subscriptions(tenant)}


class MarketplaceListingCreateRequest(BaseModel):
    seller_tenant: str
    title: str
    description: str
    category: str
    listing_type: str = "squad"
    price_cents: int
    price_model: str = "monthly"
    tags: list[str] = []


@app.post("/marketplace/listings")
def marketplace_create_listing(
    req: MarketplaceListingCreateRequest, _: None = Depends(audited_admin("saas:marketplace_listing_create"))
):
    """Create a new marketplace listing."""
    return _marketplace.create_listing(
        seller_tenant=req.seller_tenant,
        title=req.title,
        description=req.description,
        category=req.category,
        listing_type=req.listing_type,
        price_cents=req.price_cents,
        price_model=req.price_model,
        tags=req.tags,
    )


@app.get("/marketplace/earnings")
def marketplace_earnings(
    tenant: str = Query(...), _: None = Depends(audited_admin("saas:marketplace_earnings_read"))
):
    """Seller earnings summary for a tenant."""
    return _marketplace.my_earnings(tenant)


# --- Notification preferences ---


class NotificationPreferencesRequest(BaseModel):
    """Configure notification channels for a tenant."""

    email: bool = True
    telegram: bool = False
    webhook: Optional[str] = None
    in_app: bool = True


@app.post("/tenants/{slug}/notifications")
def set_notification_prefs(slug: str, req: NotificationPreferencesRequest, _: None = Depends(audited_admin("saas:notifications_set"))):
    """Configure notification preferences for a tenant."""
    tenant = registry.get(slug)
    if not tenant:
        raise HTTPException(404, f"Tenant {slug} not found")
    prefs = {
        "email": req.email,
        "telegram": req.telegram,
        "webhook": req.webhook,
        "in_app": req.in_app,
    }
    get_notification_router().set_preferences(slug, prefs)
    log_admin(
        "notification_preferences.updated",
        tenant=slug,
        details=prefs,
    )
    return {"ok": True, "preferences": get_notification_router().get_preferences(slug)}


@app.get("/tenants/{slug}/notifications")
def get_notification_prefs(slug: str, _: None = Depends(audited_admin("saas:notifications_read"))):
    """Get notification preferences for a tenant."""
    tenant = registry.get(slug)
    if not tenant:
        raise HTTPException(404, f"Tenant {slug} not found")
    return get_notification_router().get_preferences(slug)


# --- Onboarding endpoint ---


class CreateSeatRequest(BaseModel):
    """Create a new seat (MCP token) for a tenant."""

    label: str  # e.g., "Sarah - Marketing", "Dev Team"
    role: str = "admin"  # admin | editor | viewer


class OnboardRequest(BaseModel):
    """Customer questionnaire answers -> full site provisioning."""

    email: str
    business_name: str
    industry: str
    services: list[str] = []
    primary_color: Optional[str] = None
    tagline: Optional[str] = None
    domain: Optional[str] = None  # custom domain, optional
    plan: str = "starter"  # starter | growth | scale


@app.post("/onboard")
async def onboard_customer(
    req: OnboardRequest,
    _: None = Depends(audited_admin("saas:customer_onboard")),
    idempotency_key: Optional[str] = Header(default=None, alias="Idempotency-Key"),
):
    """Full onboarding: questionnaire -> tenant -> squad -> build -> live site."""
    from sos.kernel.idempotency import with_idempotency

    # Derive tenant slug once so idempotency keys tenant-scope correctly.
    slug_for_idem = re.sub(r"[^a-z0-9]+", "-", req.business_name.lower()).strip("-")[:32]

    async def _do() -> dict:
        # Generate slug from business name
        slug = re.sub(r"[^a-z0-9]+", "-", req.business_name.lower()).strip("-")[:32]

        # Check if tenant already exists
        existing = registry.get(slug)
        if existing:
            raise HTTPException(409, f"Tenant {slug} already exists")

        # 1. Create tenant in registry
        plan_map = {
            "starter": TenantPlan.STARTER,
            "growth": TenantPlan.GROWTH,
            "scale": TenantPlan.SCALE,
        }
        tenant = registry.create(
            TenantCreate(
                slug=slug,
                label=req.business_name,
                email=req.email,
                plan=plan_map.get(req.plan, TenantPlan.STARTER),
                domain=req.domain,
                industry=req.industry,
                services=req.services,
                primary_color=req.primary_color,
                tagline=req.tagline,
            )
        )

        # 2. Create squad for this tenant
        _create_tenant_squad(slug, req.business_name, req.industry)

        # 3. Generate initial content based on questionnaire
        initial_pages = _generate_initial_content(slug, req)

        # 4. Trigger build via queue (async, non-blocking)
        try:
            build_queue.enqueue(slug, trigger="onboard", priority=5)
            log.info("Enqueued build for %s (onboard)", slug)
        except Exception as exc:
            log.warning("Build enqueue failed for %s (falling back to inline): %s", slug, exc)
            asyncio.create_task(_safe_build(slug, trigger="onboard"))

        # 5. Activate tenant
        registry.activate(slug, squad_id=slug, bus_token="pending")

        # 6. Generate initial MCP token and send welcome email
        initial_token = f"sk-{slug}-{secrets.token_hex(16)}"
        _register_bus_token(slug, initial_token)
        mcp_url = f"https://mcp.mumega.com/sse/{initial_token}"
        site_url = f"https://{tenant.subdomain}"
        try:
            from sos.services.saas.email import send_onboard_welcome
            asyncio.create_task(
                send_onboard_welcome(req.email, req.business_name, mcp_url, slug, site_url)
            )
        except Exception as exc:
            log.warning("Onboard welcome email task creation failed (non-blocking): %s", exc)

        return {
            "tenant": tenant.model_dump(),
            "site_url": f"https://{tenant.subdomain}",
            "pages_generated": len(initial_pages),
            "status": "provisioning",
            "message": (
                f"Your site is being built at {tenant.subdomain}. "
                "You'll receive a Telegram notification when it's ready."
            ),
        }

    return await with_idempotency(
        key=idempotency_key,
        tenant=slug_for_idem,
        request_body=req.model_dump(),
        fn=_do,
    )


# --- Onboarding helpers ---


def _create_tenant_squad(slug: str, business_name: str, industry: str) -> None:
    """Create a squad with initial roles for the tenant."""
    import sqlite3 as _sqlite3

    db_path = Path.home() / ".sos" / "data" / "squads.db"
    conn = _sqlite3.connect(str(db_path))
    now = datetime.now(timezone.utc).isoformat()

    conn.execute(
        """
        INSERT OR IGNORE INTO squads (id, tenant_id, name, project, objective, tier, status,
            roles_json, members_json, kpis_json, budget_cents_monthly, created_at, updated_at,
            dna_vector, coherence, receptivity, conductance_json)
        VALUES (?, 'default', ?, ?, ?, 'nomad', 'active',
            '[]', ?, '[]', 2000, ?, ?,
            '[]', 0.5, 0.7, '{"content": 0.5, "seo": 0.3, "outreach": 0.2}')
        """,
        (
            slug,
            f"{business_name} squad",
            slug,
            f"Operate {business_name} online presence",
            json.dumps([{"agent_id": "worker", "role": "operator", "is_human": False}]),
            now,
            now,
        ),
    )

    conn.execute(
        """
        INSERT OR IGNORE INTO squad_wallets
        (squad_id, tenant_id, balance_cents, total_earned_cents, total_spent_cents,
         fuel_budget_json, updated_at)
        VALUES (?, 'default', 2000, 2000, 0, '{"diesel": 2000}', ?)
        """,
        (slug, now),
    )

    conn.commit()
    conn.close()


def _generate_initial_content(slug: str, req: OnboardRequest) -> list[str]:
    """Generate starter pages from questionnaire answers."""
    pages: list[tuple[str, str]] = []

    # Home page
    services_list = (
        "\n".join(f"- **{s}**" for s in req.services)
        if req.services
        else f"We specialize in {req.industry}."
    )
    home_content = f"""\
---
title: "{req.business_name}"
description: "{req.tagline or f'{req.business_name} - {req.industry} services'}"
---

# Welcome to {req.business_name}

{req.tagline or f'Professional {req.industry} services you can trust.'}

## Our Services

{services_list}

## Get Started

Contact us today to learn how we can help your business grow.
"""
    pages.append(("index", home_content))

    # About page
    about_content = f"""\
---
title: "About {req.business_name}"
description: "Learn about {req.business_name} and our {req.industry} expertise"
---

# About Us

{req.business_name} is a {req.industry} business dedicated to delivering exceptional results for our clients.

## Why Choose Us

- Industry expertise in {req.industry}
- Personalized service tailored to your needs
- Results-driven approach

## Contact

Email: {req.email}
"""
    pages.append(("about", about_content))

    # Services page (only if services provided)
    if req.services:
        services_md = "\n\n".join(
            f"### {s}\n\nProfessional {s.lower()} services tailored to your business needs."
            for s in req.services
        )
        services_content = f"""\
---
title: "Services - {req.business_name}"
description: "{req.business_name} services: {', '.join(req.services)}"
---

# Our Services

{services_md}

## Ready to Get Started?

Contact us at {req.email} to discuss your project.
"""
        pages.append(("services", services_content))

    # Write pages to content directory for the build orchestrator
    content_dir = Path.home() / ".sos" / "data" / "tenant-content" / slug
    content_dir.mkdir(parents=True, exist_ok=True)

    for page_slug, content in pages:
        (content_dir / f"{page_slug}.md").write_text(content)

    return [p[0] for p in pages]


def _generate_signup_content(slug: str, name: str) -> None:
    """Generate minimal starter content for a new signup.

    Creates a welcome page in the tenant's content directory.
    """
    content_dir = Path.home() / ".sos" / "data" / "tenant-content" / slug
    content_dir.mkdir(parents=True, exist_ok=True)

    welcome_content = f"""\
---
title: "{name}"
description: "Welcome to {name} — powered by Mumega"
---

# Welcome to {name}

Your site is being set up by Mumega. Use your AI to publish content:

1. Connect your AI tool using the MCP config from your welcome email
2. Say "publish a blog post about [your topic]"
3. Your content appears here automatically

## Next Steps

- Complete payment (if needed) at your checkout link
- Connect your AI tool to start building
- Check back in a few minutes to see your live site
"""
    (content_dir / "index.md").write_text(welcome_content)
    log.info("Generated signup content for tenant %s", slug)


async def _safe_build(slug: str, trigger: str = "manual") -> None:
    """Wrapper for async build with error handling."""
    try:
        from sos.services.saas.builder import build_tenant

        result = await build_tenant(slug, trigger=trigger)
        if result.get("success"):
            log.info("Build complete for %s (trigger: %s)", slug, trigger)
        else:
            log.error("Build failed for %s: %s", slug, result.get("error"))
    except Exception as exc:
        log.error("Build crashed for %s: %s", slug, exc)


# --- Build queue endpoints ---

from sos.services.saas.build_queue import BuildQueue

build_queue = BuildQueue()


@app.post("/builds/enqueue/{slug}")
async def enqueue_build(
    slug: str,
    trigger: str = "manual",
    priority: int = 0,
    _: None = Depends(audited_admin("saas:build_enqueue")),
    idempotency_key: Optional[str] = Header(default=None, alias="Idempotency-Key"),
):
    from sos.kernel.idempotency import with_idempotency

    async def _do() -> dict:
        build_queue.enqueue(slug, trigger, priority)
        return {"queued": True, "queue_length": build_queue.queue_length()}

    body = {"slug": slug, "trigger": trigger, "priority": priority}
    return await with_idempotency(
        key=idempotency_key,
        tenant=slug,
        request_body=body,
        fn=_do,
    )


@app.get("/builds/status")
def build_status(_: None = Depends(audited_admin("saas:build_status"))):
    return build_queue.get_status()


# --- Custom domain endpoints ---

from sos.services.saas.domains import DomainManager

domain_mgr = DomainManager(registry)


@app.post("/tenants/{slug}/domain")
async def set_custom_domain(slug: str, domain: str, _: None = Depends(audited_admin("saas:domain_set"))):
    result = await domain_mgr.provision_custom_domain(slug, domain)
    return result


@app.delete("/tenants/{slug}/domain")
async def remove_custom_domain(slug: str, _: None = Depends(audited_admin("saas:domain_remove"))):
    result = await domain_mgr.remove_custom_domain(slug)
    return result


# --- Self-serve signup ---


class SignupRequest(BaseModel):
    email: str
    name: str  # business or person name
    plan: str = "starter"


def _register_bus_token(slug: str, token: str) -> None:
    """Add a new token to the SOS bus tokens.json. Raw token is never stored."""
    tokens_path = Path.home() / "SOS" / "sos" / "bus" / "tokens.json"
    tokens: list[dict[str, object]] = []
    if tokens_path.exists():
        tokens = json.loads(tokens_path.read_text())

    tokens.append({
        "token": "",  # Never store raw token
        "token_hash": hashlib.sha256(token.encode()).hexdigest(),
        "project": slug,
        "label": f"Customer: {slug}",
        "scope": "customer",
        "active": True,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "agent": slug,
    })

    tokens_path.write_text(json.dumps(tokens, indent=2))


def _count_seats(slug: str) -> int:
    """Count active seats (customer-scoped tokens) for a tenant."""
    tokens_path = Path.home() / "SOS" / "sos" / "bus" / "tokens.json"
    if not tokens_path.exists():
        return 0
    tokens = json.loads(tokens_path.read_text())
    return sum(
        1
        for t in tokens
        if t.get("project") == slug and t.get("active", True) and t.get("scope") == "customer"
    )


def _get_seats(slug: str) -> list[dict]:
    """Get all seats for a tenant with token info."""
    tokens_path = Path.home() / "SOS" / "sos" / "bus" / "tokens.json"
    if not tokens_path.exists():
        return []
    tokens = json.loads(tokens_path.read_text())
    seats = []
    for t in tokens:
        if t.get("project") == slug and t.get("scope") == "customer":
            # Use last 12 chars of token_hash for unique identifier
            token_hash = t.get("token_hash", "") or hashlib.sha256(t.get("token", "").encode()).hexdigest()
            token_id = token_hash[-12:] if token_hash else "unknown"
            seats.append({
                "label": t.get("label", ""),
                "token_id": token_id,  # last 12 chars of hash, unique enough
                "active": t.get("active", True),
                "created_at": t.get("created_at", ""),
                "agent": t.get("agent", slug),
                "role": t.get("role", "admin"),
            })
    return seats


def _register_bus_token_with_label(
    slug: str,
    token: str,
    label: str,
    role: str = "admin",
    plan: str | None = None,
) -> None:
    """Add a new token to the SOS bus tokens.json with a custom label. Raw token is never stored."""
    tokens_path = Path.home() / "SOS" / "sos" / "bus" / "tokens.json"
    tokens: list[dict] = []
    if tokens_path.exists():
        tokens = json.loads(tokens_path.read_text())

    tokens.append({
        "token": "",  # Never store raw token
        "token_hash": hashlib.sha256(token.encode()).hexdigest(),
        "project": slug,
        "label": f"{slug}: {label}",
        "scope": "customer",
        "active": True,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "agent": slug,
        "role": role,
        "plan": plan,
    })

    tokens_path.write_text(json.dumps(tokens, indent=2))
    log.info("Registered seat token for %s: %s (role=%s)", slug, label, role)


def _revoke_seat(slug: str, token_id: str) -> None:
    """Revoke a seat by marking its token as inactive.

    Args:
        slug: Tenant slug
        token_id: Last 12 characters of the token_hash (unique identifier)
    """
    tokens_path = Path.home() / "SOS" / "sos" / "bus" / "tokens.json"
    if not tokens_path.exists():
        return

    tokens = json.loads(tokens_path.read_text())
    found = False
    for t in tokens:
        if t.get("project") == slug and t.get("scope") == "customer":
            token_hash = t.get("token_hash", "") or hashlib.sha256(t.get("token", "").encode()).hexdigest()
            if token_hash.endswith(token_id):
                t["active"] = False
                found = True

    if found:
        tokens_path.write_text(json.dumps(tokens, indent=2))
        log.info("Revoked seat token for %s: %s", slug, token_id)


@app.post("/signup")
async def signup(
    req: SignupRequest,
    idempotency_key: Optional[str] = Header(default=None, alias="Idempotency-Key"),
):
    """Minimal signup -- get MCP config in 10 seconds."""
    from sos.kernel.idempotency import with_idempotency

    slug_for_idem = re.sub(r"[^a-z0-9]+", "-", req.name.lower()).strip("-")[:32]

    async def _do() -> dict:
        return await _signup_impl(req)

    return await with_idempotency(
        key=idempotency_key,
        tenant=slug_for_idem,
        request_body=req.model_dump(),
        fn=_do,
    )


async def _signup_impl(req: SignupRequest) -> dict:
    """Actual signup body (extracted so idempotency replay can re-enter cleanly)."""
    import secrets

    slug = re.sub(r"[^a-z0-9]+", "-", req.name.lower()).strip("-")[:32]

    existing = registry.get(slug)
    if existing:
        raise HTTPException(409, "Name already taken. Try a different name.")

    # 1. Generate bus token
    token = f"sk-{slug}-{secrets.token_hex(16)}"

    # 2. Register token in tokens.json
    _register_bus_token(slug, token)

    # 3. Create tenant
    plan_map = {
        "starter": TenantPlan.STARTER,
        "growth": TenantPlan.GROWTH,
        "scale": TenantPlan.SCALE,
    }
    tenant = registry.create(
        TenantCreate(
            slug=slug,
            label=req.name,
            email=req.email,
            plan=plan_map.get(req.plan, TenantPlan.STARTER),
        )
    )
    log_admin("tenant.created", tenant=slug, details={"plan": req.plan})
    registry.activate(slug, squad_id=slug, bus_token=token)
    log_admin("tenant.activated", tenant=slug)

    # 4. Generate minimal starter content for signup
    _generate_signup_content(slug, req.name)

    # 4b. Sync tenant to Cloudflare edge D1 (best-effort, fire-and-forget)
    async def _sync_to_edge() -> None:
        edge_url = os.environ.get("MUMEGA_EDGE_URL", "https://mumega-edge.workers.dev")
        try:
            async with httpx.AsyncClient(timeout=5) as client:
                sync_secret = os.environ.get("VPS_SYNC_SECRET", "")
                headers = {"x-sync-secret": sync_secret} if sync_secret else {}
                resp = await client.post(
                    f"{edge_url}/sync-tenant",
                    json={
                        "slug": slug,
                        "label": req.name,
                        "email": req.email,
                        "plan": req.plan,
                        "bus_token": token,
                    },
                    headers=headers,
                )
                if resp.status_code == 200:
                    log.info("Edge sync OK for tenant %s", slug)
                else:
                    log.warning("Edge sync returned %s for tenant %s", resp.status_code, slug)
        except Exception as exc:
            log.warning("Edge sync failed for tenant %s (non-blocking): %s", slug, exc)

    asyncio.create_task(_sync_to_edge())

    # 4c. Bootstrap portal auth_identity + portal_account (best-effort, fire-and-forget)
    async def _bootstrap_portal() -> None:
        portal_url = os.environ.get("MUMEGA_PORTAL_URL", "https://portal.mumega.com")
        mumega_token = os.environ.get("MUMEGA_TOKEN", "")
        if not mumega_token:
            log.warning("MUMEGA_TOKEN not set — skipping portal bootstrap for tenant %s", slug)
            return
        try:
            async with httpx.AsyncClient(timeout=10) as client:
                resp = await client.post(
                    f"{portal_url}/api/portal/auth/bootstrap",
                    json={
                        "customerSlug": slug,
                        "email": req.email,
                        "channel": "email",
                        "fullName": req.name,
                    },
                    headers={"Authorization": f"Bearer {mumega_token}"},
                )
                if resp.status_code in (200, 201):
                    log.info("Portal bootstrap OK for tenant %s", slug)
                else:
                    log.warning(
                        "Portal bootstrap returned %s for tenant %s: %s",
                        resp.status_code, slug, resp.text[:200],
                    )
        except Exception as exc:
            log.warning("Portal bootstrap failed for tenant %s (non-blocking): %s", slug, exc)

    asyncio.create_task(_bootstrap_portal())

    # 5. Trigger initial site build via queue (fire-and-forget)
    try:
        build_queue.enqueue(slug, trigger="signup", priority=5)
        log.info("Enqueued build for %s (signup)", slug)
    except Exception as exc:
        log.warning("Build enqueue failed for %s (falling back to inline): %s", slug, exc)
        asyncio.create_task(_safe_build(slug, trigger="signup"))

    # 6. Send welcome email (fire-and-forget)
    mcp_url = f"https://mcp.mumega.com/sse/{token}"
    try:
        from sos.services.saas.email import send_welcome
        asyncio.create_task(send_welcome(req.email, req.name, mcp_url, slug))
    except Exception as exc:
        log.warning("Welcome email task creation failed (non-blocking): %s", exc)

    # 7. Create Stripe customer and checkout session (optional)
    stripe_customer_id = None
    checkout_url = None

    stripe_secret = os.environ.get("STRIPE_SECRET_KEY", "")
    if stripe_secret:
        try:
            import stripe as stripe_lib
            stripe_lib.api_key = stripe_secret

            # Create Stripe customer
            customer = stripe_lib.Customer.create(
                email=req.email,
                name=req.name,
                metadata={"tenant_slug": slug, "plan": req.plan},
            )
            stripe_customer_id = customer.id

            # Update tenant with Stripe customer ID
            registry.update(slug, TenantUpdate(stripe_customer_id=stripe_customer_id))
            log.info("Created Stripe customer %s for tenant %s", stripe_customer_id, slug)

            # Create checkout session for paid plans
            price_map = {
                "starter": os.environ.get("STRIPE_PRICE_STARTER", ""),
                "growth": os.environ.get("STRIPE_PRICE_GROWTH", ""),
                "scale": os.environ.get("STRIPE_PRICE_SCALE", ""),
            }
            price_id = price_map.get(req.plan, "")

            if price_id:
                session = stripe_lib.checkout.Session.create(
                    customer=stripe_customer_id,
                    mode="subscription",
                    line_items=[{"price": price_id, "quantity": 1}],
                    metadata={"slug": slug, "plan": req.plan},
                    success_url=f"https://mumega.com/welcome?tenant={slug}",
                    cancel_url="https://mumega.com/pricing",
                )
                checkout_url = session.url
                log.info("Created Stripe checkout session for tenant %s", slug)
        except ImportError:
            log.warning("Stripe module not installed — skipping Stripe integration")
        except Exception as exc:
            log.error("Stripe integration failed for tenant %s: %s", slug, exc)

    # 5. Return MCP connection configs for every platform
    mcp_url = f"https://mcp.mumega.com/sse/{token}"

    response = {
        "welcome": f"Welcome to Mumega, {req.name}!",
        "tenant": slug,
        "site_url": f"https://{slug}.mumega.com",
        "mcp_url": mcp_url,
        "connect": {
            "claude_code": f'claude mcp add mumega --transport sse --url "{mcp_url}"',
            "claude_desktop": {
                "mcpServers": {
                    "mumega": {
                        "url": mcp_url,
                    }
                }
            },
            "cursor": {
                "mcpServers": {
                    "mumega": {
                        "url": mcp_url,
                    }
                }
            },
            "chatgpt": (
                f"Use Custom GPT with action URL: "
                f"{mcp_url.replace('/sse/', '/api/')}"
            ),
            "generic": {
                "transport": "sse",
                "url": mcp_url,
            },
        },
        "next_steps": [
            "1. Copy the config for your AI tool above",
            "2. Paste it into your AI tool's MCP settings",
            "3. Ask your AI: 'What can you do with Mumega?'",
            "4. Say 'remember that my business does X' to start building memory",
        ],
    }

    # Add Stripe checkout URL if available
    if checkout_url:
        response["checkout_url"] = checkout_url
        response["next_steps"].insert(0, "0. Complete payment at the checkout link above")
        response["billing"] = {
            "stripe_customer_id": stripe_customer_id,
            "plan": req.plan,
            "checkout_url": checkout_url,
        }

    return response


# ---------------------------------------------------------------------------
# Customer-facing /my/* endpoints
# All authenticated with the tenant's MCP token via require_customer.
# ---------------------------------------------------------------------------

_SQUADS_DB = Path.home() / ".sos" / "data" / "squads.db"
_SQUAD_SERVICE_URL = "http://localhost:8060"


def _squads_db(writable: bool = False) -> sqlite3.Connection:
    """Open squads.db. Read-only by default, writable for wallet operations."""
    if writable:
        conn = sqlite3.connect(_SQUADS_DB)
    else:
        conn = sqlite3.connect(f"file:{_SQUADS_DB}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    return conn


def _get_squad_service_header() -> dict[str, str]:
    """Return the Authorization header for Squad Service calls."""
    # Squad Service uses SOS_SYSTEM_TOKEN (defaults to sk-sos-system).
    key = os.environ.get("SOS_SYSTEM_TOKEN", "sk-sos-system")
    return {"Authorization": f"Bearer {key}"}


class CreateTaskRequest(BaseModel):
    title: str
    description: str = ""
    priority: str = "medium"  # critical | high | medium | low
    squad_id: str = ""


@app.get("/my/connect")
def my_connect(tenant_slug: str = Depends(audited_customer("saas:my_connect"))):
    """Return MCP connection configs so the customer can always find their connect instructions."""
    tenant = registry.get(tenant_slug)
    if not tenant:
        raise HTTPException(404, detail="Tenant not found")

    # Reconstruct mcp_url from the tenant's stored bus_token
    bus_token = tenant.bus_token or ""
    mcp_url = f"https://mcp.mumega.com/sse/{bus_token}" if bus_token else "token not found — contact support"

    return {
        "tenant": tenant_slug,
        "mcp_url": mcp_url,
        "connect": {
            "claude_code": f'claude mcp add mumega --transport sse --url "{mcp_url}"',
            "claude_desktop": {"mcpServers": {"mumega": {"url": mcp_url}}},
            "cursor": {"mcpServers": {"mumega": {"url": mcp_url}}},
            "chatgpt": f"Use Custom GPT with action URL: {mcp_url.replace('/sse/', '/api/')}",
            "generic": {"transport": "sse", "url": mcp_url},
        },
    }


@app.get("/my/dashboard")
def my_dashboard(tenant_slug: str = Depends(audited_customer("saas:my_dashboard"))):
    """Return tenant info + KPIs: wallet balance, task counts, content count."""
    tenant = registry.get(tenant_slug)
    if not tenant:
        raise HTTPException(404, detail="Tenant not found")

    try:
        conn = _squads_db()
        # Wallet
        wallet_row = conn.execute(
            "SELECT balance_cents, total_earned_cents, total_spent_cents FROM squad_wallets WHERE squad_id = ?",
            (tenant_slug,),
        ).fetchone()
        wallet = dict(wallet_row) if wallet_row else {"balance_cents": 0, "total_earned_cents": 0, "total_spent_cents": 0}

        # Task counts by status
        task_counts_rows = conn.execute(
            "SELECT status, COUNT(*) as cnt FROM squad_tasks WHERE project = ? GROUP BY status",
            (tenant_slug,),
        ).fetchall()
        task_counts = {row["status"]: row["cnt"] for row in task_counts_rows}
        total_tasks = sum(task_counts.values())

        # Content count (markdown files generated for this tenant)
        content_dir = Path.home() / ".sos" / "data" / "tenant-content" / tenant_slug
        content_count = len(list(content_dir.glob("*.md"))) if content_dir.exists() else 0

        conn.close()
    except Exception as exc:
        log.error("Dashboard DB error for %s: %s", tenant_slug, exc)
        wallet = {"balance_cents": 0, "total_earned_cents": 0, "total_spent_cents": 0}
        task_counts = {}
        total_tasks = 0
        content_count = 0

    return {
        "tenant": tenant.model_dump(),
        "kpis": {
            "wallet_balance_cents": wallet["balance_cents"],
            "total_earned_cents": wallet["total_earned_cents"],
            "total_spent_cents": wallet["total_spent_cents"],
            "tasks": {
                "total": total_tasks,
                "by_status": task_counts,
            },
            "content_pages": content_count,
        },
    }


@app.get("/my/wallet")
def my_wallet(tenant_slug: str = Depends(audited_customer("saas:my_wallet"))):
    """Return wallet balance and totals for this tenant."""
    try:
        conn = _squads_db()
        row = conn.execute(
            "SELECT balance_cents, total_earned_cents, total_spent_cents, updated_at FROM squad_wallets WHERE squad_id = ?",
            (tenant_slug,),
        ).fetchone()
        conn.close()
    except Exception as exc:
        log.error("Wallet DB error for %s: %s", tenant_slug, exc)
        raise HTTPException(500, detail="Database error")

    if not row:
        return {"balance_cents": 0, "total_earned_cents": 0, "total_spent_cents": 0, "updated_at": None}

    return dict(row)


@app.get("/my/transactions")
def my_transactions(
    tenant_slug: str = Depends(audited_customer("saas:my_transactions")),
    limit: int = Query(50, ge=1, le=200),
    type: Optional[str] = Query(None, description="earn | spend"),
):
    """Return transaction history for this tenant."""
    try:
        conn = _squads_db()
        if type:
            rows = conn.execute(
                "SELECT * FROM squad_transactions WHERE tenant_id = ? AND type = ? ORDER BY created_at DESC LIMIT ?",
                (tenant_slug, type, limit),
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT * FROM squad_transactions WHERE tenant_id = ? ORDER BY created_at DESC LIMIT ?",
                (tenant_slug, limit),
            ).fetchall()
        conn.close()
    except Exception as exc:
        log.error("Transactions DB error for %s: %s", tenant_slug, exc)
        raise HTTPException(500, detail="Database error")

    return {"transactions": [dict(r) for r in rows], "count": len(rows)}


@app.get("/my/tasks")
def my_tasks(
    tenant_slug: str = Depends(audited_customer("saas:my_tasks_list")),
    status: Optional[str] = Query(None, description="backlog | claimed | done"),
    limit: int = Query(50, ge=1, le=200),
):
    """Return tasks for this tenant."""
    try:
        conn = _squads_db()
        if status:
            rows = conn.execute(
                "SELECT * FROM squad_tasks WHERE project = ? AND status = ? ORDER BY created_at DESC LIMIT ?",
                (tenant_slug, status, limit),
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT * FROM squad_tasks WHERE project = ? ORDER BY created_at DESC LIMIT ?",
                (tenant_slug, limit),
            ).fetchall()
        conn.close()
    except Exception as exc:
        log.error("Tasks DB error for %s: %s", tenant_slug, exc)
        raise HTTPException(500, detail="Database error")

    return {"tasks": [dict(r) for r in rows], "count": len(rows)}


@app.post("/my/tasks")
async def create_my_task(
    req: CreateTaskRequest,
    tenant_slug: str = Depends(audited_customer("saas:my_task_create")),
    idempotency_key: Optional[str] = Header(default=None, alias="Idempotency-Key"),
):
    """Create a task for this tenant via the Squad Service."""
    from sos.kernel.idempotency import with_idempotency

    async def _do() -> dict:
        squad_id = req.squad_id or tenant_slug  # default to tenant's own squad

        payload = {
            "id": f"{tenant_slug}-{secrets.token_hex(4)}",
            "squad_id": squad_id,
            "title": req.title,
            "description": req.description,
            "priority": req.priority,
            "project": tenant_slug,
            "status": "backlog",
            "labels": [],
        }

        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                resp = await client.post(
                    f"{_SQUAD_SERVICE_URL}/tasks",
                    json=payload,
                    headers=_get_squad_service_header(),
                )
                resp.raise_for_status()
                return resp.json()
        except httpx.HTTPStatusError as exc:
            log.error("Squad Service task creation failed for %s: %s", tenant_slug, exc)
            raise HTTPException(exc.response.status_code, detail=exc.response.text)
        except Exception as exc:
            log.error("Squad Service unreachable for %s: %s", tenant_slug, exc)
            raise HTTPException(503, detail="Squad Service unavailable")

    return await with_idempotency(
        key=idempotency_key,
        tenant=tenant_slug,
        request_body=req.model_dump(),
        fn=_do,
    )


@app.get("/my/squads")
def my_squads(tenant_slug: str = Depends(audited_customer("saas:my_squads"))):
    """Return squads that serve this tenant."""
    try:
        conn = _squads_db()
        rows = conn.execute(
            "SELECT * FROM squads WHERE project = ? OR project = '*' ORDER BY created_at",
            (tenant_slug,),
        ).fetchall()
        conn.close()
    except Exception as exc:
        log.error("Squads DB error for %s: %s", tenant_slug, exc)
        raise HTTPException(500, detail="Database error")

    return {"squads": [dict(r) for r in rows], "count": len(rows)}


@app.get("/my/activity")
def my_activity(tenant_slug: str = Depends(audited_customer("saas:my_activity"))):
    """Return recent completed tasks + events for this tenant (last 20 items)."""
    try:
        conn = _squads_db()

        # Recent completed tasks
        tasks = conn.execute(
            """
            SELECT 'task' as item_type, id, title as detail, completed_at as ts
            FROM squad_tasks
            WHERE project = ? AND status = 'done' AND completed_at IS NOT NULL
            ORDER BY completed_at DESC LIMIT 10
            """,
            (tenant_slug,),
        ).fetchall()

        # Recent squad events for this tenant's squads
        events = conn.execute(
            """
            SELECT 'event' as item_type, id as id, event_type as detail, timestamp as ts
            FROM squad_events
            WHERE tenant_id = ?
            ORDER BY timestamp DESC LIMIT 10
            """,
            (tenant_slug,),
        ).fetchall()

        conn.close()
    except Exception as exc:
        log.error("Activity DB error for %s: %s", tenant_slug, exc)
        raise HTTPException(500, detail="Database error")

    # Merge and sort by timestamp, return top 20
    items = [dict(r) for r in tasks] + [dict(r) for r in events]
    items.sort(key=lambda x: x.get("ts") or "", reverse=True)

    return {"activity": items[:20], "count": len(items[:20])}


# ── Team Invite ──────────────────────────────────────────────────────────────


class InviteRequest(BaseModel):
    email: str
    role: str = "member"  # member, manager, viewer


@app.post("/my/invite")
async def invite_team_member(req: InviteRequest, tenant_slug: str = Depends(audited_customer("saas:my_invite"))):
    """Invite a team member by email. Creates a seat token and sends invite email."""
    tenant = registry.get(tenant_slug)
    if not tenant:
        raise HTTPException(404, "Tenant not found")

    # Check seat limits
    seat_limits = {"starter": 1, "growth": 5, "scale": -1}
    plan_key = tenant.plan.value.lower() if hasattr(tenant.plan, "value") else str(tenant.plan).lower()
    limit = seat_limits.get(plan_key, 1)
    current = _count_seats(tenant_slug)
    if limit != -1 and current >= limit:
        raise HTTPException(403, f"Plan allows {limit} seats. Current: {current}. Upgrade to add more.")

    # Generate seat token
    token = f"sk-{tenant_slug}-{secrets.token_hex(16)}"
    _register_bus_token_with_label(tenant_slug, token, f"Invite: {req.email}", role=req.role)

    # Send invite email via Resend
    try:
        resend_key = os.environ.get("RESEND_API_KEY", "")
        if resend_key:
            import httpx as _httpx
            async with _httpx.AsyncClient(timeout=10) as client:
                await client.post(
                    "https://api.resend.com/emails",
                    headers={"Authorization": f"Bearer {resend_key}", "Content-Type": "application/json"},
                    json={
                        "from": "Mumega <hello@mumega.com>",
                        "to": [req.email],
                        "subject": f"You're invited to {tenant.label or tenant_slug}",
                        "html": f"<p>You've been invited as a {req.role}.</p><p>Use this token to connect: <code>{token}</code></p>",
                    },
                )
    except Exception as exc:
        log.warning(f"Invite email failed: {exc}")

    return {"ok": True, "email": req.email, "role": req.role}


# ── Glass Commerce → Wallet Earning ──────────────────────────────────────────

class GlassWebhookPayload(BaseModel):
    tenant_slug: str
    amount_cents: int
    platform_fee_cents: int
    transaction_id: str
    description: str = ""


@app.post("/webhooks/glass")
async def glass_commerce_webhook(payload: GlassWebhookPayload):
    """Glass Commerce calls this when a tenant earns revenue.
    Credits the tenant's squad wallet with earnings (minus platform fee)."""
    tenant_earning = payload.amount_cents - payload.platform_fee_cents
    try:
        conn = _squads_db(writable=True)
        # Credit the tenant's wallet
        conn.execute(
            """UPDATE squad_wallets
               SET balance_cents = balance_cents + ?,
                   total_earned_cents = total_earned_cents + ?,
                   updated_at = ?
               WHERE squad_id = ? OR squad_id IN (
                   SELECT id FROM squads WHERE project = ?
               )""",
            (tenant_earning, tenant_earning,
             datetime.now(timezone.utc).isoformat(),
             payload.tenant_slug, payload.tenant_slug),
        )
        # Record earn transaction
        import uuid
        conn.execute(
            """INSERT INTO squad_transactions
               (id, squad_id, tenant_id, type, amount_cents, counterparty, reason, created_at)
               VALUES (?, ?, ?, 'earn', ?, 'glass_commerce', ?, ?)""",
            (str(uuid.uuid4())[:8], payload.tenant_slug, payload.tenant_slug,
             tenant_earning,
             f"Glass Commerce: {payload.description} (tx:{payload.transaction_id})",
             datetime.now(timezone.utc).isoformat()),
        )
        conn.commit()
        conn.close()
    except Exception as exc:
        log.error(f"Glass webhook failed for {payload.tenant_slug}: {exc}")
        raise HTTPException(500, detail="Wallet credit failed")

    log.info(f"Glass Commerce: {payload.tenant_slug} earned {tenant_earning}c (fee: {payload.platform_fee_cents}c)")
    return {"credited": tenant_earning, "tenant": payload.tenant_slug}


# ── Customer Chat (proxy to MCP) ────────────────────────────────────────────

class ChatMessage(BaseModel):
    message: str


@app.post("/my/chat")
async def my_chat(req: ChatMessage, tenant_slug: str = Depends(audited_customer("saas:my_chat"))):
    """Customer sends a message to their AI squad via chat.
    Proxies to the MCP SSE server's tools or to a configured model."""
    # For v1: create a task from the chat message and return acknowledgment
    # The sovereign loop picks it up and dispatches to an agent
    try:
        headers = _get_squad_service_header()
        headers["Content-Type"] = "application/json"
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.post(
                "http://localhost:8060/tasks",
                headers=headers,
                json={
                    "id": f"{tenant_slug}-chat-{secrets.token_hex(4)}",
                    "squad_id": tenant_slug,
                    "title": req.message[:100],
                    "description": req.message,
                    "priority": "medium",
                    "project": tenant_slug,
                    "labels": ["chat", "customer-request"],
                    "assignee": None,
                },
            )
        task_data = resp.json() if resp.status_code == 200 else {}
        task_id = task_data.get("task", {}).get("id", "unknown")
    except Exception as exc:
        log.warning(f"Chat task creation failed: {exc}")
        task_id = None

    # Also store as memory in Mirror
    try:
        async with httpx.AsyncClient(timeout=5) as client:
            await client.post(
                "http://localhost:8844/store",
                json={
                    "agent": tenant_slug,
                    "text": f"Customer request: {req.message}",
                    "tags": ["chat", "customer"],
                },
                headers={"Authorization": f"Bearer {os.environ.get('MUMEGA_MASTER_KEY', '')}"},
            )
    except Exception:
        pass  # Mirror store is best-effort

    return {
        "reply": f"Got it. I've created a task for your request. Your squad will work on it.",
        "task_id": task_id,
        "status": "queued",
    }


# ---------------------------------------------------------------------------
# Auth endpoints — PUBLIC (no require_admin or require_customer)
# ---------------------------------------------------------------------------


class LoginRequest(BaseModel):
    email: str
    password: str


class RegisterRequest(BaseModel):
    email: str
    password: str
    name: str


class ForgotPasswordRequest(BaseModel):
    email: str


def _find_tenant_by_email(email: str):
    """Look up the most-recently created tenant matching the given email."""
    try:
        conn = sqlite3.connect(str(Path.home() / ".sos" / "data" / "squads.db"))
        conn.row_factory = sqlite3.Row
        row = conn.execute(
            "SELECT slug FROM tenants WHERE email = ? ORDER BY created_at DESC LIMIT 1",
            (email,),
        ).fetchone()
        conn.close()
        if not row:
            return None
        return registry.get(str(row["slug"]))
    except Exception as exc:
        log.error("_find_tenant_by_email error: %s", exc)
        return None


@app.post("/auth/login")
async def auth_login(req: LoginRequest):
    """Email + password login (v0.5: email lookup only, bcrypt in v1).

    Returns: { user: { id, email, name, tenant } } or 401.
    """
    tenant = _find_tenant_by_email(req.email)
    if not tenant:
        raise HTTPException(401, detail="Invalid credentials")

    return {
        "user": {
            "id": tenant.slug,
            "email": tenant.email,
            "name": tenant.label,
            "tenant": tenant.slug,
        }
    }


@app.get("/auth/tenant")
def auth_tenant(email: str = Query(...)):
    """Look up tenant by email — used by Auth.js callback to map email → tenant + bus_token.

    Returns: { tenant_slug, bus_token, plan, domain } or 404.
    """
    tenant = _find_tenant_by_email(email)
    if not tenant:
        raise HTTPException(404, detail="No tenant found for this email")

    return {
        "tenant_slug": tenant.slug,
        "bus_token": tenant.bus_token or "",
        "plan": tenant.plan.value if hasattr(tenant.plan, "value") else str(tenant.plan),
        "domain": tenant.domain or tenant.subdomain,
    }


@app.post("/auth/register")
async def auth_register(req: RegisterRequest):
    """Create account with email + password (v0.5: delegates to signup flow).

    Returns: { user: { id, email, name, tenant } }.
    """
    # Delegate to the existing signup flow
    signup_req = SignupRequest(email=req.email, name=req.name, plan="starter")
    result = await signup(signup_req)

    tenant_slug = result.get("tenant", "")
    return {
        "user": {
            "id": tenant_slug,
            "email": req.email,
            "name": req.name,
            "tenant": tenant_slug,
        }
    }


@app.post("/auth/forgot-password")
async def auth_forgot_password(req: ForgotPasswordRequest):
    """Send password reset / magic link email via Resend.

    Returns: { ok: true, message: "Check your email" }.
    """
    tenant = _find_tenant_by_email(req.email)
    if not tenant:
        # Don't reveal whether email exists — always return ok
        return {"ok": True, "message": "If that email is registered, you'll receive a reset link."}

    # Generate a short-lived magic link token (store in KV or simple in-memory for v0.5)
    token = secrets.token_urlsafe(32)
    reset_url = f"https://mumega.com/dashboard/reset?token={token}&email={req.email}"

    try:
        from sos.services.saas.email import send_magic_link
        asyncio.create_task(send_magic_link(req.email, reset_url))
    except Exception as exc:
        log.warning("Forgot-password email task creation failed (non-blocking): %s", exc)

    return {"ok": True, "message": "Check your email for a login link."}


@app.post("/billing/webhook")
async def stripe_billing_webhook(request: Request):
    """Receive Stripe webhook events.

    Verifies the signature using STRIPE_WEBHOOK_SECRET when set.
    Handles: checkout.session.completed, customer.subscription.updated,
    customer.subscription.deleted, invoice.paid, invoice.payment_failed.
    """
    raw_body = await request.body()
    sig_header = request.headers.get("stripe-signature", "")

    stripe_secret = os.environ.get("STRIPE_SECRET_KEY", "")
    webhook_secret = os.environ.get("STRIPE_WEBHOOK_SECRET", "")

    event = None

    if stripe_secret and webhook_secret and not webhook_secret.startswith("whsec_REPLACE"):
        try:
            import stripe as stripe_lib
            stripe_lib.api_key = stripe_secret
            event = stripe_lib.Webhook.construct_event(raw_body, sig_header, webhook_secret)
        except Exception as exc:
            log.warning("Stripe webhook signature verification failed: %s", exc)
            raise HTTPException(400, "Invalid webhook signature")
    else:
        # Webhook secret not configured — accept without verification and log only
        try:
            import json as _json
            event = _json.loads(raw_body)
            log.warning(
                "Stripe webhook received WITHOUT signature verification "
                "(STRIPE_WEBHOOK_SECRET not configured). Set it in .env to enable verification."
            )
        except Exception as exc:
            log.error("Failed to parse Stripe webhook body: %s", exc)
            raise HTTPException(400, "Invalid JSON body")

    event_type = event.get("type") if isinstance(event, dict) else getattr(event, "type", "unknown")
    log.info("Stripe webhook event received: %s", event_type)

    try:
        data_obj = event.get("data", {}).get("object", {}) if isinstance(event, dict) else event.data.object

        if event_type == "checkout.session.completed":
            slug = (
                data_obj.get("metadata", {}).get("slug")
                if isinstance(data_obj, dict)
                else getattr(getattr(data_obj, "metadata", None), "slug", None)
            )
            if slug:
                tenant = registry.get(slug)
                if tenant:
                    registry.activate(slug, squad_id=slug, bus_token=tenant.bus_token or "")
                    log.info("Tenant %s activated via checkout.session.completed", slug)
                else:
                    log.warning("checkout.session.completed: tenant %s not found", slug)

        elif event_type in ("customer.subscription.updated", "customer.subscription.deleted"):
            customer_id = (
                data_obj.get("customer")
                if isinstance(data_obj, dict)
                else getattr(data_obj, "customer", None)
            )
            status = (
                data_obj.get("status")
                if isinstance(data_obj, dict)
                else getattr(data_obj, "status", None)
            )
            if customer_id:
                tenant = registry.find_by_stripe_customer(customer_id)
                if tenant:
                    if event_type == "customer.subscription.deleted" or status == "canceled":
                        registry.suspend(tenant.slug)
                        log.info("Tenant %s suspended (subscription %s)", tenant.slug, event_type)
                    else:
                        log.info("Tenant %s subscription updated: status=%s", tenant.slug, status)

        elif event_type == "invoice.paid":
            customer_id = (
                data_obj.get("customer")
                if isinstance(data_obj, dict)
                else getattr(data_obj, "customer", None)
            )
            if customer_id:
                tenant = registry.find_by_stripe_customer(customer_id)
                if tenant:
                    log.info("Invoice paid for tenant %s", tenant.slug)

        elif event_type == "invoice.payment_failed":
            customer_id = (
                data_obj.get("customer")
                if isinstance(data_obj, dict)
                else getattr(data_obj, "customer", None)
            )
            if customer_id:
                tenant = registry.find_by_stripe_customer(customer_id)
                if tenant:
                    log.warning("Invoice payment failed for tenant %s", tenant.slug)

    except Exception as exc:
        log.error("Error processing Stripe webhook event %s: %s", event_type, exc)
        # Return 200 to Stripe so it doesn't retry — log error internally
        return {"ok": False, "error": str(exc)}

    return {"ok": True, "event": event_type}


if __name__ == "__main__":
    import uvicorn

    port = int(os.environ.get("SOS_SAAS_PORT", "8075"))
    uvicorn.run(app, host="0.0.0.0", port=port)
