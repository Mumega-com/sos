from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import os
import re
import secrets
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from fastapi import FastAPI, HTTPException
from fastapi.responses import HTMLResponse
from pydantic import BaseModel

from sos.services.saas.audit import get_audit, log_admin
from sos.services.saas.billing import SaaSBilling
from sos.services.saas.logging_config import setup_logging
from sos.services.saas.models import TenantCreate, TenantPlan, TenantStatus, TenantUpdate
from sos.services.saas.notifications import get_router as get_notification_router
from sos.services.saas.registry import TenantRegistry

# Configure structured logging
setup_logging("saas")

log = logging.getLogger("sos.saas")

app = FastAPI(title="Mumega SaaS Service", version="0.1.0")
registry = TenantRegistry()
billing = SaaSBilling(registry)


@app.get("/", response_class=HTMLResponse)
def landing():
    """Self-serve signup page."""
    from sos.services.saas.signup_page import SIGNUP_HTML

    return SIGNUP_HTML


@app.get("/health")
def health():
    return {"status": "ok", "service": "saas", "tenants": len(registry.list())}


@app.post("/tenants")
async def create_tenant(req: TenantCreate):
    existing = registry.get(req.slug)
    if existing:
        raise HTTPException(409, f"Tenant {req.slug} already exists")
    tenant = registry.create(req)
    log_admin("tenant.created", tenant=req.slug, details={"plan": req.plan.value if hasattr(req.plan, "value") else str(req.plan)})
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
    log_admin("tenant.activated", tenant=slug)
    return tenant.model_dump()


@app.post("/tenants/{slug}/suspend")
def suspend_tenant(slug: str):
    tenant = registry.update(slug, TenantUpdate(status=TenantStatus.SUSPENDED))
    if not tenant:
        raise HTTPException(404, f"Tenant {slug} not found")
    log_admin("tenant.suspended", tenant=slug)
    return tenant.model_dump()


# --- Multi-seat token management ---


@app.post("/tenants/{slug}/seats")
def create_seat(slug: str, req: CreateSeatRequest):
    """Create a new seat (additional MCP token) for a tenant."""
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
    _register_bus_token_with_label(slug, token, req.label)
    log_admin("seat.created", tenant=slug, details={"label": req.label})

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


@app.get("/tenants/{slug}/seats")
def list_seats(slug: str):
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
def revoke_seat(slug: str, token_id: str):
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
def record_usage(slug: str, metric: str, quantity: int):
    billing.record_usage(slug, metric, quantity)
    return {"ok": True}


@app.get("/tenants/{slug}/usage")
def get_usage(slug: str, period: Optional[str] = None):
    return billing.get_usage(slug, period)


@app.post("/tenants/{slug}/transaction")
def record_transaction(
    slug: str,
    tx_type: str,
    amount_cents: int,
    description: str = "",
    stripe_id: str = "",
):
    tx_id = billing.record_transaction(
        slug, tx_type, amount_cents, description, stripe_id
    )
    return {"ok": True, "transaction_id": tx_id}


@app.get("/tenants/{slug}/invoice")
def get_invoice(slug: str):
    return billing.get_tenant_invoice(slug)


@app.get("/revenue")
def platform_revenue(period: Optional[str] = None):
    return billing.get_revenue(period=period)


@app.get("/tenants/{slug}/audit")
def get_audit_log(slug: str, event_type: Optional[str] = None, limit: int = 100):
    """Query audit log for a tenant."""
    return {"events": get_audit().query(tenant_slug=slug, event_type=event_type, limit=limit)}


# --- Notification preferences ---


class NotificationPreferencesRequest(BaseModel):
    """Configure notification channels for a tenant."""

    email: bool = True
    telegram: bool = False
    webhook: Optional[str] = None
    in_app: bool = True


@app.post("/tenants/{slug}/notifications")
def set_notification_prefs(slug: str, req: NotificationPreferencesRequest):
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
def get_notification_prefs(slug: str):
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
async def onboard_customer(req: OnboardRequest):
    """Full onboarding: questionnaire -> tenant -> squad -> build -> live site."""
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
def enqueue_build(slug: str, trigger: str = "manual", priority: int = 0):
    build_queue.enqueue(slug, trigger, priority)
    return {"queued": True, "queue_length": build_queue.queue_length()}


@app.get("/builds/status")
def build_status():
    return build_queue.get_status()


# --- Custom domain endpoints ---

from sos.services.saas.domains import DomainManager

domain_mgr = DomainManager(registry)


@app.post("/tenants/{slug}/domain")
async def set_custom_domain(slug: str, domain: str):
    result = await domain_mgr.provision_custom_domain(slug, domain)
    return result


@app.delete("/tenants/{slug}/domain")
async def remove_custom_domain(slug: str):
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
            })
    return seats


def _register_bus_token_with_label(slug: str, token: str, label: str) -> None:
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
    })

    tokens_path.write_text(json.dumps(tokens, indent=2))
    log.info("Registered seat token for %s: %s", slug, label)


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
async def signup(req: SignupRequest):
    """Minimal signup -- get MCP config in 10 seconds."""
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


if __name__ == "__main__":
    import uvicorn

    port = int(os.environ.get("SOS_SAAS_PORT", "8075"))
    uvicorn.run(app, host="0.0.0.0", port=port)
