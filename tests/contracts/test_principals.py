"""
§1A Principals & Role Registry contract tests — Sprint 003 / Burst 2B.

Unit tests: model validation, frozen, seed role guard.
Integration tests (requires DB): upsert, get, role CRUD, assignments, MFA flag.

Run all:     DATABASE_URL=... pytest tests/contracts/test_principals.py -v
Run unit:    pytest tests/contracts/test_principals.py -v -m "not db"
"""
from __future__ import annotations

import os
import uuid
from datetime import datetime, timezone

import pytest
from pydantic import ValidationError

from sos.contracts.principals import (
    Principal,
    Role,
    RoleAssignment,
    _SEED_PREFIX,
    add_permission,
    assign_role,
    create_role,
    get_assignee_roles,
    get_principal,
    get_principal_by_email,
    get_role,
    get_role_assignments,
    get_role_permissions,
    list_roles,
    remove_permission,
    requires_mfa,
    revoke_role,
    set_principal_status,
    upsert_principal,
)


# ── helpers ────────────────────────────────────────────────────────────────────


def _has_db() -> bool:
    return bool(os.getenv('MIRROR_DATABASE_URL') or os.getenv('DATABASE_URL'))


def _uid(prefix: str = 'p') -> str:
    return f'test-{prefix}-{uuid.uuid4().hex[:8]}'


db = pytest.mark.skipif(not _has_db(), reason='Mirror DB not configured')


# ── Unit: Principal model ──────────────────────────────────────────────────────


class TestPrincipalModel:
    def test_valid(self) -> None:
        now = datetime.now(timezone.utc)
        p = Principal(
            id='pid-abc',
            tenant_id='default',
            email='hadi@example.com',
            display_name='Hadi',
            principal_type='human',
            status='active',
            mfa_required=False,
            last_login_at=None,
            created_at=now,
        )
        assert p.principal_type == 'human'
        assert p.status == 'active'

    def test_frozen(self) -> None:
        now = datetime.now(timezone.utc)
        p = Principal(
            id='pid-x',
            tenant_id='default',
            email=None,
            display_name=None,
            principal_type='agent',
            status='active',
            mfa_required=False,
            last_login_at=None,
            created_at=now,
        )
        with pytest.raises(ValidationError):
            p.status = 'suspended'  # type: ignore[misc]

    def test_invalid_status(self) -> None:
        now = datetime.now(timezone.utc)
        with pytest.raises(ValidationError):
            Principal(
                id='x', tenant_id='default', email=None, display_name=None,
                principal_type='human', status='deleted',  # type: ignore[arg-type]
                mfa_required=False, last_login_at=None, created_at=now,
            )

    def test_invalid_type(self) -> None:
        now = datetime.now(timezone.utc)
        with pytest.raises(ValidationError):
            Principal(
                id='x', tenant_id='default', email=None, display_name=None,
                principal_type='robot',  # type: ignore[arg-type]
                status='active', mfa_required=False, last_login_at=None, created_at=now,
            )


# ── Unit: Role model ───────────────────────────────────────────────────────────


class TestRoleModel:
    def test_valid(self) -> None:
        now = datetime.now(timezone.utc)
        r = Role(
            id='role:sos:builder',
            project_id='sos',
            tenant_id='default',
            name='builder',
            description='Implementation agent',
            mfa_required=False,
            created_at=now,
        )
        assert r.name == 'builder'

    def test_frozen(self) -> None:
        now = datetime.now(timezone.utc)
        r = Role(
            id='role:sos:gate',
            project_id='sos',
            tenant_id='default',
            name='gate',
            description=None,
            mfa_required=True,
            created_at=now,
        )
        with pytest.raises(ValidationError):
            r.mfa_required = False  # type: ignore[misc]


# ── Unit: seed role guard ──────────────────────────────────────────────────────


class TestSeedRoleGuard:
    def test_add_permission_to_seed_raises(self) -> None:
        with pytest.raises(ValueError, match='immutable'):
            add_permission('role:sos:builder', 'some_permission')

    def test_remove_permission_from_seed_raises(self) -> None:
        with pytest.raises(ValueError, match='immutable'):
            remove_permission('role:sos:coordinator', 'some_permission')


# ── Integration: principal CRUD ────────────────────────────────────────────────


@db
class TestUpsertPrincipal:
    def test_create_with_email(self) -> None:
        email = f'{_uid()}@test.local'
        p = upsert_principal(email=email, display_name='Test User')
        assert p.email == email
        assert p.status == 'active'
        assert p.principal_type == 'human'

    def test_idempotent_on_email(self) -> None:
        email = f'{_uid()}@test.local'
        p1 = upsert_principal(email=email, display_name='Alice')
        p2 = upsert_principal(email=email, display_name='Alice Updated')
        assert p1.id == p2.id

    def test_create_agent_without_email(self) -> None:
        pid = _uid('agent')
        p = upsert_principal(principal_id=pid, principal_type='agent', display_name='test-agent')
        assert p.id == pid
        assert p.email is None
        assert p.principal_type == 'agent'

    def test_get_by_id(self) -> None:
        email = f'{_uid()}@test.local'
        p1 = upsert_principal(email=email)
        p2 = get_principal(p1.id)
        assert p2 is not None
        assert p2.id == p1.id

    def test_get_missing_returns_none(self) -> None:
        assert get_principal('nonexistent-pid') is None

    def test_get_by_email(self) -> None:
        email = f'{_uid()}@test.local'
        p1 = upsert_principal(email=email)
        p2 = get_principal_by_email(email)
        assert p2 is not None
        assert p2.id == p1.id

    def test_set_status_suspended(self) -> None:
        email = f'{_uid()}@test.local'
        p = upsert_principal(email=email)
        set_principal_status(p.id, 'suspended', updated_by='test')
        p2 = get_principal(p.id)
        assert p2 is not None
        assert p2.status == 'suspended'


