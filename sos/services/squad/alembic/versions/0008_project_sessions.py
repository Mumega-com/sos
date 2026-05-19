"""Add project_sessions, project_session_events, project_members tables

Revision ID: 0008
Revises: 0007
Create Date: 2026-04-24
"""
from __future__ import annotations

from typing import Sequence, Union

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0008"
down_revision: Union[str, None] = "0007"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute("""
        CREATE TABLE IF NOT EXISTS project_sessions (
            id                      TEXT PRIMARY KEY,
            project_id              TEXT NOT NULL,
            agent_id                TEXT NOT NULL,
            tenant_id               TEXT NOT NULL DEFAULT 'default',
            opened_at               TEXT NOT NULL,
            closed_at               TEXT,
            close_reason            TEXT,
            first_human_response_ms INTEGER,
            active_engagement_ms    INTEGER DEFAULT 0
        )
    """)
    op.execute(
        "CREATE INDEX IF NOT EXISTS idx_project_sessions_project "
        "ON project_sessions(project_id, tenant_id)"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS idx_project_sessions_agent "
        "ON project_sessions(agent_id, opened_at DESC)"
    )

    op.execute("""
        CREATE TABLE IF NOT EXISTS project_session_events (
            id           TEXT PRIMARY KEY,
            session_id   TEXT NOT NULL REFERENCES project_sessions(id),
            ts           TEXT NOT NULL,
            kind         TEXT NOT NULL,
            actor        TEXT NOT NULL,
            payload_json TEXT DEFAULT '{}'
        )
    """)
    op.execute(
        "CREATE INDEX IF NOT EXISTS idx_pse_session "
        "ON project_session_events(session_id, ts DESC)"
    )

    op.execute("""
        CREATE TABLE IF NOT EXISTS project_members (
            project_id  TEXT NOT NULL,
            agent_id    TEXT NOT NULL,
            tenant_id   TEXT NOT NULL DEFAULT 'default',
            role        TEXT NOT NULL DEFAULT 'member',
            added_at    TEXT NOT NULL,
            PRIMARY KEY (project_id, agent_id, tenant_id)
        )
    """)


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS project_members")
    op.execute("DROP INDEX IF EXISTS idx_pse_session")
    op.execute("DROP TABLE IF EXISTS project_session_events")
    op.execute("DROP INDEX IF EXISTS idx_project_sessions_agent")
    op.execute("DROP INDEX IF EXISTS idx_project_sessions_project")
    op.execute("DROP TABLE IF EXISTS project_sessions")
