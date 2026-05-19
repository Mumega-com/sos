"""Tests for customer intake → knight spawn pipeline.

Spec: SOS/docs/superpowers/specs/2026-04-24-customer-intake-design.md
13 tests covering: create, mint guards, approve/reject, GHL webhook, validation,
role seeding, and system-bearer-only auth gates.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Generator
from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from sos.services.squad.intake import (
    CustomerIntakeService,
    IntakeNotFoundError,
    IntakeStatusError,
    MintFailedError,
    validate_initial_roles,
)
from sos.services.squad.service import DEFAULT_TENANT_ID, SquadDB, now_iso


# ---------------------------------------------------------------------------
# Schema — create tables for intake tests (no need for full alembic)
# ---------------------------------------------------------------------------

_INTAKE_DDL = """
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
);

CREATE INDEX IF NOT EXISTS idx_customer_intakes_status
ON customer_intakes (status);

CREATE INDEX IF NOT EXISTS idx_customer_intakes_slug
ON customer_intakes (customer_slug);
"""

_ROLES_DDL = """
CREATE TABLE IF NOT EXISTS roles (
    id          TEXT PRIMARY KEY,
    project_id  TEXT NOT NULL,
    tenant_id   TEXT NOT NULL DEFAULT 'default',
    name        TEXT NOT NULL,
    description TEXT,
    created_at  TEXT NOT NULL,
    UNIQUE(project_id, name, tenant_id)
);

CREATE TABLE IF NOT EXISTS role_permissions (
    role_id     TEXT NOT NULL REFERENCES roles(id),
    permission  TEXT NOT NULL,
    PRIMARY KEY (role_id, permission)
);

