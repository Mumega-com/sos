"""league_scores: add week column for snapshot idempotency

Adds a week column (ISO week-year, e.g. '17-2026') to league_scores so that
snapshot_league_scores can detect and skip duplicate snapshots within the same
calendar week.

Revision ID: 0006
Revises: 0005
Create Date: 2026-04-22
"""
from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0006"
down_revision: Union[str, None] = "0005"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("league_scores", sa.Column("week", sa.String(7), nullable=True))
    op.create_index(
        "idx_league_scores_week",
        "league_scores",
        ["season_id", "week"],
    )


def downgrade() -> None:
    op.drop_index("idx_league_scores_week", table_name="league_scores")
    op.drop_column("league_scores", "week")
