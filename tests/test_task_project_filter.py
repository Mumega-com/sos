"""Tests for project_id filter on SquadTaskService.list().

Verifies that:
- Tasks can be filtered by project_id
- Tasks with a different project are excluded
- project_id=None returns all tasks (no filter)
"""
from __future__ import annotations

from pathlib import Path
from typing import Any


def _apply_squad_migrations(db_path: Path) -> None:
    """Run Squad service Alembic migrations against db_path."""
    from alembic import command
    from alembic.config import Config

    repo_root = Path(__file__).resolve().parents[1]
    cfg = Config(str(repo_root / "sos" / "services" / "squad" / "alembic.ini"))
    cfg.set_main_option("sqlalchemy.url", f"sqlite:///{db_path}")
    command.upgrade(cfg, "head")


def _make_service(tmp_path: Path, monkeypatch: Any):
    """Return a SquadTaskService backed by a temp SQLite DB."""
    from sos.services.squad import service as squad_service
    from sos.services.squad.tasks import SquadTaskService

    class _RedisStub:
        def publish(self, *args: Any, **kwargs: Any) -> int:
            return 1

        def xadd(self, *args: Any, **kwargs: Any) -> str:
            return "1-0"

    monkeypatch.setattr(squad_service.redis, "Redis", lambda **kwargs: _RedisStub())

    db_path = tmp_path / "test_squads.db"
    _apply_squad_migrations(db_path)
    db = squad_service.SquadDB(db_path)
    return SquadTaskService(db=db)


def _task(title: str, project: str | None = None):
    import uuid
    from sos.contracts.squad import SquadTask

    return SquadTask(
        id=str(uuid.uuid4()),
        squad_id="sq-test",
        title=title,
        project=project,
    )


def test_filter_by_project_id_returns_matching_tasks(tmp_path, monkeypatch):
    svc = _make_service(tmp_path, monkeypatch)

    svc.create(_task("river-task-1", project="river"), tenant_id="tenant-a")
    svc.create(_task("river-task-2", project="river"), tenant_id="tenant-a")
    svc.create(_task("inkwell-task", project="inkwell"), tenant_id="tenant-a")

    results = svc.list(project_id="river", tenant_id="tenant-a")

    assert len(results) == 2
    assert all(t.project == "river" for t in results)


def test_filter_by_project_id_excludes_other_projects(tmp_path, monkeypatch):
    svc = _make_service(tmp_path, monkeypatch)

    svc.create(_task("sos-task", project="sos"), tenant_id="tenant-b")
    svc.create(_task("hermes-task", project="hermes"), tenant_id="tenant-b")

    results = svc.list(project_id="sos", tenant_id="tenant-b")

    assert len(results) == 1
    assert results[0].project == "sos"
    assert results[0].title == "sos-task"


def test_no_project_id_returns_all_tasks(tmp_path, monkeypatch):
    svc = _make_service(tmp_path, monkeypatch)

    svc.create(_task("alpha", project="river"), tenant_id="tenant-c")
    svc.create(_task("beta", project="inkwell"), tenant_id="tenant-c")
    svc.create(_task("gamma", project="sos"), tenant_id="tenant-c")

    results = svc.list(project_id=None, tenant_id="tenant-c")

    assert len(results) == 3


def test_project_id_no_match_returns_empty(tmp_path, monkeypatch):
    svc = _make_service(tmp_path, monkeypatch)

    svc.create(_task("some-task", project="river"), tenant_id="tenant-d")

    results = svc.list(project_id="nonexistent-project", tenant_id="tenant-d")

    assert results == []


def test_project_id_empty_string_filters_on_empty_not_all(tmp_path, monkeypatch):
    """project_id="" must filter on tasks with project="", not return all tasks.

    Previously, `if project_id:` evaluated "" as falsy and skipped the filter,
    returning every task. The fix changes the guard to `if project_id is not None:`
    so that the empty string is still applied as a WHERE clause.
    """
    svc = _make_service(tmp_path, monkeypatch)

    svc.create(_task("task-with-empty-project", project=""), tenant_id="tenant-e")
    svc.create(_task("task-with-named-project", project="river"), tenant_id="tenant-e")

    results = svc.list(project_id="", tenant_id="tenant-e")

    # Only the task whose project column is "" should be returned.
    assert len(results) == 1
    assert results[0].project == ""
    assert results[0].title == "task-with-empty-project"


def test_assignee_filter_returns_matching_tasks(tmp_path, monkeypatch):
    svc = _make_service(tmp_path, monkeypatch)

    alice = _task("alice-task", project="sos")
    alice.assignee = "alice"
    bob = _task("bob-task", project="sos")
    bob.assignee = "bob"
    svc.create(alice, tenant_id="tenant-f")
    svc.create(bob, tenant_id="tenant-f")

    results = svc.list(assignee="alice", tenant_id="tenant-f")

    assert len(results) == 1
    assert results[0].title == "alice-task"
    assert results[0].assignee == "alice"


def test_limit_caps_task_results(tmp_path, monkeypatch):
    svc = _make_service(tmp_path, monkeypatch)

    for index in range(3):
        svc.create(_task(f"task-{index}", project="sos"), tenant_id="tenant-g")

    results = svc.list(limit=2, tenant_id="tenant-g")

    assert len(results) == 2


def test_reap_stale_claims_resets_legacy_unowned_claim(tmp_path, monkeypatch):
    svc = _make_service(tmp_path, monkeypatch)
    task = _task("legacy-claimed-task", project="sos")
    svc.create(task, tenant_id="tenant-h")

    with svc.db.connect() as conn:
        conn.execute(
            """UPDATE squad_tasks
                  SET status = 'claimed',
                      assignee = 'old-worker',
                      claimed_at = '2026-01-01T00:00:00+00:00',
                      claim_owner_pid = NULL,
                      claim_owner_instance = NULL,
                      claim_owner_acquired_at = NULL,
                      claim_token = 'stale-token'
                WHERE id = ? AND tenant_id = ?""",
            (task.id, "tenant-h"),
        )

    reset = svc.reap_stale_claims(tenant_id="tenant-h")

    assert reset == 1
    with svc.db.connect() as conn:
        row = conn.execute(
            """SELECT status, assignee, claimed_at, claim_token
                 FROM squad_tasks
                WHERE id = ? AND tenant_id = ?""",
            (task.id, "tenant-h"),
        ).fetchone()
    assert row["status"] == "backlog"
    assert row["assignee"] is None
    assert row["claimed_at"] is None
    assert row["claim_token"] is None
