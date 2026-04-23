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
