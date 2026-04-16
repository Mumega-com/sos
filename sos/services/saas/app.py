from __future__ import annotations

import asyncio
import json
import logging
import os
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from fastapi import FastAPI, HTTPException
from fastapi.responses import HTMLResponse
from pydantic import BaseModel

from sos.services.saas.billing import SaaSBilling
from sos.services.saas.models import TenantCreate, TenantPlan, TenantStatus, TenantUpdate
from sos.services.saas.registry import TenantRegistry

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


# --- Onboarding endpoint ---


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

    # 4. Trigger build (async, non-blocking)
    asyncio.create_task(_safe_build(slug))

    # 5. Activate tenant
    registry.activate(slug, squad_id=slug, bus_token="pending")

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


async def _safe_build(slug: str) -> None:
    """Wrapper for async build with error handling."""
    try:
        from sos.services.saas.builder import build_tenant

        result = await build_tenant(slug, trigger="onboard")
        if result.get("success"):
            log.info("Onboard build complete for %s", slug)
        else:
            log.error("Onboard build failed for %s: %s", slug, result.get("error"))
    except Exception as exc:
        log.error("Onboard build crashed for %s: %s", slug, exc)


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
    """Add a new token to the SOS bus tokens.json."""
    tokens_path = Path.home() / "SOS" / "sos" / "bus" / "tokens.json"
    tokens: list[dict[str, object]] = []
    if tokens_path.exists():
        tokens = json.loads(tokens_path.read_text())

    tokens.append({
        "token": token,
        "token_hash": "",
        "project": slug,
        "label": f"Customer: {slug}",
        "active": True,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "agent": slug,
    })

    tokens_path.write_text(json.dumps(tokens, indent=2))


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
    registry.activate(slug, squad_id=slug, bus_token=token)

    # 4. Create Stripe customer and checkout session (optional)
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
