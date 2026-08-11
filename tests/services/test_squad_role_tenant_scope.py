"""P0-B fix regression tests (sos-205-47f5f8c2 gate-3).

Five RBAC routes in app.py used to authenticate a caller and then discard
the AuthContext entirely (`await lookup_token(...) or _raise_401()`), so any
tenant's valid api key could mutate or read ANY other tenant's role:

  - POST   /roles/{role_id}/permissions             (add_role_permission)
  - DELETE /roles/{role_id}/permissions/{permission} (remove_role_permission)
  - DELETE /roles/{role_id}/assignments/{assignee_id}(revoke_role_assignment)
  - GET    /roles/{role_id}/assignments              (list_role_assignments)
  - GET    /agents/{agent_id}/roles                  (get_agent_roles)

The fix binds `auth` and scopes every role_id lookup by `auth.tenant_scope`,
mirroring the sibling create_project_role / list_project_roles routes that
were already correct. Driven end-to-end over real HTTP (TestClient), the
same production code path a real client hits — not the service layer in
isolation.
"""
from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from sos.services.squad import app as app_module
from sos.services.squad import auth
from sos.services.squad.roles import RoleService
from sos.services.squad.service import SquadDB


_DDL = """
    CREATE TABLE api_keys (
        token_hash TEXT PRIMARY KEY,
        tenant_id TEXT NOT NULL,
        identity_type TEXT NOT NULL,
        created_at TEXT NOT NULL
    );
    CREATE TABLE roles (
        id          TEXT PRIMARY KEY,
        project_id  TEXT NOT NULL,
        tenant_id   TEXT NOT NULL DEFAULT 'default',
        name        TEXT NOT NULL,
        description TEXT,
        created_at  TEXT NOT NULL,
        rank        INTEGER NOT NULL DEFAULT 0,
        UNIQUE(project_id, name, tenant_id)
    );
    CREATE TABLE role_permissions (
        role_id    TEXT NOT NULL,
        permission TEXT NOT NULL,
        PRIMARY KEY (role_id, permission)
    );
    CREATE TABLE role_assignments (
        role_id       TEXT NOT NULL,
        assignee_id   TEXT NOT NULL,
        assignee_type TEXT NOT NULL DEFAULT 'agent',
        assigned_at   TEXT NOT NULL,
        assigned_by   TEXT NOT NULL,
        PRIMARY KEY (role_id, assignee_id)
    );
"""


