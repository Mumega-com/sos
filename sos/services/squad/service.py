from __future__ import annotations

import json
import os
import sqlite3
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import redis

from sos.contracts.squad import Squad, SquadEvent, SquadMember, SquadRole, SquadStatus, SquadTier
from sos.kernel import Message, MessageType, Response, ResponseStatus
from sos.kernel.config import DB_PATH, DEFAULT_TENANT_ID, SOS_DATA_DIR
from sos.observability.logging import get_logger

try:
    from dotenv import load_dotenv
except ImportError:
    load_dotenv = None

if load_dotenv:
    load_dotenv(str(Path.home() / ".env.secrets"))


log = get_logger("squad_service")

REDIS_PASSWORD = os.getenv("REDIS_PASSWORD", "")
REDIS_HOST = os.getenv("REDIS_HOST", "127.0.0.1")
REDIS_PORT = int(os.getenv("REDIS_PORT", "6379"))


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _dumps(value: Any) -> str:
    return json.dumps(value)


def _loads(value: str | None, fallback: Any) -> Any:
    if not value:
        return fallback
    return json.loads(value)


class SquadDB:
    """Thin SQLite wrapper for the Squad service.

    Schema is owned by Alembic — run ``scripts/migrate-db.sh squad``
    (or ``alembic -c sos/services/squad/alembic.ini upgrade head``)
    before first use. This class no longer runs DDL at init time;
    see ``sos/services/squad/alembic/versions/0001_initial.py``.
    """

    def __init__(self, db_path: Path = DB_PATH):
        self.db_path = db_path
        self.db_path.parent.mkdir(parents=True, exist_ok=True)

    def connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        return conn


class SquadBus:
    def __init__(self):
        self.redis = redis.Redis(
            host=REDIS_HOST,
            port=REDIS_PORT,
            password=REDIS_PASSWORD or None,
            decode_responses=True,
        )

    def emit(self, event_type: str, squad_id: str, actor: str, payload: dict[str, Any]) -> None:
        message = Message(
            type=MessageType.SIGNAL,
            source="service:squad",
            target=f"squad:{squad_id}",
            payload={
                "event_type": event_type,
                "squad_id": squad_id,
                "actor": actor,
                "payload": payload,
            },
        )
        raw = message.to_json()
        channels = [f"sos:channel:squad:{squad_id}", "sos:channel:global"]
        stream = f"sos:stream:global:squad:{squad_id}"
        try:
            for channel in channels:
                self.redis.publish(channel, raw)
            self.redis.xadd(stream, {"payload": raw}, maxlen=1000)
        except Exception as exc:
            log.warn("Squad bus emit failed", error=str(exc), event_type=event_type, squad_id=squad_id)


def row_to_squad(row: sqlite3.Row) -> Squad:
    return Squad(
        id=row["id"],
        name=row["name"],
        project=row["project"],
        objective=row["objective"],
        tier=SquadTier(row["tier"]),
        status=SquadStatus(row["status"]),
        roles=[SquadRole(**item) for item in _loads(row["roles_json"], [])],
        members=[SquadMember(**item) for item in _loads(row["members_json"], [])],
        kpis=_loads(row["kpis_json"], []),
        budget_cents_monthly=row["budget_cents_monthly"],
        created_at=row["created_at"],
        updated_at=row["updated_at"],
    )


