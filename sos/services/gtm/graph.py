"""GTM relationship graph ORM helpers — Sprint 008 S008-D / G79.

CRUD operations for the gtm schema tables. All functions take a psycopg2
connection and return dicts. Upserts on UNIQUE constraints return existing
rows (true upsert behavior).

Raises GraphPersistError on unexpected failures (not on upsert conflicts).
"""
from __future__ import annotations

import logging
import uuid
from datetime import datetime, timezone
from typing import Any

log = logging.getLogger("sos.gtm.graph")


# ---------------------------------------------------------------------------
# Exceptions
# ---------------------------------------------------------------------------


class GraphPersistError(RuntimeError):
    """Unexpected failure persisting to the GTM graph."""


class EdgeAlreadyExistsError(GraphPersistError):
    """Edge with same from/to/type already exists."""


# ---------------------------------------------------------------------------
# People
# ---------------------------------------------------------------------------


def upsert_person(
    conn: Any,
    name: str,
    email: str | None = None,
    phone: str | None = None,
    source: str = "manual",
) -> dict[str, Any]:
    """Insert or update a person. Returns the row as dict.

    On email conflict: updates name, phone, last_seen_at. Returns existing id.
    """
    now = datetime.now(timezone.utc)
    try:
        with conn.cursor() as cur:
            if email:
                cur.execute(
                    """
                    INSERT INTO gtm.people (name, email, phone, source, last_seen_at)
                    VALUES (%s, %s, %s, %s, %s)
                    ON CONFLICT (email) WHERE email IS NOT NULL AND deleted_at IS NULL
                    DO UPDATE SET name = EXCLUDED.name, phone = COALESCE(EXCLUDED.phone, gtm.people.phone),
                                  last_seen_at = EXCLUDED.last_seen_at
                    RETURNING id, name, email, phone, source, created_at, last_seen_at
                    """,
                    (name, email, phone, source, now),
                )
            else:
                cur.execute(
                    """
                    INSERT INTO gtm.people (name, email, phone, source, last_seen_at)
                    VALUES (%s, %s, %s, %s, %s)
                    RETURNING id, name, email, phone, source, created_at, last_seen_at
                    """,
                    (name, email, phone, source, now),
                )
            row = cur.fetchone()
            conn.commit()
            if row:
                cols = [d[0] for d in cur.description]
                return dict(zip(cols, row))
            raise GraphPersistError("upsert_person returned no row")
    except GraphPersistError:
        raise
    except Exception as exc:
        conn.rollback()
        raise GraphPersistError(f"upsert_person failed: {exc}") from exc


# ---------------------------------------------------------------------------
# Companies
# ---------------------------------------------------------------------------


def upsert_company(
    conn: Any,
    name: str,
    domain: str | None = None,
    industry: str | None = None,
    source: str = "manual",
) -> dict[str, Any]:
    """Insert or update a company. Returns the row as dict."""
    try:
        with conn.cursor() as cur:
            if domain:
                cur.execute(
                    """
                    INSERT INTO gtm.companies (name, domain, industry, source)
                    VALUES (%s, %s, %s, %s)
                    ON CONFLICT (domain) WHERE domain IS NOT NULL AND deleted_at IS NULL
                    DO UPDATE SET name = EXCLUDED.name, industry = COALESCE(EXCLUDED.industry, gtm.companies.industry)
                    RETURNING id, name, domain, industry, source, created_at
                    """,
                    (name, domain, industry, source),
                )
            else:
                cur.execute(
                    """
                    INSERT INTO gtm.companies (name, domain, industry, source)
                    VALUES (%s, %s, %s, %s)
                    RETURNING id, name, domain, industry, source, created_at
                    """,
                    (name, domain, industry, source),
                )
            row = cur.fetchone()
            conn.commit()
            if row:
                cols = [d[0] for d in cur.description]
                return dict(zip(cols, row))
            raise GraphPersistError("upsert_company returned no row")
    except GraphPersistError:
        raise
    except Exception as exc:
        conn.rollback()
        raise GraphPersistError(f"upsert_company failed: {exc}") from exc


# ---------------------------------------------------------------------------
# Deals
# ---------------------------------------------------------------------------


def upsert_deal(
    conn: Any,
    person_id: str | None,
    company_id: str | None,
    product: str,
    stage: str,
    value_cents: int | None = None,
    owner_knight_id: str | None = None,
    ghl_opportunity_id: str | None = None,
) -> dict[str, Any]:
    """Insert or update a deal. Returns the row as dict."""
    now = datetime.now(timezone.utc)
    try:
        with conn.cursor() as cur:
            if ghl_opportunity_id:
                cur.execute(
                    """
                    INSERT INTO gtm.deals (person_id, company_id, product, stage, value_cents,
                                           owner_knight_id, ghl_opportunity_id, last_action_at)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                    ON CONFLICT (ghl_opportunity_id)
                    DO UPDATE SET stage = EXCLUDED.stage, value_cents = COALESCE(EXCLUDED.value_cents, gtm.deals.value_cents),
                                  last_action_at = EXCLUDED.last_action_at
                    RETURNING id, person_id, company_id, product, stage, value_cents,
                              owner_knight_id, ghl_opportunity_id, created_at, last_action_at
                    """,
                    (person_id, company_id, product, stage, value_cents,
                     owner_knight_id, ghl_opportunity_id, now),
                )
            else:
                cur.execute(
                    """
                    INSERT INTO gtm.deals (person_id, company_id, product, stage, value_cents,
                                           owner_knight_id, last_action_at)
                    VALUES (%s, %s, %s, %s, %s, %s, %s)
                    RETURNING id, person_id, company_id, product, stage, value_cents,
                              owner_knight_id, ghl_opportunity_id, created_at, last_action_at
                    """,
                    (person_id, company_id, product, stage, value_cents, owner_knight_id, now),
                )
            row = cur.fetchone()
            conn.commit()
            if row:
                cols = [d[0] for d in cur.description]
                return dict(zip(cols, row))
            raise GraphPersistError("upsert_deal returned no row")
    except GraphPersistError:
        raise
    except Exception as exc:
        conn.rollback()
        raise GraphPersistError(f"upsert_deal failed: {exc}") from exc


