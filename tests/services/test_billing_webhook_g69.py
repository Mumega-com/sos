"""
Sprint 006 E.3 / G69 — Stripe webhook knight-mint tests (v0.3 — Athena BLOCKs 1-5 applied).

Architecture under test:
  POST /webhook/stripe receives Stripe event.
  payment_intent.succeeded → livemode guard → idempotency INSERT (savepoint) →
    contract lookup (stripe_customer_id) → project scope (contracts.project) →
    knight mint (stripe_webhook_id FK) → atomic DB update → emit.

TC-G69a  Valid webhook + contract → knight minted, idempotency row processed, emits fire
TC-G69b  Same webhook replayed → 200 OK, no second mint, outcome=replay_skipped
TC-G69c  Invalid Stripe signature → 400, no DB writes, outcome=signature_invalid
TC-G69d  Valid webhook + no contract row → 200 noop, status=failed, Athena alert
TC-G69e  Valid webhook + contract but mint fails → mint_failed returned, tx rolled back, retry-safe
TC-G69f  Valid webhook with contracts.project != mumega → 200 noop, project_scope_refused
"""
from __future__ import annotations

import json
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_payment_intent(
    payment_intent_id: str = "pi_test_abc123",
    customer: str = "cus_test_xyz",
    project: str = "mumega",
    tenant_slug: str = "acme",
    knight_name: str = "acme-knight",
    receipt_email: str = "test@acme.com",
) -> dict[str, Any]:
    return {
        "id": payment_intent_id,
        "customer": customer,
        "receipt_email": receipt_email,
        "metadata": {
            "project": project,
            "tenant_slug": tenant_slug,
            "knight_name": knight_name,
            "customer_name": "Acme Corp",
            "cause": "Serves Acme Corp as their dedicated agent on the substrate.",
        },
    }


def _make_stripe_event(payment_intent: dict, event_type: str = "payment_intent.succeeded") -> dict:
    return {
        "type": event_type,
        "data": {"object": payment_intent},
        "id": "evt_test_001",
    }


def _make_contract_row(
    stripe_customer_id: str = "cus_test_xyz",
    tenant_slug: str = "acme",
    cause: str = "Serves Acme Corp.",
    project: str = "mumega",
) -> dict:
    return {
        "id": "c0000000-0000-0000-0000-000000000001",
        "principal_id": "p0000000-0000-0000-0000-000000000001",
        "tenant_slug": tenant_slug,
        "stripe_customer_id": stripe_customer_id,
        "cause_statement": cause,
        "status": "sent",
        "project": project,  # BLOCK-2: authoritative project source
    }


def _make_mock_conn(*, fetchrow_return=None):
    """Build an asyncpg mock connection with non-exception-suppressing transactions."""
    mock_conn = AsyncMock()
    mock_conn.execute = AsyncMock(return_value=None)
    mock_conn.fetchrow = AsyncMock(return_value=fetchrow_return)
    mock_conn.close = AsyncMock()
    # BLOCK-1: transaction mock must NOT suppress exceptions (__aexit__ returns False = propagate).
    # Default AsyncMock().__aexit__ returns MagicMock() which is truthy = suppresses exceptions.
    mock_tx = AsyncMock()
    mock_tx.__aenter__ = AsyncMock(return_value=None)
    mock_tx.__aexit__ = AsyncMock(return_value=False)
    mock_conn.transaction = MagicMock(return_value=mock_tx)
    return mock_conn


# ---------------------------------------------------------------------------
# TC-G69a: valid webhook + contract → minted
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_g69a_valid_webhook_mints_knight() -> None:
    """TC-G69a: payment_intent.succeeded with valid signature + contract → knight minted."""
    from sos.services.billing.webhook import handle_payment_intent_succeeded

    pi = _make_payment_intent()
    contract = _make_contract_row()
    mock_conn = _make_mock_conn(fetchrow_return=contract)

    emitted_knight: list[dict] = []
    emitted_webhook: list[dict] = []

    def _emit_knight(knight_id, customer_id, payment_intent_id, project, **kw):
        emitted_knight.append({"knight_id": knight_id, "project": project})

    def _emit_webhook(payment_intent_id, event_type, outcome, **kw):
        emitted_webhook.append({"outcome": outcome})

    with patch("sos.services.billing.webhook.asyncpg") as mock_asyncpg, \
         patch("sos.services.billing.webhook.mint_knight_programmatic",
               return_value={"ok": True, "knight_id": "agent:acme-knight",
                             "knight_slug": "acme-knight", "qnft_uri": "qnft:acme-knight:abc",
                             "error": None, "skipped": False}), \
         patch("sos.services.billing.webhook.emit_knight_minted", side_effect=_emit_knight), \
         patch("sos.services.billing.webhook.emit_stripe_webhook", side_effect=_emit_webhook), \
         patch.dict("os.environ", {"DATABASE_URL": "postgresql://test/test", "SOS_ENV": "test"}):

        mock_asyncpg.connect = AsyncMock(return_value=mock_conn)
        mock_asyncpg.UniqueViolationError = Exception  # won't be raised in this TC

        result = await handle_payment_intent_succeeded(pi)

    assert result["ok"] is True, f"Expected ok=True, got: {result}"
    assert result["knight_id"] == "agent:acme-knight"
    assert result["qnft_uri"] == "qnft:acme-knight:abc"
    assert len(emitted_knight) == 1
    assert emitted_knight[0]["project"] == "mumega"
    assert len(emitted_webhook) == 1
    assert emitted_webhook[0]["outcome"] == "minted"
    mock_conn.close.assert_called_once()


