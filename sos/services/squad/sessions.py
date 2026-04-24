"""ProjectSessionService — check-in/checkout/heartbeat and engagement tracking."""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from typing import Optional
from uuid import uuid4

from sos.services.squad.service import DEFAULT_TENANT_ID, SquadDB, now_iso


IDLE_TIMEOUT_MINUTES: int = 30


class SessionNotFoundError(ValueError):
    pass


class SessionAlreadyClosedError(ValueError):
    pass


def _idle_cutoff(minutes: int = IDLE_TIMEOUT_MINUTES) -> str:
    cutoff = datetime.now(timezone.utc) - timedelta(minutes=minutes)
    return cutoff.isoformat()


class ProjectSessionService:
    """Manage project sessions in the Squad Service SQLite DB."""

    def __init__(self, db: Optional[SquadDB] = None) -> None:
        self.db = db or SquadDB()

    # ------------------------------------------------------------------
    # Idle close helpers
    # ------------------------------------------------------------------

    def close_idle_sessions(
        self,
        project_id: str,
        tenant_id: str = DEFAULT_TENANT_ID,
        cutoff_iso: Optional[str] = None,
    ) -> int:
        """Close all open sessions for project_id with no activity since cutoff.

        Returns the number of sessions closed. Safe to call concurrently —
        UPDATE only matches rows where closed_at IS NULL (first writer wins).
        """
        cutoff = cutoff_iso or _idle_cutoff()

        with self.db.connect() as conn:
            # Find open sessions with no recent activity event
            stale_rows = conn.execute(
                """
                SELECT ps.id FROM project_sessions ps
                WHERE ps.project_id = ?
                  AND ps.tenant_id = ?
                  AND ps.closed_at IS NULL
                  AND NOT EXISTS (
                      SELECT 1 FROM project_session_events pse
                      WHERE pse.session_id = ps.id
                        AND pse.kind IN ('heartbeat', 'task_claim', 'task_complete', 'checkin')
                        AND pse.ts > ?
                  )
                  AND ps.opened_at < ?
                """,
                (project_id, tenant_id, cutoff, cutoff),
            ).fetchall()

            closed_count = 0
            now = now_iso()
            for row in stale_rows:
                result = conn.execute(
                    """
                    UPDATE project_sessions
                    SET closed_at = ?, close_reason = 'idle_timeout'
                    WHERE id = ? AND closed_at IS NULL
                    """,
                    (now, row["id"]),
                )
                if result.rowcount > 0:
                    conn.execute(
                        """
                        INSERT INTO project_session_events
                            (id, session_id, ts, kind, actor, payload_json)
                        VALUES (?, ?, ?, 'checkout', 'system', '{"reason":"idle_timeout"}')
                        """,
                        (str(uuid4()), row["id"], now),
                    )
                    closed_count += 1
            return closed_count

    def _close_idle_for_agent(
        self,
        conn,
        project_id: str,
        agent_id: str,
        tenant_id: str,
    ) -> None:
        """Lazy idle-close: close stale open session for this agent+project before opening new."""
        cutoff = _idle_cutoff()
        stale = conn.execute(
            """
            SELECT ps.id FROM project_sessions ps
            WHERE ps.project_id = ?
              AND ps.agent_id = ?
              AND ps.tenant_id = ?
              AND ps.closed_at IS NULL
              AND NOT EXISTS (
                  SELECT 1 FROM project_session_events pse
                  WHERE pse.session_id = ps.id
                    AND pse.kind IN ('heartbeat', 'task_claim', 'task_complete', 'checkin')
                    AND pse.ts > ?
              )
              AND ps.opened_at < ?
            """,
            (project_id, agent_id, tenant_id, cutoff, cutoff),
        ).fetchall()

        now = now_iso()
        for row in stale:
            result = conn.execute(
                """
                UPDATE project_sessions
                SET closed_at = ?, close_reason = 'idle_timeout'
                WHERE id = ? AND closed_at IS NULL
                """,
                (now, row["id"]),
            )
            if result.rowcount > 0:
                conn.execute(
                    """
                    INSERT INTO project_session_events
                        (id, session_id, ts, kind, actor, payload_json)
                    VALUES (?, ?, ?, 'checkout', 'system', '{"reason":"idle_timeout"}')
                    """,
                    (str(uuid4()), row["id"], now),
                )

    # ------------------------------------------------------------------
    # Check-in
    # ------------------------------------------------------------------

    def checkin(
        self,
        project_id: str,
        agent_id: str,
        tenant_id: str = DEFAULT_TENANT_ID,
        context: Optional[dict] = None,
    ) -> dict:
        """Open or return existing open session. Idempotent.

        Lazy-closes any idle stale sessions for this agent+project first.
        """
        with self.db.connect() as conn:
            # Lazy close stale idle sessions for this agent+project
            self._close_idle_for_agent(conn, project_id, agent_id, tenant_id)

            # Check for existing open session
            existing = conn.execute(
                """
                SELECT id, opened_at FROM project_sessions
                WHERE project_id = ? AND agent_id = ? AND tenant_id = ?
                  AND closed_at IS NULL
                ORDER BY opened_at DESC LIMIT 1
                """,
                (project_id, agent_id, tenant_id),
            ).fetchone()

            if existing:
                # Emit a checkin event on the existing session
                conn.execute(
                    """
                    INSERT INTO project_session_events
                        (id, session_id, ts, kind, actor, payload_json)
                    VALUES (?, ?, ?, 'checkin', ?, ?)
                    """,
                    (
                        str(uuid4()),
                        existing["id"],
                        now_iso(),
                        agent_id,
                        json.dumps(context or {}),
                    ),
                )
                return {"session_id": existing["id"], "opened_at": existing["opened_at"], "resumed": True}

            # Create new session
            session_id = str(uuid4())
            opened_at = now_iso()
            conn.execute(
                """
                INSERT INTO project_sessions
                    (id, project_id, agent_id, tenant_id, opened_at)
                VALUES (?, ?, ?, ?, ?)
                """,
                (session_id, project_id, agent_id, tenant_id, opened_at),
            )
            conn.execute(
                """
                INSERT INTO project_session_events
                    (id, session_id, ts, kind, actor, payload_json)
                VALUES (?, ?, ?, 'checkin', ?, ?)
                """,
                (
                    str(uuid4()),
                    session_id,
                    opened_at,
                    agent_id,
                    json.dumps(context or {}),
                ),
            )
            return {"session_id": session_id, "opened_at": opened_at, "resumed": False}

    # ------------------------------------------------------------------
    # Heartbeat
    # ------------------------------------------------------------------

    def heartbeat(self, session_id: str, actor: str = "agent") -> None:
        """Emit a heartbeat event. Raises SessionNotFoundError if session unknown."""
        with self.db.connect() as conn:
            row = conn.execute(
                "SELECT id, closed_at FROM project_sessions WHERE id = ?",
                (session_id,),
            ).fetchone()
            if not row:
                raise SessionNotFoundError(session_id)
            conn.execute(
                """
                INSERT INTO project_session_events
                    (id, session_id, ts, kind, actor, payload_json)
                VALUES (?, ?, ?, 'heartbeat', ?, '{}')
                """,
                (str(uuid4()), session_id, now_iso(), actor),
            )

    # ------------------------------------------------------------------
    # Checkout
    # ------------------------------------------------------------------

    def checkout(
        self,
        session_id: str,
        reason: str = "explicit",
        actor: str = "agent",
    ) -> dict:
        """Close a session. Returns final session data.

        Idempotent — if already closed, raises SessionAlreadyClosedError.
        """
        now = now_iso()
        with self.db.connect() as conn:
            row = conn.execute(
                "SELECT id, closed_at, opened_at FROM project_sessions WHERE id = ?",
                (session_id,),
            ).fetchone()
            if not row:
                raise SessionNotFoundError(session_id)
            if row["closed_at"]:
                raise SessionAlreadyClosedError(session_id)

            # Compute active_engagement_ms before closing
            engagement_ms = self._compute_active_engagement(conn, session_id)

            result = conn.execute(
                """
                UPDATE project_sessions
                SET closed_at = ?, close_reason = ?, active_engagement_ms = ?
                WHERE id = ? AND closed_at IS NULL
                """,
                (now, reason, engagement_ms, session_id),
            )
            if result.rowcount == 0:
                # Race — another writer closed it
                raise SessionAlreadyClosedError(session_id)

            conn.execute(
                """
                INSERT INTO project_session_events
                    (id, session_id, ts, kind, actor, payload_json)
                VALUES (?, ?, ?, 'checkout', ?, ?)
                """,
                (
                    str(uuid4()),
                    session_id,
                    now,
                    actor,
                    json.dumps({"reason": reason}),
                ),
            )
            return {
                "session_id": session_id,
                "closed_at": now,
                "close_reason": reason,
                "active_engagement_ms": engagement_ms,
            }

    # ------------------------------------------------------------------
    # Human message event (fed by Discord/Telegram bot hooks)
    # ------------------------------------------------------------------

    def record_human_msg(self, session_id: str, actor: str, payload: Optional[dict] = None) -> None:
        """Emit human_msg event and set first_human_response_ms if first."""
        now = now_iso()
        with self.db.connect() as conn:
            row = conn.execute(
                "SELECT opened_at, first_human_response_ms FROM project_sessions WHERE id = ?",
                (session_id,),
            ).fetchone()
            if not row:
                raise SessionNotFoundError(session_id)

            # Compute first_human_response_ms on first human_msg
            if row["first_human_response_ms"] is None:
                opened_dt = datetime.fromisoformat(row["opened_at"].replace("Z", "+00:00"))
                now_dt = datetime.fromisoformat(now.replace("Z", "+00:00"))
                elapsed_ms = int((now_dt - opened_dt).total_seconds() * 1000)
                conn.execute(
                    "UPDATE project_sessions SET first_human_response_ms = ? WHERE id = ?",
                    (elapsed_ms, session_id),
                )

            conn.execute(
                """
                INSERT INTO project_session_events
                    (id, session_id, ts, kind, actor, payload_json)
                VALUES (?, ?, ?, 'human_msg', ?, ?)
                """,
                (str(uuid4()), session_id, now, actor, json.dumps(payload or {})),
            )

    # ------------------------------------------------------------------
    # Active engagement computation
    # ------------------------------------------------------------------

    def _compute_active_engagement(self, conn, session_id: str) -> int:
        """Compute cumulative active_engagement_ms for a session.

        Each active window = time from a human_msg to the next agent_msg
        or task_complete, capped at 600,000ms (10 min).
        """
        rows = conn.execute(
            """
            WITH human_msgs AS (
                SELECT ts AS human_ts
                FROM project_session_events
                WHERE session_id = ? AND kind = 'human_msg'
            ),
            paired AS (
                SELECT hm.human_ts,
                       MIN(ae.ts) AS close_ts
                FROM human_msgs hm
                JOIN project_session_events ae
                    ON ae.session_id = ?
                    AND ae.ts > hm.human_ts
                    AND ae.kind IN ('agent_msg', 'task_complete')
                GROUP BY hm.human_ts
            ),
            windows AS (
                SELECT MIN(
                    CAST(ROUND((julianday(close_ts) - julianday(human_ts)) * 86400000) AS INTEGER),
                    600000
                ) AS window_ms
                FROM paired
            )
            SELECT COALESCE(SUM(window_ms), 0) AS active_engagement_ms
            FROM windows
            """,
            (session_id, session_id),
        ).fetchone()
        return rows["active_engagement_ms"] if rows else 0

    def get_active_engagement(self, session_id: str) -> int:
        """On-demand compute of active_engagement_ms."""
        with self.db.connect() as conn:
            return self._compute_active_engagement(conn, session_id)

    # ------------------------------------------------------------------
    # Read
    # ------------------------------------------------------------------

    def get_session(self, session_id: str) -> dict:
        with self.db.connect() as conn:
            row = conn.execute(
                "SELECT * FROM project_sessions WHERE id = ?",
                (session_id,),
            ).fetchone()
            if not row:
                raise SessionNotFoundError(session_id)
            events = conn.execute(
                "SELECT * FROM project_session_events WHERE session_id = ? ORDER BY ts ASC",
                (session_id,),
            ).fetchall()
            return {
                **dict(row),
                "events": [dict(e) for e in events],
            }

    def list_sessions(
        self,
        project_id: str,
        tenant_id: str = DEFAULT_TENANT_ID,
        limit: int = 50,
        offset: int = 0,
    ) -> list[dict]:
        with self.db.connect() as conn:
            rows = conn.execute(
                """
                SELECT * FROM project_sessions
                WHERE project_id = ? AND tenant_id = ?
                ORDER BY opened_at DESC
                LIMIT ? OFFSET ?
                """,
                (project_id, tenant_id, limit, offset),
            ).fetchall()
            return [dict(r) for r in rows]
