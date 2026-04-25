"""
§14 Inventory — Unified Capability Read (Sprint 003 Track C).

Gate: Athena G9 APPROVED v1.1
Migration: 021_inventory.sql (inventory_grants)

One read, many sources. Inventory is an index, not a new authority.
Source domains stay where they are; inventory_grants is the unifying lookup.

Contract surface:
  - Capability type (immutable snapshot)
  - list_capabilities / assert_capability (reads — hot path, no I/O on verifiers)
  - grant_capability / revoke_capability / reverify (audited mutations)
  - VERIFIERS registry — pluggable, one per capability_kind, registered at boot
  - 8 built-in verifiers wired in _register_defaults()

DB: psycopg2 (sync) against MIRROR_DATABASE_URL or DATABASE_URL.
Audit: async emit_audit to stream='inventory' (fire-and-forget, never fatal).
"""
from __future__ import annotations

import asyncio
import hashlib
import logging
import os
from datetime import datetime, timezone
from typing import Any, Callable, Literal

import psycopg2
import psycopg2.extras
from pydantic import BaseModel, ConfigDict

log = logging.getLogger(__name__)

# ── Literal type aliases ───────────────────────────────────────────────────────

HolderType      = Literal['human', 'agent', 'squad', 'guild']
CapabilityKind  = Literal[
    'credential', 'tool', 'automation', 'template',
    'oauth_connection', 'guild_role', 'data_access', 'mcp_server',
]
GrantStatus     = Literal['active', 'stale', 'orphaned', 'revoked', 'expired']

# Verifier return type: (is_valid, status_hint)
VerifyResult    = tuple[bool, GrantStatus]
VerifyFn        = Callable[[str], VerifyResult]

# ── Types ──────────────────────────────────────────────────────────────────────


class Capability(BaseModel):
    """Immutable snapshot of one inventory_grants row."""

    model_config = ConfigDict(frozen=True)

    grant_id: str
    holder_type: HolderType
    holder_id: str
    kind: CapabilityKind
    ref: str                            # soft pointer into source domain
    source_domain: str
    scope: dict[str, Any] | None
    granted_by: str
    granted_at: datetime
    expires_at: datetime | None
    last_verified_at: datetime
    verify_attempt_count: int
    last_error: str | None
    status: GrantStatus


# ── DB connection ──────────────────────────────────────────────────────────────


def _db_url() -> str:
    url = os.getenv('MIRROR_DATABASE_URL') or os.getenv('DATABASE_URL')
    if not url:
        raise RuntimeError(
            'MIRROR_DATABASE_URL or DATABASE_URL is not set — '
            'inventory contract cannot connect to Mirror'
        )
    return url


def _connect():
    return psycopg2.connect(_db_url(), cursor_factory=psycopg2.extras.RealDictCursor)


# ── Grant ID generation ────────────────────────────────────────────────────────


def _grant_id(holder_id: str, kind: str, ref: str) -> str:
    """Deterministic grant ID: inv:{kind}:{holder_id}:{ref_hash8}."""
    ref_hash = hashlib.sha256(ref.encode()).hexdigest()[:8]
    return f'inv:{kind}:{holder_id}:{ref_hash}'


# ── Audit emission (fire-and-forget, never fatal) ──────────────────────────────


def _emit_audit(
    action: str,
    actor: str,
    resource: str,
    payload: dict[str, Any] | None = None,
) -> None:
    try:
        from sos.kernel.audit_chain import AuditChainEvent, emit_audit

        event = AuditChainEvent(
            stream_id='inventory',
            actor_id=actor,
            actor_type='human',
            action=action,
            resource=resource,
            payload=payload,
        )
        loop = None
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            pass
        if loop and loop.is_running():
            asyncio.ensure_future(emit_audit(event))
        else:
            asyncio.run(emit_audit(event))
    except Exception:  # noqa: BLE001
        log.debug('inventory audit emission failed (non-fatal)', exc_info=True)


# ── Verifier registry ──────────────────────────────────────────────────────────

# Populated by register_verifier() and _register_defaults() at module init.
VERIFIERS: dict[str, VerifyFn] = {}


def register_verifier(kind: str, verify_fn: VerifyFn) -> None:
    """Register a per-kind verifier. Called at service boot."""
    VERIFIERS[kind] = verify_fn


def _row_to_capability(row: dict[str, Any]) -> Capability:
    return Capability(
        grant_id=row['grant_id'],
        holder_type=row['holder_type'],
        holder_id=row['holder_id'],
        kind=row['capability_kind'],
        ref=row['capability_ref'],
        source_domain=row['source_domain'],
        scope=row['scope'],
        granted_by=row['granted_by'],
        granted_at=row['granted_at'],
        expires_at=row['expires_at'],
        last_verified_at=row['last_verified_at'],
        verify_attempt_count=row['verify_attempt_count'],
        last_error=row['last_error'],
        status=row['status'],
    )


