"""0022 — task workflow runtime tables.

S028 splits long-running workflow execution state out of squad_tasks:

  - task_runs       execution attempts, fenced by claim_token
  - task_steps      checkpoint/progress records within a run
  - task_events     durable state transitions, idempotent by key
  - task_artifacts  outputs/proofs/files
  - task_approvals  human/agent gates

All tables carry tenant_id and are queried through tenant-scoped service
methods. Squad remains the source of truth for workflow state; MCP should only
proxy summaries.
"""
from alembic import op

revision = "0022"
down_revision = "0021"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """CREATE TABLE IF NOT EXISTS task_runs (
            id TEXT PRIMARY KEY,
            tenant_id TEXT NOT NULL DEFAULT 'default',
            task_id TEXT NOT NULL,
            status TEXT NOT NULL,
            actor TEXT NOT NULL,
            claim_token TEXT NULL,
            idempotency_key TEXT NULL,
            correlation_id TEXT NULL,
            metadata_json TEXT NOT NULL DEFAULT '{}',
            started_at TEXT NOT NULL,
            completed_at TEXT NULL,
            updated_at TEXT NOT NULL
        )"""
    )
    op.execute("CREATE INDEX IF NOT EXISTS idx_task_runs_task ON task_runs (tenant_id, task_id, started_at DESC)")
    op.execute(
        """CREATE UNIQUE INDEX IF NOT EXISTS uq_task_runs_idempotency
           ON task_runs (tenant_id, task_id, idempotency_key)
           WHERE idempotency_key IS NOT NULL"""
    )

    op.execute(
        """CREATE TABLE IF NOT EXISTS task_steps (
            id TEXT PRIMARY KEY,
            tenant_id TEXT NOT NULL DEFAULT 'default',
            run_id TEXT NOT NULL,
            task_id TEXT NOT NULL,
            name TEXT NOT NULL,
            status TEXT NOT NULL,
            payload_json TEXT NOT NULL DEFAULT '{}',
            started_at TEXT NOT NULL,
            completed_at TEXT NULL,
            updated_at TEXT NOT NULL
        )"""
    )
    op.execute("CREATE INDEX IF NOT EXISTS idx_task_steps_run ON task_steps (tenant_id, run_id, updated_at DESC)")

    op.execute(
        """CREATE TABLE IF NOT EXISTS task_events (
            id TEXT PRIMARY KEY,
            tenant_id TEXT NOT NULL DEFAULT 'default',
            run_id TEXT NOT NULL,
            task_id TEXT NOT NULL,
            event_type TEXT NOT NULL,
            actor TEXT NOT NULL,
            payload_json TEXT NOT NULL DEFAULT '{}',
            idempotency_key TEXT NULL,
            correlation_id TEXT NULL,
            created_at TEXT NOT NULL
        )"""
    )
    op.execute("CREATE INDEX IF NOT EXISTS idx_task_events_task ON task_events (tenant_id, task_id, created_at DESC)")
    op.execute("CREATE INDEX IF NOT EXISTS idx_task_events_run ON task_events (tenant_id, run_id, created_at DESC)")
    op.execute(
        """CREATE UNIQUE INDEX IF NOT EXISTS uq_task_events_idempotency
           ON task_events (tenant_id, run_id, idempotency_key)
           WHERE idempotency_key IS NOT NULL"""
    )

    op.execute(
        """CREATE TABLE IF NOT EXISTS task_artifacts (
            id TEXT PRIMARY KEY,
            tenant_id TEXT NOT NULL DEFAULT 'default',
            run_id TEXT NOT NULL,
            task_id TEXT NOT NULL,
            kind TEXT NOT NULL,
            uri TEXT NOT NULL,
            metadata_json TEXT NOT NULL DEFAULT '{}',
            idempotency_key TEXT NULL,
            created_at TEXT NOT NULL
        )"""
    )
    op.execute("CREATE INDEX IF NOT EXISTS idx_task_artifacts_task ON task_artifacts (tenant_id, task_id, created_at DESC)")
    op.execute("CREATE INDEX IF NOT EXISTS idx_task_artifacts_run ON task_artifacts (tenant_id, run_id, created_at DESC)")
    op.execute(
        """CREATE UNIQUE INDEX IF NOT EXISTS uq_task_artifacts_idempotency
           ON task_artifacts (tenant_id, run_id, idempotency_key)
           WHERE idempotency_key IS NOT NULL"""
    )

    op.execute(
        """CREATE TABLE IF NOT EXISTS task_approvals (
            id TEXT PRIMARY KEY,
            tenant_id TEXT NOT NULL DEFAULT 'default',
            run_id TEXT NOT NULL,
            task_id TEXT NOT NULL,
            gate TEXT NOT NULL,
            status TEXT NOT NULL,
            requested_by TEXT NOT NULL,
            decided_by TEXT NULL,
            payload_json TEXT NOT NULL DEFAULT '{}',
            requested_at TEXT NOT NULL,
            decided_at TEXT NULL,
            updated_at TEXT NOT NULL
        )"""
    )
    op.execute("CREATE INDEX IF NOT EXISTS idx_task_approvals_task ON task_approvals (tenant_id, task_id, updated_at DESC)")


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS task_approvals")
    op.execute("DROP TABLE IF EXISTS task_artifacts")
    op.execute("DROP TABLE IF EXISTS task_events")
    op.execute("DROP TABLE IF EXISTS task_steps")
    op.execute("DROP TABLE IF EXISTS task_runs")
