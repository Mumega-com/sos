"""league_tables

Adds the squad league system: league_seasons and league_scores tables.
Squads compete in monthly seasons scored by KPI. Tiers are updated weekly.

Revision ID: 0003
Revises: 0002
Create Date: 2026-04-22
"""
from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0003"
down_revision: Union[str, None] = "0002"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # ---- league_seasons ---------------------------------------------------
    op.create_table(
        "league_seasons",
        sa.Column("id", sa.Text(), primary_key=True, nullable=True),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("start_date", sa.Text(), nullable=False),
        sa.Column("end_date", sa.Text(), nullable=False),
        sa.Column("status", sa.Text(), server_default=sa.text("'active'")),
        sa.Column("tenant_id", sa.Text()),
        sa.Column(
            "created_at",
            sa.Text(),
            server_default=sa.text("(datetime('now'))"),
        ),
    )

    # ---- league_scores ----------------------------------------------------
    op.create_table(
        "league_scores",
        sa.Column("id", sa.Text(), primary_key=True, nullable=True),
        sa.Column("season_id", sa.Text(), nullable=False),
        sa.Column("squad_id", sa.Text(), nullable=False),
        sa.Column("score", sa.REAL(), server_default=sa.text("0")),
        sa.Column("rank", sa.Integer(), server_default=sa.text("0")),
        sa.Column("tier", sa.Text(), server_default=sa.text("'nomad'")),
        sa.Column(
            "snapshot_at",
            sa.Text(),
            server_default=sa.text("(datetime('now'))"),
        ),
        sa.Column("tenant_id", sa.Text()),
    )
    op.execute(
        "CREATE INDEX idx_league_scores_season_rank ON league_scores (season_id, rank)"
    )
    op.execute(
        "CREATE INDEX idx_league_scores_squad_season ON league_scores (squad_id, season_id)"
    )


def downgrade() -> None:
    op.drop_table("league_scores")
    op.drop_table("league_seasons")
