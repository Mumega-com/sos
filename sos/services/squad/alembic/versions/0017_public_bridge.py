"""0017 — public migration bridge.

The internal distribution uses this revision id for private product seed data.
Public SOS keeps the revision id so later public-safe migrations remain
upgradeable without shipping private product roles.
"""

revision = "0017"
down_revision = "0016"
branch_labels = None
depends_on = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
