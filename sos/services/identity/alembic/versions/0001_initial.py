"""initial

Baseline revision for the Identity service. Creates the schema
produced today by ``IdentityCore._init_db`` in
``sos/services/identity/core.py``:

- users
- guilds
- memberships (composite PK guild_id + user_id)
- pairings (pairing-code workflow)
- allowlists (composite PK channel + sender_id + agent_id)

Revision ID: 0001
Revises:
Create Date: 2026-04-18
"""
from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0001"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # NOTE: PK columns are nullable=True throughout this revision to
    # match the live schema — the original ad-hoc DDL didn't declare
    # NOT NULL and SQLite happily stored notnull=0 for these columns.
    # v0.6.1+ can tighten via a follow-up revision.
    op.create_table(
        "users",
        sa.Column("id", sa.Text(), primary_key=True, nullable=True),
        sa.Column("name", sa.Text()),
        sa.Column("bio", sa.Text()),
        sa.Column("avatar_url", sa.Text()),
        sa.Column("level", sa.Integer(), server_default=sa.text("1")),
        sa.Column("xp", sa.Integer(), server_default=sa.text("0")),
        sa.Column("metadata", sa.Text()),
        sa.Column("created_at", sa.Text()),
    )

    op.create_table(
        "guilds",
        sa.Column("id", sa.Text(), primary_key=True, nullable=True),
        sa.Column("name", sa.Text()),
        sa.Column("owner_id", sa.Text()),
        sa.Column("description", sa.Text()),
        sa.Column("metadata", sa.Text()),
        sa.Column("created_at", sa.Text()),
    )

    op.create_table(
        "memberships",
        sa.Column("guild_id", sa.Text(), primary_key=True, nullable=True),
        sa.Column("user_id", sa.Text(), primary_key=True, nullable=True),
        sa.Column("role", sa.Text()),
        sa.Column("joined_at", sa.Text()),
    )

    op.create_table(
        "pairings",
        sa.Column("code", sa.Text(), primary_key=True, nullable=True),
        sa.Column("channel", sa.Text()),
        sa.Column("sender_id", sa.Text()),
        sa.Column("agent_id", sa.Text()),
        sa.Column("issued_at", sa.Text()),
        sa.Column("expires_at", sa.Text()),
        sa.Column("status", sa.Text()),
        sa.Column("approved_by", sa.Text()),
        sa.Column("approved_at", sa.Text()),
    )

    op.create_table(
        "allowlists",
        sa.Column("channel", sa.Text(), primary_key=True, nullable=True),
        sa.Column("sender_id", sa.Text(), primary_key=True, nullable=True),
        sa.Column("agent_id", sa.Text(), primary_key=True, nullable=True),
        sa.Column("added_at", sa.Text()),
        sa.Column("added_by", sa.Text()),
    )


def downgrade() -> None:
    op.drop_table("allowlists")
    op.drop_table("pairings")
    op.drop_table("memberships")
    op.drop_table("guilds")
    op.drop_table("users")
