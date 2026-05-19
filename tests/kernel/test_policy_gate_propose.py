"""Tests for sos.kernel.policy.gate — v0.5.2 propose_first arbitration path.

asyncio_mode="auto" is set in pyproject.toml; do NOT add @pytest.mark.asyncio.
Patch target for verify_bearer is sos.kernel.policy.gate.verify_bearer.
"""
from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest

from sos.contracts.audit import AuditEventKind
from sos.kernel.arbitration import propose_intent
from sos.kernel.audit import read_events
from sos.kernel.auth import AuthContext
from sos.kernel.policy.gate import can_execute

_VERIFY_BEARER = "sos.kernel.policy.gate.verify_bearer"


def _stub_auth(agent: str, tenant: str) -> AuthContext:
    return AuthContext(agent=agent, project=tenant, is_system=False, is_admin=False)


def _setup_audit(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    import sos.kernel.audit as audit
    audit_dir = tmp_path / "audit"
    audit_dir.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(audit, "_audit_dir", lambda: audit_dir)


# ---------------------------------------------------------------------------
# 1. Single proposer wins arbitration → allowed
# ---------------------------------------------------------------------------

async def test_propose_first_winner_allowed(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    _setup_audit(monkeypatch, tmp_path)
    auth = _stub_auth("alpha", "mumega")

    with patch(_VERIFY_BEARER, return_value=auth):
        decision = await can_execute(
            agent="alpha",
            action="publish",
            resource="post_42",
            tenant="mumega",
            authorization="Bearer tok",
            propose_first=True,
            priority=5,
        )

    assert decision.allowed is True
    assert "arbitration" in (decision.pillars_passed or [])
    assert decision.metadata.get("arbitration_winner") == "alpha"


# ---------------------------------------------------------------------------
# 2. Lower-priority proposer loses to a pre-seeded higher-priority proposal
# ---------------------------------------------------------------------------

async def test_propose_first_loser_denied(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    _setup_audit(monkeypatch, tmp_path)

    # Seed beta with priority=10 first.
    await propose_intent(
        agent="beta",
        action="publish",
        resource="post_42",
        tenant="mumega",
        priority=10,
    )

    auth = _stub_auth("alpha", "mumega")
    with patch(_VERIFY_BEARER, return_value=auth):
        decision = await can_execute(
            agent="alpha",
            action="publish",
            resource="post_42",
            tenant="mumega",
            authorization="Bearer tok",
            propose_first=True,
            priority=1,
            window_ms=5000,
        )

    assert decision.allowed is False
    assert "arbitration" in (decision.pillars_failed or [])
    # reason must name the winner or the word "arbitration"
    assert "beta" in decision.reason or "arbitration" in decision.reason


# ---------------------------------------------------------------------------
# 3. propose_first=False (default) — arbitration path never runs
# ---------------------------------------------------------------------------

async def test_propose_first_default_false_unchanged(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    _setup_audit(monkeypatch, tmp_path)

    await can_execute(
        agent="system",
        action="noop",
        resource="thing_99",
        tenant="mumega",
        authorization=None,
        # propose_first omitted — defaults to False
    )

    # No INTENT event with metadata.arbitration==True should exist for this resource.
    events = read_events("mumega", kind=AuditEventKind.INTENT)
    arb_events = [
        ev for ev in events
        if ev.target == "thing_99" and ev.metadata.get("arbitration") is True
    ]
    assert arb_events == [], f"Unexpected arbitration INTENT events: {arb_events}"
