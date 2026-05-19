"""squad_tasks: add index on project column for project_id filtering

Adds an index on the existing ``project`` column of ``squad_tasks`` so
that ``GET /tasks?project_id=<value>`` queries run efficiently.

Revision ID: 0007
Revises: 0006
Create Date: 2026-04-23
"""
from __future__ import annotations

from typing import Sequence, Union

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0007"
down_revision: Union[str, None] = "0006"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_index(
        "idx_squad_tasks_project",
        "squad_tasks",
        ["project"],
    )


def downgrade() -> None:
    op.drop_index("idx_squad_tasks_project", table_name="squad_tasks")
