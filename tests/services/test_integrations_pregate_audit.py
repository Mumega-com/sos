"""Regression tests for the integrations pre-gate audit-gap fix (v0.5.6.x).

Three auth-protected routes in ``sos.services.integrations.app`` short-circuit
with 401 when the Authorization header is missing — *before* the unified
policy gate runs. Without the ``_emit_integrations_deny`` helper those
pre-gate rejections would never hit the audit spine.

These tests pin the contract so the gap cannot silently reopen:

- GET  /oauth/credentials/{tenant}/{provider}  → oauth_credentials_read
- POST /oauth/ghl/callback/{tenant}            → oauth_ghl_callback
- POST /oauth/google/callback/{tenant}         → oauth_google_callback

Each missing-bearer request must emit exactly one ``AuditEvent`` with
``kind=POLICY_DECISION``, ``decision=DENY``, ``policy_tier='integrations_pregate'``,
``agent='anonymous'``, and a matching action/target — then raise 401.
"""
from __future__ import annotations

import pytest
from httpx import ASGITransport, AsyncClient

from sos.contracts.audit import AuditDecision, AuditEvent, AuditEventKind
from sos.services.integrations.app import app


# ---------------------------------------------------------------------------
# Fixture — capture AuditEvents emitted by the pre-gate helper
# ---------------------------------------------------------------------------


@pytest.fixture
def captured_events(monkeypatch: pytest.MonkeyPatch) -> list[AuditEvent]:
    """Intercept ``append_event`` so audit records never hit disk/Redis.

    The integrations service imports ``append_event`` lazily inside the
    helper (``from sos.kernel.audit import append_event, new_event``), so
    patching the source module is sufficient.
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
    tenant: str,
) -> None:
    """Shared assertions for every pre-gate deny event."""
    assert event.kind == AuditEventKind.POLICY_DECISION, (
        f"expected POLICY_DECISION, got {event.kind}"
    )
    assert event.decision == AuditDecision.DENY, (
        f"expected DENY decision, got {event.decision}"
    )
    assert event.policy_tier == "integrations_pregate", (
        f"expected policy_tier='integrations_pregate', got {event.policy_tier!r}"
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
    assert event.tenant == tenant, (
        f"expected tenant={tenant!r}, got {event.tenant!r}"
    )


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


async def test_credentials_missing_bearer_emits_pregate_deny(
    captured_events: list[AuditEvent],
) -> None:
    """GET /oauth/credentials without Authorization → 401 + one pre-gate deny."""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as client:
        response = await client.get("/oauth/credentials/acme/ghl")

    assert response.status_code == 401, (
        f"expected 401, got {response.status_code}: {response.text}"
    )
    assert len(captured_events) == 1, (
        f"expected exactly 1 audit event, got {len(captured_events)}"
    )
    _assert_pregate_deny(
        captured_events[0],
        action="oauth_credentials_read",
        target="acme/ghl",
        tenant="acme",
    )


async def test_ghl_callback_missing_bearer_emits_pregate_deny(
    captured_events: list[AuditEvent],
) -> None:
    """POST /oauth/ghl/callback without Authorization → 401 + one pre-gate deny."""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as client:
        response = await client.post(
            "/oauth/ghl/callback/acme",
            json={"code": "abc"},
        )

    assert response.status_code == 401, (
        f"expected 401, got {response.status_code}: {response.text}"
    )
    assert len(captured_events) == 1, (
        f"expected exactly 1 audit event, got {len(captured_events)}"
    )
    _assert_pregate_deny(
        captured_events[0],
        action="oauth_ghl_callback",
        target="acme",
        tenant="acme",
    )


async def test_google_callback_missing_bearer_emits_pregate_deny(
    captured_events: list[AuditEvent],
) -> None:
    """POST /oauth/google/callback without Authorization → 401 + one pre-gate deny."""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as client:
        response = await client.post(
            "/oauth/google/callback/acme",
            json={"code": "abc", "service": "analytics"},
        )

    assert response.status_code == 401, (
        f"expected 401, got {response.status_code}: {response.text}"
    )
    assert len(captured_events) == 1, (
        f"expected exactly 1 audit event, got {len(captured_events)}"
    )
    _assert_pregate_deny(
        captured_events[0],
        action="oauth_google_callback",
        target="acme/analytics",
        tenant="acme",
    )
