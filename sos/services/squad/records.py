"""RecordsService — Section 3: contacts, partners, opportunities, referrals."""
from __future__ import annotations

import json
import os
from typing import Any, Optional
from uuid import uuid4

import redis

from sos.services.squad.service import SquadDB, now_iso

_REDIS_PASSWORD = os.getenv("REDIS_PASSWORD", "")
_REDIS_HOST = os.getenv("REDIS_HOST", "127.0.0.1")
_REDIS_PORT = int(os.getenv("REDIS_PORT", "6379"))


def _bus() -> redis.Redis:
    return redis.Redis(
        host=_REDIS_HOST,
        port=_REDIS_PORT,
        password=_REDIS_PASSWORD or None,
        decode_responses=True,
    )


def _emit(event_type: str, entity_id: str, workspace_id: str, actor: str, payload: dict) -> None:
    """Fire-and-forget bus event; never raises."""
    try:
        r = _bus()
        msg = json.dumps({
            "event_type": event_type,
            "entity_id": entity_id,
            "workspace_id": workspace_id,
            "timestamp": now_iso(),
            "actor": actor,
            "payload": payload,
        })
        # derive stream from event_type: structured_record:contact:created → sos:event:squad:records:contact:created
        parts = event_type.split(":")
        stream = f"sos:event:squad:records:{':'.join(parts[1:])}" if len(parts) > 1 else f"sos:event:squad:records:{event_type}"
        r.xadd(stream, {"data": msg})
    except Exception:
        pass


class RecordNotFoundError(ValueError):
    pass


class RecordConflictError(ValueError):
    pass


# ---------------------------------------------------------------------------
# Contacts
# ---------------------------------------------------------------------------

