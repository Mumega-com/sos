"""0019 — add claim_owner_pid to squad_tasks (Sprint 006 A.3 / G53).

Enables orphan-task recovery in dual-instance Squad Service HA.

When a Squad Service instance claims a task, it records its OS PID in
claim_owner_pid.  A background reaper running on each instance calls
os.kill(claim_owner_pid, 0) for every claimed task.  If the process is
gone (ProcessLookupError), the task is reset to BACKLOG so the other
instance or the restarted instance can re-claim it.

Schema intent:
  - claim_owner_pid INTEGER NULL  — OS PID of the instance that claimed
    the task; NULL for tasks claimed before this migration or never
    claimed.
  - Index on (status, claim_owner_pid) supports the reaper's query:
    SELECT id, claim_owner_pid FROM squad_tasks
    WHERE status = 'claimed' AND claim_owner_pid IS NOT NULL
"""
from alembic import op

revision = "0019"
down_revision = "0018"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        "ALTER TABLE squad_tasks ADD COLUMN claim_owner_pid INTEGER NULL"
    )
    op.execute(
        """CREATE INDEX IF NOT EXISTS idx_squad_tasks_claimed_pid
           ON squad_tasks (status, claim_owner_pid)
           WHERE status = 'claimed' AND claim_owner_pid IS NOT NULL"""
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS idx_squad_tasks_claimed_pid")
    # SQLite does not support DROP COLUMN before 3.35; use recreate approach
    op.execute(
        """CREATE TABLE squad_tasks_tmp AS
           SELECT id, squad_id, tenant_id, title, description, status,
                  priority, labels, assignee, inputs, outputs, done_when,
                  error, created_at, updated_at, claimed_at, completed_at,
                  attempt, estimated_cost_cents, skill_id, pipeline_step,
                  project_id, external_ref, session_id, resource_ids
           FROM squad_tasks"""
    )
    op.execute("DROP TABLE squad_tasks")
    op.execute("ALTER TABLE squad_tasks_tmp RENAME TO squad_tasks")
