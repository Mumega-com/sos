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
import uuid as _uuid
from typing import Any

import asyncpg  # type: ignore[import]
import stripe
from fastapi import HTTPException, Request
from fastapi.responses import JSONResponse

from sos.services.billing.knight_mint import mint_knight_programmatic
from sos.services.billing.provision import provision_tenant
from sos.contracts.tenant import TenantCreate, TenantPlan, TenantUpdate, TenantStatus
from sos.clients.saas import AsyncSaasClient
from sos.observability.sprint_telemetry import emit_knight_minted, emit_stripe_webhook

log = logging.getLogger("sos.billing.webhook")


def _saas_client() -> AsyncSaasClient:
    """Lazy construction so tests can override SOS_SAAS_URL/token per test.

    Tests that mutate ``SOS_SAAS_URL`` at runtime should call
    ``sos.kernel.settings.reload_settings()`` after the mutation — the
    default ``get_settings()`` accessor is cached.
    """
    from sos.kernel.settings import ServiceURLSettings
    # Instantiate fresh so env-var test overrides are honored without
    # forcing a global settings reload.
    return AsyncSaasClient(base_url=ServiceURLSettings().saas)


STRIPE_WEBHOOK_SECRET = os.environ.get("STRIPE_WEBHOOK_SECRET", "")
STRIPE_SECRET_KEY = os.environ.get("STRIPE_SECRET_KEY", "")

if STRIPE_SECRET_KEY:
    stripe.api_key = STRIPE_SECRET_KEY


def _slug_from_email(email: str) -> str:
    """Generate a tenant slug from email: john.doe@gmail.com -> john-doe."""
    local = email.split("@")[0] if "@" in email else email
    return local.replace(".", "-").replace("_", "-").lower()


def _verify_signature(payload: bytes, sig_header: str) -> dict[str, Any]:
    """Verify Stripe webhook signature. Raises HTTPException on failure.

    BLOCK-3: tolerance=300 — Stripe SDK enforces 5-minute timestamp window.
    WARN-1: dual-secret rotation — try STRIPE_WEBHOOK_SECRET then STRIPE_WEBHOOK_SECRET_NEW.
    """
    # WARN-1: collect both secrets to support zero-downtime rotation
    secrets_to_try = [s for s in [
        STRIPE_WEBHOOK_SECRET,
        os.environ.get("STRIPE_WEBHOOK_SECRET_NEW", ""),
    ] if s]

    if not secrets_to_try:
        log.warning("STRIPE_WEBHOOK_SECRET not set — skipping signature verification")
        return json.loads(payload)

    last_exc: Exception | None = None
    for secret in secrets_to_try:
        try:
            event = stripe.Webhook.construct_event(payload, sig_header, secret, tolerance=300)  # BLOCK-3
            return event  # type: ignore[return-value]
        except stripe.error.SignatureVerificationError as exc:  # type: ignore[attr-error]
            last_exc = exc
        except Exception as exc:
            log.error("Stripe webhook verification error: %s", exc)
            raise HTTPException(status_code=400, detail="Webhook verification failed")

    log.error("Stripe signature verification failed")
    raise HTTPException(status_code=400, detail="Invalid signature")


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

    saas = _saas_client()

    # Register in SaaS tenant registry — TenantCreate is kept as the type
    # contract (sos.contracts.tenant) but we serialize it to a dict for
    # the HTTP boundary.
    plan_map = {"seo": TenantPlan.STARTER, "seo-ads": TenantPlan.GROWTH, "full": TenantPlan.SCALE}
    plan = plan_map.get(metadata.get("plan", ""), TenantPlan.STARTER)
    try:
        payload = TenantCreate(
            slug=slug, label=label, email=customer_email, plan=plan,
            domain=metadata.get("domain"),
            industry=metadata.get("industry"),
            tagline=metadata.get("tagline"),
        ).model_dump(mode="json")
        await saas.create_tenant(payload)
        log.info("Tenant %s registered in SaaS registry (plan=%s)", slug, plan.value)
    except Exception as exc:
        log.error("SaaS registry insert failed for %s (non-blocking): %s", slug, exc)

    result = await provision_tenant(slug, label, customer_email)

    # Activate tenant in registry with provisioning results
    bus_token = result.get("bus_token", "")
    if bus_token and result.get("status") == "provisioned":
        try:
            await saas.activate_tenant(slug, squad_id=slug, bus_token=bus_token)
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


