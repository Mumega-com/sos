"""
§1A Principals & Role Registry — identity spine for SSO and DISP-001.

Gate: Athena G6 (schema shipped in migrations 023 + 025)

Manages:
  - principals (humans, agents, services) across tenants
  - roles / role_permissions / role_assignments
  - role introspection for DISP-001 token issuance
  - MFA requirement evaluation (reads roles — does not touch mfa_enrolled_methods)
  - §6.11 PIPEDA erasure via deactivate_principal() — delegates to DB function

Constitutional constraints:
  1. Secrets (TOTP seeds, WebAuthn keys) never live here.
     They live in Vault (2B.4). This module stores only opaque refs.
  2. Role assignments emit a log entry on revoke; they are delete-not-soft-delete
     because the audit_chain trigger on audit_events is the append-only record.
  3. Seed roles (role:sos:*) are read-only — code enforces this with a prefix guard.
  4. Principal erasure MUST go through deactivate_principal() — never raw UPDATE.
     The DB function is SECURITY DEFINER and owns the audit trail.

DB: psycopg2 sync against MIRROR_DATABASE_URL or DATABASE_URL.
"""
from __future__ import annotations

import logging
import os
import uuid
from datetime import datetime, timezone
from typing import Literal

import psycopg2
import psycopg2.extras
from pydantic import BaseModel, ConfigDict

log = logging.getLogger(__name__)

# ── Types ──────────────────────────────────────────────────────────────────────

PrincipalType = Literal['human', 'agent', 'service']
PrincipalStatus = Literal['active', 'suspended', 'deprovisioned', 'deactivated']
AssigneeType = Literal['agent', 'human', 'service']

_SEED_PREFIX = 'role:sos:'


class Principal(BaseModel):
    model_config = ConfigDict(frozen=True)

    id: str
    tenant_id: str
    email: str | None
    display_name: str | None
    principal_type: PrincipalType
    status: PrincipalStatus
    mfa_required: bool
    last_login_at: datetime | None
    created_at: datetime
    deactivated_at: datetime | None = None


class Role(BaseModel):
    model_config = ConfigDict(frozen=True)

    id: str
    project_id: str
    tenant_id: str
    name: str
    description: str | None
    mfa_required: bool
    created_at: datetime


class RoleAssignment(BaseModel):
    model_config = ConfigDict(frozen=True)

    role_id: str
    assignee_id: str
    assignee_type: AssigneeType
    assigned_at: datetime
    assigned_by: str


# ── DB ─────────────────────────────────────────────────────────────────────────


def _db_url() -> str:
    url = os.getenv('MIRROR_DATABASE_URL') or os.getenv('DATABASE_URL')
    if not url:
        raise RuntimeError('MIRROR_DATABASE_URL or DATABASE_URL not set')
    return url


def _connect():
    return psycopg2.connect(_db_url(), cursor_factory=psycopg2.extras.RealDictCursor)


def _new_id(prefix: str = 'id') -> str:
    return f'{prefix}-{uuid.uuid4().hex[:12]}'


# ── Principal helpers ──────────────────────────────────────────────────────────


def _row_to_principal(row: dict) -> Principal:
    return Principal(
        id=row['id'],
        tenant_id=row['tenant_id'],
        email=row['email'],
        display_name=row['display_name'],
        principal_type=row['principal_type'],
        status=row['status'],
        mfa_required=row['mfa_required'],
        last_login_at=row['last_login_at'],
        created_at=row['created_at'],
        deactivated_at=row.get('deactivated_at'),
    )


def _row_to_role(row: dict) -> Role:
    return Role(
        id=row['id'],
        project_id=row['project_id'],
        tenant_id=row['tenant_id'],
        name=row['name'],
        description=row['description'],
        mfa_required=row['mfa_required'],
        created_at=row['created_at'],
    )


# ── Principal reads ────────────────────────────────────────────────────────────


def get_principal(principal_id: str) -> Principal | None:
    with _connect() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """SELECT id, tenant_id, email, display_name, principal_type,
                          status, mfa_required, last_login_at, created_at, deactivated_at
                     FROM principals WHERE id = %s""",
                (principal_id,),
            )
            row = cur.fetchone()
    if not row:
        return None
    return _row_to_principal(row)


def get_principal_by_email(email: str, tenant_id: str = 'default') -> Principal | None:
    with _connect() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """SELECT id, tenant_id, email, display_name, principal_type,
                          status, mfa_required, last_login_at, created_at, deactivated_at
                     FROM principals
                    WHERE tenant_id = %s AND email = %s""",
                (tenant_id, email),
            )
            row = cur.fetchone()
    if not row:
        return None
    return _row_to_principal(row)


# ── Principal mutations ────────────────────────────────────────────────────────


