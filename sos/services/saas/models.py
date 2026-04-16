from __future__ import annotations

from enum import Enum
from typing import Optional

from pydantic import BaseModel, Field


class TenantPlan(str, Enum):
    STARTER = "starter"      # $29/mo
    GROWTH = "growth"        # $79/mo
    SCALE = "scale"          # $199/mo


class TenantStatus(str, Enum):
    PROVISIONING = "provisioning"
    ACTIVE = "active"
    SUSPENDED = "suspended"
    CANCELLED = "cancelled"


class Tenant(BaseModel):
    slug: str
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
    inkwell_config: Optional[dict] = None  # tenant-specific config overrides
    created_at: str
    updated_at: str


class TenantCreate(BaseModel):
    slug: str = Field(..., min_length=2, max_length=32, pattern="^[a-z0-9-]+$")
    label: str
    email: str
    plan: TenantPlan = TenantPlan.STARTER
    domain: Optional[str] = None
    # Questionnaire answers for site generation
    industry: Optional[str] = None
    services: Optional[list[str]] = None
    primary_color: Optional[str] = None
    tagline: Optional[str] = None


class TenantUpdate(BaseModel):
    label: Optional[str] = None
    domain: Optional[str] = None
    plan: Optional[TenantPlan] = None
    status: Optional[TenantStatus] = None
    telegram_chat_id: Optional[str] = None
    inkwell_config: Optional[dict] = None
    stripe_customer_id: Optional[str] = None
    stripe_subscription_id: Optional[str] = None