class ContactsService:
    def __init__(self, db: Optional[SquadDB] = None) -> None:
        self.db = db or SquadDB()

    def create(
        self,
        workspace_id: str,
        first_name: str,
        last_name: str,
        *,
        external_id: Optional[str] = None,
        email: Optional[str] = None,
        phone: Optional[str] = None,
        title: Optional[str] = None,
        org_id: Optional[str] = None,
        visibility_tier: str = "firm_internal",
        engagement_status: str = "prospect",
        source: Optional[str] = None,
        next_action: Optional[str] = None,
        notes_ref: Optional[str] = None,
        notes: Optional[str] = None,
        owner_id: str = "system",
        actor: str = "system",
    ) -> dict:
        contact_id = str(uuid4())
        now = now_iso()
        with self.db.connect() as conn:
            try:
                conn.execute(
                    """
                    INSERT INTO contacts (
                        id, workspace_id, external_id, first_name, last_name,
                        email, phone, title, org_id,
                        visibility_tier, engagement_status, source,
                        next_action, notes_ref, notes,
                        owner_id, created_at, updated_at, created_by, updated_by
                    ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                    """,
                    (
                        contact_id, workspace_id, external_id, first_name, last_name,
                        email, phone, title, org_id,
                        visibility_tier, engagement_status, source,
                        next_action, notes_ref, notes,
                        owner_id, now, now, actor, actor,
                    ),
                )
            except Exception as exc:
                if "UNIQUE" in str(exc):
                    raise RecordConflictError(str(exc)) from exc
                raise
        row = self._get(contact_id)
        _emit(f"structured_record:contact:created", contact_id, workspace_id, actor, {"first_name": first_name, "last_name": last_name})
        return row

    def list(
        self,
        workspace_id: str,
        *,
        owner_id: Optional[str] = None,
        org_id: Optional[str] = None,
        status: Optional[str] = None,
        include_archived: bool = False,
        tier: Optional[str] = None,
    ) -> list[dict]:
        where = ["workspace_id = ?"]
        params: list[Any] = [workspace_id]
        if owner_id:
            where.append("owner_id = ?"); params.append(owner_id)
        if org_id:
            where.append("org_id = ?"); params.append(org_id)
        if status:
            where.append("engagement_status = ?"); params.append(status)
        if not include_archived:
            where.append("archived_at IS NULL")
        if tier:
            where.append("visibility_tier = ?"); params.append(tier)
        sql = f"SELECT * FROM contacts WHERE {' AND '.join(where)} ORDER BY updated_at DESC"
        with self.db.connect() as conn:
            rows = conn.execute(sql, params).fetchall()
        return [dict(r) for r in rows]

    def get(self, contact_id: str, workspace_id: str) -> dict:
        row = self._get(contact_id, workspace_id)
        return row

    def _get(self, contact_id: str, workspace_id: Optional[str] = None) -> dict:
        with self.db.connect() as conn:
            if workspace_id:
                row = conn.execute(
                    "SELECT * FROM contacts WHERE id = ? AND workspace_id = ?",
                    (contact_id, workspace_id),
                ).fetchone()
            else:
                row = conn.execute(
                    "SELECT * FROM contacts WHERE id = ?", (contact_id,)
                ).fetchone()
        if not row:
            raise RecordNotFoundError(f"Contact {contact_id} not found")
        return dict(row)

    def update(self, contact_id: str, workspace_id: str, actor: str, **fields: Any) -> dict:
        self._get(contact_id, workspace_id)
        allowed = {
            "first_name", "last_name", "email", "phone", "title", "org_id",
            "visibility_tier", "engagement_status", "source", "next_action",
            "notes_ref", "notes", "owner_id",
        }
        updates = {k: v for k, v in fields.items() if k in allowed and v is not None}
        if not updates:
            return self._get(contact_id, workspace_id)
        updates["updated_at"] = now_iso()
        updates["updated_by"] = actor
        set_clause = ", ".join(f"{k} = ?" for k in updates)
        params = list(updates.values()) + [contact_id, workspace_id]
        with self.db.connect() as conn:
            conn.execute(
                f"UPDATE contacts SET {set_clause} WHERE id = ? AND workspace_id = ?",
                params,
            )
        row = self._get(contact_id, workspace_id)
        _emit("structured_record:contact:updated", contact_id, workspace_id, actor, updates)
        return row

    def touch(self, contact_id: str, workspace_id: str, actor: str, note: Optional[str] = None) -> dict:
        now = now_iso()
        updates: dict[str, Any] = {"last_touched_at": now, "updated_at": now, "updated_by": actor}
        if note:
            existing = self._get(contact_id, workspace_id)
            old_notes = existing.get("notes") or ""
            updates["notes"] = f"{old_notes}\n[{now}] {note}".strip()
        set_clause = ", ".join(f"{k} = ?" for k in updates)
        params = list(updates.values()) + [contact_id, workspace_id]
        with self.db.connect() as conn:
            conn.execute(
                f"UPDATE contacts SET {set_clause} WHERE id = ? AND workspace_id = ?",
                params,
            )
        return self._get(contact_id, workspace_id)

    def soft_delete(self, contact_id: str, workspace_id: str, actor: str) -> dict:
        now = now_iso()
        with self.db.connect() as conn:
            conn.execute(
                "UPDATE contacts SET archived_at = ?, updated_at = ?, updated_by = ? WHERE id = ? AND workspace_id = ?",
                (now, now, actor, contact_id, workspace_id),
            )
        row = self._get(contact_id)
        _emit("structured_record:contact:deleted", contact_id, workspace_id, actor, {})
        return row

    def get_by_email(self, workspace_id: str, email: str) -> Optional[dict]:
        with self.db.connect() as conn:
            row = conn.execute(
                "SELECT * FROM contacts WHERE workspace_id = ? AND email = ?",
                (workspace_id, email),
            ).fetchone()
        return dict(row) if row else None


# ---------------------------------------------------------------------------
# Partners
# ---------------------------------------------------------------------------

