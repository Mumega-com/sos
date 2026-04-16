"""ToRivers Marketplace — browse, subscribe to, and sell agent squads and tools.

Listings are stored in the shared squads.db. A listing is a packageable
squad or skill that other tenants can subscribe to.
"""
from __future__ import annotations

import json
import logging
import secrets
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

log = logging.getLogger("sos.saas.marketplace")

DB_PATH = Path.home() / ".sos" / "data" / "squads.db"


class Marketplace:
    def __init__(self) -> None:
        self._ensure_tables()
        self.seed_defaults()

    def _conn(self) -> sqlite3.Connection:
        conn = sqlite3.connect(str(DB_PATH))
        conn.row_factory = sqlite3.Row
        return conn

    def _now(self) -> str:
        return datetime.now(timezone.utc).isoformat()

    def _ensure_tables(self) -> None:
        with self._conn() as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS marketplace_listings (
                    id TEXT PRIMARY KEY,
                    seller_tenant TEXT NOT NULL,
                    title TEXT NOT NULL,
                    description TEXT NOT NULL,
                    category TEXT NOT NULL,
                    listing_type TEXT NOT NULL,
                    price_cents INTEGER NOT NULL,
                    price_model TEXT NOT NULL DEFAULT 'monthly',
                    squad_template TEXT,
                    skill_ids TEXT,
                    tags TEXT DEFAULT '[]',
                    active INTEGER DEFAULT 1,
                    subscriber_count INTEGER DEFAULT 0,
                    rating REAL DEFAULT 0.0,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                )
            """)
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_listings_cat ON marketplace_listings(category, active)"
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_listings_seller ON marketplace_listings(seller_tenant)"
            )

            conn.execute("""
                CREATE TABLE IF NOT EXISTS marketplace_subscriptions (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    listing_id TEXT NOT NULL REFERENCES marketplace_listings(id),
                    buyer_tenant TEXT NOT NULL,
                    status TEXT DEFAULT 'active',
                    subscribed_at TEXT NOT NULL,
                    cancelled_at TEXT
                )
            """)
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_subs_buyer ON marketplace_subscriptions(buyer_tenant, status)"
            )

    def seed_defaults(self) -> None:
        """Seed starter listings if not already present (OR IGNORE is safe to call repeatedly)."""
        defaults = [
            (
                "lst-seo-audit",
                "mumega",
                "SEO Audit Squad",
                "Weekly technical SEO audit — crawl errors, meta tags, speed, schema markup. Report delivered every Monday.",
                "seo",
                "squad",
                4900,
                "monthly",
                ["seo", "audit", "technical"],
            ),
            (
                "lst-content-writer",
                "mumega",
                "Content Writing Squad",
                "3 blog posts per week, researched and SEO-optimized. Topics based on your industry and Glass analytics.",
                "content",
                "squad",
                7900,
                "monthly",
                ["content", "blog", "writing"],
            ),
            (
                "lst-lead-gen",
                "mumega",
                "Lead Generation Squad",
                "Outbound email campaigns. Prospect research, personalized outreach, CRM logging.",
                "outreach",
                "squad",
                9900,
                "monthly",
                ["leads", "email", "outreach"],
            ),
            (
                "lst-contract-gen",
                "mumega",
                "Contract Generator",
                "Generate professional e-sign contracts from a brief description. Includes SMS + email notifications.",
                "other",
                "tool",
                900,
                "monthly",
                ["contracts", "legal"],
            ),
            (
                "lst-grant-scanner",
                "mumega",
                "Canadian Grant Scanner",
                "Scan 200+ Canadian government grants for eligibility. Returns matched programs with deadlines.",
                "data",
                "tool",
                1900,
                "monthly",
                ["grants", "canada", "funding"],
            ),
        ]
        with self._conn() as conn:
            for d in defaults:
                conn.execute(
                    """INSERT OR IGNORE INTO marketplace_listings
                       (id, seller_tenant, title, description, category, listing_type,
                        price_cents, price_model, tags, active, created_at, updated_at)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 1, ?, ?)""",
                    (*d[:8], json.dumps(d[8]), self._now(), self._now()),
                )

    def browse(
        self,
        category: Optional[str] = None,
        query: Optional[str] = None,
        limit: int = 20,
    ) -> list[dict]:
        """Browse marketplace listings."""
        with self._conn() as conn:
            if query:
                rows = conn.execute(
                    """SELECT * FROM marketplace_listings
                       WHERE active = 1
                         AND (title LIKE ? OR description LIKE ? OR tags LIKE ?)
                       ORDER BY subscriber_count DESC LIMIT ?""",
                    (f"%{query}%", f"%{query}%", f"%{query}%", limit),
                ).fetchall()
            elif category:
                rows = conn.execute(
                    """SELECT * FROM marketplace_listings
                       WHERE active = 1 AND category = ?
                       ORDER BY subscriber_count DESC LIMIT ?""",
                    (category, limit),
                ).fetchall()
            else:
                rows = conn.execute(
                    """SELECT * FROM marketplace_listings
                       WHERE active = 1
                       ORDER BY subscriber_count DESC LIMIT ?""",
                    (limit,),
                ).fetchall()
        return [dict(r) for r in rows]

    def subscribe(self, buyer_tenant: str, listing_id: str) -> dict:
        """Subscribe a tenant to a marketplace listing."""
        with self._conn() as conn:
            listing = conn.execute(
                "SELECT * FROM marketplace_listings WHERE id = ? AND active = 1",
                (listing_id,),
            ).fetchone()
            if not listing:
                return {"error": "Listing not found", "success": False}

            existing = conn.execute(
                """SELECT id FROM marketplace_subscriptions
                   WHERE listing_id = ? AND buyer_tenant = ? AND status = 'active'""",
                (listing_id, buyer_tenant),
            ).fetchone()
            if existing:
                return {"error": "Already subscribed", "success": False}

            conn.execute(
                """INSERT INTO marketplace_subscriptions
                   (listing_id, buyer_tenant, status, subscribed_at)
                   VALUES (?, ?, 'active', ?)""",
                (listing_id, buyer_tenant, self._now()),
            )
            conn.execute(
                """UPDATE marketplace_listings
                   SET subscriber_count = subscriber_count + 1
                   WHERE id = ?""",
                (listing_id,),
            )
        return {
            "success": True,
            "listing": dict(listing),
            "message": f"Subscribed to {listing['title']}",
        }

    def my_subscriptions(self, tenant: str) -> list[dict]:
        """Get tenant's active subscriptions."""
        with self._conn() as conn:
            rows = conn.execute(
                """SELECT s.*, l.title, l.description, l.price_cents, l.price_model, l.category
                   FROM marketplace_subscriptions s
                   JOIN marketplace_listings l ON s.listing_id = l.id
                   WHERE s.buyer_tenant = ? AND s.status = 'active'""",
                (tenant,),
            ).fetchall()
        return [dict(r) for r in rows]

    def create_listing(
        self,
        seller_tenant: str,
        title: str,
        description: str,
        category: str,
        listing_type: str,
        price_cents: int,
        price_model: str = "monthly",
        tags: Optional[list[str]] = None,
    ) -> dict:
        """Create a new marketplace listing."""
        listing_id = f"lst-{secrets.token_hex(8)}"
        now = self._now()
        with self._conn() as conn:
            conn.execute(
                """INSERT INTO marketplace_listings
                   (id, seller_tenant, title, description, category, listing_type,
                    price_cents, price_model, tags, created_at, updated_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    listing_id,
                    seller_tenant,
                    title,
                    description,
                    category,
                    listing_type,
                    price_cents,
                    price_model,
                    json.dumps(tags or []),
                    now,
                    now,
                ),
            )
        return {"success": True, "listing_id": listing_id, "title": title}

    def my_earnings(self, seller_tenant: str) -> dict:
        """Get earnings for a seller."""
        with self._conn() as conn:
            listings = conn.execute(
                """SELECT id, title, subscriber_count, price_cents
                   FROM marketplace_listings WHERE seller_tenant = ?""",
                (seller_tenant,),
            ).fetchall()
        total_mrr = sum(r["subscriber_count"] * r["price_cents"] for r in listings)
        return {
            "listings": [dict(r) for r in listings],
            "total_mrr_cents": total_mrr,
            "platform_fee_cents": int(total_mrr * 0.05),
            "net_earnings_cents": total_mrr - int(total_mrr * 0.05),
        }

    def unsubscribe(self, buyer_tenant: str, listing_id: str) -> dict:
        """Cancel a subscription."""
        with self._conn() as conn:
            conn.execute(
                """UPDATE marketplace_subscriptions
                   SET status = 'cancelled', cancelled_at = ?
                   WHERE listing_id = ? AND buyer_tenant = ? AND status = 'active'""",
                (self._now(), listing_id, buyer_tenant),
            )
            conn.execute(
                """UPDATE marketplace_listings
                   SET subscriber_count = MAX(0, subscriber_count - 1)
                   WHERE id = ?""",
                (listing_id,),
            )
        return {"success": True, "message": "Subscription cancelled"}
