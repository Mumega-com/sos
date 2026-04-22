"""SQLAlchemy declarative models for the Squad service.

These mirror the on-disk schema that ``SquadDB._init_db``,
``_PipelineDB._init_db``, and ``SquadSkillService._ensure_schema``
produce today (CREATE TABLE + incremental ALTER TABLE columns).

The models are the target_metadata for Alembic autogenerate and are
the single source of truth for the Squad service's relational schema
going forward. Keep this file in sync with any future migration that
adds or drops columns.
"""
from __future__ import annotations

from sqlalchemy import (
    REAL,
    CheckConstraint,
    Column,
    Index,
    Integer,
    PrimaryKeyConstraint,
    String,
    Text,
    text,
)
from sqlalchemy.orm import declarative_base

Base = declarative_base()


class Squad(Base):
    __tablename__ = "squads"

    id = Column(Text, primary_key=True)
    name = Column(Text, nullable=False)
    project = Column(Text, nullable=False)
    objective = Column(Text, nullable=False)
    tier = Column(Text, nullable=False)
    status = Column(Text, nullable=False)
    roles_json = Column(Text, nullable=False)
    members_json = Column(Text, nullable=False)
    kpis_json = Column(Text, nullable=False)
    budget_cents_monthly = Column(Integer, nullable=False)
    created_at = Column(Text, nullable=False)
    updated_at = Column(Text, nullable=False)
    # Added via ALTER in service._init_db (living-graph + tenancy columns)
    tenant_id = Column(Text, nullable=False, server_default=text("'default'"))
    dna_vector = Column(Text, server_default=text("'[]'"))
    coherence = Column(REAL, server_default=text("0.5"))
    receptivity = Column(REAL, server_default=text("0.5"))
    conductance_json = Column(Text, server_default=text("'{}'"))

    __table_args__ = (
        Index("idx_squads_tenant", "tenant_id", text("updated_at DESC")),
    )


class SquadTask(Base):
    __tablename__ = "squad_tasks"

    id = Column(Text, primary_key=True)
    squad_id = Column(Text, nullable=False)
    title = Column(Text, nullable=False)
    description = Column(Text, nullable=False)
    status = Column(Text, nullable=False)
    priority = Column(Text, nullable=False)
    assignee = Column(Text)
    skill_id = Column(Text)
    project = Column(Text, nullable=False)
    labels_json = Column(Text, nullable=False)
    blocked_by_json = Column(Text, nullable=False)
    blocks_json = Column(Text, nullable=False)
    inputs_json = Column(Text, nullable=False)
    result_json = Column(Text, nullable=False)
    token_budget = Column(Integer, nullable=False)
    bounty_json = Column(Text, nullable=False)
    external_ref = Column(Text)
    done_when_json = Column(Text, nullable=False, server_default=text("'[]'"))
    created_at = Column(Text, nullable=False)
    updated_at = Column(Text, nullable=False)
    completed_at = Column(Text)
    claimed_at = Column(Text)
    attempt = Column(Integer, nullable=False)
    tenant_id = Column(Text, nullable=False, server_default=text("'default'"))

    __table_args__ = (
        Index("idx_squad_tasks_squad_status", "squad_id", "status"),
        Index("idx_squad_tasks_tenant", "tenant_id", text("updated_at DESC")),
    )


class SquadSkill(Base):
    __tablename__ = "squad_skills"

    id = Column(Text, primary_key=True)
    name = Column(Text, nullable=False)
    description = Column(Text, nullable=False)
    labels_json = Column(Text, nullable=False)
    keywords_json = Column(Text, nullable=False)
    entrypoint = Column(Text, nullable=False)
    required_inputs_json = Column(Text, nullable=False)
    status = Column(Text, nullable=False)
    fuel_grade = Column(Text, nullable=False)
    version = Column(Text, nullable=False)
    # Added via ALTER in skills._ensure_schema
    input_schema_json = Column(Text, server_default=text("'{}'"))
    output_schema_json = Column(Text, server_default=text("'{}'"))
    trust_tier = Column(Integer, server_default=text("4"))
    loading_level = Column(Integer, server_default=text("2"))
    skill_dir = Column(Text, server_default=text("''"))
    deprecated_at = Column(Text)
    tenant_id = Column(Text, nullable=False, server_default=text("'default'"))

    __table_args__ = (
        Index("idx_squad_skills_tenant", "tenant_id", text("name ASC")),
    )


class SquadState(Base):
    __tablename__ = "squad_state"

    squad_id = Column(Text, primary_key=True)
    project = Column(Text, nullable=False)
    data_json = Column(Text, nullable=False)
    version = Column(Integer, nullable=False)
    updated_at = Column(Text, nullable=False)
    tenant_id = Column(Text, nullable=False, server_default=text("'default'"))

    __table_args__ = (
        Index("idx_squad_state_tenant", "tenant_id", text("updated_at DESC")),
    )


class SquadEvent(Base):
    __tablename__ = "squad_events"

    id = Column(Integer, primary_key=True, autoincrement=True)
    squad_id = Column(Text, nullable=False)
    event_type = Column(Text, nullable=False)
    actor = Column(Text, nullable=False)
    payload_json = Column(Text, nullable=False)
    timestamp = Column(Text, nullable=False)
    tenant_id = Column(Text, nullable=False, server_default=text("'default'"))

    __table_args__ = (
        Index("idx_squad_events_squad_timestamp", "squad_id", text("timestamp DESC")),
        Index("idx_squad_events_tenant", "tenant_id", text("timestamp DESC")),
    )


class ApiKey(Base):
    __tablename__ = "api_keys"

    token_hash = Column(Text, primary_key=True)
    tenant_id = Column(Text, nullable=False)
    identity_type = Column(Text, nullable=False)
    created_at = Column(Text, nullable=False)


