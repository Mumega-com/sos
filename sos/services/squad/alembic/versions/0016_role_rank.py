"""0016 — add rank column to roles table for privilege escalation guard."""
from alembic import op

revision = "0016"
down_revision = "0015"
branch_labels = None
depends_on = None

# Canonical rank scale: higher = more privileged.
_SEED_RANKS = {
    "principal":   100,
    "coordinator":  90,
    "gate":         85,
    "knight":       70,
    "builder":      60,
    "partner":      50,
    "worker":       40,
    "customer":     20,
    "observer":     10,
}


def upgrade() -> None:
    op.execute("ALTER TABLE roles ADD COLUMN rank INTEGER NOT NULL DEFAULT 0")
    op.execute("CREATE INDEX IF NOT EXISTS idx_roles_rank ON roles (rank)")

    # Backfill ranks for the 9 seed roles (project_id='_system', tenant_id='default')
    for name, rank in _SEED_RANKS.items():
        op.execute(
            f"UPDATE roles SET rank = {rank} WHERE name = '{name}' AND project_id = '_system'"
        )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS idx_roles_rank")
    # SQLite doesn't support DROP COLUMN before 3.35 — skip; schema reset handles it