# ── Reads ──────────────────────────────────────────────────────────────────────


def list_capabilities(
    holder_id: str,
    holder_type: str = 'human',
    kind: str | None = None,
    fresh_within_seconds: int | None = None,
) -> list[Capability]:
    """
    Return all active capabilities for holder_id.

    Args:
        holder_id: Profile ID, agent slug, squad ID, or guild ID.
        holder_type: One of 'human', 'agent', 'squad', 'guild'.
        kind: Filter to a specific capability_kind (optional).
        fresh_within_seconds: If set, only return rows verified within this window.
    """
    filters = [
        'holder_type = %s',
        'holder_id = %s',
        "status = 'active'",
    ]
    params: list[Any] = [holder_type, holder_id]

    if kind is not None:
        filters.append('capability_kind = %s')
        params.append(kind)

    if fresh_within_seconds is not None:
        filters.append(
            "last_verified_at >= now() - (%s || ' seconds')::interval"
        )
        params.append(str(fresh_within_seconds))

    sql = (
        'SELECT grant_id, holder_type, holder_id, capability_kind, capability_ref, '
        'source_domain, scope, granted_by, granted_at, expires_at, last_verified_at, '
        'verify_attempt_count, last_error, status '
        'FROM inventory_grants '
        f'WHERE {" AND ".join(filters)} '
        'ORDER BY granted_at'
    )

    with _connect() as conn:
        with conn.cursor() as cur:
            cur.execute(sql, params)
            rows = cur.fetchall()
    return [_row_to_capability(dict(r)) for r in rows]


def assert_capability(
    holder_id: str,
    kind: str,
    ref: str,
    action: str,
    *,
    holder_type: str = 'human',
    fresh_within_seconds: int | None = 86400,
) -> bool:
    """
    Return True iff holder_id has an active, non-expired capability (kind, ref).

    Does NOT trigger inline verification — hot path stays synchronous and cheap.
    Use reverify(grant_id) separately when stakes are high.

    fresh_within_seconds=86400 (24h) by default: returns False for stale rows
    not re-verified within the window. Pass None to skip freshness filter.
    """
    grant_id = _grant_id(holder_id, kind, ref)

    extra = ''
    params: list[Any] = [grant_id]

    if fresh_within_seconds is not None:
        extra = " AND last_verified_at >= now() - (%s || ' seconds')::interval"
        params.append(str(fresh_within_seconds))

    sql = (
        "SELECT scope FROM inventory_grants "
        f"WHERE grant_id = %s AND status = 'active'"
        f"  AND (expires_at IS NULL OR expires_at > now()){extra} "
        "LIMIT 1"
    )

    with _connect() as conn:
        with conn.cursor() as cur:
            cur.execute(sql, params)
            row = cur.fetchone()
    if not row:
        return False

    # If scope lists allowed actions, check the action is permitted
    scope: dict[str, Any] | None = row['scope']
    if scope and 'allow_actions' in scope:
        return action in scope['allow_actions']
    return True


# ── Mutations ──────────────────────────────────────────────────────────────────


def grant_capability(
    holder_id: str,
    kind: str,
    ref: str,
    source_domain: str,
    scope: dict[str, Any] | None,
    granted_by: str,
    *,
    holder_type: HolderType = 'human',
    expires_at: datetime | None = None,
    metadata: dict[str, Any] | None = None,
) -> Capability:
    """
    Grant a capability (idempotent).

    Uses ON CONFLICT (unique active index) DO UPDATE to handle re-grants:
    updates scope, granted_by, expires_at and resets status → 'active'.
    Emits audit_event to stream='inventory'.
    """
    grant_id = _grant_id(holder_id, kind, ref)

    with _connect() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """INSERT INTO inventory_grants
                       (grant_id, holder_type, holder_id, capability_kind, capability_ref,
                        source_domain, scope, granted_by, expires_at, metadata)
                   VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                   ON CONFLICT (holder_type, holder_id, capability_kind, capability_ref)
                   WHERE status = 'active'
                   DO UPDATE SET
                       scope               = EXCLUDED.scope,
                       granted_by          = EXCLUDED.granted_by,
                       expires_at          = EXCLUDED.expires_at,
                       last_verified_at    = now(),
                       verify_attempt_count = 0,
                       last_error          = NULL,
                       status              = 'active',
                       metadata            = COALESCE(EXCLUDED.metadata, inventory_grants.metadata)
                   RETURNING grant_id, holder_type, holder_id, capability_kind, capability_ref,
                             source_domain, scope, granted_by, granted_at, expires_at,
                             last_verified_at, verify_attempt_count, last_error, status""",
                (
                    grant_id, holder_type, holder_id, kind, ref,
                    source_domain,
                    psycopg2.extras.Json(scope) if scope else None,
                    granted_by, expires_at,
                    psycopg2.extras.Json(metadata) if metadata else None,
                ),
            )
            row = cur.fetchone()
            if row is None:
                # Revoked/orphaned row with same (holder, kind, ref) — fetch it
                cur.execute(
                    'SELECT grant_id, holder_type, holder_id, capability_kind, capability_ref, '
                    'source_domain, scope, granted_by, granted_at, expires_at, '
                    'last_verified_at, verify_attempt_count, last_error, status '
                    'FROM inventory_grants WHERE grant_id = %s',
                    (grant_id,),
                )
                row = cur.fetchone()
        conn.commit()

    _emit_audit(
        'capability_granted', granted_by,
        resource=f'inventory:{grant_id}',
        payload={'holder_id': holder_id, 'kind': kind, 'ref': ref},
    )
    return _row_to_capability(dict(row))


