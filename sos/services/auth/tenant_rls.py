"""Tenant row-level security — Sprint 011 OmniB.

LOCK-5: App MUST connect as mumega_app_role (PG RLS bypassed for superuser).
Both layers: PG RLS policies (defense-in-depth) + middleware enforcement.

Migration creates:
  - mumega_app_role PG role
  - RLS policies on gtm.* tables
  - SET app.tenant_id per-connection for RLS WHERE clauses

Middleware:
  - verify_tenant_scope(session, requested_tenant_id) → raises on mismatch
  - check_pg_role() → raises if connected as superuser
"""
from __future__ import annotations

import logging
import os
from typing import Any

log = logging.getLogger("sos.auth.tenant_rls")


class TenantScopeViolation(RuntimeError):
    """Tenant scope mismatch — request tried to access another tenant's data."""


class PgRoleViolation(RuntimeError):
    """App connected as postgres/superuser — RLS is bypassed."""


# ---------------------------------------------------------------------------
# Middleware: tenant scope check
# ---------------------------------------------------------------------------


def verify_tenant_scope(session_tenant_id: str, requested_tenant_id: str) -> None:
    """Verify the request's tenant matches the session's tenant.

    tenant_id comes from SESSION ONLY (Redis-backed, server-side).
    NEVER from caller-supplied parameter.

    Raises TenantScopeViolation on mismatch.
    """
    if not session_tenant_id:
        raise TenantScopeViolation("Session has no tenant_id — cannot verify scope")

    if session_tenant_id != requested_tenant_id:
        raise TenantScopeViolation(
            f"Tenant scope violation: session tenant={session_tenant_id!r}, "
            f"requested tenant={requested_tenant_id!r}"
        )


# ---------------------------------------------------------------------------
# PG role check
# ---------------------------------------------------------------------------


def check_pg_role(conn: Any) -> None:
    """Verify app is NOT connected as postgres/superuser.

    LOCK-5: PG RLS is bypassed for superuser. If the app connects as
    postgres, all RLS policies are theater.

    Call at service startup. Raises PgRoleViolation if unsafe.
    """
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT current_user, (SELECT rolsuper FROM pg_roles WHERE rolname = current_user)")
            row = cur.fetchone()
            if row:
                username, is_super = row
                if is_super:
                    raise PgRoleViolation(
                        f"App connected as superuser '{username}' — PG RLS is bypassed. "
                        f"Use mumega_app_role instead."
                    )
                if username in ("postgres", "mirror"):
                    log.warning(
                        "tenant_rls: connected as '%s' — verify this role has RLS enforced",
                        username,
                    )
    except PgRoleViolation:
        raise
    except Exception as exc:
        log.warning("tenant_rls: PG role check failed (non-fatal): %s", exc)


# ---------------------------------------------------------------------------
# PG connection: set tenant context for RLS
# ---------------------------------------------------------------------------


def set_tenant_context(conn: Any, tenant_id: str) -> None:
    """Set app.tenant_id on the PG connection for RLS policies.

    RLS policies use: current_setting('app.tenant_id')
    """
    try:
        with conn.cursor() as cur:
            cur.execute("SET app.tenant_id = %s", (tenant_id,))
    except Exception as exc:
        raise TenantScopeViolation(f"Failed to set tenant context: {exc}") from exc


# ---------------------------------------------------------------------------
# Audit coupling (LOCK-6: same transaction)
# ---------------------------------------------------------------------------


def audit_mutation(
    conn: Any,
    actor_id: str,
    action: str,
    resource: str,
    payload: dict | None = None,
) -> None:
    """Write audit record in SAME transaction as mutation.

    LOCK-6: If audit INSERT fails, mutation rolls back.
    No async bus for security mutations.
    """
    import json
    from datetime import datetime, timezone

    try:
        with conn.cursor() as cur:
            # Allocate seq via PG function
            cur.execute("SELECT audit_next_seq(%s)", ("kernel",))
            seq = cur.fetchone()[0]
            cur.execute(
                "INSERT INTO audit_events (stream_id, seq, actor_id, actor_type, action, resource, payload) "
                "VALUES (%s, %s, %s, %s, %s, %s, %s)",
                ("kernel", seq, actor_id, "user", action, resource,
                 json.dumps(payload or {})),
            )
    except Exception as exc:
        # LOCK-6: audit failure = mutation rollback
        raise RuntimeError(f"Audit write failed — mutation rolled back: {exc}") from exc
