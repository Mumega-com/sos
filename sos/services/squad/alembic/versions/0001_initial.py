"""initial

Baseline revision for the Squad service. Creates the full on-disk
schema produced today by:

- ``SquadDB._init_db`` (sos/services/squad/service.py) — CREATE TABLE
  + ALTER TABLE for tenant_id / living-graph columns.
- ``_PipelineDB._init_db`` (sos/services/squad/pipeline.py).
- ``SquadSkillService._ensure_schema`` (sos/services/squad/skills.py)
  — ALTER TABLE columns for schema/trust/loading/skill_dir.

Columns ``framework`` and ``agent`` on ``squad_skills`` and
``review_enabled`` / ``review_agent`` / ``review_cmd`` / ``reviewer_notes``
on pipeline_* tables are kept for live-DB parity; they were added by
prior releases whose migration code has since been removed. v0.6.1+
can drop them in a follow-up revision.

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
    # ---- api_keys ---------------------------------------------------------
    # NOTE: PK columns are nullable=True throughout this revision to match
    # the live schema — SQLite's historical behavior leaves PK columns as
    # notnull=0 unless explicitly declared, and the original ad-hoc DDL
    # didn't declare NOT NULL. v0.6.1+ can tighten via a follow-up.
    op.create_table(
        "api_keys",
        sa.Column("token_hash", sa.Text(), primary_key=True, nullable=True),
        sa.Column("tenant_id", sa.Text(), nullable=False),
        sa.Column("identity_type", sa.Text(), nullable=False),
        sa.Column("created_at", sa.Text(), nullable=False),
    )

    # ---- squads -----------------------------------------------------------
    op.create_table(
        "squads",
        sa.Column("id", sa.Text(), primary_key=True, nullable=True),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("project", sa.Text(), nullable=False),
        sa.Column("objective", sa.Text(), nullable=False),
        sa.Column("tier", sa.Text(), nullable=False),
        sa.Column("status", sa.Text(), nullable=False),
        sa.Column("roles_json", sa.Text(), nullable=False),
        sa.Column("members_json", sa.Text(), nullable=False),
        sa.Column("kpis_json", sa.Text(), nullable=False),
        sa.Column("budget_cents_monthly", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.Text(), nullable=False),
        sa.Column("updated_at", sa.Text(), nullable=False),
        sa.Column(
            "tenant_id",
            sa.Text(),
            server_default=sa.text("'default'"),
            nullable=False,
        ),
        sa.Column("dna_vector", sa.Text(), server_default=sa.text("'[]'")),
        sa.Column("coherence", sa.REAL(), server_default=sa.text("0.5")),
        sa.Column("receptivity", sa.REAL(), server_default=sa.text("0.5")),
        sa.Column("conductance_json", sa.Text(), server_default=sa.text("'{}'")),
    )
    # DESC indexes need raw SQL — Alembic's IndexOp can't express them
    # cleanly for SQLite.
    op.execute(
        "CREATE INDEX idx_squads_tenant ON squads (tenant_id, updated_at DESC)"
    )

    # ---- squad_tasks ------------------------------------------------------
    op.create_table(
        "squad_tasks",
        sa.Column("id", sa.Text(), primary_key=True, nullable=True),
        sa.Column("squad_id", sa.Text(), nullable=False),
        sa.Column("title", sa.Text(), nullable=False),
        sa.Column("description", sa.Text(), nullable=False),
        sa.Column("status", sa.Text(), nullable=False),
        sa.Column("priority", sa.Text(), nullable=False),
        sa.Column("assignee", sa.Text()),
        sa.Column("skill_id", sa.Text()),
        sa.Column("project", sa.Text(), nullable=False),
        sa.Column("labels_json", sa.Text(), nullable=False),
        sa.Column("blocked_by_json", sa.Text(), nullable=False),
        sa.Column("blocks_json", sa.Text(), nullable=False),
        sa.Column("inputs_json", sa.Text(), nullable=False),
        sa.Column("result_json", sa.Text(), nullable=False),
        sa.Column("token_budget", sa.Integer(), nullable=False),
        sa.Column("bounty_json", sa.Text(), nullable=False),
        sa.Column("external_ref", sa.Text()),
        sa.Column("created_at", sa.Text(), nullable=False),
        sa.Column("updated_at", sa.Text(), nullable=False),
        sa.Column("completed_at", sa.Text()),
        sa.Column("claimed_at", sa.Text()),
        sa.Column("attempt", sa.Integer(), nullable=False),
        sa.Column(
            "tenant_id",
            sa.Text(),
            server_default=sa.text("'default'"),
            nullable=False,
        ),
    )
    op.create_index(
        "idx_squad_tasks_squad_status", "squad_tasks", ["squad_id", "status"]
    )
    op.execute(
        "CREATE INDEX idx_squad_tasks_tenant ON squad_tasks (tenant_id, updated_at DESC)"
    )

    # ---- squad_skills -----------------------------------------------------
    op.create_table(
        "squad_skills",
        sa.Column("id", sa.Text(), primary_key=True, nullable=True),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("description", sa.Text(), nullable=False),
        sa.Column("labels_json", sa.Text(), nullable=False),
        sa.Column("keywords_json", sa.Text(), nullable=False),
        sa.Column("entrypoint", sa.Text(), nullable=False),
        sa.Column("required_inputs_json", sa.Text(), nullable=False),
        sa.Column("status", sa.Text(), nullable=False),
        sa.Column("fuel_grade", sa.Text(), nullable=False),
        sa.Column("version", sa.Text(), nullable=False),
        sa.Column("input_schema_json", sa.Text(), server_default=sa.text("'{}'")),
        sa.Column("output_schema_json", sa.Text(), server_default=sa.text("'{}'")),
        sa.Column("trust_tier", sa.Integer(), server_default=sa.text("4")),
        sa.Column("loading_level", sa.Integer(), server_default=sa.text("2")),
        sa.Column("skill_dir", sa.Text(), server_default=sa.text("''")),
        sa.Column("deprecated_at", sa.Text()),
        sa.Column(
            "tenant_id",
            sa.Text(),
            server_default=sa.text("'default'"),
            nullable=False,
        ),
        # Parity columns (see module docstring).
        sa.Column("framework", sa.Text(), server_default=sa.text("'any'")),
        sa.Column("agent", sa.Text(), server_default=sa.text("''")),
    )
    op.execute(
        "CREATE INDEX idx_squad_skills_tenant ON squad_skills (tenant_id, name ASC)"
    )

    # ---- squad_state ------------------------------------------------------
    op.create_table(
        "squad_state",
        sa.Column("squad_id", sa.Text(), primary_key=True, nullable=True),
        sa.Column("project", sa.Text(), nullable=False),
        sa.Column("data_json", sa.Text(), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("updated_at", sa.Text(), nullable=False),
        sa.Column(
            "tenant_id",
            sa.Text(),
            server_default=sa.text("'default'"),
            nullable=False,
        ),
    )
    op.execute(
        "CREATE INDEX idx_squad_state_tenant ON squad_state (tenant_id, updated_at DESC)"
    )

    # ---- squad_events -----------------------------------------------------
    # AUTOINCREMENT on SQLite — live schema stores notnull=0 for id.
    # Use raw DDL because SA insists on NOT NULL for INTEGER PRIMARY KEY
    # AUTOINCREMENT regardless of nullable=True on the Column.
    op.execute(
        """
        CREATE TABLE squad_events (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            squad_id TEXT NOT NULL,
            event_type TEXT NOT NULL,
            actor TEXT NOT NULL,
            payload_json TEXT NOT NULL,
            timestamp TEXT NOT NULL,
            tenant_id TEXT NOT NULL DEFAULT 'default'
        )
        """
    )
    op.execute(
        "CREATE INDEX idx_squad_events_squad_timestamp ON squad_events "
        "(squad_id, timestamp DESC)"
    )
    op.execute(
        "CREATE INDEX idx_squad_events_tenant ON squad_events "
        "(tenant_id, timestamp DESC)"
    )

    # ---- squad_wallets ----------------------------------------------------
    op.create_table(
        "squad_wallets",
        sa.Column("squad_id", sa.Text(), primary_key=True, nullable=True),
        sa.Column(
            "tenant_id",
            sa.Text(),
            server_default=sa.text("'default'"),
            nullable=False,
        ),
        sa.Column(
            "balance_cents",
            sa.Integer(),
            server_default=sa.text("0"),
            nullable=False,
        ),
        sa.Column(
            "total_earned_cents",
            sa.Integer(),
            server_default=sa.text("0"),
            nullable=False,
        ),
        sa.Column(
            "total_spent_cents",
            sa.Integer(),
            server_default=sa.text("0"),
            nullable=False,
        ),
        sa.Column(
            "fuel_budget_json",
            sa.Text(),
            server_default=sa.text("'{}'"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.Text(),
            server_default=sa.text("''"),
            nullable=False,
        ),
    )

    # ---- squad_transactions -----------------------------------------------
    op.create_table(
        "squad_transactions",
        sa.Column("id", sa.Text(), primary_key=True, nullable=True),
        sa.Column("squad_id", sa.Text(), nullable=False),
        sa.Column(
            "tenant_id",
            sa.Text(),
            server_default=sa.text("'default'"),
            nullable=False,
        ),
        sa.Column("type", sa.Text(), nullable=False),
        sa.Column("amount_cents", sa.Integer(), nullable=False),
        sa.Column("counterparty", sa.Text(), server_default=sa.text("'system'")),
        sa.Column("reason", sa.Text(), nullable=False),
        sa.Column("task_id", sa.Text()),
        sa.Column("created_at", sa.Text(), nullable=False),
        sa.CheckConstraint(
            "type IN ('earn', 'spend', 'transfer', 'mint')",
            name="squad_transactions_type_check",
        ),
    )
    op.execute(
        "CREATE INDEX idx_squad_transactions_squad ON squad_transactions "
        "(squad_id, created_at DESC)"
    )

    # ---- squad_goals ------------------------------------------------------
    op.create_table(
        "squad_goals",
        sa.Column("id", sa.Text(), primary_key=True, nullable=True),
        sa.Column("squad_id", sa.Text(), nullable=False),
        sa.Column(
            "tenant_id",
            sa.Text(),
            server_default=sa.text("'default'"),
            nullable=False,
        ),
        sa.Column("target", sa.Text(), nullable=False),
        sa.Column(
            "markers_json",
            sa.Text(),
            server_default=sa.text("'[]'"),
            nullable=False,
        ),
        sa.Column(
            "coherence_threshold",
            sa.REAL(),
            server_default=sa.text("0.6"),
            nullable=False,
        ),
        sa.Column("deadline", sa.Text()),
        sa.Column(
            "status",
            sa.Text(),
            server_default=sa.text("'active'"),
            nullable=False,
        ),
        sa.Column(
            "progress",
            sa.REAL(),
            server_default=sa.text("0.0"),
            nullable=False,
        ),
        sa.Column("created_at", sa.Text(), nullable=False),
        sa.Column("updated_at", sa.Text(), nullable=False),
        sa.CheckConstraint(
            "status IN ('active', 'achieved', 'abandoned')",
            name="squad_goals_status_check",
        ),
    )
    op.create_index("idx_squad_goals_squad", "squad_goals", ["squad_id", "status"])

    # ---- pipeline_specs ---------------------------------------------------
    op.create_table(
        "pipeline_specs",
        sa.Column("squad_id", sa.Text(), primary_key=True, nullable=True),
        sa.Column("repo", sa.Text(), nullable=False),
        sa.Column("workdir", sa.Text(), nullable=False),
        sa.Column("default_branch", sa.Text(), nullable=False),
        sa.Column("feature_branch_prefix", sa.Text(), nullable=False),
        sa.Column("pr_mode", sa.Text(), nullable=False),
        sa.Column("build_cmd", sa.Text(), nullable=False),
        sa.Column("test_cmd", sa.Text(), nullable=False),
        sa.Column("deploy_cmd", sa.Text(), nullable=False),
        sa.Column("smoke_cmd", sa.Text(), nullable=False),
        sa.Column("deploy_mode", sa.Text(), nullable=False),
        sa.Column("deploy_on_task_labels", sa.Text(), nullable=False),
        sa.Column("rollback_cmd", sa.Text(), nullable=False),
        sa.Column("enabled", sa.Integer(), nullable=False),
        sa.Column(
            "tenant_id",
            sa.Text(),
            server_default=sa.text("'default'"),
            nullable=False,
        ),
        # Parity columns (see module docstring).
        sa.Column(
            "review_enabled",
            sa.Integer(),
            server_default=sa.text("0"),
            nullable=False,
        ),
        sa.Column(
            "review_agent",
            sa.Text(),
            server_default=sa.text("'athena'"),
            nullable=False,
        ),
        sa.Column(
            "review_cmd",
            sa.Text(),
            server_default=sa.text("''"),
            nullable=False,
        ),
    )
    op.create_index(
        "idx_pipeline_specs_tenant", "pipeline_specs", ["tenant_id", "squad_id"]
    )

    # ---- pipeline_runs ----------------------------------------------------
    op.create_table(
        "pipeline_runs",
        sa.Column("id", sa.Text(), primary_key=True, nullable=True),
        sa.Column("squad_id", sa.Text(), nullable=False),
        sa.Column("task_id", sa.Text(), nullable=False),
        sa.Column("status", sa.Text(), nullable=False),
        sa.Column("commit_sha", sa.Text(), nullable=False),
        sa.Column("branch", sa.Text(), nullable=False),
        sa.Column("pr_url", sa.Text(), nullable=False),
        sa.Column("logs", sa.Text(), nullable=False),
        sa.Column("error", sa.Text(), nullable=False),
        sa.Column("created_at", sa.Text(), nullable=False),
        sa.Column("completed_at", sa.Text(), nullable=False),
        sa.Column(
            "tenant_id",
            sa.Text(),
            server_default=sa.text("'default'"),
            nullable=False,
        ),
        # Parity column (see module docstring).
        sa.Column(
            "reviewer_notes",
            sa.Text(),
            server_default=sa.text("''"),
            nullable=False,
        ),
    )
    op.execute(
        "CREATE INDEX idx_pipeline_runs_squad ON pipeline_runs "
        "(squad_id, created_at DESC)"
    )
    op.execute(
        "CREATE INDEX idx_pipeline_runs_tenant ON pipeline_runs "
        "(tenant_id, squad_id, created_at DESC)"
    )


def downgrade() -> None:
    op.drop_table("pipeline_runs")
    op.drop_table("pipeline_specs")
    op.drop_table("squad_goals")
    op.drop_table("squad_transactions")
    op.drop_table("squad_wallets")
    op.drop_table("squad_events")
    op.drop_table("squad_state")
    op.drop_table("squad_skills")
    op.drop_table("squad_tasks")
    op.drop_table("squads")
    op.drop_table("api_keys")