class PartnersService:
    def __init__(self, db: Optional[SquadDB] = None) -> None:
        self.db = db or SquadDB()

    def create(
        self,
        workspace_id: str,
        name: str,
        type: str,
        *,
        external_id: Optional[str] = None,
        website_url: Optional[str] = None,
        hq_country: Optional[str] = None,
        primary_contact_id: Optional[str] = None,
        parent_partner_id: Optional[str] = None,
        revenue_split_pct: Optional[float] = None,
        visibility_tier: str = "firm_internal",
        engagement_status: str = "prospect",
        notes: Optional[str] = None,
        inkwell_page_slug: Optional[str] = None,
        onboarded_at: Optional[str] = None,
        actor: str = "system",
    ) -> dict:
        partner_id = str(uuid4())
        now = now_iso()
        with self.db.connect() as conn:
            try:
                conn.execute(
                    """
                    INSERT INTO partners (
                        id, workspace_id, external_id, name, type,
                        website_url, hq_country, primary_contact_id, parent_partner_id,
                        revenue_split_pct, visibility_tier, engagement_status,
                        notes, inkwell_page_slug, onboarded_at,
                        active, created_at, updated_at, created_by, updated_by
                    ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                    """,
                    (
                        partner_id, workspace_id, external_id, name, type,
                        website_url, hq_country, primary_contact_id, parent_partner_id,
                        revenue_split_pct, visibility_tier, engagement_status,
                        notes, inkwell_page_slug, onboarded_at,
                        1, now, now, actor, actor,
                    ),
                )
            except Exception as exc:
                if "UNIQUE" in str(exc):
                    raise RecordConflictError(str(exc)) from exc
                raise
        row = self._get(partner_id)
        _emit("structured_record:partner:created", partner_id, workspace_id, actor, {"name": name, "type": type})
        return row

    def list(
        self,
        workspace_id: str,
        *,
        type: Optional[str] = None,
        active_only: bool = True,
        status: Optional[str] = None,
    ) -> list[dict]:
        where = ["workspace_id = ?"]
        params: list[Any] = [workspace_id]
        if type:
            where.append("type = ?"); params.append(type)
        if active_only:
            where.append("active = 1")
        if status:
            where.append("engagement_status = ?"); params.append(status)
        sql = f"SELECT * FROM partners WHERE {' AND '.join(where)} ORDER BY name"
        with self.db.connect() as conn:
            rows = conn.execute(sql, params).fetchall()
        return [dict(r) for r in rows]

    def get(self, partner_id: str, workspace_id: str) -> dict:
        return self._get(partner_id, workspace_id)

    def _get(self, partner_id: str, workspace_id: Optional[str] = None) -> dict:
        with self.db.connect() as conn:
            if workspace_id:
                row = conn.execute(
                    "SELECT * FROM partners WHERE id = ? AND workspace_id = ?",
                    (partner_id, workspace_id),
                ).fetchone()
            else:
                row = conn.execute(
                    "SELECT * FROM partners WHERE id = ?", (partner_id,)
                ).fetchone()
        if not row:
            raise RecordNotFoundError(f"Partner {partner_id} not found")
        return dict(row)

    def update(self, partner_id: str, workspace_id: str, actor: str, **fields: Any) -> dict:
        self._get(partner_id, workspace_id)
        allowed = {
            "name", "type", "website_url", "hq_country", "primary_contact_id",
            "parent_partner_id", "revenue_split_pct", "visibility_tier",
            "engagement_status", "notes", "inkwell_page_slug", "onboarded_at", "active",
        }
        updates = {k: v for k, v in fields.items() if k in allowed}
        if not updates:
            return self._get(partner_id, workspace_id)
        updates["updated_at"] = now_iso()
        updates["updated_by"] = actor
        set_clause = ", ".join(f"{k} = ?" for k in updates)
        params = list(updates.values()) + [partner_id, workspace_id]
        with self.db.connect() as conn:
            conn.execute(
                f"UPDATE partners SET {set_clause} WHERE id = ? AND workspace_id = ?",
                params,
            )
        row = self._get(partner_id, workspace_id)
        _emit("structured_record:partner:updated", partner_id, workspace_id, actor, updates)
        return row

    def get_contacts(self, partner_id: str, workspace_id: str) -> list[dict]:
        with self.db.connect() as conn:
            rows = conn.execute(
                "SELECT * FROM contacts WHERE org_id = ? AND workspace_id = ? AND archived_at IS NULL ORDER BY last_name",
                (partner_id, workspace_id),
            ).fetchall()
        return [dict(r) for r in rows]

    def get_opportunities(self, partner_id: str, workspace_id: str) -> list[dict]:
        with self.db.connect() as conn:
            rows = conn.execute(
                "SELECT * FROM opportunities WHERE partner_id = ? AND workspace_id = ? AND archived_at IS NULL ORDER BY updated_at DESC",
                (partner_id, workspace_id),
            ).fetchall()
        return [dict(r) for r in rows]