# ── Integration: role CRUD ─────────────────────────────────────────────────────


@db
class TestCreateRole:
    def test_create_role(self) -> None:
        proj = _uid('proj')
        r = create_role(project_id=proj, name='analyst', description='Read-only analyst')
        assert r.name == 'analyst'
        assert r.project_id == proj
        assert r.mfa_required is False

    def test_create_with_mfa_required(self) -> None:
        proj = _uid('proj')
        r = create_role(project_id=proj, name='admin', mfa_required=True)
        assert r.mfa_required is True

    def test_create_idempotent(self) -> None:
        proj = _uid('proj')
        r1 = create_role(project_id=proj, name='viewer')
        r2 = create_role(project_id=proj, name='viewer')
        assert r1.id == r2.id

    def test_list_roles(self) -> None:
        proj = _uid('proj')
        create_role(project_id=proj, name='alpha')
        create_role(project_id=proj, name='beta')
        roles = list_roles(proj)
        assert len(roles) >= 2
        assert all(r.project_id == proj for r in roles)

    def test_seed_roles_present(self) -> None:
        """Seed roles from migration 023 must exist."""
        roles = list_roles('sos', tenant_id='default')
        names = {r.name for r in roles}
        assert 'builder' in names
        assert 'coordinator' in names
        assert 'gate' in names


@db
class TestRolePermissions:
    def test_add_and_get_permission(self) -> None:
        proj = _uid('proj')
        r = create_role(project_id=proj, name='tester')
        add_permission(r.id, 'read:tasks')
        perms = get_role_permissions(r.id)
        assert 'read:tasks' in perms

    def test_add_permission_idempotent(self) -> None:
        proj = _uid('proj')
        r = create_role(project_id=proj, name='dup-perm')
        add_permission(r.id, 'write:tasks')
        add_permission(r.id, 'write:tasks')
        assert get_role_permissions(r.id).count('write:tasks') == 1

    def test_remove_permission(self) -> None:
        proj = _uid('proj')
        r = create_role(project_id=proj, name='rm-perm')
        add_permission(r.id, 'delete:tasks')
        remove_permission(r.id, 'delete:tasks')
        assert 'delete:tasks' not in get_role_permissions(r.id)


@db
class TestRoleAssignments:
    def test_assign_role(self) -> None:
        proj = _uid('proj')
        r = create_role(project_id=proj, name='assigner')
        email = f'{_uid()}@test.local'
        p = upsert_principal(email=email)
        ra = assign_role(r.id, p.id, assignee_type='human', assigned_by='test')
        assert ra.role_id == r.id
        assert ra.assignee_id == p.id

    def test_assign_idempotent(self) -> None:
        proj = _uid('proj')
        r = create_role(project_id=proj, name='idm-assign')
        email = f'{_uid()}@test.local'
        p = upsert_principal(email=email)
        ra1 = assign_role(r.id, p.id, assigned_by='test')
        ra2 = assign_role(r.id, p.id, assigned_by='test2')
        assert ra1.role_id == ra2.role_id

    def test_get_role_assignments(self) -> None:
        proj = _uid('proj')
        r = create_role(project_id=proj, name='get-assign')
        email = f'{_uid()}@test.local'
        p = upsert_principal(email=email)
        assign_role(r.id, p.id, assigned_by='test')
        assignments = get_role_assignments(r.id)
        assert any(a.assignee_id == p.id for a in assignments)

    def test_get_assignee_roles(self) -> None:
        proj = _uid('proj')
        r1 = create_role(project_id=proj, name='r1')
        r2 = create_role(project_id=proj, name='r2')
        email = f'{_uid()}@test.local'
        p = upsert_principal(email=email)
        assign_role(r1.id, p.id, assigned_by='test')
        assign_role(r2.id, p.id, assigned_by='test')
        roles = get_assignee_roles(p.id)
        ids = {r.id for r in roles}
        assert r1.id in ids
        assert r2.id in ids

    def test_revoke_role(self) -> None:
        proj = _uid('proj')
        r = create_role(project_id=proj, name='revoke-me')
        email = f'{_uid()}@test.local'
        p = upsert_principal(email=email)
        assign_role(r.id, p.id, assigned_by='test')
        deleted = revoke_role(r.id, p.id, revoked_by='test')
        assert deleted is True
        assignments = get_role_assignments(r.id)
        assert not any(a.assignee_id == p.id for a in assignments)

    def test_revoke_missing_returns_false(self) -> None:
        proj = _uid('proj')
        r = create_role(project_id=proj, name='revoke-miss')
        assert revoke_role(r.id, 'nonexistent-pid', revoked_by='test') is False


@db
class TestRequiresMfa:
    def test_no_mfa_roles(self) -> None:
        proj = _uid('proj')
        r = create_role(project_id=proj, name='no-mfa', mfa_required=False)
        email = f'{_uid()}@test.local'
        p = upsert_principal(email=email)
        assign_role(r.id, p.id, assigned_by='test')
        assert requires_mfa(p.id) is False

    def test_mfa_required_role(self) -> None:
        proj = _uid('proj')
        r = create_role(project_id=proj, name='mfa-role', mfa_required=True)
        email = f'{_uid()}@test.local'
        p = upsert_principal(email=email)
        assign_role(r.id, p.id, assigned_by='test')
        assert requires_mfa(p.id) is True

    def test_no_assignments(self) -> None:
        email = f'{_uid()}@test.local'
        p = upsert_principal(email=email)
        assert requires_mfa(p.id) is False
