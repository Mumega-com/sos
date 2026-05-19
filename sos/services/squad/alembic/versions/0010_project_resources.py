"""0010 — project_resources table for onboarded repos and other project assets."""
from alembic import op

revision = "0010"
down_revision = "0009"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
        CREATE TABLE IF NOT EXISTS project_resources (
            id           TEXT PRIMARY KEY,
            project_id   TEXT NOT NULL,
            tenant_id    TEXT NOT NULL DEFAULT 'default',
            resource_type TEXT NOT NULL,       -- 'repo', 'domain', 'analytics', etc.
            url          TEXT,
            local_path   TEXT,
            meta_json    TEXT DEFAULT '{}',
            added_at     TEXT NOT NULL
        )
    """)
    op.execute("""
        CREATE INDEX IF NOT EXISTS idx_project_resources_project
        ON project_resources (project_id, tenant_id)
    """)


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS project_resources")
