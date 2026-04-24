"""RoleService — Section 1A RBAC: roles, permissions, assignments."""
from __future__ import annotations

from typing import Optional
from uuid import uuid4

from sos.services.squad.service import SquadDB, now_iso


class RoleNotFoundError(ValueError):
    pass


class RoleDuplicateError(ValueError):
    pass


class RoleService:
    def __init__(self, db: Optional[SquadDB] = None) -> None:
        self.db = db or SquadDB()

    # ------------------------------------------------------------------
    # Roles
    # ------------------------------------------------------------------

    def create_role(
        self,
        project_id: str,
        name: str,
        *,
        tenant_id: str = "default",
        description: Optional[str] = None,
    ) -> dict:
        role_id = str(uuid4())
        created_at = now_iso()
        with self.db.connect() as conn:
            try:
                conn.execute(
                    """
                    INSERT INTO roles (id, project_id, tenant_id, name, description, created_at)
                    VALUES (?, ?, ?, ?, ?, ?)
                    """,
                    (role_id, project_id, tenant_id, name, description, created_at),
                )
            except Exception as exc:
                if "UNIQUE" in str(exc):
                    raise RoleDuplicateError(f"Role '{name}' already exists in project '{project_id}'") from exc
                raise
        return self._get_role_row(role_id)

    def list_roles(self, project_id: str, *, tenant_id: str = "default") -> list[dict]:
        with self.db.connect() as conn:
            rows = conn.execute(
                "SELECT * FROM roles WHERE project_id = ? AND tenant_id = ? ORDER BY name",
                (project_id, tenant_id),
            ).fetchall()
        return [dict(r) for r in rows]

    def get_role(self, role_id: str) -> dict:
        return self._get_role_row(role_id)

    def _get_role_row(self, role_id: str) -> dict:
        with self.db.connect() as conn:
            row = conn.execute(
                "SELECT * FROM roles WHERE id = ?", (role_id,)
            ).fetchone()
        if not row:
            raise RoleNotFoundError(f"Role {role_id} not found")
        return dict(row)

    # ------------------------------------------------------------------
    # Permissions
    # ------------------------------------------------------------------

    def add_permission(self, role_id: str, permission: str) -> dict:
        self._get_role_row(role_id)  # raises if missing
        with self.db.connect() as conn:
            conn.execute(
                "INSERT OR IGNORE INTO role_permissions (role_id, permission) VALUES (?, ?)",
                (role_id, permission),
            )
        return {"role_id": role_id, "permission": permission}

    def remove_permission(self, role_id: str, permission: str) -> None:
        with self.db.connect() as conn:
            conn.execute(
                "DELETE FROM role_permissions WHERE role_id = ? AND permission = ?",
                (role_id, permission),
            )

    def list_permissions(self, role_id: str) -> list[str]:
        with self.db.connect() as conn:
            rows = conn.execute(
                "SELECT permission FROM role_permissions WHERE role_id = ? ORDER BY permission",
                (role_id,),
            ).fetchall()
        return [r["permission"] for r in rows]

    # ------------------------------------------------------------------
    # Assignments
    # ------------------------------------------------------------------

    def assign_role(
        self,
        role_id: str,
        assignee_id: str,
        *,
        assignee_type: str = "agent",
        assigned_by: str,
    ) -> dict:
        self._get_role_row(role_id)
        assigned_at = now_iso()
        with self.db.connect() as conn:
            conn.execute(
                """
                INSERT OR REPLACE INTO role_assignments
                    (role_id, assignee_id, assignee_type, assigned_at, assigned_by)
                VALUES (?, ?, ?, ?, ?)
                """,
                (role_id, assignee_id, assignee_type, assigned_at, assigned_by),
            )
        return {
            "role_id": role_id,
            "assignee_id": assignee_id,
            "assignee_type": assignee_type,
            "assigned_at": assigned_at,
            "assigned_by": assigned_by,
        }

    def revoke_assignment(self, role_id: str, assignee_id: str) -> None:
        with self.db.connect() as conn:
            conn.execute(
                "DELETE FROM role_assignments WHERE role_id = ? AND assignee_id = ?",
                (role_id, assignee_id),
            )

    def list_assignments(self, role_id: str) -> list[dict]:
        with self.db.connect() as conn:
            rows = conn.execute(
                "SELECT * FROM role_assignments WHERE role_id = ? ORDER BY assigned_at",
                (role_id,),
            ).fetchall()
        return [dict(r) for r in rows]

    def get_agent_roles(self, assignee_id: str) -> list[dict]:
        """All roles held by an agent across all projects."""
        with self.db.connect() as conn:
            rows = conn.execute(
                """
                SELECT r.*, ra.assignee_type, ra.assigned_at, ra.assigned_by
                FROM role_assignments ra
                JOIN roles r ON r.id = ra.role_id
                WHERE ra.assignee_id = ?
                ORDER BY r.project_id, r.name
                """,
                (assignee_id,),
            ).fetchall()
        return [dict(r) for r in rows]

    def get_token_roles(self, tenant_id: str) -> list[dict]:
        """All roles assigned to the identity matching tenant_id (for /me/roles)."""
        return self.get_agent_roles(tenant_id)
