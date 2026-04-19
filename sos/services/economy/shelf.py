"""Shelf commerce (Phase 7 Step 7.6).

Exposes the per-tenant Shelf over the economy service:

- ``GET /economy/shelf/{tenant}`` — list active products.
- ``POST /economy/shelf/{tenant}`` — admin adds a product.
- ``POST /economy/shelf/checkout/{tenant}/{product_id}`` — creates a Stripe
  Checkout Session, returns ``{url, session_id}``.
- ``POST /economy/shelf/capture`` — Stripe webhook. On
  ``checkout.session.completed`` credits the tenant $MIND wallet with
  ``amount_cents/100 × product.mind_multiplier`` and records a
  ``shelf_captures`` row (idempotent by ``stripe_session_id``).

Storage is SQLite (same pattern as ``backends.py``); the canonical schema
lives in ``migrations/0003_shelf.sql`` so D1 deployments apply the same
definition.

Stripe interaction is indirected through two module-level overrides so
tests can swap in fakes without patching the `stripe` package globally:

    _create_checkout_session(product, success_url, cancel_url) -> dict
    _construct_stripe_event(payload: bytes, signature: str) -> dict

Production code plugs these in to the real ``stripe`` SDK in
``_wire_stripe``; tests leave them mocked.
"""
from __future__ import annotations

import os
import sqlite3
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Optional

from fastapi import APIRouter, Header, HTTPException, Request
from pydantic import BaseModel, Field

from sos.contracts.policy import PolicyDecision
from sos.contracts.shelf import (
    CheckoutSession,
    ShelfCapture,
    ShelfCaptureResult,
    ShelfProduct,
    ShelfProductList,
)
from sos.kernel.policy.gate import can_execute
from sos.observability.logging import get_logger

log = get_logger("economy.shelf", min_level=os.getenv("SOS_LOG_LEVEL", "info"))

_DB_ENV = "SOS_SHELF_DB_PATH"
_DEFAULT_DB_PATH = Path("data/economy.db")


def _db_path() -> Path:
    return Path(os.environ.get(_DB_ENV) or _DEFAULT_DB_PATH)


