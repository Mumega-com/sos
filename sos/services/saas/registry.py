from __future__ import annotations

import json
import logging
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from sos.services.saas.models import (
    Tenant,
    TenantCreate,
    TenantStatus,
    TenantUpdate,
)

log = logging.getLogger("sos.saas.registry")

DB_PATH = Path.home() / ".sos" / "data" / "squads.db"


class TenantRegistry:
    def __init__(self, db_path: Path = DB_PATH) -> None:
        self.db_path = db_path
        self._ensure_table()

    def _conn(self) -> sqlite3.Connection:
        conn = sqlite3.connect(str(self.db_path))
        conn.row_factory = sqlite3.Row
        return conn

    def _ensure_table(self) -> None:
        with self._conn() as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS tenants (
                    slug TEXT PRIMARY KEY,
                    label TEXT NOT NULL,
                    email TEXT NOT NULL,
                    domain TEXT,
                    subdomain TEXT NOT NULL,
                    stripe_customer_id TEXT,
                    stripe_subscription_id TEXT,
                    plan TEXT NOT NULL DEFAULT 'starter',
                    status TEXT NOT NULL DEFAULT 'provisioning',
                    squad_id TEXT,
                    mirror_project TEXT,
                    bus_token TEXT,
                    telegram_chat_id TEXT,
                    inkwell_config TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                )
            """)
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_tenants_status ON tenants(status)"
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_tenants_domain ON tenants(domain)"
            )

    def _now(self) -> str:
        return datetime.now(timezone.utc).isoformat()

    def create(self, req: TenantCreate) -> Tenant:
        now = self._now()
        tenant = Tenant(
            slug=req.slug,
            label=req.label,
            email=req.email,
            domain=req.domain,
            subdomain=f"{req.slug}.mumega.com",
            plan=req.plan,
            status=TenantStatus.PROVISIONING,
            mirror_project=f"inkwell-{req.slug}",
            inkwell_config=self._generate_config(req),
            created_at=now,
            updated_at=now,
        )
        with self._conn() as conn:
            conn.execute(
                """INSERT INTO tenants
                   (slug, label, email, domain, subdomain, plan, status,
                    mirror_project, inkwell_config, created_at, updated_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    tenant.slug,
                    tenant.label,
                    tenant.email,
                    tenant.domain,
                    tenant.subdomain,
                    tenant.plan.value,
                    tenant.status.value,
                    tenant.mirror_project,
                    json.dumps(tenant.inkwell_config) if tenant.inkwell_config else None,
                    tenant.created_at,
                    tenant.updated_at,
                ),
            )
        log.info("Tenant %s created", tenant.slug)
        return tenant

    def get(self, slug: str) -> Optional[Tenant]:
        with self._conn() as conn:
            row = conn.execute(
                "SELECT * FROM tenants WHERE slug = ?", (slug,)
            ).fetchone()
        if not row:
            return None
        return self._row_to_tenant(row)

    def list(self, status: Optional[str] = None) -> list[Tenant]:
        with self._conn() as conn:
            if status:
                rows = conn.execute(
                    "SELECT * FROM tenants WHERE status = ? ORDER BY created_at DESC",
                    (status,),
                ).fetchall()
            else:
                rows = conn.execute(
                    "SELECT * FROM tenants ORDER BY created_at DESC"
                ).fetchall()
        return [self._row_to_tenant(r) for r in rows]

    def update(self, slug: str, req: TenantUpdate) -> Optional[Tenant]:
        tenant = self.get(slug)
        if not tenant:
            return None
        updates: dict[str, str | None] = {}
        if req.label is not None:
            updates["label"] = req.label
        if req.domain is not None:
            updates["domain"] = req.domain
        if req.plan is not None:
            updates["plan"] = req.plan.value
        if req.status is not None:
            updates["status"] = req.status.value
        if req.telegram_chat_id is not None:
            updates["telegram_chat_id"] = req.telegram_chat_id
        if req.inkwell_config is not None:
            updates["inkwell_config"] = json.dumps(req.inkwell_config)
        if not updates:
            return tenant
        updates["updated_at"] = self._now()
        set_clause = ", ".join(f"{k} = ?" for k in updates)
        values = list(updates.values()) + [slug]
        with self._conn() as conn:
            conn.execute(
                f"UPDATE tenants SET {set_clause} WHERE slug = ?",  # noqa: S608
                values,
            )
        return self.get(slug)

    def activate(self, slug: str, squad_id: str, bus_token: str) -> Optional[Tenant]:
        """Mark tenant as active after provisioning completes."""
        now = self._now()
        with self._conn() as conn:
            conn.execute(
                "UPDATE tenants SET status = 'active', squad_id = ?, bus_token = ?, updated_at = ? WHERE slug = ?",
                (squad_id, bus_token, now, slug),
            )
        return self.get(slug)

    def resolve_domain(self, hostname: str) -> Optional[Tenant]:
        """Resolve tenant from hostname (subdomain or custom domain)."""
        with self._conn() as conn:
            # Try custom domain first
            row = conn.execute(
                "SELECT * FROM tenants WHERE domain = ? AND status = 'active'",
                (hostname,),
            ).fetchone()
            if row:
                return self._row_to_tenant(row)
            # Try subdomain pattern
            if hostname.endswith(".mumega.com"):
                slug = hostname.replace(".mumega.com", "")
                row = conn.execute(
                    "SELECT * FROM tenants WHERE slug = ? AND status = 'active'",
                    (slug,),
                ).fetchone()
                if row:
                    return self._row_to_tenant(row)
        return None

    def _row_to_tenant(self, row: sqlite3.Row) -> Tenant:
        d = dict(row)
        if d.get("inkwell_config") and isinstance(d["inkwell_config"], str):
            d["inkwell_config"] = json.loads(d["inkwell_config"])
        return Tenant(**d)

    def _generate_config(self, req: TenantCreate) -> dict:
        """Generate inkwell.config overrides from questionnaire answers."""
        config: dict = {
            "name": req.label,
            "domain": req.domain or f"{req.slug}.mumega.com",
        }
        if req.primary_color:
            config["theme"] = {"colors": {"primary": req.primary_color}}
        if req.tagline:
            config["tagline"] = req.tagline
        if req.industry:
            config["industry"] = req.industry
        if req.services:
            config["services"] = req.services
        return config
