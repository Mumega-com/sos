"""squad_task_done_when

Adds ``done_when_json`` to ``squad_tasks`` so the /complete endpoint
can gate on a structured list of DoneCheck entries instead of closing
tasks unconditionally (task #270, Part 2 of T1.3).

The column defaults to ``'[]'`` at the DB level so existing rows
rehydrate into an empty ``done_when`` list — which makes the gate
vacuously true for any task created before this migration, preserving
today's "complete always succeeds" behaviour for pre-existing rows.

Revision ID: 0002
Revises: 0001
Create Date: 2026-04-19
"""
from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0002"
down_revision: Union[str, None] = "0001"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "squad_tasks",
        sa.Column(
            "done_when_json",
            sa.Text(),
            server_default=sa.text("'[]'"),
            nullable=False,
        ),
    )


def downgrade() -> None:
    op.drop_column("squad_tasks", "done_when_json")
