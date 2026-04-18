"""Tests for sos.kernel.policy.gate — v0.5.1 policy gate.

asyncio_mode="auto" is set in pyproject.toml; do NOT add @pytest.mark.asyncio.

Patch target for verify_bearer is sos.kernel.policy.gate.verify_bearer because
gate.py does `from sos.kernel.auth import verify_bearer` (name-binding import).
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from sos.contracts.policy import PolicyDecision
from sos.kernel.auth import AuthContext
from sos.kernel.policy.gate import can_execute

# Correct patch target — gate.py binds the name at import time.
_VERIFY_BEARER = "sos.kernel.policy.gate.verify_bearer"


# ---------------------------------------------------------------------------
# 1. System token allows cross-tenant access
# ---------------------------------------------------------------------------

async def test_system_token_allows_cross_tenant(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """SOS_SYSTEM_TOKEN bearer → is_system=True → allowed for any tenant."""
    import sos.kernel.audit as audit
    monkeypatch.setattr(audit, "_audit_dir", lambda: tmp_path / "audit")
    monkeypatch.setenv("SOS_SYSTEM_TOKEN", "sys-123")

    decision = await can_execute(
        action="x",
        resource="y",
        tenant="othertenant",
        authorization="Bearer sys-123",
    )

    assert decision.allowed is True
    # gate.py: reason includes the scope reason "system/admin scope" for system tokens.
    assert "system/admin" in decision.reason
    assert decision.tier != "denied"


# ---------------------------------------------------------------------------
# 2. Scoped token allows own tenant
# ---------------------------------------------------------------------------

async def test_scoped_token_allows_own_tenant(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Token scoped to 'acme' → allowed for tenant='acme'."""
    import sos.kernel.audit as audit
    monkeypatch.setattr(audit, "_audit_dir", lambda: tmp_path / "audit")

    auth_ctx = AuthContext(agent="alice", project="acme", is_system=False, is_admin=False)

    with patch(_VERIFY_BEARER, return_value=auth_ctx):
        decision = await can_execute(
            action="x",
            resource="y",
            tenant="acme",
            authorization="Bearer tok",
        )

    assert decision.allowed is True


# ---------------------------------------------------------------------------
# 3. Scoped token denies other tenant
# ---------------------------------------------------------------------------

async def test_scoped_token_denies_other_tenant(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Token scoped to 'acme' → denied for tenant='othertenant'."""
    import sos.kernel.audit as audit
    monkeypatch.setattr(audit, "_audit_dir", lambda: tmp_path / "audit")

    auth_ctx = AuthContext(agent="alice", project="acme", is_system=False, is_admin=False)

    with patch(_VERIFY_BEARER, return_value=auth_ctx):
        decision = await can_execute(
            action="x",
            resource="y",
            tenant="othertenant",
            authorization="Bearer tok",
        )

    assert decision.allowed is False
    # gate.py: f"token scoped to '{scope}', not '{tenant}'" — contains "scoped"
    assert "scoped" in decision.reason
    assert "tenant_scope" in decision.pillars_failed


# ---------------------------------------------------------------------------
# 4. Token with no tenant scope is denied
# ---------------------------------------------------------------------------

async def test_no_scope_token_denied(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Token with no project/tenant_slug → denied with 'no tenant scope' reason."""
    import sos.kernel.audit as audit
    monkeypatch.setattr(audit, "_audit_dir", lambda: tmp_path / "audit")

    auth_ctx = AuthContext(agent="ghost", project=None, tenant_slug=None, is_system=False, is_admin=False)

    with patch(_VERIFY_BEARER, return_value=auth_ctx):
        decision = await can_execute(
            action="x",
            resource="y",
            tenant="anyplace",
            authorization="Bearer tok",
        )

    assert decision.allowed is False
    # gate.py: "token has no tenant scope" — contains "no tenant scope"
    assert "no tenant scope" in decision.reason


# ---------------------------------------------------------------------------
# 5. Invalid bearer is denied
# ---------------------------------------------------------------------------

async def test_invalid_bearer_denied(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """verify_bearer returns None → denied with 'invalid' reason and 'auth' in pillars_failed."""
    import sos.kernel.audit as audit
    monkeypatch.setattr(audit, "_audit_dir", lambda: tmp_path / "audit")

    with patch(_VERIFY_BEARER, return_value=None):
        decision = await can_execute(
            action="x",
            resource="y",
            tenant="mumega",
            authorization="Bearer bad",
        )

    assert decision.allowed is False
    assert "invalid" in decision.reason
    assert "auth" in decision.pillars_failed


# ---------------------------------------------------------------------------
# 6. No authorization header → kernel-internal path → allowed
# ---------------------------------------------------------------------------

async def test_no_authorization_allows(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """authorization=None skips bearer+scope, runs FMAAP/tier → allowed."""
    import sos.kernel.audit as audit
    monkeypatch.setattr(audit, "_audit_dir", lambda: tmp_path / "audit")

    decision = await can_execute(
        action="x",
        resource="y",
        tenant="mumega",
        authorization=None,
    )

    assert decision.allowed is True


# ---------------------------------------------------------------------------
# 7. Audit event is written with kind=policy_decision and correct action
# ---------------------------------------------------------------------------

async def test_audit_event_written(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """can_execute writes a JSONL audit event with kind=policy_decision and action=x."""
    import sos.kernel.audit as audit
    monkeypatch.setattr(audit, "_audit_dir", lambda: tmp_path)

    decision = await can_execute(
        action="x",
        resource="y",
        tenant="mumega",
        authorization=None,
    )

    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    audit_file = tmp_path / "mumega" / f"{today}.jsonl"

    assert audit_file.exists(), f"Expected audit file at {audit_file}"

    lines = [json.loads(line) for line in audit_file.read_text().splitlines() if line.strip()]
    assert any(
        line.get("kind") == "policy_decision" and line.get("action") == "x"
        for line in lines
    ), f"No matching audit line found. Lines: {lines}"

    assert decision.audit_id is not None
    assert len(decision.audit_id) > 0
