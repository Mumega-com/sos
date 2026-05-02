from __future__ import annotations

import uuid
from pathlib import Path
from typing import Any

import pytest


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

    db_path = tmp_path / "test_squads.db"
    _apply_squad_migrations(db_path)
    db = squad_service.SquadDB(db_path)
    return SquadTaskService(db=db)


def _task(title: str):
    from sos.contracts.squad import SquadTask

    return SquadTask(
        id=str(uuid.uuid4()),
        squad_id="sq-test",
        title=title,
        project="sos",
    )


def test_run_creation_is_tenant_scoped(tmp_path, monkeypatch):
    svc = _make_service(tmp_path, monkeypatch)
    task = _task("tenant-a-task")
    svc.create(task, tenant_id="tenant-a")

    run = svc.create_run(task.id, "codex", tenant_id="tenant-a")

    assert run["task_id"] == task.id
    assert run["tenant_id"] == "tenant-a"
    assert svc.list_runs(task.id, tenant_id="tenant-a")[0]["id"] == run["id"]
    with pytest.raises(KeyError):
        svc.list_runs(task.id, tenant_id="tenant-b")


def test_run_creation_is_idempotent_per_task_and_tenant(tmp_path, monkeypatch):
    svc = _make_service(tmp_path, monkeypatch)
    task = _task("idempotent-run")
    svc.create(task, tenant_id="tenant-a")

    first = svc.create_run(
        task.id,
        "codex",
        idempotency_key="run-key-1",
        correlation_id="corr-1",
        tenant_id="tenant-a",
    )
    second = svc.create_run(
        task.id,
        "codex",
        idempotency_key="run-key-1",
        correlation_id="corr-2",
        tenant_id="tenant-a",
    )

    assert second["id"] == first["id"]
    assert second["correlation_id"] == "corr-1"
    assert len(svc.list_runs(task.id, tenant_id="tenant-a")) == 1


def test_duplicate_run_event_is_suppressed_by_idempotency_key(tmp_path, monkeypatch):
    svc = _make_service(tmp_path, monkeypatch)
    task = _task("event-dedupe")
    svc.create(task, tenant_id="tenant-a")
    run = svc.create_run(task.id, "codex", tenant_id="tenant-a")

    first = svc.add_event(
        run["id"],
        "step.started",
        "codex",
        payload={"step": 1},
        idempotency_key="event-key-1",
        tenant_id="tenant-a",
    )
    second = svc.add_event(
        run["id"],
        "step.started",
        "codex",
        payload={"step": 2},
        idempotency_key="event-key-1",
        tenant_id="tenant-a",
    )

    assert second["id"] == first["id"]
    assert second["payload"] == {"step": 1}
    assert len(svc.list_events(task.id, tenant_id="tenant-a")) == 1


def test_stale_claim_token_rejected_when_starting_run(tmp_path, monkeypatch):
    from sos.services.squad.tasks import ClaimTokenMismatchError

    svc = _make_service(tmp_path, monkeypatch)
    task = _task("stale-claim")
    svc.create(task, tenant_id="tenant-a")
    claim = svc.claim(task.id, "codex", attempt=0, tenant_id="tenant-a")

    assert claim.claim_token
    with pytest.raises(ClaimTokenMismatchError):
        svc.create_run(
            task.id,
            "codex",
            claim_token=str(uuid.uuid4()),
            tenant_id="tenant-a",
        )

    run = svc.create_run(
        task.id,
        "codex",
        claim_token=claim.claim_token,
        tenant_id="tenant-a",
    )
    assert run["claim_token"] == claim.claim_token


def test_artifacts_are_tenant_scoped_and_idempotent(tmp_path, monkeypatch):
    svc = _make_service(tmp_path, monkeypatch)
    task = _task("artifact")
    svc.create(task, tenant_id="tenant-a")
    run = svc.create_run(task.id, "codex", tenant_id="tenant-a")

    first = svc.add_artifact(
        run["id"],
        "log",
        "r2://bucket/log.txt",
        metadata={"sha256": "abc"},
        idempotency_key="artifact-key-1",
        tenant_id="tenant-a",
    )
    second = svc.add_artifact(
        run["id"],
        "log",
        "r2://bucket/log-new.txt",
        idempotency_key="artifact-key-1",
        tenant_id="tenant-a",
    )

    assert second["id"] == first["id"]
    assert second["uri"] == "r2://bucket/log.txt"
    assert svc.list_artifacts(task.id, tenant_id="tenant-a")[0]["metadata"] == {"sha256": "abc"}
    with pytest.raises(KeyError):
        svc.add_artifact(run["id"], "log", "r2://leak", tenant_id="tenant-b")