class SquadWallet(Base):
    __tablename__ = "squad_wallets"

    squad_id = Column(Text, primary_key=True)
    tenant_id = Column(Text, nullable=False, server_default=text("'default'"))
    balance_cents = Column(Integer, nullable=False, server_default=text("0"))
    total_earned_cents = Column(Integer, nullable=False, server_default=text("0"))
    total_spent_cents = Column(Integer, nullable=False, server_default=text("0"))
    fuel_budget_json = Column(Text, nullable=False, server_default=text("'{}'"))
    updated_at = Column(Text, nullable=False, server_default=text("''"))


class SquadTransaction(Base):
    __tablename__ = "squad_transactions"

    id = Column(Text, primary_key=True)
    squad_id = Column(Text, nullable=False)
    tenant_id = Column(Text, nullable=False, server_default=text("'default'"))
    type = Column(
        Text,
        nullable=False,
    )
    amount_cents = Column(Integer, nullable=False)
    counterparty = Column(Text, server_default=text("'system'"))
    reason = Column(Text, nullable=False)
    task_id = Column(Text)
    created_at = Column(Text, nullable=False)

    __table_args__ = (
        CheckConstraint(
            "type IN ('earn', 'spend', 'transfer', 'mint')",
            name="squad_transactions_type_check",
        ),
        Index("idx_squad_transactions_squad", "squad_id", text("created_at DESC")),
    )


class SquadGoal(Base):
    __tablename__ = "squad_goals"

    id = Column(Text, primary_key=True)
    squad_id = Column(Text, nullable=False)
    tenant_id = Column(Text, nullable=False, server_default=text("'default'"))
    target = Column(Text, nullable=False)
    markers_json = Column(Text, nullable=False, server_default=text("'[]'"))
    coherence_threshold = Column(REAL, nullable=False, server_default=text("0.6"))
    deadline = Column(Text)
    status = Column(Text, nullable=False, server_default=text("'active'"))
    progress = Column(REAL, nullable=False, server_default=text("0.0"))
    created_at = Column(Text, nullable=False)
    updated_at = Column(Text, nullable=False)

    __table_args__ = (
        CheckConstraint(
            "status IN ('active', 'achieved', 'abandoned')",
            name="squad_goals_status_check",
        ),
        Index("idx_squad_goals_squad", "squad_id", "status"),
    )


class PipelineSpec(Base):
    __tablename__ = "pipeline_specs"

    squad_id = Column(Text, primary_key=True)
    repo = Column(Text, nullable=False)
    workdir = Column(Text, nullable=False)
    default_branch = Column(Text, nullable=False)
    feature_branch_prefix = Column(Text, nullable=False)
    pr_mode = Column(Text, nullable=False)
    build_cmd = Column(Text, nullable=False)
    test_cmd = Column(Text, nullable=False)
    deploy_cmd = Column(Text, nullable=False)
    smoke_cmd = Column(Text, nullable=False)
    deploy_mode = Column(Text, nullable=False)
    deploy_on_task_labels = Column(Text, nullable=False)
    rollback_cmd = Column(Text, nullable=False)
    enabled = Column(Integer, nullable=False)
    tenant_id = Column(Text, nullable=False, server_default=text("'default'"))

    __table_args__ = (
        Index("idx_pipeline_specs_tenant", "tenant_id", "squad_id"),
    )


class PipelineRun(Base):
    __tablename__ = "pipeline_runs"

    id = Column(Text, primary_key=True)
    squad_id = Column(Text, nullable=False)
    task_id = Column(Text, nullable=False)
    status = Column(Text, nullable=False)
    commit_sha = Column(Text, nullable=False)
    branch = Column(Text, nullable=False)
    pr_url = Column(Text, nullable=False)
    logs = Column(Text, nullable=False)
    error = Column(Text, nullable=False)
    created_at = Column(Text, nullable=False)
    completed_at = Column(Text, nullable=False)
    tenant_id = Column(Text, nullable=False, server_default=text("'default'"))

    __table_args__ = (
        Index("idx_pipeline_runs_squad", "squad_id", text("created_at DESC")),
        Index(
            "idx_pipeline_runs_tenant",
            "tenant_id",
            "squad_id",
            text("created_at DESC"),
        ),
    )


class LeagueSeason(Base):
    __tablename__ = "league_seasons"

    id = Column(Text, primary_key=True)
    name = Column(Text, nullable=False)
    start_date = Column(Text, nullable=False)
    end_date = Column(Text, nullable=False)
    status = Column(Text, server_default=text("'active'"))
    tenant_id = Column(Text)
    created_at = Column(Text, server_default=text("(datetime('now'))"))


class LeagueScore(Base):
    __tablename__ = "league_scores"

    id = Column(Text, primary_key=True)
    season_id = Column(Text, nullable=False)
    squad_id = Column(Text, nullable=False)
    score = Column(REAL, server_default=text("0"))
    rank = Column(Integer, server_default=text("0"))
    tier = Column(Text, server_default=text("'nomad'"))
    snapshot_at = Column(Text, server_default=text("(datetime('now'))"))
    tenant_id = Column(Text)

    __table_args__ = (
        Index("idx_league_scores_season_rank", "season_id", "rank"),
        Index("idx_league_scores_squad_season", "squad_id", "season_id"),
    )


__all__ = [
    "Base",
    "Squad",
    "SquadTask",
    "SquadSkill",
    "SquadState",
    "SquadEvent",
    "ApiKey",
    "SquadWallet",
    "SquadTransaction",
    "SquadGoal",
    "PipelineSpec",
    "PipelineRun",
    "LeagueSeason",
    "LeagueScore",
]
