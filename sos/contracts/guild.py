"""
§13 Guild — Durable Organization Primitive (Sprint 003 Track C).

Gate: Athena G8 APPROVED v1.1
Migration: 020_guild.sql (guilds, guild_members, guild_treasuries, guild_governance_log)

Contract surface: types + read/write functions keyed on the Mirror DB.
DB: psycopg2 (sync) against MIRROR_DATABASE_URL or DATABASE_URL.
Audit: async emit_audit via kernel audit_chain (fire-and-forget; never fatal).
"""
from __future__ import annotations

import asyncio
import logging
import os
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any, Literal

import psycopg2
import psycopg2.extras
from pydantic import BaseModel, ConfigDict

log = logging.getLogger(__name__)

# ── Literal type aliases ───────────────────────────────────────────────────────

GuildKind        = Literal['company', 'project', 'community', 'meta-guild']
GovernanceTier   = Literal['principal-only', 'consensus', 'delegated', 'automated']
GuildStatus      = Literal['active', 'dormant', 'dissolved']
MemberType       = Literal['human', 'agent', 'squad']
MemberStatus     = Literal['active', 'suspended', 'left', 'removed']
GovernanceAction = Literal[
    'member_added', 'rank_changed', 'treasury_debited', 'treasury_credited',
    'charter_amended', 'status_changed', 'dissolution_initiated', 'dissolution_finalized',
]

# ── Types ──────────────────────────────────────────────────────────────────────


class Guild(BaseModel):
    """Immutable snapshot of a guild row."""

    model_config = ConfigDict(frozen=True)

    id: str                         # slug, e.g. 'mumega-inc'
    name: str
    kind: GuildKind
    parent_guild_id: str | None
    founded_at: datetime
    charter_doc_node_id: str | None
    governance_tier: GovernanceTier
    status: GuildStatus
    metadata: dict[str, Any] | None
    created_at: datetime
    updated_at: datetime


class GuildMember(BaseModel):
    """Immutable snapshot of a guild_members row."""

    model_config = ConfigDict(frozen=True)

    guild_id: str
    member_type: MemberType
    member_id: str
    rank: str
    scopes: dict[str, Any] | None
    status: MemberStatus
    joined_at: datetime
    left_at: datetime | None


class GuildTreasury(BaseModel):
    """Immutable snapshot of a guild_treasuries row."""

    model_config = ConfigDict(frozen=True)

    guild_id: str
    currency: str
    balance: Decimal
    frozen_balance: Decimal
    last_settled_at: datetime | None


class GuildSpec(BaseModel):
    """Input spec for create_guild — validated by callers before passing in."""

    id: str                         # target slug (must be URL-safe)
    name: str
    kind: GuildKind
    parent_guild_id: str | None = None
    charter_doc_node_id: str | None = None
    governance_tier: GovernanceTier = 'principal-only'
    metadata: dict[str, Any] | None = None


# ── DB connection ──────────────────────────────────────────────────────────────


def _db_url() -> str:
    url = os.getenv('MIRROR_DATABASE_URL') or os.getenv('DATABASE_URL')
    if not url:
        raise RuntimeError(
            'MIRROR_DATABASE_URL or DATABASE_URL is not set — '
            'guild contract cannot connect to Mirror'
        )
    return url


def _connect():
    """Open a new psycopg2 connection. Caller owns lifecycle."""
    return psycopg2.connect(_db_url(), cursor_factory=psycopg2.extras.RealDictCursor)


# ── Audit emission (fire-and-forget; non-fatal on any error) ───────────────────


def _emit_audit(
    guild_id: str,
    action: str,
    actor: str,
    resource: str,
    payload: dict[str, Any] | None = None,
) -> None:
    """Best-effort async audit emission into the guild:<slug> stream."""
    try:
        from sos.kernel.audit_chain import AuditChainEvent, emit_audit

        event = AuditChainEvent(
            stream_id=f'guild:{guild_id}',
            actor_id=actor,
            actor_type='human',       # guild ops are principal-initiated; agents override if needed
            action=action,
            resource=resource,
            payload=payload,
        )

        loop = None
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            pass  # no loop running

        if loop and loop.is_running():
            asyncio.ensure_future(emit_audit(event))
        else:
            asyncio.run(emit_audit(event))
    except Exception:  # noqa: BLE001
        log.debug('guild audit emission failed (non-fatal)', exc_info=True)


# ── Internal: governance log write (runs inside caller's transaction) ──────────