async def handle_payment_intent_succeeded(
    payment_intent: dict[str, Any],
    *,
    livemode: bool = True,
) -> dict[str, Any]:
    """Handle payment_intent.succeeded — idempotent knight mint (Sprint 006 E.3 / G69 v0.3).

    Flow (Athena BLOCKs 1-5 + WARNs 1-3):
      WARN-3: livemode guard before any DB work.
      BLOCK-1: single atomic transaction wraps all DB writes; savepoint for idempotency INSERT.
      BLOCK-2: stripe_customer_id from pi.customer; project from contracts.project (not metadata).
      BLOCK-5: stripe_webhook_id passed as FK proof to mint_knight_programmatic.
      Mint failure raises → transaction rollback → row gone → Stripe retry-safe.
    """
    payment_intent_id: str = payment_intent.get("id", "")
    stripe_customer_id: str = payment_intent.get("customer", "")  # BLOCK-2: Stripe-managed field
    metadata: dict = payment_intent.get("metadata", {})
    customer_email: str = payment_intent.get("receipt_email", "") or metadata.get("email", "")

    # WARN-3: livemode guard — refuse test-mode payments in production
    sos_env = os.environ.get("SOS_ENV", "production")
    if sos_env != "test" and not livemode:
        log.warning(
            "payment_intent.succeeded: livemode=False rejected in SOS_ENV=%s payment_intent_id=%s",
            sos_env, payment_intent_id,
        )
        return {"ok": False, "reason": "not_livemode", "payment_intent_id": payment_intent_id}

    db_url = os.environ.get("MIRROR_DATABASE_URL") or os.environ.get("DATABASE_URL")
    if not db_url:
        log.error("payment_intent.succeeded: DATABASE_URL not set — cannot process")
        return {"ok": False, "reason": "db_not_configured"}

    webhook_id = str(_uuid.uuid4())
    conn = await asyncpg.connect(db_url)
    try:
        async with conn.transaction():  # BLOCK-1: single atomic transaction
            # ── 1. Idempotency INSERT (savepoint to catch UniqueViolationError inside tx) ──
            try:
                async with conn.transaction():  # inner = SAVEPOINT
                    await conn.execute(
                        "INSERT INTO stripe_webhook_processed (id, payment_intent_id, status) "
                        "VALUES ($1, $2, 'processing')",
                        webhook_id, payment_intent_id,
                    )
            except asyncpg.UniqueViolationError:
                existing = await conn.fetchrow(
                    "SELECT id, status, resulting_knight_id "
                    "FROM stripe_webhook_processed WHERE payment_intent_id = $1",
                    payment_intent_id,
                )
                status = existing["status"] if existing else "unknown"
                knight_id_prior = existing["resulting_knight_id"] if existing else None
                if status == "processed":
                    log.info(
                        "payment_intent.succeeded: replay_idempotent_skip payment_intent_id=%s knight=%s",
                        payment_intent_id, knight_id_prior,
                    )
                    emit_stripe_webhook(payment_intent_id, "payment_intent.succeeded", "replay_skipped")
                    return {
                        "ok": True,
                        "reason": "replay_idempotent_skip",
                        "knight_id": knight_id_prior,
                    }
                elif status == "processing":
                    log.warning(
                        "payment_intent.succeeded: concurrent retry in-flight payment_intent_id=%s",
                        payment_intent_id,
                    )
                    return {"ok": False, "reason": "retry_in_flight", "payment_intent_id": payment_intent_id}
                else:  # failed — needs human investigation
                    log.error(
                        "payment_intent.succeeded: prior_attempt_failed payment_intent_id=%s",
                        payment_intent_id,
                    )
                    _alert_athena(
                        f"E.3 prior_attempt_failed: payment {payment_intent_id} status=failed — "
                        "manual investigation required before retry."
                    )
                    emit_stripe_webhook(payment_intent_id, "payment_intent.succeeded", "prior_failed")
                    return {
                        "ok": False,
                        "reason": "prior_attempt_failed",
                        "payment_intent_id": payment_intent_id,
                    }

            # ── 2. Contract verification (BLOCK-2: stripe_customer_id from pi.customer) ──
            contract = await conn.fetchrow(
                "SELECT id, principal_id, tenant_slug, stripe_customer_id, "
                "cause_statement, status, project "
                "FROM contracts WHERE stripe_customer_id = $1 ORDER BY created_at DESC LIMIT 1",
                stripe_customer_id,
            )
            if not contract:
                log.warning(
                    "payment_intent.succeeded: no contract for stripe_customer_id=%s",
                    stripe_customer_id,
                )
                await conn.execute(
                    "UPDATE stripe_webhook_processed SET status='failed', last_error='no_contract' "
                    "WHERE id = $1",
                    webhook_id,
                )
                emit_stripe_webhook(payment_intent_id, "payment_intent.succeeded", "no_contract")
                _alert_athena(
                    f"E.3 no_contract: payment {payment_intent_id} received for "
                    f"stripe_customer={stripe_customer_id} but no contracts row found."
                )
                return {"ok": False, "reason": "no_contract", "stripe_customer_id": stripe_customer_id}

            # ── 3. Project scope guard (BLOCK-2: authoritative source is contracts.project) ──
            project = (contract["project"] or "mumega").strip()
            if project != "mumega":
                log.warning(
                    "payment_intent.succeeded: project=%r refused (BLOCK-2) payment_intent_id=%s",
                    project, payment_intent_id,
                )
                await conn.execute(
                    "UPDATE stripe_webhook_processed SET status='failed', "
                    "last_error='project_scope_refused' WHERE id = $1",
                    webhook_id,
                )
                emit_stripe_webhook(payment_intent_id, "payment_intent.succeeded", "project_scope_refused")
                _alert_athena(
                    f"E.3 scope guard: contracts.project={project!r} refused for payment {payment_intent_id}"
                )
                return {
                    "ok": False,
                    "reason": "project_scope_refused",
                    "payment_intent_id": payment_intent_id,
                }

            # ── 4. Derive knight inputs ──
            tenant_slug: str = contract["tenant_slug"] or metadata.get("tenant_slug", "")
            cause_raw: str = (
                contract["cause_statement"]
                or metadata.get("cause", "")
                or f"Serves {tenant_slug or 'this customer'} as their dedicated agent on the substrate."
            )
            customer_name: str = metadata.get("customer_name", tenant_slug or stripe_customer_id)
            knight_name: str = metadata.get("knight_name", tenant_slug or f"knight-{payment_intent_id[-8:]}")

            # ── 5. Mint knight (BLOCK-5: stripe_webhook_id mandatory FK proof) ──
            mint_result = mint_knight_programmatic(
                knight_name=knight_name,
                customer_slug=tenant_slug or knight_name,
                customer_name=customer_name,
                customer_email=customer_email or None,
                cause_statement=cause_raw,
                project=project,
                stripe_webhook_id=webhook_id,  # BLOCK-5
            )

            if not mint_result["ok"]:
                log.error(
                    "payment_intent.succeeded: mint failed payment_intent_id=%s error=%s",
                    payment_intent_id, mint_result.get("error"),
                )
                # Raise → transaction rollback → idempotency row removed → Stripe retry-safe
                raise RuntimeError(f"mint_failed: {mint_result.get('error', 'unknown')}")

            knight_id_str: str = mint_result["knight_id"] or ""
            knight_slug: str = mint_result["knight_slug"] or ""
            qnft_uri: str = mint_result["qnft_uri"] or ""

            # ── 6. Persist results (inside single transaction, BLOCK-1) ──
            await conn.execute(
                "UPDATE stripe_webhook_processed SET status='processed', resulting_knight_id=$1, "
                "resulting_knight_qnft_uri=$2, completed_at=now() WHERE id=$3",
                knight_id_str, qnft_uri, webhook_id,
            )
            await conn.execute(
                "UPDATE contracts SET signed_at=now(), knight_id=$1 WHERE id=$2",
                knight_id_str, contract["id"],
            )
            # Transaction commits here (end of async with conn.transaction())

        # ── 7. After commit: emit (BLOCK-1: emits fire AFTER transaction commit) ──
        emit_knight_minted(knight_id_str, stripe_customer_id, payment_intent_id, project)
        emit_stripe_webhook(payment_intent_id, "payment_intent.succeeded", "minted")

        log.info(
            "payment_intent.succeeded: knight minted knight_id=%s payment_intent_id=%s",
            knight_id_str, payment_intent_id,
        )
        return {
            "ok": True,
            "knight_id": knight_id_str,
            "knight_slug": knight_slug,
            "qnft_uri": qnft_uri,
            "payment_intent_id": payment_intent_id,
            "skipped": mint_result.get("skipped", False),
        }

    except RuntimeError as exc:
        # Mint failure: transaction rolled back, idempotency row gone, retry-safe
        if "mint_failed" in str(exc):
            log.error(
                "payment_intent.succeeded: mint_failed payment_intent_id=%s — tx rolled back, retry-safe",
                payment_intent_id,
            )
            try:
                emit_stripe_webhook(payment_intent_id, "payment_intent.succeeded", "mint_failed")
            except Exception:
                pass
            return {"ok": False, "reason": "mint_failed", "payment_intent_id": payment_intent_id}
        raise

    except Exception as exc:
        log.error(
            "payment_intent.succeeded: unexpected error payment_intent_id=%s: %s",
            payment_intent_id, exc, exc_info=True,
        )
        try:
            emit_stripe_webhook(payment_intent_id, "payment_intent.succeeded", "mint_failed")
        except Exception:
            pass
        raise

    finally:
        await conn.close()


