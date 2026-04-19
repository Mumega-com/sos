"""SquadTask.done_when + /complete gate — T1.3 Part 2 (task #270).

Pins three things the next refactor must not silently change:

1. Empty ``done_when`` is vacuously satisfied — old tasks still complete.
2. An unchecked entry blocks completion *before* any mutation or bus
   emission (the refund-after-emit trap the closure-v1 plan warns about).
3. An all-ticked list completes normally and the rehydrated task carries
   the full DoneCheck list round-trip.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from sos.contracts.done_check import DoneCheck
from sos.contracts.squad import (
    SquadTask,
    TaskPriority,
    TaskStatus,
)


def _apply_squad_migrations(db_path: Path) -> None:
    """Mirror of the helper in tests/test_squad_runtime.py.

    Kept local so this test file is runnable in isolation — you can
    `pytest tests/test_squad_done_when_gate.py` without pulling the
    full runtime-test module into the import graph.
    """
    from alembic import command
    from alembic.config import Config

    repo_root = Path(__file__).resolve().parents[1]
    cfg = Config(str(repo_root / "sos" / "services" / "squad" / "alembic.ini"))
    cfg.set_main_option("sqlalchemy.url", f"sqlite:///{db_path}")
    command.upgrade(cfg, "head")


class _RedisStub:
    """Minimal Redis double — enough to satisfy SquadBus.emit and the
    in-flight _emit_task_completed + delivery writes inside complete().
    """

    def publish(self, *args, **kwargs):
        return 1

    def xadd(self, *args, **kwargs):
        return "1-0"


@pytest.fixture
def task_service(tmp_path, monkeypatch):
    """Build a SquadTaskService against a fresh migrated DB with Redis stubbed.

    Scope is function so each test gets its own DB file — the economy-update
    block in complete() mutates squads/squad_wallets and we don't want that
    leaking between tests.
    """
    from sos.services.squad import service as squad_service
    from sos.services.squad import tasks as squad_tasks

    db_path = tmp_path / "squads.db"
    _apply_squad_migrations(db_path)

    monkeypatch.setattr(squad_service.redis, "Redis", lambda **kwargs: _RedisStub())
    # tasks._emit_task_completed imports `redis` locally via `import redis as
    # _redis`, then calls _redis.Redis(...). Patch the installed module's
    # Redis class so that local import resolves to the stub too.
    import redis as _real_redis

    monkeypatch.setattr(_real_redis, "Redis", lambda **kwargs: _RedisStub())

    db = squad_service.SquadDB(db_path)
    service = squad_tasks.SquadTaskService(db=db)
    return service


def _make_task(
    *,
    task_id: str = "t-gate-1",
    done_when: list[DoneCheck] | None = None,
) -> SquadTask:
    return SquadTask(
        id=task_id,
        squad_id="sq-gate",
        title="Gate test",
        description="",
        status=TaskStatus.CLAIMED,
        priority=TaskPriority.MEDIUM,
        assignee="worker",
        project="sos",
        token_budget=3000,
        done_when=done_when or [],
        attempt=1,
    )


def test_complete_succeeds_with_empty_done_when(task_service):
    """Empty list = no gate. Legacy tasks still complete."""
    task = _make_task(task_id="t-empty", done_when=[])
    task_service.create(task)

    out = task_service.complete("t-empty", {"summary": "ok"})

    assert out.status == TaskStatus.DONE
    assert out.completed_at is not None


def test_complete_refused_when_any_entry_unchecked(task_service):
    """Uncompleted check → NotAllDoneError, and the task's persisted
    status MUST remain un-DONE (no half-commit)."""
    from sos.services.squad.tasks import NotAllDoneError

    checks = [
        DoneCheck(id="c1", text="unit tests pass", done=True),
        DoneCheck(id="c2", text="lint clean", done=False),
    ]
    task = _make_task(task_id="t-pending", done_when=checks)
    task_service.create(task)

    with pytest.raises(NotAllDoneError) as excinfo:
        task_service.complete("t-pending", {"summary": "premature"})

    assert "c2" in str(excinfo.value)

    # No partial state mutation — DB row still CLAIMED, completed_at unset.
    still = task_service.get("t-pending")
    assert still is not None
    assert still.status == TaskStatus.CLAIMED
    assert still.completed_at in (None, "")


def test_complete_succeeds_when_all_checks_done(task_service):
    """Every entry ticked → happy path completes cleanly, and the
    rehydrated task still carries the done_when list (round-trip)."""
    checks = [
        DoneCheck(id="c1", text="impl", done=True, acked_by="worker", acked_at="2026-04-19T00:00:00Z"),
        DoneCheck(id="c2", text="tests", done=True, acked_by="worker", acked_at="2026-04-19T00:01:00Z"),
    ]
    task = _make_task(task_id="t-done", done_when=checks)
    task_service.create(task)

    out = task_service.complete("t-done", {"summary": "shipped"})

    assert out.status == TaskStatus.DONE

    rehydrated = task_service.get("t-done")
    assert rehydrated is not None
    assert len(rehydrated.done_when) == 2
    assert {c.id for c in rehydrated.done_when} == {"c1", "c2"}
    assert all(c.done for c in rehydrated.done_when)


def test_done_when_round_trips_through_get_and_list(task_service):
    """Separate from the gate: make sure rehydration parses dicts back
    into DoneCheck instances (not raw dicts), so callers can rely on
    the type and not .get() their way through it."""
    checks = [DoneCheck(id="c1", text="one", done=False)]
    task = _make_task(task_id="t-rt", done_when=checks)
    task_service.create(task)

    got = task_service.get("t-rt")
    assert got is not None
    assert got.done_when and isinstance(got.done_when[0], DoneCheck)
    assert got.done_when[0].id == "c1"
    assert got.done_when[0].done is False