def _log_governance(
    cur: Any,
    guild_id: str,
    action: GovernanceAction,
    decided_by: str,
    payload: dict[str, Any] | None = None,
    ratified_by: list[str] | None = None,
    evidence_ref: str | None = None,
) -> None:
    cur.execute(
        """INSERT INTO guild_governance_log
               (guild_id, action, decided_by, ratified_by, evidence_ref, payload)
           VALUES (%s, %s, %s, %s, %s, %s)""",
        (
            guild_id, action, decided_by,
            ratified_by or [],
            evidence_ref,
            psycopg2.extras.Json(payload) if payload is not None else None,
        ),
    )


# ── Reads ──────────────────────────────────────────────────────────────────────


def get_guild(guild_id: str) -> Guild | None:
    """Return guild by slug, or None if not found."""
    with _connect() as conn:
        with conn.cursor() as cur:
            cur.execute('SELECT * FROM guilds WHERE id = %s', (guild_id,))
            row = cur.fetchone()
    return Guild(**dict(row)) if row else None


def list_member_guilds(
    member_id: str,
    member_type: str = 'human',
    *,
    status: str = 'active',
) -> list[Guild]:
    """
    Return all guilds where member_id holds an active membership.
    Used by CallerContext resolution (one query per request).
    """
    with _connect() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """SELECT g.*
                     FROM guilds g
                     JOIN guild_members m ON m.guild_id = g.id
                    WHERE m.member_id   = %s
                      AND m.member_type = %s
                      AND m.status      = %s
                    ORDER BY m.joined_at""",
                (member_id, member_type, status),
            )
            rows = cur.fetchall()
    return [Guild(**dict(r)) for r in rows]


def list_guild_members(
    guild_id: str,
    status: str = 'active',
) -> list[GuildMember]:
    """Return all members of guild_id with the given status."""
    with _connect() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """SELECT guild_id, member_type, member_id, rank, scopes,
                          status, joined_at, left_at
                     FROM guild_members
                    WHERE guild_id = %s AND status = %s
                    ORDER BY joined_at""",
                (guild_id, status),
            )
            rows = cur.fetchall()
    return [GuildMember(**dict(r)) for r in rows]


def assert_member(guild_id: str, member_id: str) -> bool:
    """Return True iff member_id is an active member of guild_id."""
    with _connect() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """SELECT 1 FROM guild_members
                    WHERE guild_id = %s AND member_id = %s AND status = 'active'
                    LIMIT 1""",
                (guild_id, member_id),
            )
            return cur.fetchone() is not None


def member_rank(guild_id: str, member_id: str) -> str | None:
    """Return the rank for member_id's active membership, or None if not a member."""
    with _connect() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """SELECT rank FROM guild_members
                    WHERE guild_id = %s AND member_id = %s AND status = 'active'
                    LIMIT 1""",
                (guild_id, member_id),
            )
            row = cur.fetchone()
    return row['rank'] if row else None


def get_treasury(guild_id: str, currency: str = 'USD') -> Decimal:
    """
    Return available balance (balance − frozen_balance) for the currency.
    Returns Decimal(0) if no treasury row exists for that currency.
    """
    with _connect() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """SELECT balance, frozen_balance FROM guild_treasuries
                    WHERE guild_id = %s AND currency = %s""",
                (guild_id, currency),
            )
            row = cur.fetchone()
    if not row:
        return Decimal(0)
    return Decimal(str(row['balance'])) - Decimal(str(row['frozen_balance']))


def can_act_for_guild(member_id: str, guild_id: str, action: str) -> bool:
    """
    Check whether member_id may perform action within guild_id.

    Logic:
    - Not an active member → False.
    - No scopes JSON set → rank governs: founder/coordinator/builder get all actions.
    - Scopes set → action must appear in scopes['allow'] list, or '*' wildcards all.
    """
    with _connect() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """SELECT rank, scopes FROM guild_members
                    WHERE guild_id = %s AND member_id = %s AND status = 'active'
                    LIMIT 1""",
                (guild_id, member_id),
            )
            row = cur.fetchone()
    if not row:
        return False
    rank: str = row['rank']
    scopes: dict[str, Any] | None = row['scopes']
    if scopes is None:
        return rank in ('founder', 'coordinator', 'builder')
    allowed: list[str] = scopes.get('allow', []) if isinstance(scopes, dict) else []
    return action in allowed or '*' in allowed


# ── Mutations ──────────────────────────────────────────────────────────────────