def _alert_athena(message: str) -> None:
    """Best-effort bus alert to Athena for payment anomalies."""
    try:
        import importlib.util
        from pathlib import Path
        spec = importlib.util.spec_from_file_location(
            "bus_send", Path.home() / "scripts" / "bus-send.py"
        )
        if spec and spec.loader:
            mod = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(mod)  # type: ignore[union-attr]
            mod.send(to="athena", text=f"[ALERT] E.3 Stripe webhook anomaly: {message}", source="billing")
    except Exception as exc:
        log.warning("_alert_athena failed (non-fatal): %s", exc)


async def handle_subscription_deleted(subscription: dict[str, Any]) -> dict[str, Any]:
    """Handle customer.subscription.deleted — mark tenant inactive."""
    customer_id = subscription.get("customer", "")
    metadata = subscription.get("metadata", {})
    slug = metadata.get("slug", "")

    log.info("Subscription deleted for customer %s (slug: %s) — marking inactive", customer_id, slug)

    # Suspend in SaaS registry
    if slug:
        try:
            update = TenantUpdate(status=TenantStatus.CANCELLED).model_dump(
                mode="json", exclude_none=True
            )
            await _saas_client().update_tenant(slug, update)
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

    elif event_type == "payment_intent.succeeded":
        # E.3 / G69: idempotent knight mint on payment success
        try:
            result = await handle_payment_intent_succeeded(data_object, livemode=event.get("livemode", True))
        except Exception as exc:
            log.error("payment_intent.succeeded handler crashed: %s", exc, exc_info=True)
            return JSONResponse({"status": "error", "error": str(exc)}, status_code=500)
        if result.get("ok"):
            return JSONResponse({"status": "ok", **result})
        # ok=False with known reason: return 200 so Stripe doesn't retry
        # (no_contract, project_scope_refused, replay_idempotent_skip)
        return JSONResponse({"status": "noop", **result})

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
