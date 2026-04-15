"""SaaS billing — platform fees, usage metering, and reconciliation.

Tracks:
- Subscription revenue per tenant
- Platform fees on Glass transactions (5%)
- Usage metering (API calls, agent minutes, storage)
- Overage calculations per tier
"""
from __future__ import annotations

import logging
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from sos.services.saas.registry import TenantRegistry

log = logging.getLogger("sos.saas.billing")

DB_PATH = Path.home() / ".sos" / "data" / "squads.db"

# Tier limits
TIER_LIMITS: dict[str, dict[str, int]] = {
    "starter": {
        "api_calls_monthly": 10_000,
        "agent_minutes_monthly": 60,
        "storage_mb": 500,
        "squads": 1,
    },
    "growth": {
        "api_calls_monthly": 50_000,
        "agent_minutes_monthly": 300,
        "storage_mb": 2_000,
        "squads": 3,
    },
    "scale": {
        "api_calls_monthly": 500_000,
        "agent_minutes_monthly": -1,  # unlimited
        "storage_mb": 10_000,
        "squads": -1,  # unlimited
    },
}

PLATFORM_FEE_RATE = 0.05  # 5% on Glass transactions

# Plan base prices in cents
PLAN_PRICES: dict[str, int] = {
    "starter": 2900,
    "growth": 7900,
    "scale": 19900,
}

# Overage rates in cents per unit over limit
OVERAGE_RATES: dict[str, int] = {
    "api_calls_monthly": 1,
    "agent_minutes_monthly": 10,
    "storage_mb": 2,
}