# ---------------------------------------------------------------------------
# Opportunities
# ---------------------------------------------------------------------------

class OpportunitiesService:
    def __init__(self, db: Optional[SquadDB] = None) -> None:
        self.db = db or SquadDB()

    def create(
        self,
        workspace_id: str,
        name: str,
        type: str,
        *,
        external_id: Optional[str] = None,
        partner_id: Optional[str] = None,
        primary_contact_id: Optional[str] = None,
        stage: str = "prospect",
        estimated_value: Optional[float] = None,
        estimated_close_at: Optional[str] = None,
        owner_id: str = "system",
        notes_ref: Optional[str] = None,
        notes: Optional[str] = None,
        actor: str = "system",
    ) -> dict:
        opp_id = str(uuid4())
        now = now_iso()
        with self.db.connect() as conn:
            try:
                conn.execute(
                    """
                    INSERT INTO opportunities (
                        id, workspace_id, external_id, name, type,
                        partner_id, primary_contact_id, stage, stage_entered_at,
                        estimated_value, estimated_close_at, owner_id,
                        notes_ref, notes,
                        created_at, updated_at, created_by, updated_by
                    ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                    """,
                    (
                        opp_id, workspace_id, external_id, name, type,
                        partner_id, primary_contact_id, stage, now,
                        estimated_value, estimated_close_at, owner_id,
                        notes_ref, notes,
                        now, now, actor, actor,
                    ),
                )
            except Exception as exc:
                if "UNIQUE" in str(exc):
                    raise RecordConflictError(str(exc)) from exc
                raise
        row = self._get(opp_id)
        _emit("structured_record:opportunity:created", opp_id, workspace_id, actor, {"name": name, "stage": stage})
        return row

    def list(
        self,
        workspace_id: str,
        *,
        stage: Optional[str] = None,
        partner_id: Optional[str] = None,
        owner_id: Optional[str] = None,
        include_archived: bool = False,
    ) -> list[dict]:
        where = ["workspace_id = ?"]
        params: list[Any] = [workspace_id]
        if stage:
            where.append("stage = ?"); params.append(stage)
        if partner_id:
            where.append("partner_id = ?"); params.append(partner_id)
        if owner_id:
            where.append("owner_id = ?"); params.append(owner_id)
        if not include_archived:
            where.append("archived_at IS NULL")
        sql = f"SELECT * FROM opportunities WHERE {' AND '.join(where)} ORDER BY updated_at DESC"
        with self.db.connect() as conn:
            rows = conn.execute(sql, params).fetchall()
        return [dict(r) for r in rows]

    def get(self, opp_id: str, workspace_id: str) -> dict:
        return self._get(opp_id, workspace_id)

    def _get(self, opp_id: str, workspace_id: Optional[str] = None) -> dict:
        with self.db.connect() as conn:
            if workspace_id:
                row = conn.execute(
                    "SELECT * FROM opportunities WHERE id = ? AND workspace_id = ?",
                    (opp_id, workspace_id),
                ).fetchone()
            else:
                row = conn.execute(
                    "SELECT * FROM opportunities WHERE id = ?", (opp_id,)
                ).fetchone()
        if not row:
            raise RecordNotFoundError(f"Opportunity {opp_id} not found")
        return dict(row)

    def transition_stage(self, opp_id: str, workspace_id: str, to_stage: str, actor: str) -> dict:
        existing = self._get(opp_id, workspace_id)
        from_stage = existing["stage"]
        now = now_iso()
        log_id = str(uuid4())
        with self.db.connect() as conn:
            conn.execute(
                "UPDATE opportunities SET stage = ?, stage_entered_at = ?, updated_at = ?, updated_by = ? WHERE id = ? AND workspace_id = ?",
                (to_stage, now, now, actor, opp_id, workspace_id),
            )
            conn.execute(
                "INSERT INTO opportunity_stage_log (id, opportunity_id, from_stage, to_stage, transitioned_at, transitioned_by) VALUES (?,?,?,?,?,?)",
                (log_id, opp_id, from_stage, to_stage, now, actor),
            )
        row = self._get(opp_id, workspace_id)
        _emit("structured_record:opportunity:updated", opp_id, workspace_id, actor, {"from_stage": from_stage, "to_stage": to_stage})
        return row

    def update(self, opp_id: str, workspace_id: str, actor: str, **fields: Any) -> dict:
        self._get(opp_id, workspace_id)
        allowed = {
            "name", "type", "partner_id", "primary_contact_id",
            "estimated_value", "estimated_close_at", "close_reason",
            "owner_id", "notes_ref", "notes",
        }
        updates = {k: v for k, v in fields.items() if k in allowed}
        if not updates:
            return self._get(opp_id, workspace_id)
        updates["updated_at"] = now_iso()
        updates["updated_by"] = actor
        set_clause = ", ".join(f"{k} = ?" for k in updates)
        params = list(updates.values()) + [opp_id, workspace_id]
        with self.db.connect() as conn:
            conn.execute(
                f"UPDATE opportunities SET {set_clause} WHERE id = ? AND workspace_id = ?",
                params,
            )
        row = self._get(opp_id, workspace_id)
        _emit("structured_record:opportunity:updated", opp_id, workspace_id, actor, updates)
        return row

    def pipeline_summary(self, workspace_id: str) -> list[dict]:
        with self.db.connect() as conn:
            rows = conn.execute(
                """
                SELECT stage,
                       COUNT(*) AS count,
                       SUM(COALESCE(estimated_value, 0)) AS total_value
                FROM opportunities
                WHERE workspace_id = ? AND archived_at IS NULL
                GROUP BY stage
                ORDER BY stage
                """,
                (workspace_id,),
            ).fetchall()
        return [dict(r) for r in rows]


