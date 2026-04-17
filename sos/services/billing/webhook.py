"""Stripe webhook handler — auto-provisions tenant on payment.

Listens for checkout.session.completed events.
Extracts customer email, name, and metadata.
Calls tenant provisioning pipeline.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
from typing import Any

import stripe
from fastapi import HTTPException, Request
from fastapi.responses import JSONResponse

from sos.services.billing.provision import provision_tenant
from sos.contracts.tenant import TenantCreate, TenantPlan, TenantUpdate, TenantStatus
from sos.services.saas.registry import TenantRegistry

log = logging.getLogger("sos.billing.webhook")

_registry = TenantRegistry()

STRIPE_WEBHOOK_SECRET = os.environ.get("STRIPE_WEBHOOK_SECRET", "")
STRIPE_SECRET_KEY = os.environ.get("STRIPE_SECRET_KEY", "")

if STRIPE_SECRET_KEY:
    stripe.api_key = STRIPE_SECRET_KEY


def _slug_from_email(email: str) -> str:
    """Generate a tenant slug from email: john.doe@gmail.com -> john-doe."""
    local = email.split("@")[0] if "@" in email else email
    return local.replace(".", "-").replace("_", "-").lower()


def _verify_signature(payload: bytes, sig_header: str) -> dict[str, Any]:
    """Verify Stripe webhook signature. Raises HTTPException on failure."""
    if not STRIPE_WEBHOOK_SECRET:
        log.warning("STRIPE_WEBHOOK_SECRET not set — skipping signature verification")
        return json.loads(payload)

    try:
        event = stripe.Webhook.construct_event(payload, sig_header, STRIPE_WEBHOOK_SECRET)
        return event  # type: ignore[return-value]
    except stripe.error.SignatureVerificationError:  # type: ignore[attr-error]
        log.error("Stripe signature verification failed")
        raise HTTPException(status_code=400, detail="Invalid signature")
    except Exception as exc:
        log.error("Stripe webhook verification error: %s", exc)
        raise HTTPException(status_code=400, detail="Webhook verification failed")


async def handle_checkout_completed(session: dict[str, Any]) -> dict[str, Any]:
    """Handle checkout.session.completed — provision a new tenant."""
    customer_email = session.get("customer_email") or session.get("customer_details", {}).get("email", "")
    customer_name = session.get("customer_details", {}).get("name", "")
    metadata = session.get("metadata", {})

    if not customer_email:
        log.error("No email in checkout session %s", session.get("id"))
        return {"error": "no email in session"}

    slug = metadata.get("slug") or _slug_from_email(customer_email)
    label = customer_name or slug

    log.info("Provisioning tenant %s (%s) from Stripe checkout", slug, customer_email)

    # Register in SaaS tenant registry
    plan_map = {"seo": TenantPlan.STARTER, "seo-ads": TenantPlan.GROWTH, "full": TenantPlan.SCALE}
    plan = plan_map.get(metadata.get("plan", ""), TenantPlan.STARTER)
    try:
        tenant = _registry.create(TenantCreate(
            slug=slug, label=label, email=customer_email, plan=plan,
            domain=metadata.get("domain"),
            industry=metadata.get("industry"),
            tagline=metadata.get("tagline"),
        ))
        log.info("Tenant %s registered in SaaS registry (plan=%s)", slug, plan.value)
    except Exception as exc:
        log.error("SaaS registry insert failed for %s (non-blocking): %s", slug, exc)

    result = await provision_tenant(slug, label, customer_email)

    # Activate tenant in registry with provisioning results
    bus_token = result.get("bus_token", "")
    if bus_token and result.get("status") == "provisioned":
        try:
            _registry.activate(slug, squad_id=slug, bus_token=bus_token)
            log.info("Tenant %s activated in SaaS registry", slug)
        except Exception as exc:
            log.error("SaaS registry activation failed for %s: %s", slug, exc)

    # Wire 1: Stripe → Bank — convert payment to $MIND treasury deposit
    amount_cents = session.get("amount_total", 0)
    if amount_cents > 0:
        amount_usd = amount_cents / 100.0
        try:
            import sys
            from pathlib import Path
            sys.path.insert(0, str(Path.home()))
            from sovereign.bank import SovereignBank
            bank = SovereignBank.__new__(SovereignBank)  # Skip __init__ (avoids Solana dependency)
            deposit_result = await bank.deposit(slug, amount_usd, source="stripe")
            result["deposit"] = deposit_result
            log.info("Bank deposit for %s: %s MIND", slug, deposit_result.get("mind_amount"))
        except Exception as exc:
            log.error("Bank deposit failed for %s (non-blocking): %s", slug, exc)
            result["deposit_error"] = str(exc)

    return result


async def handle_subscription_deleted(subscription: dict[str, Any]) -> dict[str, Any]:
    """Handle customer.subscription.deleted — mark tenant inactive."""
    customer_id = subscription.get("customer", "")
    metadata = subscription.get("metadata", {})
    slug = metadata.get("slug", "")

    log.info("Subscription deleted for customer %s (slug: %s) — marking inactive", customer_id, slug)

    # Suspend in SaaS registry
    if slug:
        try:
            _registry.update(slug, TenantUpdate(status=TenantStatus.CANCELLED))
            log.info("Tenant %s cancelled in SaaS registry", slug)
        except Exception as exc:
            log.error("SaaS registry cancel failed for %s: %s", slug, exc)

    # Mark tenant inactive in tokens.json (don't delete)
    if slug:
        from pathlib import Path
        tokens_path = Path.home() / "SOS" / "sos" / "bus" / "tokens.json"
        if tokens_path.exists():
            try:
                tokens = json.loads(tokens_path.read_text())
                for token in tokens:
                    if token.get("project") == slug:
                        token["active"] = False
                        log.info("Deactivated bus token for %s", slug)
                tokens_path.write_text(json.dumps(tokens, indent=2))
            except Exception as exc:
                log.error("Failed to deactivate token for %s: %s", slug, exc)

    return {"status": "deactivated", "slug": slug, "customer": customer_id}


async def stripe_webhook_handler(request: Request) -> JSONResponse:
    """Main webhook entry point — called by the FastAPI route."""
    payload = await request.body()
    sig_header = request.headers.get("stripe-signature", "")

    event = _verify_signature(payload, sig_header)
    event_type = event.get("type", "")
    data_object = event.get("data", {}).get("object", {})

    log.info("Stripe webhook received: %s", event_type)

    if event_type == "checkout.session.completed":
        # Fire-and-forget provisioning so we return 200 within Stripe's 30s window
        asyncio.create_task(_safe_provision(data_object))
        return JSONResponse({"status": "accepted", "event": event_type})

    elif event_type == "customer.subscription.deleted":
        result = await handle_subscription_deleted(data_object)
        return JSONResponse({"status": "ok", **result})

    # Acknowledge but ignore other events
    return JSONResponse({"status": "ignored", "event": event_type})


async def _safe_provision(session: dict[str, Any]) -> None:
    """Wrapper to catch provisioning errors without crashing the server."""
    try:
        result = await handle_checkout_completed(session)
        if result.get("error"):
            log.error("Provisioning failed: %s", result["error"])
        else:
            log.info("Provisioning complete: %s", result.get("slug"))
    except Exception as exc:
        log.error("Provisioning crashed: %s", exc, exc_info=True)