def upsert_principal(
    *,
    email: str | None = None,
    display_name: str | None = None,
    principal_type: PrincipalType = 'human',
    tenant_id: str = 'default',
    principal_id: str | None = None,
) -> Principal:
    """
    Create or update a principal. Idempotent on (tenant_id, email).
    Used by SSO JIT provisioning — first login creates the principal.
    If email is None, principal_id must be supplied (non-human principals).
    """
    pid = principal_id or _new_id('pid')
    with _connect() as conn:
        with conn.cursor() as cur:
            if email:
                cur.execute(
                    """INSERT INTO principals (id, tenant_id, email, display_name, principal_type)
                           VALUES (%s, %s, %s, %s, %s)
                       ON CONFLICT (tenant_id, email)
                       DO UPDATE SET
                           display_name = COALESCE(EXCLUDED.display_name, principals.display_name),
                           updated_at   = now()
                       RETURNING id, tenant_id, email, display_name, principal_type,
                                 status, mfa_required, last_login_at, created_at, deactivated_at""",
                    (pid, tenant_id, email, display_name, principal_type),
                )
            else:
                cur.execute(
                    """INSERT INTO principals (id, tenant_id, email, display_name, principal_type)
                           VALUES (%s, %s, NULL, %s, %s)
                       ON CONFLICT (id)
                       DO UPDATE SET
                           display_name = COALESCE(EXCLUDED.display_name, principals.display_name),
                           updated_at   = now()
                       RETURNING id, tenant_id, email, display_name, principal_type,
                                 status, mfa_required, last_login_at, created_at, deactivated_at""",
                    (pid, tenant_id, display_name, principal_type),
                )
            row = cur.fetchone()
        conn.commit()
    return _row_to_principal(row)


def update_last_login(principal_id: str) -> None:
    with _connect() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "UPDATE principals SET last_login_at = now(), updated_at = now() WHERE id = %s",
                (principal_id,),
            )
        conn.commit()


def set_principal_status(
    principal_id: str,
    status: PrincipalStatus,
    *,
    updated_by: str,
) -> None:
    with _connect() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "UPDATE principals SET status = %s, updated_at = now() WHERE id = %s",
                (status, principal_id),
            )
        conn.commit()
    log.info(
        'principal %s status → %s (by %s)', principal_id, status, updated_by,
    )


def list_principals_by_tenant(
    tenant_id: str,
    *,
    limit: int = 50,
    offset: int = 0,
) -> list[Principal]:
    """Return principals for a tenant ordered by created_at. Used by SCIM list endpoint."""
    with _connect() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """SELECT id, tenant_id, email, display_name, principal_type,
                          status, mfa_required, last_login_at, created_at, deactivated_at
                     FROM principals
                    WHERE tenant_id = %s AND status != 'deactivated'
                    ORDER BY created_at
                    LIMIT %s OFFSET %s""",
                (tenant_id, limit, offset),
            )
            rows = cur.fetchall()
    return [_row_to_principal(r) for r in rows]


def deactivate_principal(principal_id: str, *, requested_by: str) -> None:
    """
    §6.11 PIPEDA erasure — delegates entirely to the SECURITY DEFINER DB function.

    The DB function owns the audit trail:
      - anonymize_profile(): nulls email + display_name, per-field audit events
      - status → 'deactivated', deactivated_at = now()
      - hard-DELETE sso_identity_links
      - disable mfa_enrolled_methods
      - final erasure_complete audit event

    mirror_engrams are NOT touched (system continuity, not personal data).
    The principal row (id, tenant_id, principal_type, status, deactivated_at) is retained
    as a reactivation token carrier per the "nullify and confiscate" model.
    """
    with _connect() as conn:
        with conn.cursor() as cur:
            cur.execute('SELECT deactivate_principal(%s, %s)', (principal_id, requested_by))
        conn.commit()
    log.info('principal %s deactivated by %s (§6.11)', principal_id, requested_by)


# ── Role reads ─────────────────────────────────────────────────────────────────


def get_role(role_id: str) -> Role | None:
    with _connect() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """SELECT id, project_id, tenant_id, name, description,
                          mfa_required, created_at
                     FROM roles WHERE id = %s""",
                (role_id,),
            )
            row = cur.fetchone()
    if not row:
        return None
    return _row_to_role(row)


def list_roles(project_id: str, tenant_id: str = 'default') -> list[Role]:
    with _connect() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """SELECT id, project_id, tenant_id, name, description,
                          mfa_required, created_at
                     FROM roles
                    WHERE project_id = %s AND tenant_id = %s
                    ORDER BY name""",
                (project_id, tenant_id),
            )
            rows = cur.fetchall()
    return [_row_to_role(r) for r in rows]


