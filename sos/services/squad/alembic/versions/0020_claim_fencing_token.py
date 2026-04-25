"""0020 — add claim_owner_instance, claim_owner_acquired_at, claim_token to squad_tasks.

Sprint 006 A.3 / G72 — Squad dual-instance HA fencing token + TTL fallback.

Schema additions:
  - claim_owner_instance TEXT NULL  — SQUAD_INSTANCE_ID env var of the claiming instance
                                      disambiguates PID-reuse across restarts
  - claim_owner_acquired_at TEXT NULL  — ISO8601 UTC timestamp of claim acquisition;
                                         drives time-based TTL fallback
  - claim_token TEXT NULL  — server-generated UUID returned on claim, required on
                              complete (Kleppmann fencing token). Prevents stale-owner
                              completion after TTL re-claim.

Index:
  idx_squad_tasks_ttl_eligible — speeds TTL pre-transition query (status='claimed'
  AND claim_owner_acquired_at is not null) that runs on every claim call.
"""
from alembic import op

revision = "0020"
down_revision = "0019"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("ALTER TABLE squad_tasks ADD COLUMN claim_owner_instance TEXT NULL")
    op.execute("ALTER TABLE squad_tasks ADD COLUMN claim_owner_acquired_at TEXT NULL")
    op.execute("ALTER TABLE squad_tasks ADD COLUMN claim_token TEXT NULL")
    op.execute(
        """CREATE INDEX IF NOT EXISTS idx_squad_tasks_ttl_eligible
           ON squad_tasks (status, claim_owner_acquired_at)
           WHERE status = 'claimed' AND claim_owner_acquired_at IS NOT NULL"""
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS idx_squad_tasks_ttl_eligible")
    # SQLite <3.35 does not support DROP COLUMN — recreate without the three columns
    op.execute(
        """CREATE TABLE squad_tasks_pre0020 AS
           SELECT id, squad_id, tenant_id, title, description, status,
                  priority, assignee, skill_id, project, labels_json,
                  blocked_by_json, blocks_json, inputs_json, result_json,
                  token_budget, bounty_json, external_ref, created_at,
                  updated_at, completed_at, claimed_at, attempt,
                  done_when_json, claim_owner_pid
           FROM squad_tasks"""
    )
    op.execute("DROP TABLE squad_tasks")
    op.execute("ALTER TABLE squad_tasks_pre0020 RENAME TO squad_tasks")
