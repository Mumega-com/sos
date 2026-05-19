"""Shelf commerce contracts (Phase 7 Step 7.6).

The Shelf is the SOS-side catalogue of sellable products a tenant exposes.
Inkwell's `/shelf` routes read these via the commerce adapter; Stripe
Checkout captures payment; captures credit the tenant's $MIND wallet and
grant access.

- ``ShelfProduct`` — a listable item (title, price, access grant id).
- ``ShelfCapture`` — a paid checkout.completed event recorded idempotently.
- ``CheckoutSession`` — the response returned to Inkwell after creating a
  Stripe Checkout session.
"""
from __future__ import annotations

from datetime import datetime
from typing import Any, Optional

from pydantic import BaseModel, ConfigDict, Field


class ShelfProduct(BaseModel):
    """A product a tenant sells via their Inkwell shelf."""

    model_config = ConfigDict(extra="forbid")

    id: str = Field(min_length=1, max_length=128)
    tenant: str = Field(min_length=1, max_length=128)
    title: str = Field(min_length=1, max_length=256)
    description: str = ""
    price_cents: int = Field(ge=0)
    currency: str = Field(default="usd", min_length=3, max_length=8)
    grant_id: str = Field(
        description="Inkwell content id unlocked on successful capture",
        min_length=1,
        max_length=128,
    )
    mind_multiplier: float = Field(
        default=1.0,
        ge=0.0,
        description="Multiplier applied to the captured amount when crediting $MIND",
    )
    active: bool = True
    created_at: Optional[datetime] = None


class ShelfCapture(BaseModel):
    """A recorded Stripe checkout.completed event."""

    model_config = ConfigDict(extra="forbid")

    id: str = Field(min_length=1)
    tenant: str = Field(min_length=1)
    product_id: str = Field(min_length=1)
    stripe_session_id: str = Field(min_length=1)
    amount_cents: int = Field(ge=0)
    currency: str = Field(default="usd", min_length=3)
    buyer_email: Optional[str] = None
    mind_credited: float = Field(default=0.0, ge=0.0)
    captured_at: datetime


class CheckoutSession(BaseModel):
    """Return shape for POST /economy/shelf/checkout/{tenant}/{product_id}."""

    model_config = ConfigDict(extra="forbid")

    session_id: str
    url: str
    product_id: str
    tenant: str
    amount_cents: int
    currency: str


class ShelfProductList(BaseModel):
    """Return shape for GET /economy/shelf/{tenant}."""

    model_config = ConfigDict(extra="forbid")

    tenant: str
    count: int
    products: list[ShelfProduct]


class ShelfCaptureResult(BaseModel):
    """Return shape for POST /economy/shelf/capture (Stripe webhook)."""

    model_config = ConfigDict(extra="forbid")

    ok: bool
    capture_id: Optional[str] = None
    reason: Optional[str] = None
    already_recorded: bool = False


__all__ = [
    "ShelfProduct",
    "ShelfCapture",
    "CheckoutSession",
    "ShelfProductList",
    "ShelfCaptureResult",
]


def _stripe_event_stub() -> dict[str, Any]:  # pragma: no cover
    """Shape reminder for tests — NOT a runtime contract."""
    return {
        "type": "checkout.session.completed",
        "data": {"object": {"id": "cs_test_...", "amount_total": 2900}},
    }