# ---------------------------------------------------------------------------
# Referrals
# ---------------------------------------------------------------------------

class ReferralsService:
    def __init__(self, db: Optional[SquadDB] = None) -> None:
        self.db = db or SquadDB()

    def create(
        self,
        workspace_id: str,
        source_id: str,
        source_type: str,
        target_id: str,
        target_type: str,
        relationship: str,
        *,
        strength: str = "moderate",
        context: Optional[str] = None,
        referred_at: Optional[str] = None,
        notes: Optional[str] = None,
        actor: str = "system",
    ) -> dict:
        ref_id = str(uuid4())
        now = now_iso()
        with self.db.connect() as conn:
            try:
                conn.execute(
                    """
                    INSERT INTO referrals (
                        id, workspace_id, source_id, source_type, target_id, target_type,
                        relationship, strength, context, referred_at, notes,
                        created_at, updated_at, created_by
                    ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                    """,
                    (
                        ref_id, workspace_id, source_id, source_type, target_id, target_type,
                        relationship, strength, context, referred_at, notes,
                        now, now, actor,
                    ),
                )
            except Exception as exc:
                if "UNIQUE" in str(exc):
                    raise RecordConflictError("Referral edge already exists") from exc
                raise
        row = self._get(ref_id)
        _emit("structured_record:referral:created", ref_id, workspace_id, actor, {
            "source_id": source_id, "target_id": target_id, "relationship": relationship
        })
        return row

    def list(
        self,
        workspace_id: str,
        *,
        source_id: Optional[str] = None,
        source_type: Optional[str] = None,
        target_id: Optional[str] = None,
        target_type: Optional[str] = None,
    ) -> list[dict]:
        where = ["workspace_id = ?"]
        params: list[Any] = [workspace_id]
        if source_id:
            where.append("source_id = ?"); params.append(source_id)
        if source_type:
            where.append("source_type = ?"); params.append(source_type)
        if target_id:
            where.append("target_id = ?"); params.append(target_id)
        if target_type:
            where.append("target_type = ?"); params.append(target_type)
        sql = f"SELECT * FROM referrals WHERE {' AND '.join(where)} ORDER BY created_at DESC"
        with self.db.connect() as conn:
            rows = conn.execute(sql, params).fetchall()
        return [dict(r) for r in rows]

    def network(self, entity_id: str, workspace_id: str, hops: int = 2) -> dict[str, list[dict]]:
        """BFS N-hop network around entity_id (contacts or partners)."""
        visited: set[str] = set()
        frontier = [entity_id]
        edges: list[dict] = []
        for _ in range(hops):
            if not frontier:
                break
            next_frontier: list[str] = []
            with self.db.connect() as conn:
                for node in frontier:
                    if node in visited:
                        continue
                    visited.add(node)
                    rows = conn.execute(
                        """
                        SELECT * FROM referrals
                        WHERE workspace_id = ? AND (source_id = ? OR target_id = ?)
                        """,
                        (workspace_id, node, node),
                    ).fetchall()
                    for r in rows:
                        d = dict(r)
                        edges.append(d)
                        other = d["target_id"] if d["source_id"] == node else d["source_id"]
                        if other not in visited:
                            next_frontier.append(other)
            frontier = next_frontier
        return {"center": entity_id, "hops": hops, "edges": edges}

    def _get(self, ref_id: str, workspace_id: Optional[str] = None) -> dict:
        with self.db.connect() as conn:
            if workspace_id:
                row = conn.execute(
                    "SELECT * FROM referrals WHERE id = ? AND workspace_id = ?",
                    (ref_id, workspace_id),
                ).fetchone()
            else:
                row = conn.execute("SELECT * FROM referrals WHERE id = ?", (ref_id,)).fetchone()
        if not row:
            raise RecordNotFoundError(f"Referral {ref_id} not found")
        return dict(row)

    def update(self, ref_id: str, workspace_id: str, actor: str, **fields: Any) -> dict:
        allowed = {"strength", "context", "notes", "referred_at"}
        updates = {k: v for k, v in fields.items() if k in allowed}
        if not updates:
            return self._get(ref_id, workspace_id)
        updates["updated_at"] = now_iso()
        set_clause = ", ".join(f"{k} = ?" for k in updates)
        params = list(updates.values()) + [ref_id, workspace_id]
        with self.db.connect() as conn:
            conn.execute(
                f"UPDATE referrals SET {set_clause} WHERE id = ? AND workspace_id = ?",
                params,
            )
        row = self._get(ref_id, workspace_id)
        _emit("structured_record:referral:updated", ref_id, workspace_id, actor, updates)
        return row

    def delete(self, ref_id: str, workspace_id: str, actor: str) -> None:
        with self.db.connect() as conn:
            conn.execute(
                "DELETE FROM referrals WHERE id = ? AND workspace_id = ?",
                (ref_id, workspace_id),
            )
        _emit("structured_record:referral:deleted", ref_id, workspace_id, actor, {})
