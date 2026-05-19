"""Audit logging — records every tool call, auth event, and admin action."""
from __future__ import annotations

import json
import logging
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

log = logging.getLogger("sos.saas.audit")

DB_PATH = Path.home() / ".sos" / "data" / "squads.db"


class AuditLog:
    def __init__(self) -> None:
        self._ensure_table()

    def _conn(self) -> sqlite3.Connection:
        conn = sqlite3.connect(str(DB_PATH))
        conn.row_factory = sqlite3.Row
        return conn

    def _ensure_table(self) -> None:
        with self._conn() as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS audit_log (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    tenant_slug TEXT,
                    event_type TEXT NOT NULL,
                    tool_name TEXT,
                    actor TEXT,
                    ip_address TEXT,
                    details TEXT,
                    created_at TEXT DEFAULT (datetime('now'))
                )
            """)
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_audit_tenant ON audit_log(tenant_slug, created_at DESC)"
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_audit_type ON audit_log(event_type, created_at DESC)"
            )

    def log_event(
        self,
        event_type: str,
        tenant_slug: Optional[str] = None,
        tool_name: Optional[str] = None,
        actor: Optional[str] = None,
        ip_address: Optional[str] = None,
        details: Optional[dict] = None,
    ) -> None:
        """Log an audit event. Fire-and-forget — never blocks the caller."""
        try:
            with self._conn() as conn:
                conn.execute(
                    "INSERT INTO audit_log (tenant_slug, event_type, tool_name, actor, ip_address, details, created_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
                    (
                        tenant_slug,
                        event_type,
                        tool_name,
                        actor,
                        ip_address,
                        json.dumps(details) if details else None,
                        datetime.now(timezone.utc).isoformat(),
                    ),
                )
        except Exception as exc:
            log.warning("Audit log write failed: %s", exc)

    def query(
        self,
        tenant_slug: Optional[str] = None,
        event_type: Optional[str] = None,
        limit: int = 100,
    ) -> list[dict]:
        """Query audit log. For admin/dashboard use."""
        with self._conn() as conn:
            conditions: list[str] = []
            params: list[object] = []
            if tenant_slug:
                conditions.append("tenant_slug = ?")
                params.append(tenant_slug)
            if event_type:
                conditions.append("event_type = ?")
                params.append(event_type)
            where = f"WHERE {' AND '.join(conditions)}" if conditions else ""
            params.append(limit)
            rows = conn.execute(
                f"SELECT * FROM audit_log {where} ORDER BY created_at DESC LIMIT ?",
                params,
            ).fetchall()
        return [dict(r) for r in rows]


# Module-level singleton
_audit: Optional[AuditLog] = None


def get_audit() -> AuditLog:
    global _audit
    if _audit is None:
        _audit = AuditLog()
    return _audit


def log_tool_call(
    tenant: str,
    tool: str,
    actor: str = "",
    ip: str = "",
    details: Optional[dict] = None,
) -> None:
    get_audit().log_event("tool_call", tenant, tool, actor, ip, details)


def log_auth(
    event: str,
    tenant: str = "",
    actor: str = "",
    ip: str = "",
    details: Optional[dict] = None,
) -> None:
    get_audit().log_event(f"auth.{event}", tenant, None, actor, ip, details)


def log_admin(
    event: str,
    tenant: str = "",
    actor: str = "",
    details: Optional[dict] = None,
) -> None:
    get_audit().log_event(f"admin.{event}", tenant, None, actor, None, details)
