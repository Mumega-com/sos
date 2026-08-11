"""RoleService — Section 1A RBAC: roles, permissions, assignments."""
from __future__ import annotations

from typing import Optional
from uuid import uuid4

from sos.services.squad.service import SquadDB, now_iso


class RoleNotFoundError(ValueError):
    pass


class RoleDuplicateError(ValueError):
    pass


class RolePrivilegeError(PermissionError):
    """Raised when a caller tries to assign a role ranked above their own."""
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
        rank: int = 0,
    ) -> dict:
        role_id = str(uuid4())
        created_at = now_iso()
        with self.db.connect() as conn:
            try:
                conn.execute(
                    """
                    INSERT INTO roles (id, project_id, tenant_id, name, description, created_at, rank)
                    VALUES (?, ?, ?, ?, ?, ?, ?)
                    """,
                    (role_id, project_id, tenant_id, name, description, created_at, rank),
                )
            except Exception as exc:
                if "UNIQUE" in str(exc):
                    raise RoleDuplicateError(f"Role '{name}' already exists in project '{project_id}'") from exc
                raise
        return self._get_role_row(role_id, tenant_id=tenant_id)

    def list_roles(self, project_id: str, *, tenant_id: str = "default") -> list[dict]:
        with self.db.connect() as conn:
            rows = conn.execute(
                "SELECT * FROM roles WHERE project_id = ? AND tenant_id = ? ORDER BY name",
                (project_id, tenant_id),
            ).fetchall()
        return [dict(r) for r in rows]

    def get_role(self, role_id: str, *, tenant_id: str | None) -> dict:
        return self._get_role_row(role_id, tenant_id=tenant_id)

    def _get_role_row(self, role_id: str, *, tenant_id: str | None) -> dict:
        """Fetch a role row by id.

        P0-B fix (sos-205-47f5f8c2 gate-3): `tenant_id=None` means
        UNRESTRICTED lookup — reserved for system-tier callers
        (AuthContext.tenant_scope is None only when is_system=True). Any
        other value scopes the lookup to that tenant, same as
        SquadService.get()'s `tenant_id: str | None = DEFAULT_TENANT_ID`
        pattern. A role that exists but belongs to a different tenant raises
        the SAME RoleNotFoundError as a role that doesn't exist at all — the
        route must not let a caller distinguish "not found" from "not
        yours".

        P2-F fix (sos-205-790a2a63 gate-4): `tenant_id` no longer defaults to
        `None`. A `str | None = None` default made "I forgot to scope this
        call" and "I deliberately want every tenant" the SAME call shape —
        and two call sites (BLOCK-B's `assign_role`, P2-E's
        `get_token_roles`) forgot it IN THE SAME COMMIT that added the
        kwarg. `None` is still a legal value — it is the explicit,
        documented system-tier spelling above — but every caller must now
        STATE it. Omitting the keyword is a `TypeError` at call time (or an
        import-time break for any caller that got missed), not a silent
        cross-tenant read. Same change applied to every sibling method below
        that takes `tenant_id`.
        """
        with self.db.connect() as conn:
            if tenant_id is None:
                row = conn.execute(
                    "SELECT * FROM roles WHERE id = ?", (role_id,)
                ).fetchone()
            else:
                row = conn.execute(
                    "SELECT * FROM roles WHERE id = ? AND tenant_id = ?",
                    (role_id, tenant_id),
                ).fetchone()
        if not row:
            raise RoleNotFoundError(f"Role {role_id} not found")
        return dict(row)

    # ------------------------------------------------------------------
    # Permissions
    # ------------------------------------------------------------------

    def add_permission(self, role_id: str, permission: str, *, tenant_id: str | None) -> dict:
        self._get_role_row(role_id, tenant_id=tenant_id)  # raises if missing or foreign-tenant
        with self.db.connect() as conn:
            conn.execute(
                "INSERT OR IGNORE INTO role_permissions (role_id, permission) VALUES (?, ?)",
                (role_id, permission),
            )
        return {"role_id": role_id, "permission": permission}

    def remove_permission(self, role_id: str, permission: str, *, tenant_id: str | None) -> None:
        self._get_role_row(role_id, tenant_id=tenant_id)  # raises if missing or foreign-tenant
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
    # Rank helpers
    # ------------------------------------------------------------------

    def caller_max_rank(self, caller_id: str) -> int:
        """Return highest rank held by caller_id across all role_assignments."""
        with self.db.connect() as conn:
            row = conn.execute(
                """
                SELECT MAX(r.rank) AS max_rank
                FROM role_assignments ra
                JOIN roles r ON r.id = ra.role_id
                WHERE ra.assignee_id = ?
                """,
                (caller_id,),
            ).fetchone()
        return row["max_rank"] if row and row["max_rank"] is not None else 0

    def check_can_assign(self, caller_id: str, target_role_id: str, *, tenant_id: str | None) -> None:
        """Raise RolePrivilegeError if caller cannot assign target_role_id.

        Rule: caller's max rank must be >= target role's rank.
        System identity (caller_id starting with 'system:') bypasses the check.

        The system bypass is intentional scoped-privilege for the bootstrap path —
        not a backdoor. Without it, seeding the first principal would require an
        existing principal to assign them (infinite regress). The system bearer is
        never issued to end-users; it is held only by the service runtime.

        BLOCK-B fix (sos-205-790a2a63 gate-4): this used to call
        `self._get_role_row(target_role_id)` with NO `tenant_id`, which
        defaulted to the fail-open `None` = unrestricted lookup — the actual
        cross-tenant hole (a foreign tenant could look up, and then assign,
        another tenant's role_id). `tenant_id` is now required and forwarded
        straight through, same scoping as every sibling lookup.
        """
        if caller_id.startswith("system:") or caller_id == "system":
            return
        target_role = self._get_role_row(target_role_id, tenant_id=tenant_id)
        target_rank: int = target_role.get("rank", 0)
        if target_rank == 0:
            return  # unranked role — no restriction
        caller_rank = self.caller_max_rank(caller_id)
        if caller_rank < target_rank:
            raise RolePrivilegeError(
                f"role_rank_exceeds_caller: cannot assign role '{target_role['name']}' "
                f"(rank={target_rank}); caller max rank={caller_rank}"
            )

    # ------------------------------------------------------------------
    # Assignments
    # ------------------------------------------------------------------

    def assign_role(
        self,
        role_id: str,
        assignee_id: str,
        *,
        tenant_id: str | None,
        assignee_type: str = "agent",
        assigned_by: str,
        caller_id: Optional[str] = None,
    ) -> dict:
        """Assign role_id to assignee_id. If caller_id is provided, rank check is enforced.

        BLOCK-B fix (sos-205-790a2a63 gate-4): this was the 6th RBAC route on
        this surface and the only one the P0-B fix (sos-205-47f5f8c2) missed
        — it called `self._get_role_row(role_id)` with no `tenant_id`, which
        defaulted to unrestricted, so ANY tenant's valid api key could plant
        a role_assignment row into ANOTHER tenant's role (and the target
        tenant's own `revoke_assignment`/`add_permission` calls ARE scoped,
        so the planted row was also attacker-unremovable by anyone but the
        victim tenant or system). `tenant_id` is now required and forwarded
        to both the existence check below and `check_can_assign`'s internal
        lookup, identical to the five siblings.
        """
        if caller_id:
            self.check_can_assign(caller_id, role_id, tenant_id=tenant_id)
        self._get_role_row(role_id, tenant_id=tenant_id)  # raises if missing or foreign-tenant
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

    def revoke_assignment(self, role_id: str, assignee_id: str, *, tenant_id: str | None) -> None:
        self._get_role_row(role_id, tenant_id=tenant_id)  # raises if missing or foreign-tenant
        with self.db.connect() as conn:
            conn.execute(
                "DELETE FROM role_assignments WHERE role_id = ? AND assignee_id = ?",
                (role_id, assignee_id),
            )

    def list_assignments(self, role_id: str, *, tenant_id: str | None) -> list[dict]:
        self._get_role_row(role_id, tenant_id=tenant_id)  # raises if missing or foreign-tenant
        with self.db.connect() as conn:
            rows = conn.execute(
                "SELECT * FROM role_assignments WHERE role_id = ? ORDER BY assigned_at",
                (role_id,),
            ).fetchall()
        return [dict(r) for r in rows]

    def get_agent_roles(self, assignee_id: str, *, tenant_id: str | None) -> list[dict]:
        """All roles held by an agent across all projects.

        P0-B fix (sos-205-47f5f8c2 gate-3): `tenant_id=None` (system-tier
        only) returns roles across every tenant, matching the pre-fix
        behaviour. Any other value filters the join to `r.tenant_id`, so a
        tenant-scoped caller can no longer enumerate an agent's roles in a
        tenant it doesn't own.
        """
        with self.db.connect() as conn:
            if tenant_id is None:
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
            else:
                rows = conn.execute(
                    """
                    SELECT r.*, ra.assignee_type, ra.assigned_at, ra.assigned_by
                    FROM role_assignments ra
                    JOIN roles r ON r.id = ra.role_id
                    WHERE ra.assignee_id = ? AND r.tenant_id = ?
                    ORDER BY r.project_id, r.name
                    """,
                    (assignee_id, tenant_id),
                ).fetchall()
        return [dict(r) for r in rows]

    def get_token_roles(self, tenant_id: str) -> list[dict]:
        """All roles assigned to the identity matching tenant_id (for /me/roles).

        P2-E fix (sos-205-790a2a63 gate-4): this called `get_agent_roles`
        (assignee_id) WITHOUT the new `tenant_id` kwarg, which fell through
        to the fail-open `None` default and returned every tenant's matching
        role rows — /me/roles for tenant B disclosed tenant A's role. Now
        forwarded explicitly, scoping the lookup to the caller's own tenant.
        """
        return self.get_agent_roles(tenant_id, tenant_id=tenant_id)
