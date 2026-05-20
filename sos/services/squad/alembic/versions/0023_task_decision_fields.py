"""0023 — task decision fields.

Adds durable task-level decision metadata:

  - decision_required INTEGER NOT NULL DEFAULT 0
  - options_json TEXT NOT NULL DEFAULT '[]'
"""

from alembic import op

revision = "0023"
down_revision = "0022"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("ALTER TABLE squad_tasks ADD COLUMN decision_required INTEGER NOT NULL DEFAULT 0")
    op.execute("ALTER TABLE squad_tasks ADD COLUMN options_json TEXT NOT NULL DEFAULT '[]'")
    op.execute(
        """CREATE INDEX IF NOT EXISTS idx_squad_tasks_decision_required
           ON squad_tasks (tenant_id, decision_required, updated_at DESC)"""
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS idx_squad_tasks_decision_required")
    op.execute(
        """CREATE TABLE squad_tasks_pre0023 AS
           SELECT id, squad_id, title, description, status, priority,
                  assignee, skill_id, project, labels_json, blocked_by_json,
                  blocks_json, inputs_json, result_json, token_budget,
                  bounty_json, external_ref, done_when_json, created_at,
                  updated_at, completed_at, claimed_at, attempt, tenant_id,
                  claim_owner_pid, claim_owner_instance, claim_owner_acquired_at,
                  claim_token, source, external_message_id, external_workspace_id,
                  external_user_id
           FROM squad_tasks"""
    )
    op.execute("DROP TABLE squad_tasks")
    op.execute("ALTER TABLE squad_tasks_pre0023 RENAME TO squad_tasks")