CREATE TABLE IF NOT EXISTS role_assignments (
    role_id       TEXT NOT NULL REFERENCES roles(id),
    assignee_id   TEXT NOT NULL,
    assignee_type TEXT NOT NULL DEFAULT 'agent',
    assigned_at   TEXT NOT NULL,
    assigned_by   TEXT NOT NULL,
    PRIMARY KEY (role_id, assignee_id)
);
"""


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture()
def db(tmp_path: Path) -> SquadDB:
    db_path = tmp_path / "test_intake.db"
    database = SquadDB(db_path=db_path)
    with database.connect() as conn:
        conn.executescript(_INTAKE_DDL)
    return database


@pytest.fixture()
def db_with_roles(tmp_path: Path) -> SquadDB:
    """DB with both intake and roles tables (simulates 0011+0012 applied)."""
    db_path = tmp_path / "test_intake_roles.db"
    database = SquadDB(db_path=db_path)
    with database.connect() as conn:
        conn.executescript(_INTAKE_DDL + _ROLES_DDL)
    return database


@pytest.fixture()
def svc(db: SquadDB) -> CustomerIntakeService:
    return CustomerIntakeService(db=db)


@pytest.fixture()
def svc_with_roles(db_with_roles: SquadDB) -> CustomerIntakeService:
    return CustomerIntakeService(db=db_with_roles)


def _make_intake(svc: CustomerIntakeService, slug: str = "test-co", cause: str = "") -> dict:
    return svc.create_intake(
        customer_name="Test Co",
        customer_slug=slug,
        cause_draft=cause,
    )


# ---------------------------------------------------------------------------
# Test 1: Create intake → status=pending
# ---------------------------------------------------------------------------

def test_create_intake_status_pending(svc: CustomerIntakeService) -> None:
    intake = svc.create_intake(
        customer_name="Acme Corp",
        customer_slug="acme",
        domain="acme.com",
        icp="SMB",
    )
    assert intake["status"] == "pending"
    assert intake["customer_slug"] == "acme"
    assert intake["source"] == "direct"
    assert intake["knight_name"] is None


# ---------------------------------------------------------------------------
# Test 2: Mint on pending → 409 (IntakeStatusError)
# ---------------------------------------------------------------------------

def test_mint_on_pending_raises_status_error(svc: CustomerIntakeService) -> None:
    intake = _make_intake(svc, cause="We build capital matching tools.")
    with pytest.raises(IntakeStatusError, match="approved"):
        svc.mint(intake["id"])


# ---------------------------------------------------------------------------
# Test 3: Approve → mint → status=minted, knight_name set, roles seeded
# ---------------------------------------------------------------------------

def test_approve_then_mint_success(svc_with_roles: CustomerIntakeService) -> None:
    svc = svc_with_roles
    intake = svc.create_intake(
        customer_name="GAF Corp",
        customer_slug="gaf-corp",
        cause_draft="We match Canadian SMBs with non-dilutive capital.",
        initial_roles_json='["advisor","intern"]',
    )
    svc.approve(intake["id"], approver_agent_id="system")

    mock_result = MagicMock()
    mock_result.returncode = 0
    mock_result.stdout = "minted"
    mock_result.stderr = ""

    with patch("subprocess.run", return_value=mock_result):
        result = svc.mint(intake["id"])

    assert result["knight_name"] == "gaf-corp"
    assert result["roles_seeded"] == 2  # advisor + intern

    updated = svc.get_intake(intake["id"])
    assert updated["status"] == "minted"
    assert updated["knight_name"] == "gaf-corp"
    assert updated["minted_at"] is not None
    assert updated["mint_error"] is None


# ---------------------------------------------------------------------------
# Test 4: Mint with empty cause_draft → 422 (ValueError)
# ---------------------------------------------------------------------------

def test_mint_empty_cause_raises_value_error(svc: CustomerIntakeService) -> None:
    intake = svc.create_intake(
        customer_name="Empty Cause Co",
        customer_slug="empty-cause",
        cause_draft="",  # empty
    )
    svc.approve(intake["id"], approver_agent_id="system")
    with pytest.raises(ValueError, match="cause_draft"):
        svc.mint(intake["id"])


# ---------------------------------------------------------------------------
# Test 5: Mint failure → status=failed, mint_error populated; retry succeeds
# ---------------------------------------------------------------------------

def test_mint_failure_sets_failed_status_and_retry_succeeds(svc: CustomerIntakeService) -> None:
    intake = svc.create_intake(
        customer_name="Retry Co",
        customer_slug="retry-co",
        cause_draft="We retry until we succeed.",
    )
    svc.approve(intake["id"], approver_agent_id="system")

    failing_result = MagicMock()
    failing_result.returncode = 1
    failing_result.stdout = ""
    failing_result.stderr = "Error: bad slug format\n"

    with patch("subprocess.run", return_value=failing_result):
        with pytest.raises(MintFailedError):
            svc.mint(intake["id"])

    failed = svc.get_intake(intake["id"])
    assert failed["status"] == "failed"
    assert "bad slug" in (failed["mint_error"] or "")

    # Set status back to approved (retry path)
    with svc.db.connect() as conn:
        conn.execute(
            "UPDATE customer_intakes SET status = 'approved' WHERE id = ?",
            (intake["id"],),
        )

    success_result = MagicMock()
    success_result.returncode = 0
    success_result.stdout = "minted"
    success_result.stderr = ""

    with patch("subprocess.run", return_value=success_result):
        result = svc.mint(intake["id"])

    assert result["knight_name"] == "retry-co"
    minted = svc.get_intake(intake["id"])
    assert minted["status"] == "minted"


# ---------------------------------------------------------------------------
# Test 6: PATCH pre-approval updates; PATCH post-approval → 409
# ---------------------------------------------------------------------------

def test_update_pre_approval_ok_post_approval_blocked(svc: CustomerIntakeService) -> None:
    intake = svc.create_intake(
        customer_name="Edit Co",
        customer_slug="edit-co",
    )
    # Pre-approval update should succeed
    updated = svc.update_intake(intake["id"], {"cause_draft": "Updated cause."})
    assert updated["cause_draft"] == "Updated cause."

    svc.approve(intake["id"], approver_agent_id="system")

    # Post-approval update should fail
    with pytest.raises(IntakeStatusError, match="pending"):
        svc.update_intake(intake["id"], {"cause_draft": "Should be blocked."})


# ---------------------------------------------------------------------------
# Test 7: Approve with non-system token → 403
# ---------------------------------------------------------------------------

def test_approve_requires_system_bearer(tmp_path: Path) -> None:
    """HTTP-level test: non-system bearer gets 403 on approve."""
    db_path = tmp_path / "test_http.db"
    database = SquadDB(db_path=db_path)
    with database.connect() as conn:
        conn.executescript(_INTAKE_DDL)

    # Patch the service used by app
    with patch("sos.services.squad.app._intake_svc", CustomerIntakeService(db=database)):
        from sos.services.squad.app import app as squad_app
        client = TestClient(squad_app)

        # Create intake with system bearer
        sys_token = os.getenv("SOS_SYSTEM_TOKEN", "sk-sos-system")
        resp = client.post(
            "/customers/intake",
            json={"customer_name": "Auth Test", "customer_slug": "auth-test"},
            headers={"Authorization": f"Bearer {sys_token}"},
        )
        assert resp.status_code == 200
        intake_id = resp.json()["id"]

        # Approve with a non-system token → 403
        resp = client.post(
            f"/customers/{intake_id}/approve",
            headers={"Authorization": "Bearer sk-project-owner-token"},
        )
        assert resp.status_code == 403


# ---------------------------------------------------------------------------
# Test 8: Approve → mint → mint again → 409 (already minted)
# ---------------------------------------------------------------------------

def test_double_mint_raises_status_error(svc: CustomerIntakeService) -> None:
    intake = svc.create_intake(
        customer_name="Once Only Co",
        customer_slug="once-only",
        cause_draft="One mint is enough.",
    )
    svc.approve(intake["id"], approver_agent_id="system")

    success_result = MagicMock()
    success_result.returncode = 0
    success_result.stdout = "minted"
    success_result.stderr = ""

    with patch("subprocess.run", return_value=success_result):
        svc.mint(intake["id"])

    with pytest.raises(IntakeStatusError, match="already minted"):
        with patch("subprocess.run", return_value=success_result):
            svc.mint(intake["id"])


# ---------------------------------------------------------------------------
# Test 9: GHL webhook valid secret → intake row, source=ghl
# ---------------------------------------------------------------------------

def test_ghl_webhook_valid_secret_creates_intake(tmp_path: Path) -> None:
    db_path = tmp_path / "test_ghl.db"
    database = SquadDB(db_path=db_path)
    with database.connect() as conn:
        conn.executescript(_INTAKE_DDL)

    with patch("sos.services.squad.app._intake_svc", CustomerIntakeService(db=database)), \
         patch("sos.services.squad.app._GHL_WEBHOOK_SECRET", "test-secret-abc"):
        from sos.services.squad.app import app as squad_app
        client = TestClient(squad_app)

        resp = client.post(
            "/webhooks/ghl/lead",
            json={
                "contact_id": "ghl-123",
                "company": "Maple Capital Inc",
                "domain": "maplecapital.ca",
                "custom_fields": {"icp": "Canadian SMB"},
            },
            headers={"X-GHL-Secret": "test-secret-abc"},
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["status"] == "created"
        assert body["intake_id"]

        # Verify row in DB
        intake = CustomerIntakeService(db=database).get_intake(body["intake_id"])
        assert intake["source"] == "ghl"
        assert intake["ghl_contact_id"] == "ghl-123"
        assert intake["status"] == "pending"


# ---------------------------------------------------------------------------
# Test 10: GHL webhook bad secret → 401
# ---------------------------------------------------------------------------

def test_ghl_webhook_bad_secret_returns_401(tmp_path: Path) -> None:
    db_path = tmp_path / "test_ghl_bad.db"
    database = SquadDB(db_path=db_path)
    with database.connect() as conn:
        conn.executescript(_INTAKE_DDL)

    with patch("sos.services.squad.app._intake_svc", CustomerIntakeService(db=database)), \
         patch("sos.services.squad.app._GHL_WEBHOOK_SECRET", "correct-secret"):
        from sos.services.squad.app import app as squad_app
        client = TestClient(squad_app)

        resp = client.post(
            "/webhooks/ghl/lead",
            json={"company": "Sneaky Corp"},
            headers={"X-GHL-Secret": "wrong-secret"},
        )
        assert resp.status_code == 401


# ---------------------------------------------------------------------------
# Test 11: initial_roles_json invalid JSON → 422 at create time
# ---------------------------------------------------------------------------

def test_invalid_initial_roles_json_raises_at_create(svc: CustomerIntakeService) -> None:
    with pytest.raises(ValueError, match="valid JSON"):
        svc.create_intake(
            customer_name="Bad Roles Co",
            customer_slug="bad-roles",
            initial_roles_json="not-valid-json",
        )


def test_initial_roles_too_many_items(svc: CustomerIntakeService) -> None:
    many = json.dumps([f"role-{i}" for i in range(11)])
    with pytest.raises(ValueError, match="10"):
        validate_initial_roles(many)


def test_initial_roles_invalid_chars() -> None:
    with pytest.raises(ValueError, match=r"\^"):
        validate_initial_roles('["UPPERCASE"]')


# ---------------------------------------------------------------------------
# Test 12: Roles seeded after mint — advisor + intern present in roles table
# ---------------------------------------------------------------------------

def test_roles_seeded_after_mint(svc_with_roles: CustomerIntakeService) -> None:
    svc = svc_with_roles
    intake = svc.create_intake(
        customer_name="Seeded Co",
        customer_slug="seeded-co",
        cause_draft="Role seeding test.",
        initial_roles_json='["advisor","intern"]',
    )
    svc.approve(intake["id"], approver_agent_id="system")

    success_result = MagicMock()
    success_result.returncode = 0
    success_result.stdout = ""
    success_result.stderr = ""

    with patch("subprocess.run", return_value=success_result):
        result = svc.mint(intake["id"])

    assert result["roles_seeded"] == 2

    # Verify roles exist in DB
    with svc.db.connect() as conn:
        roles = conn.execute(
            "SELECT id, name FROM roles WHERE project_id = ?", ("seeded-co",)
        ).fetchall()
        role_names = {r["name"] for r in roles}

    assert "advisor" in role_names
    assert "intern" in role_names

    # Verify permissions
    with svc.db.connect() as conn:
        advisor_role_id = f"seeded-co-advisor"
        perms = conn.execute(
            "SELECT permission FROM role_permissions WHERE role_id = ?",
            (advisor_role_id,),
        ).fetchall()
        perm_set = {p["permission"] for p in perms}

    assert "inkwell:read:role" in perm_set
    assert "inkwell:write:project" in perm_set


# ---------------------------------------------------------------------------
# Test 13: seed-roles retry when minted but roles absent
# ---------------------------------------------------------------------------

def test_seed_roles_retry_when_minted(svc_with_roles: CustomerIntakeService) -> None:
    svc = svc_with_roles
    intake = svc.create_intake(
        customer_name="Retry Seed Co",
        customer_slug="retry-seed",
        cause_draft="Retry seeding test.",
        initial_roles_json='["advisor","intern"]',
    )
    svc.approve(intake["id"], approver_agent_id="system")

    success_result = MagicMock()
    success_result.returncode = 0
    success_result.stdout = ""
    success_result.stderr = ""

    # Mint without seeding (mock seed to return 0 on first call)
    with patch("subprocess.run", return_value=success_result), \
         patch.object(svc, "_seed_roles_for_intake", return_value=0):
        svc.mint(intake["id"])

    # Verify minted but roles absent
    with svc.db.connect() as conn:
        count = conn.execute(
            "SELECT COUNT(*) FROM roles WHERE project_id = ?", ("retry-seed",)
        ).fetchone()[0]
    assert count == 0

    # Retry via seed_roles endpoint
    result = svc.seed_roles(intake["id"])
    assert result["roles_seeded"] == 2

    with svc.db.connect() as conn:
        count = conn.execute(
            "SELECT COUNT(*) FROM roles WHERE project_id = ?", ("retry-seed",)
        ).fetchone()[0]
    assert count == 2
