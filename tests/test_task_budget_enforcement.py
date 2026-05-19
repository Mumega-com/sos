"""Tests for token_budget enforcement at claim time.

Verifies that SquadTaskService.claim() rejects tasks when the squad wallet
cannot cover the estimated cost, and allows claims when:
- wallet balance is sufficient
- task has no estimated_cost_cents
- squad has no wallet row at all (treated as unlimited)
"""
from __future__ import annotations

import uuid
from pathlib import Path
from typing import Any


def _apply_squad_migrations(db_path: Path) -> None:
    from alembic import command
    from alembic.config import Config

    repo_root = Path(__file__).resolve().parents[1]
    cfg = Config(str(repo_root / "sos" / "services" / "squad" / "alembic.ini"))
    cfg.set_main_option("sqlalchemy.url", f"sqlite:///{db_path}")
    command.upgrade(cfg, "head")


def _make_service(tmp_path: Path, monkeypatch: Any):
    from sos.services.squad import service as squad_service
    from sos.services.squad.tasks import SquadTaskService

    class _RedisStub:
        def publish(self, *args: Any, **kwargs: Any) -> int:
            return 1

        def xadd(self, *args: Any, **kwargs: Any) -> str:
            return "1-0"

    monkeypatch.setattr(squad_service.redis, "Redis", lambda **kwargs: _RedisStub())

    db_path = tmp_path / "test_budget.db"
    _apply_squad_migrations(db_path)
    db = squad_service.SquadDB(db_path)
    return SquadTaskService(db=db)


def _seed_wallet(svc, squad_id: str, balance_cents: int, tenant_id: str = "tenant-budget") -> None:
    """Insert a squad_wallets row directly via the DB connection."""
    with svc.db.connect() as conn:
        conn.execute(
            """
            INSERT INTO squad_wallets (squad_id, tenant_id, balance_cents, total_spent_cents, updated_at)
            VALUES (?, ?, ?, 0, datetime('now'))
            """,
            (squad_id, tenant_id, balance_cents),
        )


def _task_with_cost(squad_id: str, estimated_cost_cents: int | None = None) -> Any:
    from sos.contracts.squad import SquadTask

    # token_budget > 0 suppresses the auto-budget path in create() — without this,
    # create() always injects estimated_cost_cents via max(1, ...) even for "free" tasks.
    task = SquadTask(
        id=str(uuid.uuid4()),
        squad_id=squad_id,
        title="budget-test-task",
        token_budget=1000,
    )
    if estimated_cost_cents is not None:
        task.inputs["estimated_cost_cents"] = estimated_cost_cents
    return task


def test_claim_succeeds_when_balance_sufficient(tmp_path, monkeypatch):
    """Claim proceeds when wallet balance >= estimated_cost_cents."""
    from sos.services.squad.tasks import InsufficientFundsError

    svc = _make_service(tmp_path, monkeypatch)
    squad_id = "sq-budget-ok"
    tenant_id = "tenant-budget"

    task = _task_with_cost(squad_id, estimated_cost_cents=50)
    svc.create(task, tenant_id=tenant_id)
    _seed_wallet(svc, squad_id, balance_cents=100, tenant_id=tenant_id)

    # Should not raise
    claim = svc.claim(task.id, assignee="kasra", attempt=0, tenant_id=tenant_id)
    assert claim.task_id == task.id


def test_claim_raises_when_balance_insufficient(tmp_path, monkeypatch):
    """Claim is rejected with InsufficientFundsError when wallet < estimated cost."""
    import pytest
    from sos.services.squad.tasks import InsufficientFundsError

    svc = _make_service(tmp_path, monkeypatch)
    squad_id = "sq-budget-low"
    tenant_id = "tenant-budget"

    task = _task_with_cost(squad_id, estimated_cost_cents=200)
    svc.create(task, tenant_id=tenant_id)
    _seed_wallet(svc, squad_id, balance_cents=50, tenant_id=tenant_id)

    with pytest.raises(InsufficientFundsError) as exc_info:
        svc.claim(task.id, assignee="kasra", attempt=0, tenant_id=tenant_id)

    err = exc_info.value
    assert err.task_id == task.id
    assert err.balance_cents == 50
    assert err.estimated_cost_cents == 200


def test_claim_succeeds_when_no_estimated_cost(tmp_path, monkeypatch):
    """Claim proceeds for free tasks (no estimated_cost_cents in inputs)."""
    svc = _make_service(tmp_path, monkeypatch)
    squad_id = "sq-free"
    tenant_id = "tenant-budget"

    task = _task_with_cost(squad_id, estimated_cost_cents=None)
    svc.create(task, tenant_id=tenant_id)
    _seed_wallet(svc, squad_id, balance_cents=0, tenant_id=tenant_id)

    # No estimated_cost_cents → no budget gate → claim proceeds
    claim = svc.claim(task.id, assignee="kasra", attempt=0, tenant_id=tenant_id)
    assert claim.task_id == task.id


def test_claim_succeeds_when_no_wallet_row(tmp_path, monkeypatch):
    """Claim proceeds when squad has no wallet row (predates wallet feature).

    Missing row = unlimited. The gate is skipped entirely.
    """
    svc = _make_service(tmp_path, monkeypatch)
    squad_id = "sq-no-wallet"
    tenant_id = "tenant-budget"

    task = _task_with_cost(squad_id, estimated_cost_cents=999)
    svc.create(task, tenant_id=tenant_id)
    # No _seed_wallet call — squad has no wallet row

    # Should not raise InsufficientFundsError
    claim = svc.claim(task.id, assignee="kasra", attempt=0, tenant_id=tenant_id)
    assert claim.task_id == task.id
