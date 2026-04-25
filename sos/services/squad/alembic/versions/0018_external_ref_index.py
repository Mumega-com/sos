"""0018 — add index on squad_tasks(external_ref, status, completed_at DESC).

Supports brain source-signal dedupe query (G35):
  SELECT 1 FROM squad_tasks
  WHERE external_ref = ?
    AND (status IN ('queued','claimed','in_flight')
         OR (status = 'completed' AND completed_at > now - TTL))
  LIMIT 1

Without this index the dedupe check is a full table scan on every brain cycle.
"""
from alembic import op

revision = "0018"
down_revision = "0017"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """CREATE INDEX IF NOT EXISTS idx_squad_tasks_external_ref_status
           ON squad_tasks (external_ref, status, completed_at DESC)
           WHERE external_ref IS NOT NULL"""
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS idx_squad_tasks_external_ref_status")
