"""Regression tests for the identity pre-gate audit-gap fix (v0.5.6.1).

Two auth-protected avatar endpoints in ``sos.services.identity.app`` perform
three short-circuit checks *before* the unified policy gate runs:

1. Missing Authorization header               → 401, reason="missing bearer token"
2. ``verify_bearer`` returns None             → 401, reason="invalid or inactive token"
3. Verified context has no tenant scope
   and is not system/admin                    → 403, reason="token has no tenant scope"

Without the ``_emit_identity_deny`` helper, none of these pre-gate rejections
would hit the audit spine (the gate itself never runs). These tests pin the
contract for both endpoints so the gap cannot silently reopen:

- POST /avatar/generate               → action="identity:avatar_generate"
- POST /avatar/social/on_alpha_drift  → action="identity:avatar_social_drift"

Each rejected request must emit exactly one ``AuditEvent`` with
``kind=POLICY_DECISION``, ``decision=DENY``, ``policy_tier='identity_pregate'``,
``agent='anonymous'``, and the expected action/target.
"""
from __future__ import annotations

from typing import Any, Dict

import pytest
from httpx import ASGITransport, AsyncClient

from sos.contracts.audit import AuditDecision, AuditEvent, AuditEventKind
from sos.kernel.auth import AuthContext
from sos.services.identity.app import app


# ---------------------------------------------------------------------------
# Request bodies — full UV16D dict so pydantic validation succeeds even
# before the auth short-circuit fires (FastAPI validates body first).
# ---------------------------------------------------------------------------


_UV_FULL: Dict[str, float] = {
    "logos": 0.5,
    "harmonia": 0.5,
    "telos": 0.5,
    "nous": 0.5,
    "mythos": 0.5,
    "khaos": 0.5,
    "kenosis": 0.5,
    "chronos": 0.5,
    "eros": 0.5,
    "thanatos": 0.5,
    "hubris": 0.5,
    "humilitas": 0.5,
    "gnosis": 0.5,
    "agnoia": 0.5,
    "poiesis": 0.5,
    "analusis": 0.5,
}

_AGENT_ID = "test-agent"

_GENERATE_BODY: Dict[str, Any] = {"agent_id": _AGENT_ID, "uv": _UV_FULL}
_DRIFT_BODY: Dict[str, Any] = {
    "agent_id": _AGENT_ID,
    "uv": _UV_FULL,
    "alpha_value": 0.7,
    "insight": "test",
}


# ---------------------------------------------------------------------------
# Fixture — intercept AuditEvents emitted by the pre-gate helper
# ---------------------------------------------------------------------------


@pytest.fixture
def captured_events(monkeypatch: pytest.MonkeyPatch) -> list[AuditEvent]:
    """Capture ``append_event`` calls so audit writes never touch disk/Redis.

    The identity service imports ``append_event`` lazily inside
    ``_emit_identity_deny`` (``from sos.kernel.audit import append_event,
    new_event``), so patching the source module is sufficient.
    """
    events: list[AuditEvent] = []

    async def _fake_append(event: AuditEvent) -> str:
        events.append(event)
        return event.id

    monkeypatch.setattr("sos.kernel.audit.append_event", _fake_append)
    return events


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _assert_pregate_deny(
    event: AuditEvent,
    *,
    action: str,
    target: str,
) -> None:
    """Shared assertions for every pre-gate deny event."""
    assert event.kind == AuditEventKind.POLICY_DECISION, (
        f"expected POLICY_DECISION, got {event.kind}"
    )
    assert event.decision == AuditDecision.DENY, (
        f"expected DENY decision, got {event.decision}"
    )
    assert event.policy_tier == "identity_pregate", (
        f"expected policy_tier='identity_pregate', got {event.policy_tier!r}"
    )
    assert event.agent == "anonymous", (
        f"expected agent='anonymous', got {event.agent!r}"
    )
    assert event.action == action, (
        f"expected action={action!r}, got {event.action!r}"
    )
    assert event.target == target, (
        f"expected target={target!r}, got {event.target!r}"
    )


def _scopeless_ctx() -> AuthContext:
    """Verified but unscoped token: no tenant, not system, not admin."""
    return AuthContext(
        agent=None,
        project=None,
        tenant_slug=None,
        is_system=False,
        is_admin=False,
        label="unit-test-scopeless",
        raw_token_hash=None,
        env_source=None,
    )