class SquadService:
    def __init__(self, db: SquadDB | None = None, bus: SquadBus | None = None):
        self.db = db or SquadDB()
        self.bus = bus or SquadBus()

    def create(self, squad: Squad, actor: str = "system", tenant_id: str = DEFAULT_TENANT_ID) -> Response:
        timestamp = now_iso()
        if not squad.created_at:
            squad.created_at = timestamp
        squad.updated_at = timestamp
        with self.db.connect() as conn:
            conn.execute(
                """
                INSERT INTO squads (
                    id, tenant_id, name, project, objective, tier, status, roles_json, members_json,
                    kpis_json, budget_cents_monthly, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    squad.id,
                    tenant_id,
                    squad.name,
                    squad.project,
                    squad.objective,
                    squad.tier.value,
                    squad.status.value,
                    _dumps([asdict(role) for role in squad.roles]),
                    _dumps([asdict(member) for member in squad.members]),
                    _dumps(squad.kpis),
                    squad.budget_cents_monthly,
                    squad.created_at,
                    squad.updated_at,
                ),
            )
        self.bus.emit("squad.created", squad.id, actor, asdict(squad))
        return Response(message_id=squad.id, status=ResponseStatus.SUCCESS, data={"squad": asdict(squad)})

    def get(self, squad_id: str, tenant_id: str | None = DEFAULT_TENANT_ID) -> Squad | None:
        with self.db.connect() as conn:
            if tenant_id is None:
                row = conn.execute("SELECT * FROM squads WHERE id = ?", (squad_id,)).fetchone()
            else:
                row = conn.execute("SELECT * FROM squads WHERE id = ? AND tenant_id = ?", (squad_id, tenant_id)).fetchone()
        return row_to_squad(row) if row else None

    def list(
        self,
        status: SquadStatus | None = None,
        project: str | None = None,
        tenant_id: str | None = DEFAULT_TENANT_ID,
    ) -> list[Squad]:
        query = "SELECT * FROM squads WHERE 1=1"
        params: list[Any] = []
        if tenant_id is not None:
            query += " AND tenant_id = ?"
            params.append(tenant_id)
        if status:
            query += " AND status = ?"
            params.append(status.value)
        if project:
            query += " AND project = ?"
            params.append(project)
        query += " ORDER BY updated_at DESC"
        with self.db.connect() as conn:
            rows = conn.execute(query, params).fetchall()
        return [row_to_squad(row) for row in rows]

    def update(self, squad_id: str, updates: dict[str, Any], actor: str = "system", tenant_id: str | None = DEFAULT_TENANT_ID) -> Squad:
        existing = self.get(squad_id, tenant_id=tenant_id)
        if not existing:
            raise KeyError(f"Squad not found: {squad_id}")
        for field_name, value in updates.items():
            if hasattr(existing, field_name) and value is not None:
                setattr(existing, field_name, value)
        existing.updated_at = now_iso()
        with self.db.connect() as conn:
            conn.execute(
                """
                UPDATE squads SET
                    name = ?, project = ?, objective = ?, tier = ?, status = ?, roles_json = ?,
                    members_json = ?, kpis_json = ?, budget_cents_monthly = ?, updated_at = ?
                WHERE id = ? AND tenant_id = ?
                """,
                (
                    existing.name,
                    existing.project,
                    existing.objective,
                    existing.tier.value,
                    existing.status.value,
                    _dumps([asdict(role) for role in existing.roles]),
                    _dumps([asdict(member) for member in existing.members]),
                    _dumps(existing.kpis),
                    existing.budget_cents_monthly,
                    existing.updated_at,
                    existing.id,
                    tenant_id if tenant_id is not None else DEFAULT_TENANT_ID,
                ),
            )
        event_type = f"squad.{existing.status.value}" if existing.status != SquadStatus.DRAFT else "squad.created"
        self.bus.emit(event_type, squad_id, actor, updates)
        return existing

    def delete(self, squad_id: str, actor: str = "system", tenant_id: str | None = DEFAULT_TENANT_ID) -> bool:
        with self.db.connect() as conn:
            if tenant_id is None:
                deleted = conn.execute("DELETE FROM squads WHERE id = ?", (squad_id,)).rowcount
            else:
                deleted = conn.execute("DELETE FROM squads WHERE id = ? AND tenant_id = ?", (squad_id, tenant_id)).rowcount
        if deleted:
            self.bus.emit("squad.archived", squad_id, actor, {"deleted": True})
        return bool(deleted)

    def record_event(self, event: SquadEvent, tenant_id: str = DEFAULT_TENANT_ID) -> SquadEvent:
        if not event.timestamp:
            event.timestamp = now_iso()
        with self.db.connect() as conn:
            conn.execute(
                "INSERT INTO squad_events (tenant_id, squad_id, event_type, actor, payload_json, timestamp) VALUES (?, ?, ?, ?, ?, ?)",
                (tenant_id, event.squad_id, event.event_type, event.actor, _dumps(event.payload), event.timestamp),
            )
        self.bus.emit(event.event_type, event.squad_id, event.actor, event.payload)
        return event
