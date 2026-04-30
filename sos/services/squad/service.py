from __future__ import annotations

import json
import math
import os
import sqlite3
from dataclasses import asdict
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from uuid import uuid4

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


def _decode_projects(raw: str | None) -> list[str]:
    """Decode the squads.project column into a list of projects.

    S016 Track F (2026-04-29): the column is now JSON-encoded list[str] for
    multi-project support (e.g. Forge squad: projects=["*"]). Older rows hold a
    bare string (e.g. "sos") — accept both for backward compat.
    """
    if not raw:
        return []
    try:
        decoded = json.loads(raw)
    except (ValueError, TypeError):
        return [raw]
    if isinstance(decoded, list):
        return [str(item) for item in decoded]
    return [str(decoded)]


def row_to_squad(row: sqlite3.Row) -> Squad:
    return Squad(
        id=row["id"],
        name=row["name"],
        projects=_decode_projects(row["project"]),
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
                    _dumps(squad.projects),
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
            # S016 Track F: project column now stores JSON-encoded list[str].
            # Substring LIKE narrows candidates; exact membership check below
            # rejects false-positives (WARN-F1-1, e.g. 'ga' matching 'gaf').
            # Wildcard "*" entry means accept-any-project.
            query += " AND (project = ? OR project LIKE ? OR project LIKE ?)"
            params.append(project)
            params.append(f'%"{project}"%')
            params.append('%"*"%')
        query += " ORDER BY updated_at DESC"
        with self.db.connect() as conn:
            rows = conn.execute(query, params).fetchall()
        squads = [row_to_squad(row) for row in rows]
        if project:
            # WARN-F1-1 fix: enforce exact JSON-array-membership semantics.
            # The LIKE clauses above are an indexable narrow filter; this loop
            # is the truth check. Accepts: exact plain match (legacy), wildcard
            # "*" entry, or exact list membership.
            def _matches(sq: Squad) -> bool:
                raw = sq.project
                if raw == project:
                    return True
                try:
                    decoded = json.loads(raw) if isinstance(raw, str) else raw
                except (json.JSONDecodeError, TypeError):
                    return raw == project
                if not isinstance(decoded, list):
                    return decoded == project
                return project in decoded or "*" in decoded
            squads = [sq for sq in squads if _matches(sq)]
        return squads

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
                    _dumps(existing.projects),
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


# ── League Service ─────────────────────────────────────────────────────────────

_CONSTRUCT_PCT = 0.15   # top 15%
_FORTRESS_PCT = 0.50    # top 50% (i.e. next 35% after construct)


class LeagueService:
    """Monthly squad league: seasons, weekly KPI snapshots, tier assignment."""

    def __init__(self, db: SquadDB | None = None):
        self.db = db or SquadDB()

    # ── Season management ──────────────────────────────────────────────────

    def get_current_season(self, tenant_id: str | None = None) -> dict[str, Any] | None:
        """Return the active season row for the given tenant, or None if none exists.

        When ``tenant_id`` is ``None`` (default), returns the active season that has
        no tenant binding (global/system-wide). When ``tenant_id`` is set, returns
        the active season scoped to that specific tenant.
        """
        with self.db.connect() as conn:
            if tenant_id is None:
                row = conn.execute(
                    """
                    SELECT id, name, start_date, end_date, status, tenant_id, created_at
                    FROM league_seasons
                    WHERE status = 'active' AND tenant_id IS NULL
                    ORDER BY start_date DESC
                    LIMIT 1
                    """,
                ).fetchone()
            else:
                row = conn.execute(
                    """
                    SELECT id, name, start_date, end_date, status, tenant_id, created_at
                    FROM league_seasons
                    WHERE status = 'active' AND tenant_id = ?
                    ORDER BY start_date DESC
                    LIMIT 1
                    """,
                    (tenant_id,),
                ).fetchone()
        if row is None:
            return None
        return dict(row)

    def list_seasons(self, tenant_id: str | None = None) -> list[dict[str, Any]]:
        """Return all seasons ordered newest first.

        When ``tenant_id`` is ``None`` (default), returns only global seasons
        (those with no tenant binding). When ``tenant_id`` is set, returns only
        seasons scoped to that specific tenant.
        """
        with self.db.connect() as conn:
            if tenant_id is None:
                rows = conn.execute(
                    """
                    SELECT id, name, start_date, end_date, status, tenant_id, created_at
                    FROM league_seasons
                    WHERE tenant_id IS NULL
                    ORDER BY start_date DESC
                    """,
                ).fetchall()
            else:
                rows = conn.execute(
                    """
                    SELECT id, name, start_date, end_date, status, tenant_id, created_at
                    FROM league_seasons
                    WHERE tenant_id = ?
                    ORDER BY start_date DESC
                    """,
                    (tenant_id,),
                ).fetchall()
        return [dict(r) for r in rows]

    def create_season(
        self,
        name: str,
        start_date: str,
        end_date: str,
        tenant_id: str | None = None,
    ) -> dict[str, Any]:
        """Insert a new season and return it."""
        season_id = str(uuid4())
        created_at = now_iso()
        with self.db.connect() as conn:
            conn.execute(
                """
                INSERT INTO league_seasons (id, name, start_date, end_date, status, tenant_id, created_at)
                VALUES (?, ?, ?, ?, 'active', ?, ?)
                """,
                (season_id, name, start_date, end_date, tenant_id, created_at),
            )
        return {
            "id": season_id,
            "name": name,
            "start_date": start_date,
            "end_date": end_date,
            "status": "active",
            "tenant_id": tenant_id,
            "created_at": created_at,
        }

    def ensure_active_season(self, tenant_id: str | None = None) -> dict[str, Any]:
        """Get or create the active season for the given tenant.

        If a season exists but its end_date has passed, marks it 'closed'
        and creates a fresh one for the current month.

        When ``tenant_id`` is ``None`` (default), operates on the global
        (no-tenant) league. When ``tenant_id`` is set, operates on that
        tenant's isolated league.
        """
        now = datetime.now(timezone.utc)
        existing = self.get_current_season(tenant_id=tenant_id)
        if existing is not None:
            # Check if it has already ended
            try:
                end = datetime.fromisoformat(existing["end_date"].replace("Z", "+00:00"))
                if end >= now:
                    return existing  # still valid
            except (ValueError, TypeError):
                return existing  # can't parse — leave it alone

            # Mark it closed
            with self.db.connect() as conn:
                conn.execute(
                    "UPDATE league_seasons SET status = 'closed' WHERE id = ?",
                    (existing["id"],),
                )

        # Create a new season for the current month
        first_day = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
        # Last day of month: first day of next month minus one day
        if first_day.month == 12:
            next_month = first_day.replace(year=first_day.year + 1, month=1)
        else:
            next_month = first_day.replace(month=first_day.month + 1)
        last_day = next_month - timedelta(days=1)

        name = f"Season {now.strftime('%Y-%m')}"
        start_date = first_day.date().isoformat()
        end_date = last_day.date().isoformat()
        return self.create_season(name=name, start_date=start_date, end_date=end_date, tenant_id=tenant_id)

    # ── League table ───────────────────────────────────────────────────────

    def get_league_table(self, season_id: str) -> list[dict[str, Any]]:
        """Return ranked league_scores joined with squad name/tier for a season."""
        with self.db.connect() as conn:
            rows = conn.execute(
                """
                SELECT
                    ls.id,
                    ls.season_id,
                    ls.squad_id,
                    ls.score,
                    ls.rank,
                    ls.tier,
                    ls.snapshot_at,
                    s.name AS squad_name
                FROM league_scores ls
                LEFT JOIN squads s ON s.id = ls.squad_id
                WHERE ls.season_id = ?
                ORDER BY ls.rank ASC
                """,
                (season_id,),
            ).fetchall()
        return [dict(r) for r in rows]

    # ── Snapshot (weekly job) ──────────────────────────────────────────────

    async def snapshot_league_scores(self, season_id: str) -> list[dict[str, Any]]:
        """Compute KPIs for all active squads in the season's tenant scope, rank them, assign tiers.

        1. Resolve the season's tenant_id to determine scope.
        2. Read squads filtered by that tenant_id (or all squads when tenant_id is NULL).
        3. Call calculate_kpis for each.
        4. Rank by kpi_score descending.
        5. Assign tiers: top 15% → construct, next 35% → fortress, rest → nomad.
        6. Upsert league_scores for this season.
        7. Update squads.tier for each squad.

        When the season has no tenant_id (global season), all active squads participate.
        When the season has a tenant_id set, only that tenant's squads participate.
        """
        from sos.services.squad.kpis import calculate_kpis

        # 1. Resolve the season's tenant_id
        with self.db.connect() as conn:
            season_row = conn.execute(
                "SELECT tenant_id FROM league_seasons WHERE id = ?",
                (season_id,),
            ).fetchone()
        season_tenant_id: str | None = season_row["tenant_id"] if season_row else None

        # 2. Fetch squads scoped to the season's tenant
        with self.db.connect() as conn:
            if season_tenant_id is not None:
                squad_rows = conn.execute(
                    "SELECT id, tenant_id FROM squads WHERE tenant_id = ? AND status = 'active'",
                    (season_tenant_id,),
                ).fetchall()
            else:
                squad_rows = conn.execute(
                    "SELECT id, tenant_id FROM squads WHERE status = 'active'",
                ).fetchall()

        if not squad_rows:
            return []

        # 3. Gather KPI scores concurrently
        import asyncio as _asyncio

        async def _kpi(row: sqlite3.Row) -> tuple[str, str | None, float]:
            try:
                snapshot = await calculate_kpis(row["id"], db=self.db)
                return row["id"], row["tenant_id"], snapshot.kpi_score
            except Exception as exc:
                log.warning(
                    "league_snapshot: kpi failed",
                    squad_id=row["id"],
                    error=str(exc),
                )
                return row["id"], row["tenant_id"], 0.0

        results = await _asyncio.gather(*[_kpi(r) for r in squad_rows])

        # 3. Sort by score descending
        ranked = sorted(results, key=lambda t: t[2], reverse=True)
        total = len(ranked)

        construct_cutoff = max(1, math.ceil(total * _CONSTRUCT_PCT))
        fortress_cutoff = max(construct_cutoff, math.ceil(total * _FORTRESS_PCT))

        snapshot_at = now_iso()
        scores: list[dict[str, Any]] = []

        with self.db.connect() as conn:
            # Idempotency: skip if we already snapshotted this week for this season
            cursor = conn.execute(
                "SELECT COUNT(*) as cnt FROM league_scores WHERE season_id = ? AND week = strftime('%W-%Y', 'now')",
                (season_id,),
            )
            existing = cursor.fetchone()
            if existing and existing["cnt"] > 0:
                return {"skipped": True, "reason": "already_snapshotted_this_week"}  # type: ignore[return-value]

            # 5. Replace scores for this season
            conn.execute(
                "DELETE FROM league_scores WHERE season_id = ?", (season_id,)
            )
            for position, (squad_id, tenant_id, score) in enumerate(ranked, start=1):
                if position <= construct_cutoff:
                    tier = "construct"
                elif position <= fortress_cutoff:
                    tier = "fortress"
                else:
                    tier = "nomad"

                row_id = str(uuid4())
                conn.execute(
                    """
                    INSERT INTO league_scores
                        (id, season_id, squad_id, score, rank, tier, snapshot_at, tenant_id, week)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, strftime('%W-%Y', 'now'))
                    """,
                    (row_id, season_id, squad_id, score, position, tier, snapshot_at, tenant_id),
                )
                # 6. Update squads.tier
                conn.execute(
                    "UPDATE squads SET tier = ?, updated_at = ? WHERE id = ?",
                    (tier, snapshot_at, squad_id),
                )
                scores.append({
                    "id": row_id,
                    "season_id": season_id,
                    "squad_id": squad_id,
                    "score": score,
                    "rank": position,
                    "tier": tier,
                    "snapshot_at": snapshot_at,
                })

        log.info(
            "league_snapshot complete",
            season_id=season_id,
            total=total,
            construct=construct_cutoff,
            fortress=fortress_cutoff - construct_cutoff,
            nomad=total - fortress_cutoff,
        )
        return scores


# ── Achievement Service ────────────────────────────────────────────────────────

from dataclasses import dataclass
from typing import Optional


BADGE_DEFINITIONS: dict[str, tuple[str, str]] = {
    "first_memory": ("First Memory", "Squad stored their first memory"),
    "ten_tasks": ("Ten Done", "Squad completed 10 tasks"),
    "hundred_tasks": ("Century", "Squad completed 100 tasks"),
    "first_bounty": ("Bounty Hunter", "Squad claimed their first bounty"),
    "tier_up_fortress": ("Fortress", "Squad reached Fortress tier"),
    "tier_up_construct": ("Construct", "Squad reached Construct tier"),
    "streak_30d": ("On Fire", "Squad active for 30 of the last 30 days"),
}


@dataclass
class AchievementRow:
    id: str
    squad_id: str
    badge: str
    name: str
    description: Optional[str]
    earned_at: str


class AchievementService:
    def __init__(self, db: SquadDB | None = None) -> None:
        self.db = db or SquadDB()

    def get_achievements(self, squad_id: str) -> list[AchievementRow]:
        """Return all earned achievements for a squad, newest first."""
        with self.db.connect() as conn:
            rows = conn.execute(
                "SELECT id, squad_id, badge, name, description, earned_at "
                "FROM squad_achievements WHERE squad_id = ? ORDER BY earned_at DESC",
                (squad_id,),
            ).fetchall()
        return [
            AchievementRow(
                id=row["id"],
                squad_id=row["squad_id"],
                badge=row["badge"],
                name=row["name"],
                description=row["description"],
                earned_at=row["earned_at"],
            )
            for row in rows
        ]

    def _award(self, squad_id: str, badge: str, metadata: dict[str, Any]) -> bool:
        """Award a badge if not already earned. Returns True if newly awarded."""
        name, description = BADGE_DEFINITIONS.get(badge, (badge, None))  # type: ignore[assignment]
        achievement_id = str(uuid4())
        try:
            with self.db.connect() as conn:
                conn.execute(
                    "INSERT OR IGNORE INTO squad_achievements "
                    "(id, squad_id, badge, name, description, earned_at, metadata_json) "
                    "VALUES (?, ?, ?, ?, ?, datetime('now'), ?)",
                    (
                        achievement_id,
                        squad_id,
                        badge,
                        name,
                        description,
                        json.dumps(metadata),
                    ),
                )
                # Verify insertion: INSERT OR IGNORE is a no-op on duplicate (squad_id, badge)
                row = conn.execute(
                    "SELECT id FROM squad_achievements WHERE squad_id = ? AND badge = ? AND id = ?",
                    (squad_id, badge, achievement_id),
                ).fetchone()
                return row is not None
        except Exception as exc:
            log.warning("achievement _award failed", squad_id=squad_id, badge=badge, error=str(exc))
            return False

    def check_and_award(self, squad_id: str) -> list[str]:
        """Check all achievement conditions and award any unearned ones.

        Returns a list of newly awarded badge keys.
        Never raises — errors are logged and swallowed so task completion
        is never blocked by achievement logic.
        """
        newly_awarded: list[str] = []
        try:
            with self.db.connect() as conn:
                # Already-earned badges
                earned_rows = conn.execute(
                    "SELECT badge FROM squad_achievements WHERE squad_id = ?",
                    (squad_id,),
                ).fetchall()
                earned = {row["badge"] for row in earned_rows}

                # Squad tier
                squad_row = conn.execute(
                    "SELECT tier FROM squads WHERE id = ?", (squad_id,)
                ).fetchone()
                tier: str = squad_row["tier"] if squad_row else "nomad"

                # Completed task count (lifetime)
                counts_row = conn.execute(
                    "SELECT COUNT(*) AS n FROM squad_tasks WHERE squad_id = ? AND status = 'done'",
                    (squad_id,),
                ).fetchone()
                done_count: int = counts_row["n"] if counts_row else 0

                # Bounty count — done tasks that had a non-empty bounty_json
                bounty_row = conn.execute(
                    "SELECT COUNT(*) AS n FROM squad_tasks "
                    "WHERE squad_id = ? AND status = 'done' AND bounty_json != '{}'",
                    (squad_id,),
                ).fetchone()
                bounty_count: int = bounty_row["n"] if bounty_row else 0

            # first_memory: awarded when squad has at least one memory push recorded
            memory_row = conn.execute(
                "SELECT COUNT(*) AS n FROM squad_memory_counts WHERE squad_id = ?",
                (squad_id,),
            ).fetchone()
            has_memory: bool = False
            if memory_row is not None:
                has_memory = memory_row["n"] > 0
            else:
                # Table may not exist yet — fall back to checking squad_achievements itself
                has_memory = False

            # streak_30d: 30 distinct active days in the last 30 days
            streak_row = conn.execute(
                """
                SELECT COUNT(DISTINCT date(completed_at)) AS active_days
                FROM squad_tasks
                WHERE squad_id = ?
                  AND status = 'done'
                  AND completed_at >= date('now', '-30 days')
                """,
                (squad_id,),
            ).fetchone()
            active_days_30: int = streak_row["active_days"] if streak_row else 0

            checks: list[tuple[str, bool]] = [
                ("ten_tasks", done_count >= 10),
                ("hundred_tasks", done_count >= 100),
                ("first_bounty", bounty_count >= 1),
                ("tier_up_fortress", tier in ("fortress", "construct")),
                ("tier_up_construct", tier == "construct"),
                ("first_memory", has_memory),
                ("streak_30d", active_days_30 >= 30),  # was 20, must be 30
            ]

            for badge, condition in checks:
                if badge not in earned and condition:
                    if self._award(squad_id, badge, {}):
                        newly_awarded.append(badge)
                        log.info("achievement_awarded", squad_id=squad_id, badge=badge)

        except Exception as exc:
            log.warning("check_and_award failed", squad_id=squad_id, error=str(exc))

        return newly_awarded