@pytest.fixture()
def two_tenants(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """A throwaway DB with one role owned by tenant-A, plus valid api keys
    for tenant-A and tenant-B. Wires app.py's per-request SquadDB() AND the
    module-level `_role_svc` (created once at import time with the real
    default SquadDB — NOT re-created per-request, so it must be repointed
    separately) at this throwaway DB."""
    db_path = tmp_path / "roles_tenant_scope.db"
    database = SquadDB(db_path=db_path)
    with database.connect() as conn:
        conn.executescript(_DDL)

    monkeypatch.setattr(app_module, "SquadDB", lambda: database)
    monkeypatch.setattr(app_module._role_svc, "db", database)

    auth._token_cache_clear()
    auth._TOKEN_CACHE_INFLIGHT.clear()

    token_a, _ = auth.create_api_key("tenant-a", "user", db=database)
    token_b, _ = auth.create_api_key("tenant-b", "user", db=database)

    role_svc = RoleService(db=database)
    role = role_svc.create_role("proj-a", "admins", tenant_id="tenant-a")
    role_svc.assign_role(
        role["id"], "kasra-test",
        tenant_id="tenant-a", assignee_type="agent", assigned_by="test",
    )

    yield {
        "client": TestClient(app_module.app),
        "role_id": role["id"],
        "token_a": token_a,
        "token_b": token_b,
        "database": database,
    }

    auth._token_cache_clear()
    auth._TOKEN_CACHE_INFLIGHT.clear()


def _bearer(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


# ── add_role_permission ───────────────────────────────────────────────────


def test_add_role_permission_owner_ok(two_tenants):
    t = two_tenants
    resp = t["client"].post(
        f"/roles/{t['role_id']}/permissions",
        json={"permission": "squad:read"},
        headers=_bearer(t["token_a"]),
    )
    assert resp.status_code == 200
    assert resp.json() == {"role_id": t["role_id"], "permission": "squad:read"}


def test_add_role_permission_foreign_tenant_blocked(two_tenants):
    t = two_tenants
    resp = t["client"].post(
        f"/roles/{t['role_id']}/permissions",
        json={"permission": "squad:admin"},
        headers=_bearer(t["token_b"]),
    )
    assert resp.status_code in (403, 404)
    # Must not have actually granted the permission.
    with t["database"].connect() as conn:
        row = conn.execute(
            "SELECT 1 FROM role_permissions WHERE role_id = ? AND permission = ?",
            (t["role_id"], "squad:admin"),
        ).fetchone()
    assert row is None


# ── remove_role_permission ────────────────────────────────────────────────


def test_remove_role_permission_foreign_tenant_blocked(two_tenants):
    t = two_tenants
    with t["database"].connect() as conn:
        conn.execute(
            "INSERT INTO role_permissions (role_id, permission) VALUES (?, ?)",
            (t["role_id"], "squad:write"),
        )
    resp = t["client"].delete(
        f"/roles/{t['role_id']}/permissions/squad:write",
        headers=_bearer(t["token_b"]),
    )
    assert resp.status_code in (403, 404)
    with t["database"].connect() as conn:
        row = conn.execute(
            "SELECT 1 FROM role_permissions WHERE role_id = ? AND permission = ?",
            (t["role_id"], "squad:write"),
        ).fetchone()
    assert row is not None  # NOT deleted by the foreign caller


def test_remove_role_permission_owner_ok(two_tenants):
    t = two_tenants
    with t["database"].connect() as conn:
        conn.execute(
            "INSERT INTO role_permissions (role_id, permission) VALUES (?, ?)",
            (t["role_id"], "squad:write"),
        )
    resp = t["client"].delete(
        f"/roles/{t['role_id']}/permissions/squad:write",
        headers=_bearer(t["token_a"]),
    )
    assert resp.status_code == 200
    assert resp.json() == {"deleted": True}


# ── revoke_role_assignment ────────────────────────────────────────────────


def test_revoke_role_assignment_foreign_tenant_blocked(two_tenants):
    t = two_tenants
    resp = t["client"].delete(
        f"/roles/{t['role_id']}/assignments/kasra-test",
        headers=_bearer(t["token_b"]),
    )
    assert resp.status_code in (403, 404)
    with t["database"].connect() as conn:
        row = conn.execute(
            "SELECT 1 FROM role_assignments WHERE role_id = ? AND assignee_id = ?",
            (t["role_id"], "kasra-test"),
        ).fetchone()
    assert row is not None  # NOT revoked by the foreign caller


def test_revoke_role_assignment_owner_ok(two_tenants):
    t = two_tenants
    resp = t["client"].delete(
        f"/roles/{t['role_id']}/assignments/kasra-test",
        headers=_bearer(t["token_a"]),
    )
    assert resp.status_code == 200
    assert resp.json() == {"revoked": True}


# ── list_role_assignments ─────────────────────────────────────────────────


def test_list_role_assignments_foreign_tenant_blocked(two_tenants):
    t = two_tenants
    resp = t["client"].get(
        f"/roles/{t['role_id']}/assignments",
        headers=_bearer(t["token_b"]),
    )
    assert resp.status_code in (403, 404)


def test_list_role_assignments_owner_ok(two_tenants):
    t = two_tenants
    resp = t["client"].get(
        f"/roles/{t['role_id']}/assignments",
        headers=_bearer(t["token_a"]),
    )
    assert resp.status_code == 200
    assignees = [a["assignee_id"] for a in resp.json()["assignments"]]
    assert "kasra-test" in assignees


# ── get_agent_roles ───────────────────────────────────────────────────────


def test_get_agent_roles_foreign_tenant_sees_nothing(two_tenants):
    t = two_tenants
    resp = t["client"].get(
        "/agents/kasra-test/roles",
        headers=_bearer(t["token_b"]),
    )
    # Not a 404 (the route never raises for this one) — but the foreign
    # tenant must not see tenant-A's role in the result.
    assert resp.status_code == 200
    assert resp.json()["roles"] == []


def test_get_agent_roles_owner_sees_role(two_tenants):
    t = two_tenants
    resp = t["client"].get(
        "/agents/kasra-test/roles",
        headers=_bearer(t["token_a"]),
    )
    assert resp.status_code == 200
    role_ids = [r["id"] for r in resp.json()["roles"]]
    assert t["role_id"] in role_ids


# ── assign_role — BLOCK-B (sos-205-790a2a63 gate-4) ───────────────────────
# The 6th RBAC route on this surface. The P0-B fix (sos-205-47f5f8c2) scoped
# the five siblings but missed this one: `assign_role` called
# `self._get_role_row(role_id)` with no `tenant_id`, defaulting to the
# fail-open unrestricted lookup, so ANY tenant's valid api key could plant a
# role_assignment row into ANOTHER tenant's role — and, because
# revoke_role_assignment IS scoped, the victim tenant (or system) was the
# only one who could remove it.


def test_assign_role_foreign_tenant_blocked(two_tenants):
    t = two_tenants
    resp = t["client"].post(
        f"/roles/{t['role_id']}/assignments",
        json={"assignee_id": "attacker-planted", "assigned_by": "tenant-b"},
        headers=_bearer(t["token_b"]),
    )
    assert resp.status_code in (403, 404)
    with t["database"].connect() as conn:
        row = conn.execute(
            "SELECT 1 FROM role_assignments WHERE role_id = ? AND assignee_id = ?",
            (t["role_id"], "attacker-planted"),
        ).fetchone()
    assert row is None  # NOT planted by the foreign caller


def test_assign_role_owner_ok(two_tenants):
    t = two_tenants
    resp = t["client"].post(
        f"/roles/{t['role_id']}/assignments",
        json={"assignee_id": "new-teammate", "assigned_by": "tenant-a"},
        headers=_bearer(t["token_a"]),
    )
    assert resp.status_code == 200
    assert resp.json()["assignee_id"] == "new-teammate"
    with t["database"].connect() as conn:
        row = conn.execute(
            "SELECT 1 FROM role_assignments WHERE role_id = ? AND assignee_id = ?",
            (t["role_id"], "new-teammate"),
        ).fetchone()
    assert row is not None


# ── /me/roles — P2-E (sos-205-790a2a63 gate-4) ────────────────────────────
# `get_token_roles` called `get_agent_roles(tenant_id)` (positional —
# `tenant_id` filled the `assignee_id` slot) with NO `tenant_id=` kwarg,
# defaulting to unrestricted. A role_assignment row whose `assignee_id`
# equals another tenant's identity — exactly what an attacker could plant
# through the unfixed BLOCK-B hole — surfaced through that OTHER tenant's
# own /me/roles. Simulated here by inserting the row directly, independent
# of whether BLOCK-B itself is fixed, so this test locks P2-E on its own.


def test_me_roles_does_not_disclose_cross_tenant_planted_assignment(two_tenants):
    t = two_tenants
    with t["database"].connect() as conn:
        conn.execute(
            """
            INSERT INTO role_assignments (role_id, assignee_id, assignee_type, assigned_at, assigned_by)
            VALUES (?, ?, 'agent', '2026-01-01T00:00:00Z', 'attacker')
            """,
            (t["role_id"], "tenant-b"),
        )
    resp = t["client"].get("/me/roles", headers=_bearer(t["token_b"]))
    assert resp.status_code == 200
    assert resp.json()["roles"] == []  # tenant-a's role must not surface for tenant-b


def test_me_roles_owner_sees_own_assignment(two_tenants):
    t = two_tenants
    with t["database"].connect() as conn:
        conn.execute(
            """
            INSERT INTO role_assignments (role_id, assignee_id, assignee_type, assigned_at, assigned_by)
            VALUES (?, ?, 'agent', '2026-01-01T00:00:00Z', 'test')
            """,
            (t["role_id"], "tenant-a"),
        )
    resp = t["client"].get("/me/roles", headers=_bearer(t["token_a"]))
    assert resp.status_code == 200
    role_ids = [r["id"] for r in resp.json()["roles"]]
    assert t["role_id"] in role_ids


# ── system tier keeps cross-tenant access ─────────────────────────────────


def test_system_bearer_bypasses_tenant_scope(two_tenants, monkeypatch: pytest.MonkeyPatch):
    t = two_tenants
    monkeypatch.setattr(auth, "SYSTEM_TOKEN", "sys-tok-test")
    resp = t["client"].get(
        f"/roles/{t['role_id']}/assignments",
        headers=_bearer("sys-tok-test"),
    )
    assert resp.status_code == 200
    assignees = [a["assignee_id"] for a in resp.json()["assignments"]]
    assert "kasra-test" in assignees

    resp = t["client"].get(
        "/agents/kasra-test/roles",
        headers=_bearer("sys-tok-test"),
    )
    assert resp.status_code == 200
    role_ids = [r["id"] for r in resp.json()["roles"]]
    assert t["role_id"] in role_ids


def test_system_bearer_can_assign_across_tenants(two_tenants, monkeypatch: pytest.MonkeyPatch):
    t = two_tenants
    monkeypatch.setattr(auth, "SYSTEM_TOKEN", "sys-tok-test")
    resp = t["client"].post(
        f"/roles/{t['role_id']}/assignments",
        json={"assignee_id": "system-planted", "assigned_by": "system"},
        headers=_bearer("sys-tok-test"),
    )
    assert resp.status_code == 200