# ---------------------------------------------------------------------------
# Conversations
# ---------------------------------------------------------------------------


def record_conversation(
    conn: Any,
    channel: str,
    participants: list[str] | dict,
    summary: str | None,
    occurred_at: datetime,
    discord_message_id: str | None = None,
    transcript_url: str | None = None,
) -> dict[str, Any]:
    """Record a conversation. Returns the row as dict.

    On discord_message_id conflict: returns existing row (idempotent).
    """
    import json as _json

    participants_json = _json.dumps(participants) if isinstance(participants, (list, dict)) else participants
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO gtm.conversations (channel, participants, summary, occurred_at,
                                               discord_message_id, transcript_url)
                VALUES (%s, %s, %s, %s, %s, %s)
                ON CONFLICT (discord_message_id) DO NOTHING
                RETURNING id, channel, participants, summary, occurred_at, discord_message_id, created_at
                """,
                (channel, participants_json, summary, occurred_at, discord_message_id, transcript_url),
            )
            row = cur.fetchone()
            conn.commit()
            if row:
                cols = [d[0] for d in cur.description]
                return dict(zip(cols, row))
            # Conflict: fetch existing
            if discord_message_id:
                cur.execute(
                    "SELECT id, channel, participants, summary, occurred_at, discord_message_id, created_at "
                    "FROM gtm.conversations WHERE discord_message_id = %s",
                    (discord_message_id,),
                )
                row = cur.fetchone()
                if row:
                    cols = [d[0] for d in cur.description]
                    return dict(zip(cols, row))
            raise GraphPersistError("record_conversation returned no row")
    except GraphPersistError:
        raise
    except Exception as exc:
        conn.rollback()
        raise GraphPersistError(f"record_conversation failed: {exc}") from exc


# ---------------------------------------------------------------------------
# Edges
# ---------------------------------------------------------------------------


def add_edge(
    conn: Any,
    from_id: str,
    from_type: str,
    to_id: str,
    to_type: str,
    edge_type: str,
    weight: float = 1.0,
) -> dict[str, Any]:
    """Add an edge to the graph. Raises EdgeAlreadyExistsError on duplicate."""
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO gtm.edges (from_id, from_type, to_id, to_type, edge_type, weight)
                VALUES (%s, %s, %s, %s, %s, %s)
                RETURNING id, from_id, from_type, to_id, to_type, edge_type, weight, created_at
                """,
                (from_id, from_type, to_id, to_type, edge_type, weight),
            )
            row = cur.fetchone()
            conn.commit()
            if row:
                cols = [d[0] for d in cur.description]
                return dict(zip(cols, row))
            raise GraphPersistError("add_edge returned no row")
    except Exception as exc:
        conn.rollback()
        from psycopg2.errors import UniqueViolation
        if isinstance(exc, UniqueViolation) or "unique" in str(exc).lower() or "duplicate" in str(exc).lower():
            raise EdgeAlreadyExistsError(
                f"Edge {from_type}:{from_id} → {to_type}:{to_id} ({edge_type}) already exists"
            ) from exc
        raise GraphPersistError(f"add_edge failed: {exc}") from exc


# ---------------------------------------------------------------------------
# Actions
# ---------------------------------------------------------------------------


def create_action(
    conn: Any,
    knight_id: str,
    action_type: str,
    target_id: str | None = None,
    target_type: str | None = None,
    due_at: datetime | None = None,
    payload: dict | None = None,
) -> dict[str, Any]:
    """Create a knight action. Returns the row as dict."""
    import json as _json

    payload_json = _json.dumps(payload) if payload else None
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO gtm.actions (knight_id, action_type, target_id, target_type, due_at, payload)
                VALUES (%s, %s, %s, %s, %s, %s)
                RETURNING id, knight_id, action_type, target_id, target_type, status, due_at, created_at
                """,
                (knight_id, action_type, target_id, target_type, due_at, payload_json),
            )
            row = cur.fetchone()
            conn.commit()
            if row:
                cols = [d[0] for d in cur.description]
                return dict(zip(cols, row))
            raise GraphPersistError("create_action returned no row")
    except GraphPersistError:
        raise
    except Exception as exc:
        conn.rollback()
        raise GraphPersistError(f"create_action failed: {exc}") from exc


def mark_action_done(conn: Any, action_id: str) -> bool:
    """Mark an action as done. Returns True if row updated."""
    now = datetime.now(timezone.utc)
    try:
        with conn.cursor() as cur:
            cur.execute(
                "UPDATE gtm.actions SET status = 'done', completed_at = %s WHERE id = %s AND status = 'pending'",
                (now, action_id),
            )
            conn.commit()
            return cur.rowcount > 0
    except Exception as exc:
        conn.rollback()
        raise GraphPersistError(f"mark_action_done failed: {exc}") from exc
