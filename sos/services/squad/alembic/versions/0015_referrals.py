"""0015 — referrals graph table (Section 3)."""
from alembic import op

revision = "0015"
down_revision = "0014"
branch_labels = None
depends_on = None

_RELATIONSHIPS = (
    "referred", "invested-in", "co-founded", "serves",
    "introduced-to", "competitor-of", "ally-of", "advises"
)
_STRENGTHS = ("weak", "moderate", "strong", "trusted")


def upgrade() -> None:
    rel_check = "', '".join(_RELATIONSHIPS)
    str_check = "', '".join(_STRENGTHS)

    op.execute(f"""
        CREATE TABLE IF NOT EXISTS referrals (
            id            TEXT PRIMARY KEY,
            workspace_id  TEXT NOT NULL,
            source_id     TEXT NOT NULL,
            source_type   TEXT NOT NULL CHECK (source_type IN ('contact', 'partner')),
            target_id     TEXT NOT NULL,
            target_type   TEXT NOT NULL CHECK (target_type IN ('contact', 'partner')),
            relationship  TEXT NOT NULL
                            CHECK (relationship IN ('{rel_check}')),
            strength      TEXT NOT NULL DEFAULT 'moderate'
                            CHECK (strength IN ('{str_check}')),
            context       TEXT,
            referred_at   TEXT,
            notes         TEXT,
            created_at    TEXT NOT NULL,
            updated_at    TEXT NOT NULL,
            created_by    TEXT NOT NULL,
            UNIQUE (workspace_id, source_id, source_type, target_id, target_type, relationship)
        )
    """)
    op.execute("CREATE INDEX IF NOT EXISTS idx_referrals_workspace ON referrals (workspace_id)")
    op.execute("CREATE INDEX IF NOT EXISTS idx_referrals_source    ON referrals (source_id, source_type)")
    op.execute("CREATE INDEX IF NOT EXISTS idx_referrals_target    ON referrals (target_id, target_type)")
    op.execute("CREATE INDEX IF NOT EXISTS idx_referrals_strength  ON referrals (strength)")


def downgrade() -> None:
    for idx in ("idx_referrals_strength", "idx_referrals_target",
                "idx_referrals_source", "idx_referrals_workspace"):
        op.execute(f"DROP INDEX IF EXISTS {idx}")
    op.execute("DROP TABLE IF EXISTS referrals")