# ---------------------------------------------------------------------------
# TC-G69b: replay → replay_idempotent_skip
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_g69b_replay_returns_prior_knight() -> None:
    """TC-G69b: same payment_intent replayed → 200, no second mint, replay_skipped emit."""
    from sos.services.billing.webhook import handle_payment_intent_succeeded

    pi = _make_payment_intent()
    # Idempotency row for a previously processed payment (status=processed)
    prior_row = {
        "id": "w0000000-0000-0000-0000-000000000001",
        "status": "processed",
        "resulting_knight_id": "agent:acme-knight",
    }

    emitted_webhook: list[dict] = []

    import asyncpg as real_asyncpg

    mock_conn = _make_mock_conn(fetchrow_return=prior_row)

    async def _execute_raises_unique(*args, **kwargs):
        raise real_asyncpg.UniqueViolationError("duplicate key")

    mock_conn.execute = AsyncMock(side_effect=_execute_raises_unique)

    def _emit_webhook(payment_intent_id, event_type, outcome, **kw):
        emitted_webhook.append({"outcome": outcome})

    with patch("sos.services.billing.webhook.asyncpg") as mock_asyncpg, \
         patch("sos.services.billing.webhook.emit_stripe_webhook", side_effect=_emit_webhook), \
         patch.dict("os.environ", {"DATABASE_URL": "postgresql://test/test", "SOS_ENV": "test"}):

        mock_asyncpg.connect = AsyncMock(return_value=mock_conn)
        mock_asyncpg.UniqueViolationError = real_asyncpg.UniqueViolationError

        result = await handle_payment_intent_succeeded(pi)

    assert result["ok"] is True
    assert result["reason"] == "replay_idempotent_skip"
    assert result["knight_id"] == "agent:acme-knight"
    assert emitted_webhook[0]["outcome"] == "replay_skipped"


# ---------------------------------------------------------------------------
# TC-G69c: invalid signature → 400
# ---------------------------------------------------------------------------


def test_g69c_invalid_signature_returns_400() -> None:
    """TC-G69c: webhook with invalid Stripe signature → 400 Bad Request, no DB writes."""
    from sos.services.billing.webhook import _verify_signature
    import stripe

    payload = b'{"type":"payment_intent.succeeded","data":{}}'
    bad_sig = "t=123,v1=badsignature"

    with patch.dict("os.environ", {"STRIPE_WEBHOOK_SECRET": "whsec_test_secret"}):
        import sos.services.billing.webhook as wh_mod
        original = wh_mod.STRIPE_WEBHOOK_SECRET
        wh_mod.STRIPE_WEBHOOK_SECRET = "whsec_test_secret"
        try:
            from fastapi import HTTPException
            with pytest.raises(HTTPException) as exc_info:
                _verify_signature(payload, bad_sig)
            assert exc_info.value.status_code == 400
        finally:
            wh_mod.STRIPE_WEBHOOK_SECRET = original


# ---------------------------------------------------------------------------
# TC-G69d: no contract → 200 noop, status=failed, Athena alert
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_g69d_no_contract_returns_noop() -> None:
    """TC-G69d: valid webhook but no contract row → 200 OK, status=failed, Athena alert."""
    from sos.services.billing.webhook import handle_payment_intent_succeeded

    pi = _make_payment_intent()
    emitted_webhook: list[dict] = []
    athena_alerts: list[str] = []

    # fetchrow returns None (no contract found)
    mock_conn = _make_mock_conn(fetchrow_return=None)

    execute_sqls: list[str] = []

    async def _track_execute(sql, *args):
        execute_sqls.append(sql)

    mock_conn.execute = AsyncMock(side_effect=_track_execute)

    def _emit_webhook(payment_intent_id, event_type, outcome, **kw):
        emitted_webhook.append({"outcome": outcome})

    def _alert(msg):
        athena_alerts.append(msg)

    with patch("sos.services.billing.webhook.asyncpg") as mock_asyncpg, \
         patch("sos.services.billing.webhook.emit_stripe_webhook", side_effect=_emit_webhook), \
         patch("sos.services.billing.webhook._alert_athena", side_effect=_alert), \
         patch.dict("os.environ", {"DATABASE_URL": "postgresql://test/test", "SOS_ENV": "test"}):

        mock_asyncpg.connect = AsyncMock(return_value=mock_conn)
        mock_asyncpg.UniqueViolationError = Exception

        result = await handle_payment_intent_succeeded(pi)

    assert result["ok"] is False
    assert result["reason"] == "no_contract"
    assert emitted_webhook[0]["outcome"] == "no_contract"
    assert len(athena_alerts) == 1
    assert "no_contract" in athena_alerts[0].lower() or "contract" in athena_alerts[0].lower()
    # WARN-6: append-only — no DELETE; instead status='failed' UPDATE (BLOCK-1 within tx)
    assert any("status='failed'" in sql for sql in execute_sqls), \
        f"Expected status='failed' UPDATE for no_contract, got: {execute_sqls}"
    mock_conn.close.assert_called_once()


