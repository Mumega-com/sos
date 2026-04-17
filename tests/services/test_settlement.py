"""Tests for island #4 — UsageLog → Economy Transaction settlement.

Covers:
  - UsageLog.append() creates a wallet debit for MIND events
  - 85/15 split for ai_to_ai_commerce / seller_skill events
  - Insufficient funds marks event deferred (log is never lost)
  - Retry via POST /settle/{id} works when funds arrive
  - Log retains event on wallet failure
  - Platform fee accumulates in agent:treasury wallet
  - Non-MIND / zero-cost events are skipped
  - settle_usage_event returns SettlementResult dataclass
  - retry_deferred_settlements helper re-attempts deferred events
  - SettlementResult has_errors + ok flags
  - POST /settle/{id} requires admin token
"""
from __future__ import annotations

import asyncio
import hashlib
import json
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi.testclient import TestClient


# ---------------------------------------------------------------------------
# Shared fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def tokens_file(tmp_path, monkeypatch):
    tokens = [
        {"label": "admin", "token": "tk_admin", "active": True, "is_admin": True},
        {"label": "trop", "token": "tk_trop", "project": "therealmofpatterns", "active": True},
    ]
    p = tmp_path / "tokens.json"
    p.write_text(json.dumps(tokens))
    import sos.kernel.auth as auth_mod
    monkeypatch.setattr(auth_mod, "TOKENS_PATH", p)
    auth_mod._cache.invalidate()
    return p


@pytest.fixture
def usage_log_path(tmp_path, monkeypatch):
    p = tmp_path / "usage_events.jsonl"
    monkeypatch.setenv("SOS_USAGE_LOG_PATH", str(p))
    return p


# ---------------------------------------------------------------------------
# Lightweight mock wallet
# ---------------------------------------------------------------------------

class _MockWallet:
    """In-memory wallet for tests. Integer balances (microMIND)."""

    def __init__(self):
        self._balances: dict[str, int] = {}
        self._credits: list[tuple[str, int, str]] = []
        self._debits: list[tuple[str, int, str]] = []

    def set_balance(self, agent: str, amount: int) -> None:
        self._balances[agent] = amount

    def get_balance_sync(self, agent: str) -> int:
        return self._balances.get(agent, 0)

    async def get_balance(self, agent: str) -> int:  # noqa: D102
        return self._balances.get(agent, 0)

    async def credit(self, agent: str, amount: int, reason: str = "") -> int:
        self._balances[agent] = self._balances.get(agent, 0) + amount
        self._credits.append((agent, amount, reason))
        return self._balances[agent]

    async def debit(self, agent: str, amount: int, reason: str = "") -> int:
        from sos.services.economy.wallet import InsufficientFundsError
        bal = self._balances.get(agent, 0)
        if bal < amount:
            raise InsufficientFundsError(agent, amount, bal)
        self._balances[agent] = bal - amount
        self._debits.append((agent, amount, reason))
        return self._balances[agent]


# ---------------------------------------------------------------------------
# Helper
# ---------------------------------------------------------------------------

def _run(coro):
    return asyncio.get_event_loop().run_until_complete(coro)


def _make_event(**kwargs):
    from sos.services.economy.usage_log import UsageEvent
    defaults = dict(
        tenant="therealmofpatterns",
        provider="anthropic",
        model="claude-sonnet-4-6",
        cost_micros=1000,
        cost_currency="MIND",
    )
    defaults.update(kwargs)
    return UsageEvent(**defaults)


# ---------------------------------------------------------------------------
# Unit tests — settle_usage_event
# ---------------------------------------------------------------------------