def get_assignee_roles(assignee_id: str) -> list[Role]:
    """Return all roles held by an agent/human across all projects."""
    with _connect() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """SELECT r.id, r.project_id, r.tenant_id, r.name, r.description,
                          r.mfa_required, r.created_at
                     FROM roles r
                     JOIN role_assignments ra ON ra.role_id = r.id
                    WHERE ra.assignee_id = %s
                    ORDER BY r.project_id, r.name""",
                (assignee_id,),
            )
            rows = cur.fetchall()
    return [_row_to_role(r) for r in rows]


def get_role_permissions(role_id: str) -> list[str]:
    with _connect() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT permission FROM role_permissions WHERE role_id = %s ORDER BY permission",
                (role_id,),
            )
            return [r['permission'] for r in cur.fetchall()]


def get_role_assignments(role_id: str) -> list[RoleAssignment]:
    with _connect() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """SELECT role_id, assignee_id, assignee_type, assigned_at, assigned_by
                     FROM role_assignments WHERE role_id = %s ORDER BY assigned_at""",
                (role_id,),
            )
            rows = cur.fetchall()
    return [
        RoleAssignment(
            role_id=r['role_id'],
            assignee_id=r['assignee_id'],
            assignee_type=r['assignee_type'],
            assigned_at=r['assigned_at'],
            assigned_by=r['assigned_by'],
        )
        for r in rows
    ]


# ── Role mutations ─────────────────────────────────────────────────────────────


def create_role(
    *,
    project_id: str,
    name: str,
    description: str | None = None,
    tenant_id: str = 'default',
    mfa_required: bool = False,
) -> Role:
    role_id = f'role:{project_id}:{name}'
    with _connect() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """INSERT INTO roles (id, project_id, tenant_id, name, description, mfa_required)
                       VALUES (%s, %s, %s, %s, %s, %s)
                   ON CONFLICT (project_id, name, tenant_id) DO NOTHING
                   RETURNING id, project_id, tenant_id, name, description, mfa_required, created_at""",
                (role_id, project_id, tenant_id, name, description, mfa_required),
            )
            row = cur.fetchone()
        conn.commit()
    if not row:
        return get_role(role_id)  # type: ignore[return-value]
    return _row_to_role(row)


def add_permission(role_id: str, permission: str) -> None:
    if role_id.startswith(_SEED_PREFIX):
        raise ValueError(f'seed role {role_id!r} permissions are immutable')
    with _connect() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "INSERT INTO role_permissions (role_id, permission) VALUES (%s, %s) ON CONFLICT DO NOTHING",
                (role_id, permission),
            )
        conn.commit()


def remove_permission(role_id: str, permission: str) -> None:
    if role_id.startswith(_SEED_PREFIX):
        raise ValueError(f'seed role {role_id!r} permissions are immutable')
    with _connect() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "DELETE FROM role_permissions WHERE role_id = %s AND permission = %s",
                (role_id, permission),
            )
        conn.commit()


def assign_role(
    role_id: str,
    assignee_id: str,
    *,
    assignee_type: AssigneeType = 'human',
    assigned_by: str,
) -> RoleAssignment:
    now = datetime.now(timezone.utc)
    with _connect() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """INSERT INTO role_assignments (role_id, assignee_id, assignee_type, assigned_at, assigned_by)
                       VALUES (%s, %s, %s, %s, %s)
                   ON CONFLICT (role_id, assignee_id)
                   DO UPDATE SET assigned_by = EXCLUDED.assigned_by,
                                 assigned_at = EXCLUDED.assigned_at
                   RETURNING role_id, assignee_id, assignee_type, assigned_at, assigned_by""",
                (role_id, assignee_id, assignee_type, now, assigned_by),
            )
            row = cur.fetchone()
        conn.commit()
    return RoleAssignment(
        role_id=row['role_id'],
        assignee_id=row['assignee_id'],
        assignee_type=row['assignee_type'],
        assigned_at=row['assigned_at'],
        assigned_by=row['assigned_by'],
    )


def revoke_role(role_id: str, assignee_id: str, *, revoked_by: str) -> bool:
    """Returns True if an assignment was deleted, False if it didn't exist."""
    with _connect() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "DELETE FROM role_assignments WHERE role_id = %s AND assignee_id = %s",
                (role_id, assignee_id),
            )
            deleted = cur.rowcount > 0
        conn.commit()
    if deleted:
        log.info('role %s revoked from %s by %s', role_id, assignee_id, revoked_by)
    return deleted


# ── MFA requirement check ──────────────────────────────────────────────────────


def requires_mfa(assignee_id: str) -> bool:
    """True if any of the principal's assigned roles have mfa_required = true."""
    with _connect() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """SELECT 1 FROM roles r
                     JOIN role_assignments ra ON ra.role_id = r.id
                    WHERE ra.assignee_id = %s AND r.mfa_required = true
                    LIMIT 1""",
                (assignee_id,),
            )
            return cur.fetchone() is not None