def create_guild(spec: GuildSpec, created_by: str) -> Guild:
    """
    Create a guild row. Idempotent: ON CONFLICT (id) DO NOTHING returns existing row.
    Emits governance_log (status_changed/guild_created) + audit_event.
    """
    with _connect() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """INSERT INTO guilds
                       (id, name, kind, parent_guild_id, charter_doc_node_id,
                        governance_tier, metadata)
                   VALUES (%s, %s, %s, %s, %s, %s, %s)
                   ON CONFLICT (id) DO NOTHING
                   RETURNING *""",
                (
                    spec.id, spec.name, spec.kind,
                    spec.parent_guild_id, spec.charter_doc_node_id,
                    spec.governance_tier,
                    psycopg2.extras.Json(spec.metadata) if spec.metadata else None,
                ),
            )
            row = cur.fetchone()
            if row is None:
                # Already existed — fetch current state
                cur.execute('SELECT * FROM guilds WHERE id = %s', (spec.id,))
                row = cur.fetchone()

            _log_governance(
                cur, spec.id, 'status_changed', created_by,
                payload={
                    'event': 'guild_created',
                    'name': spec.name,
                    'kind': spec.kind,
                    'governance_tier': spec.governance_tier,
                },
            )
        conn.commit()

    _emit_audit(
        spec.id, 'guild_created', created_by,
        resource=f'guild:{spec.id}',
        payload={'name': spec.name, 'kind': spec.kind},
    )
    return Guild(**dict(row))


def add_member(
    guild_id: str,
    member_id: str,
    rank: str,
    added_by: str,
    *,
    member_type: MemberType = 'human',
    scopes: dict[str, Any] | None = None,
) -> GuildMember:
    """
    Add a member to a guild, or re-activate a removed/suspended member.

    Uses ON CONFLICT DO UPDATE per Athena G8 soft note — handles re-adds after removal
    without unique-constraint violation. Updates rank + resets joined_at.

    Emits governance_log (member_added) + audit_event.
    """
    with _connect() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """INSERT INTO guild_members
                       (guild_id, member_type, member_id, rank, scopes)
                   VALUES (%s, %s, %s, %s, %s)
                   ON CONFLICT (guild_id, member_type, member_id)
                   DO UPDATE SET
                       status    = 'active',
                       rank      = EXCLUDED.rank,
                       joined_at = now(),
                       left_at   = NULL,
                       scopes    = COALESCE(EXCLUDED.scopes, guild_members.scopes)
                   RETURNING guild_id, member_type, member_id, rank, scopes,
                             status, joined_at, left_at""",
                (
                    guild_id, member_type, member_id, rank,
                    psycopg2.extras.Json(scopes) if scopes else None,
                ),
            )
            row = cur.fetchone()
            _log_governance(
                cur, guild_id, 'member_added', added_by,
                payload={
                    'member_id': member_id,
                    'member_type': member_type,
                    'rank': rank,
                },
            )
        conn.commit()

    _emit_audit(
        guild_id, 'member_added', added_by,
        resource=f'guild:{guild_id}:member:{member_id}',
        payload={'member_id': member_id, 'rank': rank, 'member_type': member_type},
    )
    return GuildMember(**dict(row))


def change_rank(
    guild_id: str,
    member_id: str,
    new_rank: str,
    decided_by: str,
) -> None:
    """
    Update rank for an active member.
    Raises ValueError if the member is not currently active.
    Emits governance_log (rank_changed) + audit_event.
    """
    with _connect() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """UPDATE guild_members
                      SET rank = %s
                    WHERE guild_id = %s AND member_id = %s AND status = 'active'""",
                (new_rank, guild_id, member_id),
            )
            if cur.rowcount == 0:
                raise ValueError(
                    f'No active member {member_id!r} in guild {guild_id!r}'
                )
            _log_governance(
                cur, guild_id, 'rank_changed', decided_by,
                payload={'member_id': member_id, 'new_rank': new_rank},
            )
        conn.commit()

    _emit_audit(
        guild_id, 'rank_changed', decided_by,
        resource=f'guild:{guild_id}:member:{member_id}',
        payload={'member_id': member_id, 'new_rank': new_rank},
    )


def remove_member(
    guild_id: str,
    member_id: str,
    reason: str,
    decided_by: str,
) -> None:
    """
    Soft-delete a member (status → 'removed', left_at = now).
    Preserves the row for governance history and audit trail.
    Raises ValueError if the member is not currently active.
    Emits governance_log (rank_changed with 'removed' event) + audit_event.
    """
    with _connect() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """UPDATE guild_members
                      SET status  = 'removed',
                          left_at = now()
                    WHERE guild_id = %s AND member_id = %s AND status = 'active'""",
                (guild_id, member_id),
            )
            if cur.rowcount == 0:
                raise ValueError(
                    f'No active member {member_id!r} in guild {guild_id!r}'
                )
            _log_governance(
                cur, guild_id, 'rank_changed', decided_by,
                payload={
                    'event': 'member_removed',
                    'member_id': member_id,
                    'reason': reason,
                },
            )
        conn.commit()

    _emit_audit(
        guild_id, 'member_removed', decided_by,
        resource=f'guild:{guild_id}:member:{member_id}',
        payload={'member_id': member_id, 'reason': reason},
    )
