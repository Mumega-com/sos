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
from sos.observability.logging import get_logger

try:
    from dotenv import load_dotenv
except ImportError:
    load_dotenv = None

if load_dotenv:
    load_dotenv("/home/mumega/.env.secrets")


log = get_logger("squad_service")

SOS_DATA_DIR = Path.home() / ".sos" / "data"
DB_PATH = SOS_DATA_DIR / "squads.db"
REDIS_PASSWORD = os.getenv("REDIS_PASSWORD", "")
REDIS_HOST = os.getenv("REDIS_HOST", "127.0.0.1")
REDIS_PORT = int(os.getenv("REDIS_PORT", "6379"))
DEFAULT_TENANT_ID = "default"


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _dumps(value: Any) -> str:
    return json.dumps(value)


def _loads(value: str | None, fallback: Any) -> Any:
    if not value:
        return fallback
    return json.loads(value)


class SquadDB:
    def __init__(self, db_path: Path = DB_PATH):
        self.db_path = db_path
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._init_db()

    def connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        return conn

    def _init_db(self) -> None:
        with self.connect() as conn:
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS squads (
                    id TEXT PRIMARY KEY,
                    tenant_id TEXT NOT NULL DEFAULT 'default',
                    name TEXT NOT NULL,
                    project TEXT NOT NULL,
                    objective TEXT NOT NULL,
                    tier TEXT NOT NULL,
                    status TEXT NOT NULL,
                    roles_json TEXT NOT NULL,
                    members_json TEXT NOT NULL,
                    kpis_json TEXT NOT NULL,
                    budget_cents_monthly INTEGER NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS squad_tasks (
                    id TEXT PRIMARY KEY,
                    tenant_id TEXT NOT NULL DEFAULT 'default',
                    squad_id TEXT NOT NULL,
                    title TEXT NOT NULL,
                    description TEXT NOT NULL,
                    status TEXT NOT NULL,
                    priority TEXT NOT NULL,
                    assignee TEXT,
                    skill_id TEXT,
                    project TEXT NOT NULL,
                    labels_json TEXT NOT NULL,
                    blocked_by_json TEXT NOT NULL,
                    blocks_json TEXT NOT NULL,
                    inputs_json TEXT NOT NULL,
                    result_json TEXT NOT NULL,
                    token_budget INTEGER NOT NULL,
                    bounty_json TEXT NOT NULL,
                    external_ref TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    completed_at TEXT,
                    claimed_at TEXT,
                    attempt INTEGER NOT NULL
                );

                CREATE INDEX IF NOT EXISTS idx_squad_tasks_squad_status
                    ON squad_tasks (squad_id, status);

                CREATE TABLE IF NOT EXISTS squad_skills (
                    id TEXT PRIMARY KEY,
                    tenant_id TEXT NOT NULL DEFAULT 'default',
                    name TEXT NOT NULL,
                    description TEXT NOT NULL,
                    labels_json TEXT NOT NULL,
                    keywords_json TEXT NOT NULL,
                    entrypoint TEXT NOT NULL,
                    required_inputs_json TEXT NOT NULL,
                    status TEXT NOT NULL,
                    fuel_grade TEXT NOT NULL,
                    version TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS squad_state (
                    squad_id TEXT PRIMARY KEY,
                    tenant_id TEXT NOT NULL DEFAULT 'default',
                    project TEXT NOT NULL,
                    data_json TEXT NOT NULL,
                    version INTEGER NOT NULL,
                    updated_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS squad_events (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    tenant_id TEXT NOT NULL DEFAULT 'default',
                    squad_id TEXT NOT NULL,
                    event_type TEXT NOT NULL,
                    actor TEXT NOT NULL,
                    payload_json TEXT NOT NULL,
                    timestamp TEXT NOT NULL
                );

                CREATE INDEX IF NOT EXISTS idx_squad_events_squad_timestamp
                    ON squad_events (squad_id, timestamp DESC);

                CREATE TABLE IF NOT EXISTS api_keys (
                    token_hash TEXT PRIMARY KEY,
                    tenant_id TEXT NOT NULL,
                    identity_type TEXT NOT NULL,
                    created_at TEXT NOT NULL
                );
                """
            )
            self._ensure_column(conn, "squads", "tenant_id", "TEXT NOT NULL DEFAULT 'default'")
            self._ensure_column(conn, "squad_tasks", "tenant_id", "TEXT NOT NULL DEFAULT 'default'")
            self._ensure_column(conn, "squad_skills", "tenant_id", "TEXT NOT NULL DEFAULT 'default'")
            self._ensure_column(conn, "squad_state", "tenant_id", "TEXT NOT NULL DEFAULT 'default'")
            self._ensure_column(conn, "squad_events", "tenant_id", "TEXT NOT NULL DEFAULT 'default'")
            conn.executescript(
                """
                CREATE INDEX IF NOT EXISTS idx_squads_tenant
                    ON squads (tenant_id, updated_at DESC);
                CREATE INDEX IF NOT EXISTS idx_squad_tasks_tenant
                    ON squad_tasks (tenant_id, updated_at DESC);
                CREATE INDEX IF NOT EXISTS idx_squad_skills_tenant
                    ON squad_skills (tenant_id, name ASC);
                CREATE INDEX IF NOT EXISTS idx_squad_state_tenant
                    ON squad_state (tenant_id, updated_at DESC);
                CREATE INDEX IF NOT EXISTS idx_squad_events_tenant
                    ON squad_events (tenant_id, timestamp DESC);
                """
            )

    @staticmethod
    def _ensure_column(conn: sqlite3.Connection, table: str, column: str, ddl: str) -> None:
        existing = {row["name"] for row in conn.execute(f"PRAGMA table_info({table})").fetchall()}
        if column not in existing:
            conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {ddl}")


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
