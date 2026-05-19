"""0012 — roles, role_permissions, role_assignments for Section 1A RBAC."""
from alembic import op

revision = "0012"
down_revision = "0011"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
        CREATE TABLE IF NOT EXISTS roles (
            id          TEXT PRIMARY KEY,
            project_id  TEXT NOT NULL,
            tenant_id   TEXT NOT NULL DEFAULT 'default',
            name        TEXT NOT NULL,
            description TEXT,
            created_at  TEXT NOT NULL,
            UNIQUE(project_id, name, tenant_id)
        )
    """)
    op.execute("""
        CREATE INDEX IF NOT EXISTS idx_roles_project
        ON roles (project_id, tenant_id)
    """)

    op.execute("""
        CREATE TABLE IF NOT EXISTS role_permissions (
            role_id    TEXT NOT NULL REFERENCES roles(id),
            permission TEXT NOT NULL,
            PRIMARY KEY (role_id, permission)
        )
    """)

    op.execute("""
        CREATE TABLE IF NOT EXISTS role_assignments (
            role_id       TEXT NOT NULL REFERENCES roles(id),
            assignee_id   TEXT NOT NULL,
            assignee_type TEXT NOT NULL DEFAULT 'agent',
            assigned_at   TEXT NOT NULL,
            assigned_by   TEXT NOT NULL,
            PRIMARY KEY (role_id, assignee_id)
        )
    """)
    op.execute("""
        CREATE INDEX IF NOT EXISTS idx_role_assignments_assignee
        ON role_assignments (assignee_id)
    """)

    # Seed roles for default tenant — canonical set used across all projects.
    # project_id = '_system' marks these as cross-project seed roles.
    _SEED_ROLES = [
        ("principal",   "Root authority — signs canonical mints, final policy approvals"),
        ("coordinator", "Orchestrates projects, manages agent dispatch and task routing"),
        ("builder",     "Executes implementation work — code, infra, content"),
        ("gate",        "Quality gate — approves work before promotion (Athena pattern)"),
        ("knight",      "Customer-minted agent with project-scoped authority"),
        ("worker",      "Stateless task executor, no persistent scope"),
        ("partner",     "External collaborator with limited read and submit access"),
        ("customer",    "End-user of a delivered product or service"),
        ("observer",    "Read-only access to project state and outputs"),
    ]

    from datetime import datetime, timezone
    from uuid import uuid4
    now = datetime.now(timezone.utc).isoformat()
    for name, description in _SEED_ROLES:
        role_id = str(uuid4())
        desc_escaped = description.replace("'", "''")
        op.execute(
            f"INSERT OR IGNORE INTO roles (id, project_id, tenant_id, name, description, created_at) "
            f"VALUES ('{role_id}', '_system', 'default', '{name}', '{desc_escaped}', '{now}')"
        )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS idx_role_assignments_assignee")
    op.execute("DROP TABLE IF EXISTS role_assignments")
    op.execute("DROP TABLE IF EXISTS role_permissions")
    op.execute("DROP INDEX IF EXISTS idx_roles_project")
    op.execute("DROP TABLE IF EXISTS roles")
