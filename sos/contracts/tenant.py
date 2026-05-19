"""Tenant contract — shared shape for the SOS SaaS layer.

A Tenant is a single customer/workspace: billing, domain, squad attachment.
Shared between saas (registry), billing (Stripe webhook mutations) and cli
(onboarding) so no service hand-rolls this shape.

The JSON Schema at ``sos/contracts/schemas/tenant_v1.json`` is the
cross-language source of truth; these Pydantic models are the Python binding.
"""
from __future__ import annotations

import json
from enum import Enum
from pathlib import Path
from typing import Any, Optional

from pydantic import BaseModel, ConfigDict, Field

SCHEMA_PATH = Path(__file__).parent / "schemas" / "tenant_v1.json"


class TenantPlan(str, Enum):
    """Billing plan tier."""

    STARTER = "starter"  # $29/mo
    GROWTH = "growth"    # $79/mo
    SCALE = "scale"      # $199/mo


class TenantStatus(str, Enum):
    """Tenant lifecycle status."""

    PROVISIONING = "provisioning"
    ACTIVE = "active"
    SUSPENDED = "suspended"
    CANCELLED = "cancelled"


class Tenant(BaseModel):
    """Full tenant record as persisted in the SaaS registry."""

    model_config = ConfigDict(extra="forbid")

    slug: str = Field(min_length=2, max_length=32, pattern=r"^[a-z0-9-]+$")
    label: str
    email: str
    domain: Optional[str] = None
    subdomain: str  # {slug}.mumega.com
    stripe_customer_id: Optional[str] = None
    stripe_subscription_id: Optional[str] = None
    plan: TenantPlan = TenantPlan.STARTER
    status: TenantStatus = TenantStatus.PROVISIONING
    squad_id: Optional[str] = None
    mirror_project: Optional[str] = None
    bus_token: Optional[str] = None
    telegram_chat_id: Optional[str] = None
    inkwell_config: Optional[dict] = None
    created_at: str
    updated_at: str


class TenantCreate(BaseModel):
    """Input payload for creating a new tenant.

    Optional questionnaire fields feed initial site generation.
    """

    model_config = ConfigDict(extra="forbid")

    slug: str = Field(min_length=2, max_length=32, pattern=r"^[a-z0-9-]+$")
    label: str
    email: str
    plan: TenantPlan = TenantPlan.STARTER
    domain: Optional[str] = None
    industry: Optional[str] = None
    services: Optional[list[str]] = None
    primary_color: Optional[str] = None
    tagline: Optional[str] = None


class TenantUpdate(BaseModel):
    """Partial update payload — every field optional."""

    model_config = ConfigDict(extra="forbid")

    label: Optional[str] = None
    domain: Optional[str] = None
    plan: Optional[TenantPlan] = None
    status: Optional[TenantStatus] = None
    telegram_chat_id: Optional[str] = None
    inkwell_config: Optional[dict] = None
    stripe_customer_id: Optional[str] = None
    stripe_subscription_id: Optional[str] = None


def load_schema() -> dict[str, Any]:
    """Return the JSON Schema document. Cross-language source of truth."""
    return json.loads(SCHEMA_PATH.read_text())


__all__ = [
    "Tenant",
    "TenantCreate",
    "TenantUpdate",
    "TenantPlan",
    "TenantStatus",
    "load_schema",
    "SCHEMA_PATH",
]
