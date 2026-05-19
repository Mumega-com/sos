"""Add session_id column to squad_transactions

Revision ID: 0009
Revises: 0008
Create Date: 2026-04-24
"""
from __future__ import annotations

from typing import Sequence, Union

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0009"
down_revision: Union[str, None] = "0008"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute("ALTER TABLE squad_transactions ADD COLUMN session_id TEXT")


def downgrade() -> None:
    # SQLite doesn't support DROP COLUMN — downgrade is a no-op in dev
    pass