class SaaSBilling:
    def __init__(self, registry: Optional[TenantRegistry] = None) -> None:
        self.registry = registry or TenantRegistry()
        self._ensure_tables()

    def _conn(self) -> sqlite3.Connection:
        conn = sqlite3.connect(str(DB_PATH))
        conn.row_factory = sqlite3.Row
        return conn

    def _now(self) -> str:
        return datetime.now(timezone.utc).isoformat()

    def _current_period(self) -> str:
        return datetime.now(timezone.utc).strftime("%Y-%m")

    def _ensure_tables(self) -> None:
        with self._conn() as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS saas_usage (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    tenant_slug TEXT NOT NULL,
                    metric TEXT NOT NULL,
                    quantity INTEGER NOT NULL,
                    billing_period TEXT NOT NULL,
                    recorded_at TEXT NOT NULL
                )
            """)
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_saas_usage_tenant "
                "ON saas_usage(tenant_slug, billing_period)"
            )

            conn.execute("""
                CREATE TABLE IF NOT EXISTS saas_transactions (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    tenant_slug TEXT NOT NULL,
                    tx_type TEXT NOT NULL,
                    amount_cents INTEGER NOT NULL,
                    currency TEXT DEFAULT 'usd',
                    description TEXT,
                    stripe_id TEXT,
                    platform_fee_cents INTEGER DEFAULT 0,
                    created_at TEXT NOT NULL
                )
            """)
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_saas_tx_tenant "
                "ON saas_transactions(tenant_slug, created_at DESC)"
            )

    def record_usage(self, tenant_slug: str, metric: str, quantity: int) -> None:
        """Record a usage event (api_calls, agent_minutes, storage_mb)."""
        period = self._current_period()
        with self._conn() as conn:
            conn.execute(
                "INSERT INTO saas_usage "
                "(tenant_slug, metric, quantity, billing_period, recorded_at) "
                "VALUES (?, ?, ?, ?, ?)",
                (tenant_slug, metric, quantity, period, self._now()),
            )

    def get_usage(self, tenant_slug: str, period: Optional[str] = None) -> dict:
        """Get aggregated usage for a tenant in a billing period."""
        period = period or self._current_period()
        with self._conn() as conn:
            rows = conn.execute(
                "SELECT metric, SUM(quantity) as total "
                "FROM saas_usage "
                "WHERE tenant_slug = ? AND billing_period = ? "
                "GROUP BY metric",
                (tenant_slug, period),
            ).fetchall()
        usage: dict[str, int] = {row["metric"]: row["total"] for row in rows}

        tenant = self.registry.get(tenant_slug)
        plan_key = tenant.plan.value if tenant else "starter"
        limits = TIER_LIMITS.get(plan_key, TIER_LIMITS["starter"])

        overages: dict[str, int] = {}
        for metric, limit in limits.items():
            if limit == -1:  # unlimited
                continue
            current = usage.get(metric, 0)
            if current > limit:
                overages[metric] = current - limit

        return {
            "period": period,
            "usage": usage,
            "limits": limits,
            "overages": overages,
        }

    def record_transaction(
        self,
        tenant_slug: str,
        tx_type: str,
        amount_cents: int,
        description: str = "",
        stripe_id: str = "",
    ) -> int:
        """Record a financial transaction and calculate platform fee."""
        platform_fee = (
            int(amount_cents * PLATFORM_FEE_RATE)
            if tx_type == "glass_sale"
            else 0
        )
        with self._conn() as conn:
            cursor = conn.execute(
                "INSERT INTO saas_transactions "
                "(tenant_slug, tx_type, amount_cents, description, "
                "stripe_id, platform_fee_cents, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                (
                    tenant_slug,
                    tx_type,
                    amount_cents,
                    description,
                    stripe_id,
                    platform_fee,
                    self._now(),
                ),
            )
            return cursor.lastrowid or 0

    def get_revenue(
        self,
        tenant_slug: Optional[str] = None,
        period: Optional[str] = None,
    ) -> dict:
        """Get revenue summary -- per tenant or platform-wide."""
        period = period or self._current_period()
        with self._conn() as conn:
            if tenant_slug:
                rows = conn.execute(
                    "SELECT tx_type, SUM(amount_cents) as total, "
                    "SUM(platform_fee_cents) as fees, COUNT(*) as count "
                    "FROM saas_transactions "
                    "WHERE tenant_slug = ? AND created_at LIKE ? "
                    "GROUP BY tx_type",
                    (tenant_slug, f"{period}%"),
                ).fetchall()
            else:
                rows = conn.execute(
                    "SELECT tx_type, SUM(amount_cents) as total, "
                    "SUM(platform_fee_cents) as fees, COUNT(*) as count "
                    "FROM saas_transactions "
                    "WHERE created_at LIKE ? "
                    "GROUP BY tx_type",
                    (f"{period}%",),
                ).fetchall()

        breakdown = {
            row["tx_type"]: {
                "total_cents": row["total"],
                "platform_fee_cents": row["fees"],
                "count": row["count"],
            }
            for row in rows
        }
        total_revenue = sum(r["total"] for r in rows)
        total_fees = sum(r["fees"] for r in rows)

        return {
            "period": period,
            "tenant": tenant_slug,
            "total_revenue_cents": total_revenue,
            "total_platform_fees_cents": total_fees,
            "breakdown": breakdown,
        }

    def get_tenant_invoice(self, tenant_slug: str) -> dict:
        """Generate invoice data for a tenant's current billing period."""
        tenant = self.registry.get(tenant_slug)
        if not tenant:
            return {"error": f"Tenant {tenant_slug} not found"}

        period = self._current_period()
        usage = self.get_usage(tenant_slug, period)
        revenue = self.get_revenue(tenant_slug, period)

        base_cents = PLAN_PRICES.get(tenant.plan.value, 2900)

        overage_cents = 0
        for metric, overage in usage.get("overages", {}).items():
            rate = OVERAGE_RATES.get(metric, 0)
            overage_cents += overage * rate

        return {
            "tenant": tenant_slug,
            "plan": tenant.plan.value,
            "period": period,
            "base_cents": base_cents,
            "overage_cents": overage_cents,
            "platform_fees_earned_cents": revenue.get(
                "total_platform_fees_cents", 0
            ),
            "total_due_cents": base_cents + overage_cents,
            "usage": usage,
        }
