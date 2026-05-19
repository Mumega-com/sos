from __future__ import annotations

import json
import os
import sqlite3
from dataclasses import asdict
from typing import Any

import httpx

from sos.contracts.squad import SquadEvent, SquadState
from sos.observability.logging import get_logger
from sos.services.squad.service import DEFAULT_TENANT_ID, SquadBus, SquadDB, now_iso


log = get_logger("squad_state")

MIRROR_URL = os.environ.get("MIRROR_URL", "http://localhost:8844")
MIRROR_TOKEN = os.environ.get("MIRROR_TOKEN", "")
MIRROR_HEADERS = {"Authorization": f"Bearer {MIRROR_TOKEN}", "Content-Type": "application/json"}


def _loads(value: str | None, fallback: Any) -> Any:
    return json.loads(value) if value else fallback


def _dumps(value: Any) -> str:
    return json.dumps(value)


def row_to_state(row: sqlite3.Row) -> SquadState:
    return SquadState(
        squad_id=row["squad_id"],
        project=row["project"],
        data=_loads(row["data_json"], {}),
        version=row["version"],
        updated_at=row["updated_at"],
    )


class SquadStateService:
    def __init__(self, db: SquadDB | None = None, bus: SquadBus | None = None, mirror_sync: bool = False):
        self.db = db or SquadDB()
        self.bus = bus or SquadBus()
        self.mirror_sync = mirror_sync

    def load(self, squad_id: str, tenant_id: str | None = DEFAULT_TENANT_ID) -> SquadState | None:
        with self.db.connect() as conn:
            if tenant_id is None:
                row = conn.execute("SELECT * FROM squad_state WHERE squad_id = ?", (squad_id,)).fetchone()
            else:
                row = conn.execute(
                    "SELECT * FROM squad_state WHERE squad_id = ? AND tenant_id = ?",
                    (squad_id, tenant_id),
                ).fetchone()
        return row_to_state(row) if row else None

    async def save(self, state: SquadState, actor: str = "system", tenant_id: str = DEFAULT_TENANT_ID) -> SquadState:
        previous = self.load(state.squad_id, tenant_id=tenant_id)
        state.version = (previous.version + 1) if previous else max(state.version, 1)
        state.updated_at = now_iso()
        with self.db.connect() as conn:
            conn.execute(
                """
                INSERT OR REPLACE INTO squad_state (squad_id, tenant_id, project, data_json, version, updated_at)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (state.squad_id, tenant_id, state.project, _dumps(state.data), state.version, state.updated_at),
            )
        await self.append_event(
            SquadEvent(
                squad_id=state.squad_id,
                event_type="state.saved",
                actor=actor,
                payload={"version": state.version, "project": state.project},
            ),
            tenant_id=tenant_id,
        )
        if self.mirror_sync:
            await self._sync_to_mirror(state)
        return state

    async def append_event(self, event: SquadEvent, tenant_id: str = DEFAULT_TENANT_ID) -> SquadEvent:
        if not event.timestamp:
            event.timestamp = now_iso()
        with self.db.connect() as conn:
            conn.execute(
                "INSERT INTO squad_events (tenant_id, squad_id, event_type, actor, payload_json, timestamp) VALUES (?, ?, ?, ?, ?, ?)",
                (tenant_id, event.squad_id, event.event_type, event.actor, _dumps(event.payload), event.timestamp),
            )
        self.bus.emit(event.event_type, event.squad_id, event.actor, event.payload)
        return event

    def list_events(self, squad_id: str, limit: int = 50, tenant_id: str | None = DEFAULT_TENANT_ID) -> list[SquadEvent]:
        with self.db.connect() as conn:
            if tenant_id is None:
                rows = conn.execute(
                    "SELECT squad_id, event_type, actor, payload_json, timestamp FROM squad_events WHERE squad_id = ? ORDER BY id DESC LIMIT ?",
                    (squad_id, limit),
                ).fetchall()
            else:
                rows = conn.execute(
                    "SELECT squad_id, event_type, actor, payload_json, timestamp FROM squad_events WHERE squad_id = ? AND tenant_id = ? ORDER BY id DESC LIMIT ?",
                    (squad_id, tenant_id, limit),
                ).fetchall()
        return [
            SquadEvent(
                squad_id=row["squad_id"],
                event_type=row["event_type"],
                actor=row["actor"],
                payload=_loads(row["payload_json"], {}),
                timestamp=row["timestamp"],
            )
            for row in rows
        ]

    async def _sync_to_mirror(self, state: SquadState) -> None:
        body = {
            "text": json.dumps(asdict(state), sort_keys=True),
            "agent": f"squad:{state.squad_id}",
            "context_id": f"state:{state.squad_id}",
        }
        try:
            async with httpx.AsyncClient(timeout=10) as client:
                response = await client.post(f"{MIRROR_URL}/store", headers=MIRROR_HEADERS, json=body)
                response.raise_for_status()
        except httpx.HTTPError as exc:
            log.warning("Mirror sync failed", squad_id=state.squad_id, error=str(exc))