def revoke_capability(grant_id: str, revoked_by: str, reason: str) -> None:
    """
    Mark a grant as revoked (soft delete — preserves audit trail).
    Emits audit_event to stream='inventory'.
    Raises ValueError if grant not found.
    """
    with _connect() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """UPDATE inventory_grants
                      SET status   = 'revoked',
                          last_error = %s
                    WHERE grant_id = %s""",
                (f'revoked_by:{revoked_by} reason:{reason}', grant_id),
            )
            if cur.rowcount == 0:
                raise ValueError(f'Grant {grant_id!r} not found')
        conn.commit()

    _emit_audit(
        'capability_revoked', revoked_by,
        resource=f'inventory:{grant_id}',
        payload={'reason': reason},
    )


def reverify(grant_id: str) -> Capability:
    """
    Force a per-kind verifier run for the grant.

    On success: last_verified_at = now(), verify_attempt_count reset, status → 'active'.
    On failure: verify_attempt_count incremented, last_error set, status updated per hint.
    On missing verifier: no-op, returns current state.

    Emits audit_event on status change.
    Raises ValueError if grant not found.
    """
    with _connect() as conn:
        with conn.cursor() as cur:
            cur.execute(
                'SELECT grant_id, holder_type, holder_id, capability_kind, capability_ref, '
                'source_domain, scope, granted_by, granted_at, expires_at, '
                'last_verified_at, verify_attempt_count, last_error, status '
                'FROM inventory_grants WHERE grant_id = %s',
                (grant_id,),
            )
            row = cur.fetchone()
    if not row:
        raise ValueError(f'Grant {grant_id!r} not found')

    cap = _row_to_capability(dict(row))
    verifier = VERIFIERS.get(cap.kind)
    if not verifier:
        log.debug('No verifier for kind %s — skipping reverify', cap.kind)
        return cap

    prev_status = cap.status
    try:
        is_valid, hint = verifier(cap.ref)
    except Exception as exc:  # noqa: BLE001
        # Verifier itself errored — don't update last_verified_at; increment count
        with _connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """UPDATE inventory_grants
                          SET verify_attempt_count = verify_attempt_count + 1,
                              last_error           = %s
                        WHERE grant_id = %s
                    RETURNING grant_id, holder_type, holder_id, capability_kind, capability_ref,
                              source_domain, scope, granted_by, granted_at, expires_at,
                              last_verified_at, verify_attempt_count, last_error, status""",
                    (str(exc), grant_id),
                )
                updated = cur.fetchone()
            conn.commit()
        return _row_to_capability(dict(updated))

    if is_valid:
        with _connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """UPDATE inventory_grants
                          SET last_verified_at      = now(),
                              verify_attempt_count  = 0,
                              last_error            = NULL,
                              status                = 'active'
                        WHERE grant_id = %s
                    RETURNING grant_id, holder_type, holder_id, capability_kind, capability_ref,
                              source_domain, scope, granted_by, granted_at, expires_at,
                              last_verified_at, verify_attempt_count, last_error, status""",
                    (grant_id,),
                )
                updated = cur.fetchone()
            conn.commit()
    else:
        with _connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """UPDATE inventory_grants
                          SET verify_attempt_count = verify_attempt_count + 1,
                              last_error           = %s,
                              status               = %s
                        WHERE grant_id = %s
                    RETURNING grant_id, holder_type, holder_id, capability_kind, capability_ref,
                              source_domain, scope, granted_by, granted_at, expires_at,
                              last_verified_at, verify_attempt_count, last_error, status""",
                    (f'verifier returned invalid (hint={hint})', hint, grant_id),
                )
                updated = cur.fetchone()
            conn.commit()

    result = _row_to_capability(dict(updated))
    if result.status != prev_status:
        _emit_audit(
            'capability_demoted', 'system',
            resource=f'inventory:{grant_id}',
            payload={'prev_status': prev_status, 'new_status': result.status, 'kind': cap.kind},
        )
    return result