# ---------------------------------------------------------------------------
# POST /avatar/generate
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_avatar_generate_missing_bearer_audits_deny(
    captured_events: list[AuditEvent],
) -> None:
    """No Authorization header → 401 + one pre-gate deny event."""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as client:
        response = await client.post("/avatar/generate", json=_GENERATE_BODY)

    assert response.status_code == 401, (
        f"expected 401, got {response.status_code}: {response.text}"
    )
    assert len(captured_events) == 1, (
        f"expected exactly 1 audit event, got {len(captured_events)}"
    )
    _assert_pregate_deny(
        captured_events[0],
        action="identity:avatar_generate",
        target=_AGENT_ID,
    )


@pytest.mark.asyncio
async def test_avatar_generate_invalid_bearer_audits_deny(
    captured_events: list[AuditEvent],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """verify_bearer returns None → 401 + one pre-gate deny event."""
    monkeypatch.setattr(
        "sos.services.identity.app._auth_verify_bearer",
        lambda _auth: None,
    )

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as client:
        response = await client.post(
            "/avatar/generate",
            json=_GENERATE_BODY,
            headers={"Authorization": "Bearer not-a-real-token"},
        )

    assert response.status_code == 401, (
        f"expected 401, got {response.status_code}: {response.text}"
    )
    assert len(captured_events) == 1, (
        f"expected exactly 1 audit event, got {len(captured_events)}"
    )
    _assert_pregate_deny(
        captured_events[0],
        action="identity:avatar_generate",
        target=_AGENT_ID,
    )


@pytest.mark.asyncio
async def test_avatar_generate_scopeless_bearer_audits_deny(
    captured_events: list[AuditEvent],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Verified but unscoped (non-system, non-admin) token → 403 + one deny."""
    monkeypatch.setattr(
        "sos.services.identity.app._auth_verify_bearer",
        lambda _auth: _scopeless_ctx(),
    )

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as client:
        response = await client.post(
            "/avatar/generate",
            json=_GENERATE_BODY,
            headers={"Authorization": "Bearer scopeless"},
        )

    assert response.status_code == 403, (
        f"expected 403, got {response.status_code}: {response.text}"
    )
    assert len(captured_events) == 1, (
        f"expected exactly 1 audit event, got {len(captured_events)}"
    )
    _assert_pregate_deny(
        captured_events[0],
        action="identity:avatar_generate",
        target=_AGENT_ID,
    )


# ---------------------------------------------------------------------------
# POST /avatar/social/on_alpha_drift
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_avatar_social_drift_missing_bearer_audits_deny(
    captured_events: list[AuditEvent],
) -> None:
    """No Authorization header → 401 + one pre-gate deny event."""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as client:
        response = await client.post("/avatar/social/on_alpha_drift", json=_DRIFT_BODY)

    assert response.status_code == 401, (
        f"expected 401, got {response.status_code}: {response.text}"
    )
    assert len(captured_events) == 1, (
        f"expected exactly 1 audit event, got {len(captured_events)}"
    )
    _assert_pregate_deny(
        captured_events[0],
        action="identity:avatar_social_drift",
        target=_AGENT_ID,
    )


@pytest.mark.asyncio
async def test_avatar_social_drift_invalid_bearer_audits_deny(
    captured_events: list[AuditEvent],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """verify_bearer returns None → 401 + one pre-gate deny event."""
    monkeypatch.setattr(
        "sos.services.identity.app._auth_verify_bearer",
        lambda _auth: None,
    )

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as client:
        response = await client.post(
            "/avatar/social/on_alpha_drift",
            json=_DRIFT_BODY,
            headers={"Authorization": "Bearer not-a-real-token"},
        )

    assert response.status_code == 401, (
        f"expected 401, got {response.status_code}: {response.text}"
    )
    assert len(captured_events) == 1, (
        f"expected exactly 1 audit event, got {len(captured_events)}"
    )
    _assert_pregate_deny(
        captured_events[0],
        action="identity:avatar_social_drift",
        target=_AGENT_ID,
    )


@pytest.mark.asyncio
async def test_avatar_social_drift_scopeless_bearer_audits_deny(
    captured_events: list[AuditEvent],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Verified but unscoped (non-system, non-admin) token → 403 + one deny."""
    monkeypatch.setattr(
        "sos.services.identity.app._auth_verify_bearer",
        lambda _auth: _scopeless_ctx(),
    )

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as client:
        response = await client.post(
            "/avatar/social/on_alpha_drift",
            json=_DRIFT_BODY,
            headers={"Authorization": "Bearer scopeless"},
        )

    assert response.status_code == 403, (
        f"expected 403, got {response.status_code}: {response.text}"
    )
    assert len(captured_events) == 1, (
        f"expected exactly 1 audit event, got {len(captured_events)}"
    )
    _assert_pregate_deny(
        captured_events[0],
        action="identity:avatar_social_drift",
        target=_AGENT_ID,
    )