# ---------------------------------------------------------------------------
# TC-G69e: mint fails mid-flow → tx rolled back, retry-safe
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_g69e_mint_failure_cleans_idempotency_row() -> None:
    """TC-G69e: mint fails → RuntimeError → tx rollback → idempotency row gone → retry-safe."""
    from sos.services.billing.webhook import handle_payment_intent_succeeded

    pi = _make_payment_intent()
    contract = _make_contract_row()
    mock_conn = _make_mock_conn(fetchrow_return=contract)

    execute_sqls: list[str] = []

    async def _track_execute(sql, *args):
        execute_sqls.append(sql)

    mock_conn.execute = AsyncMock(side_effect=_track_execute)

    with patch("sos.services.billing.webhook.asyncpg") as mock_asyncpg, \
         patch("sos.services.billing.webhook.mint_knight_programmatic",
               return_value={"ok": False, "error": "QNFT generation failed",
                             "knight_id": None, "knight_slug": None, "qnft_uri": None,
                             "skipped": False}), \
         patch("sos.services.billing.webhook.emit_stripe_webhook"), \
         patch.dict("os.environ", {"DATABASE_URL": "postgresql://test/test", "SOS_ENV": "test"}):

        mock_asyncpg.connect = AsyncMock(return_value=mock_conn)
        mock_asyncpg.UniqueViolationError = Exception

        result = await handle_payment_intent_succeeded(pi)

    assert result["ok"] is False
    assert result["reason"] == "mint_failed"
    # Transaction was rolled back — no 'processed' UPDATE was committed
    assert not any("status='processed'" in sql for sql in execute_sqls), \
        f"Expected no processed commit (tx rolled back), got: {execute_sqls}"
    mock_conn.close.assert_called_once()


# ---------------------------------------------------------------------------
# TC-G69f: contracts.project != mumega → scope refused (BLOCK-2)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_g69f_wrong_project_refused() -> None:
    """TC-G69f: contracts.project != mumega → 200 noop, project_scope_refused (BLOCK-2)."""
    from sos.services.billing.webhook import handle_payment_intent_succeeded

    pi = _make_payment_intent()  # metadata.project is irrelevant — check is on contracts.project
    # Contract row has project='frc' — the authoritative check (BLOCK-2)
    contract_wrong_project = _make_contract_row(project="frc")

    athena_alerts: list[str] = []
    webhook_emits: list[dict] = []
    execute_sqls: list[str] = []

    mock_conn = _make_mock_conn(fetchrow_return=contract_wrong_project)

    async def _track_execute(sql, *args):
        execute_sqls.append(sql)

    mock_conn.execute = AsyncMock(side_effect=_track_execute)

    def _emit_webhook(payment_intent_id, event_type, outcome, **kw):
        webhook_emits.append({"outcome": outcome})

    with patch("sos.services.billing.webhook.asyncpg") as mock_asyncpg, \
         patch("sos.services.billing.webhook._alert_athena",
               side_effect=lambda msg: athena_alerts.append(msg)), \
         patch("sos.services.billing.webhook.emit_stripe_webhook", side_effect=_emit_webhook), \
         patch.dict("os.environ", {"DATABASE_URL": "postgresql://test/test", "SOS_ENV": "test"}):

        mock_asyncpg.connect = AsyncMock(return_value=mock_conn)
        mock_asyncpg.UniqueViolationError = Exception

        result = await handle_payment_intent_succeeded(pi)

    assert result["ok"] is False
    assert result["reason"] == "project_scope_refused"
    assert webhook_emits[0]["outcome"] == "project_scope_refused"
    assert len(athena_alerts) == 1
    # WARN-6: append-only — no DELETE; status='failed' UPDATE
    assert any("status='failed'" in sql for sql in execute_sqls), \
        f"Expected status='failed' UPDATE for project_scope_refused, got: {execute_sqls}"
    mock_conn.close.assert_called_once()
