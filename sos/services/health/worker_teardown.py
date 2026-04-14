"""
Worker teardown policy for cold, project-local workers.

Managed workers are disposable. They are expected to run in isolated worktrees
under ~/.sos/worktrees, return a result, then get pruned.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from datetime import datetime, timedelta, timezone
from pathlib import Path


STATE_DIR = Path.home() / ".sos" / "state"
WORKER_REGISTRY_FILE = STATE_DIR / "workers.json"
WORKTREE_ROOT = Path.home() / ".sos" / "worktrees"

TEARDOWN_STALE_MINUTES = 180
TEARDOWN_COMPLETED_GRACE_MINUTES = 30
ACTIVE_STATES = {"queued", "claimed", "running", "busy", "in_progress"}
DONE_STATES = {"completed", "failed", "parked"}


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _parse_iso(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except Exception:
        return None


def _default_registry() -> dict:
    return {
        "_doc": "SOS worker registry for disposable project-local worktrees.",
        "workers": [],
    }


def load_worker_registry() -> dict:
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    WORKTREE_ROOT.mkdir(parents=True, exist_ok=True)
    if not WORKER_REGISTRY_FILE.exists():
        registry = _default_registry()
        WORKER_REGISTRY_FILE.write_text(json.dumps(registry, indent=2) + "\n")
        return registry
    try:
        registry = json.loads(WORKER_REGISTRY_FILE.read_text())
    except (OSError, json.JSONDecodeError):
        registry = _default_registry()
    registry.setdefault("workers", [])
    return registry


def save_worker_registry(registry: dict) -> None:
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    WORKER_REGISTRY_FILE.write_text(json.dumps(registry, indent=2) + "\n")


def register_worker(entry: dict) -> None:
    registry = load_worker_registry()
    workers = [w for w in registry.get("workers", []) if w.get("worker_id") != entry.get("worker_id")]
    entry.setdefault("registered_at", _now().isoformat())
    entry.setdefault("last_active_at", entry["registered_at"])
    entry.setdefault("state", "queued")
    workers.append(entry)
    registry["workers"] = workers
    save_worker_registry(registry)


def touch_worker(worker_id: str, state: str | None = None) -> None:
    registry = load_worker_registry()
    for worker in registry.get("workers", []):
        if worker.get("worker_id") == worker_id:
            worker["last_active_at"] = _now().isoformat()
            if state:
                worker["state"] = state
            break
    save_worker_registry(registry)


def _safe_worktree_path(path: str | None) -> Path | None:
    if not path:
        return None
    try:
        resolved = Path(path).expanduser().resolve()
    except FileNotFoundError:
        resolved = Path(path).expanduser()
    try:
        resolved.relative_to(WORKTREE_ROOT.resolve())
    except Exception:
        return None
    return resolved


def _kill_tmux_session(session: str | None) -> bool:
    if not session:
        return False
    probe = subprocess.run(["tmux", "has-session", "-t", session], capture_output=True, timeout=5)
    if probe.returncode != 0:
        return False
    kill = subprocess.run(["tmux", "kill-session", "-t", session], capture_output=True, timeout=5)
    return kill.returncode == 0


def _remove_worktree(repo_path: str | None, worktree_path: str | None) -> bool:
    safe_path = _safe_worktree_path(worktree_path)
    if safe_path is None:
        return False

    removed = False
    repo = Path(repo_path).expanduser() if repo_path else None
    if repo and repo.exists():
        cmd = ["git", "-C", str(repo), "worktree", "remove", "--force", str(safe_path)]
        result = subprocess.run(cmd, capture_output=True, timeout=20)
        removed = result.returncode == 0

    if safe_path.exists():
        shutil.rmtree(safe_path, ignore_errors=True)
        removed = not safe_path.exists() or removed

    return removed


def _should_prune(worker: dict, now: datetime) -> bool:
    state = str(worker.get("state", "queued"))
    last_active = _parse_iso(worker.get("last_active_at")) or _parse_iso(worker.get("registered_at"))
    if last_active is None:
        return state not in ACTIVE_STATES

    age_minutes = (now - last_active).total_seconds() / 60
    if state in DONE_STATES:
        return age_minutes >= TEARDOWN_COMPLETED_GRACE_MINUTES
    if state in ACTIVE_STATES:
        return age_minutes >= TEARDOWN_STALE_MINUTES
    return age_minutes >= TEARDOWN_COMPLETED_GRACE_MINUTES


def prune_stale_workers(now: datetime | None = None) -> list[dict]:
    current = now or _now()
    registry = load_worker_registry()
    remaining: list[dict] = []
    pruned: list[dict] = []

    for worker in registry.get("workers", []):
        if not _should_prune(worker, current):
            remaining.append(worker)
            continue

        pruned.append(
            {
                "worker_id": worker.get("worker_id"),
                "session_killed": _kill_tmux_session(worker.get("session")),
                "worktree_removed": _remove_worktree(worker.get("repo_path"), worker.get("worktree_path")),
                "state": worker.get("state", "unknown"),
            }
        )

    registry["workers"] = remaining
    save_worker_registry(registry)
    return pruned