def init_db() -> None:
    """Create shelf_* tables if missing. Safe to call repeatedly."""
    path = _db_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(path) as conn:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS shelf_products (
                id TEXT NOT NULL,
                tenant TEXT NOT NULL,
                title TEXT NOT NULL,
                description TEXT NOT NULL DEFAULT '',
                price_cents INTEGER NOT NULL,
                currency TEXT NOT NULL DEFAULT 'usd',
                grant_id TEXT NOT NULL,
                mind_multiplier REAL NOT NULL DEFAULT 1.0,
                active INTEGER NOT NULL DEFAULT 1,
                created_at REAL NOT NULL,
                PRIMARY KEY (tenant, id)
            );
            CREATE INDEX IF NOT EXISTS idx_shelf_products_tenant
                ON shelf_products(tenant, active);
            CREATE TABLE IF NOT EXISTS shelf_captures (
                id TEXT PRIMARY KEY,
                tenant TEXT NOT NULL,
                product_id TEXT NOT NULL,
                stripe_session_id TEXT NOT NULL UNIQUE,
                amount_cents INTEGER NOT NULL,
                currency TEXT NOT NULL DEFAULT 'usd',
                buyer_email TEXT,
                mind_credited REAL NOT NULL DEFAULT 0.0,
                captured_at REAL NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_shelf_captures_tenant
                ON shelf_captures(tenant, captured_at DESC);
            """
        )


# ---------------------------------------------------------------------------
# Data access
# ---------------------------------------------------------------------------


def list_products(tenant: str, *, only_active: bool = True) -> list[ShelfProduct]:
    with sqlite3.connect(_db_path()) as conn:
        conn.row_factory = sqlite3.Row
        sql = "SELECT * FROM shelf_products WHERE tenant = ?"
        args: list[Any] = [tenant]
        if only_active:
            sql += " AND active = 1"
        sql += " ORDER BY created_at"
        rows = conn.execute(sql, args).fetchall()
    return [_row_to_product(r) for r in rows]


def get_product(tenant: str, product_id: str) -> Optional[ShelfProduct]:
    with sqlite3.connect(_db_path()) as conn:
        conn.row_factory = sqlite3.Row
        row = conn.execute(
            "SELECT * FROM shelf_products WHERE tenant = ? AND id = ?",
            (tenant, product_id),
        ).fetchone()
    return _row_to_product(row) if row else None


def upsert_product(product: ShelfProduct) -> ShelfProduct:
    created_at = product.created_at or datetime.now(timezone.utc)
    with sqlite3.connect(_db_path()) as conn:
        conn.execute(
            """
            INSERT INTO shelf_products
            (id, tenant, title, description, price_cents, currency, grant_id,
             mind_multiplier, active, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(tenant, id) DO UPDATE SET
                title = excluded.title,
                description = excluded.description,
                price_cents = excluded.price_cents,
                currency = excluded.currency,
                grant_id = excluded.grant_id,
                mind_multiplier = excluded.mind_multiplier,
                active = excluded.active
            """,
            (
                product.id,
                product.tenant,
                product.title,
                product.description,
                product.price_cents,
                product.currency,
                product.grant_id,
                product.mind_multiplier,
                1 if product.active else 0,
                created_at.timestamp(),
            ),
        )
    return product.model_copy(update={"created_at": created_at})


def get_capture_by_session(stripe_session_id: str) -> Optional[ShelfCapture]:
    with sqlite3.connect(_db_path()) as conn:
        conn.row_factory = sqlite3.Row
        row = conn.execute(
            "SELECT * FROM shelf_captures WHERE stripe_session_id = ?",
            (stripe_session_id,),
        ).fetchone()
    return _row_to_capture(row) if row else None


def insert_capture(capture: ShelfCapture) -> None:
    with sqlite3.connect(_db_path()) as conn:
        conn.execute(
            """
            INSERT INTO shelf_captures
            (id, tenant, product_id, stripe_session_id, amount_cents, currency,
             buyer_email, mind_credited, captured_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                capture.id,
                capture.tenant,
                capture.product_id,
                capture.stripe_session_id,
                capture.amount_cents,
                capture.currency,
                capture.buyer_email,
                capture.mind_credited,
                capture.captured_at.timestamp(),
            ),
        )


def _row_to_product(row: sqlite3.Row) -> ShelfProduct:
    return ShelfProduct(
        id=row["id"],
        tenant=row["tenant"],
        title=row["title"],
        description=row["description"],
        price_cents=row["price_cents"],
        currency=row["currency"],
        grant_id=row["grant_id"],
        mind_multiplier=row["mind_multiplier"],
        active=bool(row["active"]),
        created_at=datetime.fromtimestamp(row["created_at"], tz=timezone.utc),
    )


def _row_to_capture(row: sqlite3.Row) -> ShelfCapture:
    return ShelfCapture(
        id=row["id"],
        tenant=row["tenant"],
        product_id=row["product_id"],
        stripe_session_id=row["stripe_session_id"],
        amount_cents=row["amount_cents"],
        currency=row["currency"],
        buyer_email=row["buyer_email"],
        mind_credited=row["mind_credited"],
        captured_at=datetime.fromtimestamp(row["captured_at"], tz=timezone.utc),
    )


# ---------------------------------------------------------------------------
# Stripe indirection (tests override these)
# ---------------------------------------------------------------------------


_CreateCheckout = Callable[[ShelfProduct, str, str], dict[str, Any]]
_ConstructEvent = Callable[[bytes, str], dict[str, Any]]


def _default_create_checkout(
    product: ShelfProduct, success_url: str, cancel_url: str
) -> dict[str, Any]:  # pragma: no cover — wired in prod only
    import stripe  # type: ignore[import-not-found]

    stripe.api_key = os.environ["STRIPE_SECRET_KEY"]
    session = stripe.checkout.Session.create(
        mode="payment",
        line_items=[
            {
                "price_data": {
                    "currency": product.currency,
                    "product_data": {"name": product.title},
                    "unit_amount": product.price_cents,
                },
                "quantity": 1,
            }
        ],
        success_url=success_url,
        cancel_url=cancel_url,
        metadata={
            "sos_tenant": product.tenant,
            "sos_product_id": product.id,
            "sos_grant_id": product.grant_id,
        },
    )
    return {"id": session.id, "url": session.url}


def _default_construct_event(
    payload: bytes, signature: str
) -> dict[str, Any]:  # pragma: no cover — wired in prod only
    import stripe  # type: ignore[import-not-found]

    secret = os.environ["STRIPE_WEBHOOK_SECRET"]
    event = stripe.Webhook.construct_event(payload, signature, secret)
    return event.to_dict() if hasattr(event, "to_dict") else dict(event)


_create_checkout_session: _CreateCheckout = _default_create_checkout
_construct_stripe_event: _ConstructEvent = _default_construct_event


def set_stripe_hooks(
    *,
    create_checkout: Optional[_CreateCheckout] = None,
    construct_event: Optional[_ConstructEvent] = None,
) -> None:
    """Swap out Stripe interactions (used by tests)."""
    global _create_checkout_session, _construct_stripe_event
    if create_checkout is not None:
        _create_checkout_session = create_checkout
    if construct_event is not None:
        _construct_stripe_event = construct_event


# ---------------------------------------------------------------------------
# Request / response models for the HTTP surface
# ---------------------------------------------------------------------------


class AddProductRequest(BaseModel):
    id: str = Field(min_length=1, max_length=128)
    title: str = Field(min_length=1, max_length=256)
    description: str = ""
    price_cents: int = Field(ge=0)
    currency: str = Field(default="usd", min_length=3, max_length=8)
    grant_id: str = Field(min_length=1, max_length=128)
    mind_multiplier: float = Field(default=1.0, ge=0.0)
    active: bool = True


# ---------------------------------------------------------------------------
# Router
# ---------------------------------------------------------------------------


router = APIRouter(prefix="/economy/shelf", tags=["shelf"])


def _raise_on_deny(decision: PolicyDecision, *, require_system: bool = False) -> None:
    if not decision.allowed:
        reason = decision.reason or "unauthorized"
        if "bearer" in reason.lower() or "auth" in reason.lower():
            raise HTTPException(status_code=401, detail=reason)
        raise HTTPException(status_code=403, detail=reason)
    if require_system and "system/admin" not in (decision.reason or ""):
        raise HTTPException(
            status_code=403, detail="admin scope required for shelf writes"
        )


@router.post("/capture", response_model=ShelfCaptureResult)
async def capture_webhook(
    request: Request,
    stripe_signature: Optional[str] = Header(None, alias="Stripe-Signature"),
) -> ShelfCaptureResult:
    """Stripe webhook endpoint.

    Authed by Stripe signature verification only — NOT by the SOS bearer gate.
    On ``checkout.session.completed`` we credit $MIND and record the capture.

    Declared before ``/{tenant}`` so FastAPI's path matching prefers the
    literal ``/capture`` over the catch-all tenant slug.
    """
    if not stripe_signature:
        raise HTTPException(status_code=400, detail="missing Stripe-Signature header")

    payload = await request.body()
    try:
        event = _construct_stripe_event(payload, stripe_signature)
    except Exception as exc:
        raise HTTPException(status_code=400, detail=f"invalid stripe signature: {exc}")

    event_type = event.get("type")
    if event_type != "checkout.session.completed":
        return ShelfCaptureResult(ok=True, reason=f"ignored event type {event_type}")

    obj = event.get("data", {}).get("object") or {}
    session_id = obj.get("id")
    if not session_id:
        raise HTTPException(status_code=400, detail="missing session id on event")

    init_db()
    existing = get_capture_by_session(session_id)
    if existing is not None:
        return ShelfCaptureResult(
            ok=True, capture_id=existing.id, already_recorded=True
        )

    metadata = obj.get("metadata") or {}
    tenant = metadata.get("sos_tenant")
    product_id = metadata.get("sos_product_id")
    if not tenant or not product_id:
        raise HTTPException(
            status_code=400, detail="session metadata missing sos_tenant/sos_product_id"
        )

    amount_cents = int(obj.get("amount_total") or 0)
    currency = obj.get("currency") or "usd"
    buyer_email = (obj.get("customer_details") or {}).get("email")

    product = get_product(tenant, product_id)
    multiplier = product.mind_multiplier if product else 1.0
    mind_credited = round((amount_cents / 100.0) * multiplier, 6)

    capture = ShelfCapture(
        id=f"sc_{uuid.uuid4().hex[:16]}",
        tenant=tenant,
        product_id=product_id,
        stripe_session_id=session_id,
        amount_cents=amount_cents,
        currency=currency,
        buyer_email=buyer_email,
        mind_credited=mind_credited,
        captured_at=datetime.now(timezone.utc),
    )
    insert_capture(capture)

    if mind_credited > 0:
        try:
            from sos.services.economy.wallet import SovereignWallet

            w = SovereignWallet()
            await w.credit(tenant, mind_credited, f"shelf:{product_id}")
        except Exception as exc:  # pragma: no cover — wallet is best-effort in webhook
            log.warning(
                "shelf.capture wallet credit failed",
                tenant=tenant,
                error=str(exc),
            )

    log.info(
        "shelf.capture recorded",
        tenant=tenant,
        product_id=product_id,
        amount_cents=amount_cents,
        mind_credited=mind_credited,
    )
    return ShelfCaptureResult(ok=True, capture_id=capture.id)


@router.get("/{tenant}", response_model=ShelfProductList)
async def list_shelf(
    tenant: str,
    authorization: Optional[str] = Header(None),
) -> ShelfProductList:
    if not authorization:
        raise HTTPException(status_code=401, detail="missing bearer token")
    decision = await can_execute(
        action="shelf_list",
        resource=tenant,
        tenant=tenant,
        authorization=authorization,
    )
    _raise_on_deny(decision)

    init_db()
    products = list_products(tenant)
    return ShelfProductList(tenant=tenant, count=len(products), products=products)


@router.post("/{tenant}", response_model=ShelfProduct)
async def add_product(
    tenant: str,
    req: AddProductRequest,
    authorization: Optional[str] = Header(None),
) -> ShelfProduct:
    if not authorization:
        raise HTTPException(status_code=401, detail="missing bearer token")
    decision = await can_execute(
        action="shelf_add",
        resource=tenant,
        tenant=tenant,
        authorization=authorization,
    )
    _raise_on_deny(decision, require_system=True)

    init_db()
    product = ShelfProduct(
        id=req.id,
        tenant=tenant,
        title=req.title,
        description=req.description,
        price_cents=req.price_cents,
        currency=req.currency,
        grant_id=req.grant_id,
        mind_multiplier=req.mind_multiplier,
        active=req.active,
    )
    return upsert_product(product)


@router.post("/checkout/{tenant}/{product_id}", response_model=CheckoutSession)
async def create_checkout(
    tenant: str,
    product_id: str,
    authorization: Optional[str] = Header(None),
) -> CheckoutSession:
    if not authorization:
        raise HTTPException(status_code=401, detail="missing bearer token")
    decision = await can_execute(
        action="shelf_checkout",
        resource=f"{tenant}/{product_id}",
        tenant=tenant,
        authorization=authorization,
    )
    _raise_on_deny(decision)

    init_db()
    product = get_product(tenant, product_id)
    if product is None or not product.active:
        raise HTTPException(status_code=404, detail="product not found or inactive")

    success_url = os.environ.get(
        "SOS_SHELF_SUCCESS_URL",
        "https://mumega.com/shelf/success?session_id={CHECKOUT_SESSION_ID}",
    )
    cancel_url = os.environ.get(
        "SOS_SHELF_CANCEL_URL", "https://mumega.com/shelf/cancel"
    )

    session = _create_checkout_session(product, success_url, cancel_url)
    if not session.get("id") or not session.get("url"):
        raise HTTPException(status_code=502, detail="stripe session creation failed")

    return CheckoutSession(
        session_id=session["id"],
        url=session["url"],
        product_id=product.id,
        tenant=tenant,
        amount_cents=product.price_cents,
        currency=product.currency,
    )


__all__ = [
    "router",
    "init_db",
    "list_products",
    "get_product",
    "upsert_product",
    "get_capture_by_session",
    "insert_capture",
    "set_stripe_hooks",
]


# Unused import hint for type checkers — keep `time` for future timestamps.
_ = time
