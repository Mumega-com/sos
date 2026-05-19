from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path


def test_register_and_prune_completed_worker(tmp_path, monkeypatch):
    from sos.services.health import worker_teardown

    registry_file = tmp_path / "workers.json"
    state_dir = tmp_path
    worktree_root = tmp_path / "worktrees"
    repo_dir = tmp_path / "repo"
    worktree_dir = worktree_root / "demo-worker"
    repo_dir.mkdir()
    worktree_dir.mkdir(parents=True)

    monkeypatch.setattr(worker_teardown, "STATE_DIR", state_dir)
    monkeypatch.setattr(worker_teardown, "WORKER_REGISTRY_FILE", registry_file)
    monkeypatch.setattr(worker_teardown, "WORKTREE_ROOT", worktree_root)

    calls = {"removed": 0}

    def fake_run(cmd, capture_output=True, timeout=20):
        calls["removed"] += 1
        class Result:
            returncode = 0
        return Result()

    monkeypatch.setattr(worker_teardown.subprocess, "run", fake_run)

    worker_teardown.register_worker(
        {
            "worker_id": "demo",
            "repo_path": str(repo_dir),
            "worktree_path": str(worktree_dir),
            "state": "completed",
            "last_active_at": (datetime.now(timezone.utc) - timedelta(minutes=45)).isoformat(),
        }
    )

    pruned = worker_teardown.prune_stale_workers()
    assert len(pruned) == 1
    assert pruned[0]["worker_id"] == "demo"
    assert calls["removed"] >= 1


def test_active_worker_not_pruned(tmp_path, monkeypatch):
    from sos.services.health import worker_teardown

    registry_file = tmp_path / "workers.json"
    state_dir = tmp_path
    worktree_root = tmp_path / "worktrees"
    monkeypatch.setattr(worker_teardown, "STATE_DIR", state_dir)
    monkeypatch.setattr(worker_teardown, "WORKER_REGISTRY_FILE", registry_file)
    monkeypatch.setattr(worker_teardown, "WORKTREE_ROOT", worktree_root)

    worker_teardown.register_worker(
        {
            "worker_id": "active",
            "repo_path": str(tmp_path / "repo"),
            "worktree_path": str(worktree_root / "active"),
            "state": "running",
            "last_active_at": datetime.now(timezone.utc).isoformat(),
        }
    )

    pruned = worker_teardown.prune_stale_workers()
    assert pruned == []


def test_safe_worktree_path_rejects_external_path(tmp_path, monkeypatch):
    from sos.services.health import worker_teardown

    worktree_root = tmp_path / "worktrees"
    monkeypatch.setattr(worker_teardown, "WORKTREE_ROOT", worktree_root)
    assert worker_teardown._safe_worktree_path("/tmp/not-managed") is None
