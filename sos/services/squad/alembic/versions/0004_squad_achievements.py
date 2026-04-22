"""squad achievements table

Revision ID: 0004
Revises: 0003
Create Date: 2026-04-22
"""
from __future__ import annotations

from typing import Sequence, Union

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0004"
down_revision: Union[str, None] = "0003"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute("""
        CREATE TABLE IF NOT EXISTS squad_achievements (
            id TEXT PRIMARY KEY,
            squad_id TEXT NOT NULL REFERENCES squads(id),
            badge TEXT NOT NULL,
            name TEXT NOT NULL,
            description TEXT,
            earned_at TEXT NOT NULL DEFAULT (datetime('now')),
            metadata_json TEXT DEFAULT '{}'
        )
    """)
    op.execute("""
        CREATE INDEX IF NOT EXISTS idx_sa_squad
        ON squad_achievements(squad_id)
    """)
    op.execute("""
        CREATE UNIQUE INDEX IF NOT EXISTS idx_sa_squad_badge
        ON squad_achievements(squad_id, badge)
    """)


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS squad_achievements")