# ── Built-in verifiers ─────────────────────────────────────────────────────────


def _verify_credential(ref: str) -> VerifyResult:
    """credential: check token exists in mirror/D1 token table."""
    try:
        with _connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT 1 FROM tokens WHERE id = %s AND status = 'active' LIMIT 1",
                    (ref,),
                )
                return (cur.fetchone() is not None, 'orphaned')
    except Exception:  # noqa: BLE001
        return (False, 'stale')


def _verify_tool(ref: str) -> VerifyResult:
    """tool: check the MCP tool name appears in any registered plugin manifest."""
    try:
        import glob as _glob
        import yaml
        for manifest_path in _glob.glob('/home/mumega/mumega.com/plugins/*/manifest.ts'):
            # Rough check: look for tool name in raw file content
            with open(manifest_path) as fh:
                content = fh.read()
            if ref in content:
                return (True, 'active')
        return (False, 'orphaned')
    except Exception:  # noqa: BLE001
        return (False, 'stale')


def _verify_template(ref: str) -> VerifyResult:
    """template: check sos/skills/{ref}/SKILL.md exists."""
    from pathlib import Path
    skill_path = Path.home() / 'SOS' / 'sos' / 'skills' / ref / 'SKILL.md'
    if skill_path.exists():
        return (True, 'active')
    # Try ~/.claude/skills/ as fallback
    claude_path = Path.home() / '.claude' / 'skills' / ref / 'SKILL.md'
    if claude_path.exists():
        return (True, 'active')
    return (False, 'orphaned')


def _verify_oauth_connection(ref: str) -> VerifyResult:
    """oauth_connection: check profile_tool_connections row status + token expiry."""
    try:
        with _connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """SELECT status, token_expires_at FROM profile_tool_connections
                        WHERE id = %s LIMIT 1""",
                    (ref,),
                )
                row = cur.fetchone()
        if not row:
            return (False, 'orphaned')
        if row['status'] != 'active':
            return (False, 'revoked')
        exp = row['token_expires_at']
        if exp and exp < datetime.now(timezone.utc):
            return (False, 'expired')
        return (True, 'active')
    except Exception:  # noqa: BLE001
        return (False, 'stale')


def _verify_guild_role(ref: str) -> VerifyResult:
    """guild_role: ref format = 'guild:{slug}:{holder_id}'. Check §13 membership."""
    try:
        parts = ref.split(':')
        if len(parts) != 3 or parts[0] != 'guild':
            return (False, 'orphaned')
        _, guild_id, member_id = parts
        from sos.contracts.guild import assert_member
        return (assert_member(guild_id, member_id), 'orphaned')
    except Exception:  # noqa: BLE001
        return (False, 'stale')


def _verify_mcp_server(ref: str) -> VerifyResult:
    """mcp_server: HEAD request to the MCP server URL."""
    try:
        import urllib.request
        req = urllib.request.Request(ref, method='HEAD')
        with urllib.request.urlopen(req, timeout=5):
            return (True, 'active')
    except Exception:  # noqa: BLE001
        return (False, 'stale')


def _verify_automation(ref: str) -> VerifyResult:
    """automation: GHL workflow check. Stale if GHL API unavailable."""
    # GHL API call — requires MUMEGA_GHL_TOKEN in env
    # Kept simple: stale on any error so reconciler retries without orphaning
    try:
        token = os.getenv('MUMEGA_GHL_TOKEN', '')
        if not token:
            return (False, 'stale')
        import urllib.request
        req = urllib.request.Request(
            f'https://services.leadconnectorhq.com/workflows/{ref}',
            headers={'Authorization': f'Bearer {token}', 'Version': '2021-07-28'},
            method='GET',
        )
        with urllib.request.urlopen(req, timeout=10) as resp:
            return (resp.status == 200, 'stale')
    except Exception:  # noqa: BLE001
        return (False, 'stale')


def _verify_data_access(ref: str) -> VerifyResult:
    """data_access: role name exists in §1A registry. Soft check."""
    # §1A role registry is not yet a queryable table — always valid at v1
    return (True, 'active')


def _register_defaults() -> None:
    VERIFIERS['credential']      = _verify_credential
    VERIFIERS['tool']            = _verify_tool
    VERIFIERS['automation']      = _verify_automation
    VERIFIERS['template']        = _verify_template
    VERIFIERS['oauth_connection'] = _verify_oauth_connection
    VERIFIERS['guild_role']      = _verify_guild_role
    VERIFIERS['data_access']     = _verify_data_access
    VERIFIERS['mcp_server']      = _verify_mcp_server


_register_defaults()
