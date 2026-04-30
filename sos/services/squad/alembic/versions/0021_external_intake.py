"""0021 — add intake provenance columns + UNIQUE INDEX on (source, external_message_id).

S018 Track C — intake connectors (Slack/Discord) need DB-level dedupe as the
ADV-C-5 fall-through behind Worker KV idempotency. KV TTL evictions or hot
retries can race past the in-memory check; the DB UNIQUE INDEX is the
authoritative dedupe.

Schema additions:
  - source TEXT NULL                  — 'slack' | 'discord' | NULL (legacy/internal)
  - external_message_id TEXT NULL     — Slack ts / Discord message id (immutable)
  - external_workspace_id TEXT NULL   — internal external_workspaces.id (provenance only)
  - external_user_id TEXT NULL        — Slack user / Discord user (provenance only)

Index:
  uq_squad_tasks_intake — partial UNIQUE on (source, external_message_id) WHERE
                          external_message_id IS NOT NULL. ADV-C-5 fall-through;
                          a 409 from this surfaces upstream as `idempotent_db`.

Brief: agents/loom/briefs/kasra-s018-c-intake-connectors.md §2.1
"""
from alembic import op

revision = "0021"
down_revision = "0020"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("ALTER TABLE squad_tasks ADD COLUMN source TEXT NULL")
    op.execute("ALTER TABLE squad_tasks ADD COLUMN external_message_id TEXT NULL")
    op.execute("ALTER TABLE squad_tasks ADD COLUMN external_workspace_id TEXT NULL")
    op.execute("ALTER TABLE squad_tasks ADD COLUMN external_user_id TEXT NULL")
    op.execute(
        """CREATE UNIQUE INDEX IF NOT EXISTS uq_squad_tasks_intake
           ON squad_tasks (source, external_message_id)
           WHERE external_message_id IS NOT NULL"""
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS uq_squad_tasks_intake")
    # SQLite <3.35 ALTER TABLE DROP COLUMN unsupported — recreate without intake cols.
    op.execute(
        """CREATE TABLE squad_tasks_pre0021 AS
           SELECT id, squad_id, tenant_id, title, description, status,
                  priority, assignee, skill_id, project, labels_json,
                  blocked_by_json, blocks_json, inputs_json, result_json,
                  token_budget, bounty_json, external_ref, created_at,
                  updated_at, completed_at, claimed_at, attempt,
                  done_when_json, claim_owner_pid,
                  claim_owner_instance, claim_owner_acquired_at, claim_token
           FROM squad_tasks"""
    )
    op.execute("DROP TABLE squad_tasks")
    op.execute("ALTER TABLE squad_tasks_pre0021 RENAME TO squad_tasks")
