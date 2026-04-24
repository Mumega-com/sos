"""0013 — contacts table (Section 3 structured records)."""
from alembic import op

revision = "0013"
down_revision = "0012"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
        CREATE TABLE IF NOT EXISTS contacts (
            id                TEXT PRIMARY KEY,
            workspace_id      TEXT NOT NULL,
            external_id       TEXT,
            first_name        TEXT NOT NULL,
            last_name         TEXT NOT NULL,
            email             TEXT,
            phone             TEXT,
            title             TEXT,
            org_id            TEXT,
            visibility_tier   TEXT NOT NULL DEFAULT 'firm_internal'
                                CHECK (visibility_tier IN ('public', 'firm_internal', 'privileged')),
            engagement_status TEXT NOT NULL DEFAULT 'prospect'
                                CHECK (engagement_status IN ('prospect', 'active', 'paused', 'closed')),
            source            TEXT,
            last_touched_at   TEXT,
            next_action       TEXT,
            notes_ref         TEXT,
            notes             TEXT,
            archived_at       TEXT,
            owner_id          TEXT NOT NULL,
            created_at        TEXT NOT NULL,
            updated_at        TEXT NOT NULL,
            created_by        TEXT NOT NULL,
            updated_by        TEXT NOT NULL,
            UNIQUE (workspace_id, external_id),
            UNIQUE (workspace_id, email)
        )
    """)
    op.execute("CREATE INDEX IF NOT EXISTS idx_contacts_workspace ON contacts (workspace_id)")
    op.execute("CREATE INDEX IF NOT EXISTS idx_contacts_org       ON contacts (org_id)")
    op.execute("CREATE INDEX IF NOT EXISTS idx_contacts_owner     ON contacts (owner_id)")
    op.execute("CREATE INDEX IF NOT EXISTS idx_contacts_status    ON contacts (workspace_id, engagement_status)")
    op.execute("CREATE INDEX IF NOT EXISTS idx_contacts_email     ON contacts (workspace_id, email)")
    op.execute("CREATE INDEX IF NOT EXISTS idx_contacts_updated   ON contacts (updated_at DESC)")


def downgrade() -> None:
    for idx in (
        "idx_contacts_updated", "idx_contacts_email", "idx_contacts_status",
        "idx_contacts_owner", "idx_contacts_org", "idx_contacts_workspace",
    ):
        op.execute(f"DROP INDEX IF EXISTS {idx}")
    op.execute("DROP TABLE IF EXISTS contacts")
