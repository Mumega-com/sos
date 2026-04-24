"""0011 — customer_intakes table for intake → knight spawn pipeline."""
from alembic import op

revision = "0011"
down_revision = "0010"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
        CREATE TABLE IF NOT EXISTS customer_intakes (
            id                  TEXT PRIMARY KEY,
            customer_name       TEXT NOT NULL,
            customer_slug       TEXT NOT NULL UNIQUE,
            domain              TEXT,
            repo_url            TEXT,
            icp                 TEXT,
            okrs_json           TEXT DEFAULT '[]',
            cause_draft         TEXT,
            descriptor_draft    TEXT,
            initial_roles_json  TEXT DEFAULT '["advisor","intern"]',
            status              TEXT NOT NULL DEFAULT 'pending',
            source              TEXT DEFAULT 'direct',
            ghl_contact_id      TEXT,
            created_at          TEXT NOT NULL,
            approved_by         TEXT,
            approved_at         TEXT,
            minted_at           TEXT,
            mint_error          TEXT,
            knight_name         TEXT
        )
    """)
    op.execute("""
        CREATE INDEX IF NOT EXISTS idx_customer_intakes_status
        ON customer_intakes (status)
    """)
    op.execute("""
        CREATE INDEX IF NOT EXISTS idx_customer_intakes_slug
        ON customer_intakes (customer_slug)
    """)


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS customer_intakes")
