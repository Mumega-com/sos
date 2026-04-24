"""0014 — partners, opportunities, opportunity_stage_log (Section 3)."""
from alembic import op

revision = "0014"
down_revision = "0013"
branch_labels = None
depends_on = None

_PARTNER_TYPES = (
    "broker", "accelerator", "university", "cert-body",
    "sr-ed-firm", "realtor", "filing-partner", "referral-source",
    "investor", "channel", "platform", "other"
)

_OPP_TYPES = (
    "customer-deal", "partnership", "investment",
    "channel-expansion", "gov-relationship"
)

_OPP_STAGES = ("prospect", "active", "won", "lost", "on-hold")


def upgrade() -> None:
    partner_type_check = "', '".join(_PARTNER_TYPES)
    opp_type_check = "', '".join(_OPP_TYPES)
    opp_stage_check = "', '".join(_OPP_STAGES)

    op.execute(f"""
        CREATE TABLE IF NOT EXISTS partners (
            id                  TEXT PRIMARY KEY,
            workspace_id        TEXT NOT NULL,
            external_id         TEXT,
            name                TEXT NOT NULL,
            type                TEXT NOT NULL
                                  CHECK (type IN ('{partner_type_check}')),
            website_url         TEXT,
            hq_country          TEXT,
            primary_contact_id  TEXT,
            parent_partner_id   TEXT,
            revenue_split_pct   REAL,
            visibility_tier     TEXT NOT NULL DEFAULT 'firm_internal'
                                  CHECK (visibility_tier IN ('public', 'firm_internal', 'privileged')),
            engagement_status   TEXT NOT NULL DEFAULT 'prospect'
                                  CHECK (engagement_status IN ('prospect', 'active', 'paused', 'closed')),
            notes               TEXT,
            inkwell_page_slug   TEXT,
            onboarded_at        TEXT,
            active              INTEGER NOT NULL DEFAULT 1,
            archived_at         TEXT,
            created_at          TEXT NOT NULL,
            updated_at          TEXT NOT NULL,
            created_by          TEXT NOT NULL,
            updated_by          TEXT NOT NULL,
            UNIQUE (workspace_id, external_id),
            UNIQUE (workspace_id, name)
        )
    """)
    op.execute("CREATE INDEX IF NOT EXISTS idx_partners_workspace ON partners (workspace_id)")
    op.execute("CREATE INDEX IF NOT EXISTS idx_partners_type      ON partners (workspace_id, type)")
    op.execute("CREATE INDEX IF NOT EXISTS idx_partners_parent    ON partners (parent_partner_id)")

    op.execute(f"""
        CREATE TABLE IF NOT EXISTS opportunities (
            id                   TEXT PRIMARY KEY,
            workspace_id         TEXT NOT NULL,
            external_id          TEXT,
            name                 TEXT NOT NULL,
            type                 TEXT NOT NULL
                                   CHECK (type IN ('{opp_type_check}')),
            partner_id           TEXT,
            primary_contact_id   TEXT,
            stage                TEXT NOT NULL DEFAULT 'prospect'
                                   CHECK (stage IN ('{opp_stage_check}')),
            stage_entered_at     TEXT NOT NULL,
            estimated_value      REAL,
            estimated_close_at   TEXT,
            close_reason         TEXT,
            owner_id             TEXT NOT NULL,
            notes_ref            TEXT,
            notes                TEXT,
            archived_at          TEXT,
            created_at           TEXT NOT NULL,
            updated_at           TEXT NOT NULL,
            created_by           TEXT NOT NULL,
            updated_by           TEXT NOT NULL,
            UNIQUE (workspace_id, external_id)
        )
    """)
    op.execute("CREATE INDEX IF NOT EXISTS idx_opportunities_workspace ON opportunities (workspace_id)")
    op.execute("CREATE INDEX IF NOT EXISTS idx_opportunities_stage     ON opportunities (workspace_id, stage)")
    op.execute("CREATE INDEX IF NOT EXISTS idx_opportunities_owner     ON opportunities (owner_id)")
    op.execute("CREATE INDEX IF NOT EXISTS idx_opportunities_partner   ON opportunities (partner_id)")

    op.execute("""
        CREATE TABLE IF NOT EXISTS opportunity_stage_log (
            id              TEXT PRIMARY KEY,
            opportunity_id  TEXT NOT NULL REFERENCES opportunities(id) ON DELETE CASCADE,
            from_stage      TEXT NOT NULL,
            to_stage        TEXT NOT NULL,
            transitioned_at TEXT NOT NULL,
            transitioned_by TEXT NOT NULL
        )
    """)
    op.execute("""
        CREATE INDEX IF NOT EXISTS idx_opp_stage_log_opp
        ON opportunity_stage_log (opportunity_id)
    """)


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS idx_opp_stage_log_opp")
    op.execute("DROP TABLE IF EXISTS opportunity_stage_log")
    for idx in ("idx_opportunities_partner", "idx_opportunities_owner",
                "idx_opportunities_stage", "idx_opportunities_workspace"):
        op.execute(f"DROP INDEX IF EXISTS {idx}")
    op.execute("DROP TABLE IF EXISTS opportunities")
    for idx in ("idx_partners_parent", "idx_partners_type", "idx_partners_workspace"):
        op.execute(f"DROP INDEX IF EXISTS {idx}")
    op.execute("DROP TABLE IF EXISTS partners")