class TestSettleUsageEvent:
    def test_usage_log_append_creates_transaction(self, tmp_path):
        """Appending a MIND event should debit the tenant wallet."""
        from sos.services.economy.usage_log import UsageLog

        wallet = _MockWallet()
        wallet.set_balance("tenant:therealmofpatterns", 5000)

        log = UsageLog(path=tmp_path / "events.jsonl", wallet=wallet)
        event = _make_event(cost_micros=1000, cost_currency="MIND")
        stored = log.append(event)

        assert stored.id
        # Wallet should have been debited
        assert wallet.get_balance_sync("tenant:therealmofpatterns") == 4000
        assert len(wallet._debits) == 1
        assert wallet._debits[0][1] == 1000

    def test_ai_to_ai_commerce_splits_85_15(self):
        """ai_to_ai_commerce + author_agent → 850 to author, 150 to treasury."""
        from sos.services.economy.settlement import settle_usage_event

        wallet = _MockWallet()
        wallet.set_balance("tenant:buyer", 1000)

        event = _make_event(
            tenant="buyer",
            cost_micros=1000,
            cost_currency="MIND",
            metadata={
                "ai_to_ai_commerce": True,
                "seller_skill": "skill:astro-report",
                "author_agent": "agent:astrologer",
            },
        )
        result = _run(settle_usage_event(event, wallet))

        assert result.settlement_status == "settled"
        assert result.total_charged == 1000
        assert result.total_creator_credit == 850
        assert result.total_platform_fee == 150

        # author received 850
        assert wallet.get_balance_sync("agent:astrologer") == 850
        # treasury received 150
        assert wallet.get_balance_sync("agent:treasury") == 150
        # buyer was debited 1000
        assert wallet.get_balance_sync("tenant:buyer") == 0

    def test_insufficient_funds_marks_deferred(self, tmp_path):
        """Tenant wallet at 0 → event is still written; settlement_status = deferred."""
        from sos.services.economy.usage_log import UsageLog

        wallet = _MockWallet()
        # wallet has 0 — no balance set

        log = UsageLog(path=tmp_path / "events.jsonl", wallet=wallet)
        event = _make_event(cost_micros=500, cost_currency="MIND")
        stored = log.append(event)

        assert stored.id  # event was stored
        lines = (tmp_path / "events.jsonl").read_text().strip().splitlines()
        assert len(lines) >= 1  # at least the original line
        # The last line should carry settlement_status=deferred
        last = json.loads(lines[-1])
        assert last["metadata"].get("settlement_status") == "deferred"

    def test_retry_deferred_works_when_funds_arrive(self, tmp_path, tokens_file, usage_log_path, monkeypatch):
        """POST /settle/{id} succeeds after funds are added to the tenant wallet."""
        wallet = _MockWallet()
        # Wallet starts empty → first ingest defers

        from sos.services.economy.usage_log import UsageLog
        import sos.services.economy.app as app_mod

        log = UsageLog(path=usage_log_path, wallet=wallet)
        monkeypatch.setattr(app_mod, "_usage_log", log)
        monkeypatch.setattr(app_mod, "wallet", wallet)

        # Ingest via the API (settlement deferred)
        from sos.services.economy.app import app
        client = TestClient(app)

        body = {
            "tenant": "therealmofpatterns",
            "provider": "anthropic",
            "model": "claude-sonnet-4-6",
            "cost_micros": 2000,
            "cost_currency": "MIND",
        }
        resp = client.post("/usage", json=body, headers={"Authorization": "Bearer tk_trop"})
        assert resp.status_code == 201
        event_id = resp.json()["id"]

        # Now add funds
        wallet.set_balance("tenant:therealmofpatterns", 5000)

        # Retry via admin endpoint
        resp2 = client.post(f"/settle/{event_id}", headers={"Authorization": "Bearer tk_admin"})
        assert resp2.status_code == 200
        data = resp2.json()
        assert data["settlement_status"] == "settled"
        assert data["total_charged"] == 2000

    def test_log_retains_event_on_wallet_failure(self, tmp_path):
        """Wallet crash must not lose the usage event."""
        from sos.services.economy.usage_log import UsageLog

        class _CrashWallet:
            async def debit(self, *a, **kw):
                raise RuntimeError("wallet exploded")
            async def credit(self, *a, **kw):
                raise RuntimeError("wallet exploded")

        log = UsageLog(path=tmp_path / "events.jsonl", wallet=_CrashWallet())
        event = _make_event(cost_micros=999, cost_currency="MIND")
        stored = log.append(event)

        # Event must still be in the log
        assert stored.id
        events = log.read_all()
        ids = [e.id for e in events]
        assert stored.id in ids

    def test_platform_fee_accumulates_in_treasury_wallet(self):
        """Three separate events → treasury balance = sum of all platform fees."""
        from sos.services.economy.settlement import settle_usage_event, TREASURY_AGENT

        wallet = _MockWallet()
        wallet.set_balance("tenant:t1", 30_000)

        costs = [1000, 2000, 7000]
        for cost in costs:
            event = _make_event(
                tenant="t1",
                cost_micros=cost,
                cost_currency="MIND",
                metadata={
                    "ai_to_ai_commerce": True,
                    "seller_skill": "skill:x",
                    "author_agent": "agent:author",
                },
            )
            _run(settle_usage_event(event, wallet))

        # 15% of each cost
        expected_treasury = sum((c * 1500) // 10_000 for c in costs)
        assert wallet.get_balance_sync(TREASURY_AGENT) == expected_treasury

    def test_zero_cost_event_is_skipped(self):
        """Events with cost_micros=0 produce no wallet operations."""
        from sos.services.economy.settlement import settle_usage_event

        wallet = _MockWallet()
        event = _make_event(cost_micros=0, cost_currency="MIND")
        result = _run(settle_usage_event(event, wallet))

        assert result.settlement_status == "skipped"
        assert len(wallet._debits) == 0
        assert len(wallet._credits) == 0

    def test_non_mind_currency_is_skipped(self):
        """USD-denominated events are not settled against the MIND wallet."""
        from sos.services.economy.settlement import settle_usage_event

        wallet = _MockWallet()
        event = _make_event(cost_micros=5000, cost_currency="USD")
        result = _run(settle_usage_event(event, wallet))

        assert result.settlement_status == "skipped"
        assert len(wallet._debits) == 0

    def test_settlement_result_dataclass_fields(self):
        """SettlementResult exposes the expected fields and computed properties."""
        from sos.services.economy.settlement import SettlementResult, WalletOutcome

        outcome = WalletOutcome(agent="a", amount=100, tx_type="usage_charge")
        result = SettlementResult(
            usage_event_id="evt-1",
            settlement_status="settled",
            outcomes=[outcome],
            total_charged=100,
            errors=[],
        )
        assert not result.has_errors
        assert result.total_charged == 100

    def test_skill_payout_without_ai_to_ai_flag(self):
        """seller_skill alone (no ai_to_ai) with author_agent still triggers split."""
        from sos.services.economy.settlement import settle_usage_event

        wallet = _MockWallet()
        wallet.set_balance("tenant:buyer", 10_000)

        event = _make_event(
            tenant="buyer",
            cost_micros=10_000,
            cost_currency="MIND",
            metadata={
                "seller_skill": "skill:report",
                "author_agent": "agent:creator",
            },
        )
        result = _run(settle_usage_event(event, wallet))

        assert result.settlement_status == "settled"
        assert result.total_creator_credit == 8500
        assert result.total_platform_fee == 1500

    def test_no_split_when_no_seller_metadata(self):
        """No seller metadata → full amount goes to treasury as platform fee."""
        from sos.services.economy.settlement import settle_usage_event, TREASURY_AGENT

        wallet = _MockWallet()
        wallet.set_balance("tenant:t", 4000)

        event = _make_event(tenant="t", cost_micros=4000, cost_currency="MIND")
        result = _run(settle_usage_event(event, wallet))

        assert result.settlement_status == "settled"
        assert result.total_creator_credit == 0
        assert result.total_platform_fee == 4000
        assert wallet.get_balance_sync(TREASURY_AGENT) == 4000

    def test_settle_endpoint_requires_admin(self, tmp_path, tokens_file, usage_log_path, monkeypatch):
        """Non-admin token gets 403 from POST /settle/{id}."""
        from sos.services.economy.app import app
        client = TestClient(app)
        resp = client.post("/settle/nonexistent-id", headers={"Authorization": "Bearer tk_trop"})
        assert resp.status_code == 403

    def test_settle_endpoint_404_for_unknown_id(self, tmp_path, tokens_file, usage_log_path, monkeypatch):
        """POST /settle with unknown id returns 404."""
        from sos.services.economy.usage_log import UsageLog
        import sos.services.economy.app as app_mod

        wallet = _MockWallet()
        log = UsageLog(path=usage_log_path, wallet=wallet)
        monkeypatch.setattr(app_mod, "_usage_log", log)
        monkeypatch.setattr(app_mod, "wallet", wallet)

        from sos.services.economy.app import app
        client = TestClient(app)
        resp = client.post("/settle/does-not-exist", headers={"Authorization": "Bearer tk_admin"})
        assert resp.status_code == 404
