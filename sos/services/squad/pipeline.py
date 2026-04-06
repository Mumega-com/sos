"""Pipeline service — CI/CD automation attached to a Squad."""
from __future__ import annotations

import json
import os
import sqlite3
import subprocess
import uuid
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from sos.contracts.squad import PipelineRun, PipelineSpec
from sos.observability.logging import get_logger
from sos.services.squad.service import DEFAULT_TENANT_ID

log = get_logger("pipeline_service")

SOS_DATA_DIR = Path.home() / ".sos" / "data"
DB_PATH = SOS_DATA_DIR / "squads.db"

# Locations to probe when resolving a repo slug → local path
# Override SOS_WORKSPACE_ROOT env var to add a custom root (e.g. a mounted volume)
_LOCAL_ROOTS: list[Path] = list(
    filter(
        None,
        [
            Path(os.environ["SOS_WORKSPACE_ROOT"]) if os.environ.get("SOS_WORKSPACE_ROOT") else None,
            Path.home(),
        ],
    )
)

STEP_TIMEOUT = 120  # seconds per subprocess step


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _dumps(value: Any) -> str:
    return json.dumps(value)


def _loads(value: str | None, fallback: Any) -> Any:
    if not value:
        return fallback
    return json.loads(value)


def _resolve_local_path(repo: str) -> Path | None:
    """
    Given "owner/project" or just "project", find the local directory.
    Checks SOS_WORKSPACE_ROOT/<project> (if set) then ~/<project>.
    """
    project = repo.split("/")[-1]
    for root in _LOCAL_ROOTS:
        candidate = root / project
        if candidate.is_dir():
            return candidate
    return None


def _run_step(cmd: str, cwd: Path, timeout: int = STEP_TIMEOUT) -> tuple[int, str]:
    """Run a shell command, return (returncode, combined output)."""
    result = subprocess.run(
        cmd,
        shell=True,
        cwd=str(cwd),
        capture_output=True,
        text=True,
        timeout=timeout,
    )
    output = result.stdout + result.stderr
    return result.returncode, output


class _PipelineDB:
    def __init__(self, db_path: Path = DB_PATH) -> None:
        self.db_path = db_path
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._init_db()

    def connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        return conn

    def _init_db(self) -> None:
        with self.connect() as conn:
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS pipeline_specs (
                    squad_id TEXT PRIMARY KEY,
                    tenant_id TEXT NOT NULL DEFAULT 'default',
                    repo TEXT NOT NULL,
                    workdir TEXT NOT NULL,
                    default_branch TEXT NOT NULL,
                    feature_branch_prefix TEXT NOT NULL,
                    pr_mode TEXT NOT NULL,
                    build_cmd TEXT NOT NULL,
                    test_cmd TEXT NOT NULL,
                    deploy_cmd TEXT NOT NULL,
                    smoke_cmd TEXT NOT NULL,
                    deploy_mode TEXT NOT NULL,
                    deploy_on_task_labels TEXT NOT NULL,
                    rollback_cmd TEXT NOT NULL,
                    enabled INTEGER NOT NULL
                );

                CREATE TABLE IF NOT EXISTS pipeline_runs (
                    id TEXT PRIMARY KEY,
                    tenant_id TEXT NOT NULL DEFAULT 'default',
                    squad_id TEXT NOT NULL,
                    task_id TEXT NOT NULL,
                    status TEXT NOT NULL,
                    commit_sha TEXT NOT NULL,
                    branch TEXT NOT NULL,
                    pr_url TEXT NOT NULL,
                    logs TEXT NOT NULL,
                    error TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    completed_at TEXT NOT NULL
                );

                CREATE INDEX IF NOT EXISTS idx_pipeline_runs_squad
                    ON pipeline_runs (squad_id, created_at DESC);
                """
            )
            self._ensure_column(conn, "pipeline_specs", "tenant_id", "TEXT NOT NULL DEFAULT 'default'")
            self._ensure_column(conn, "pipeline_runs", "tenant_id", "TEXT NOT NULL DEFAULT 'default'")
            conn.executescript(
                """
                CREATE INDEX IF NOT EXISTS idx_pipeline_specs_tenant
                    ON pipeline_specs (tenant_id, squad_id);
                CREATE INDEX IF NOT EXISTS idx_pipeline_runs_tenant
                    ON pipeline_runs (tenant_id, squad_id, created_at DESC);
                """
            )

    @staticmethod
    def _ensure_column(conn: sqlite3.Connection, table: str, column: str, ddl: str) -> None:
        existing = {row["name"] for row in conn.execute(f"PRAGMA table_info({table})").fetchall()}
        if column not in existing:
            conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {ddl}")


def _row_to_spec(row: sqlite3.Row) -> PipelineSpec:
    return PipelineSpec(
        squad_id=row["squad_id"],
        repo=row["repo"],
        workdir=row["workdir"],
        default_branch=row["default_branch"],
        feature_branch_prefix=row["feature_branch_prefix"],
        pr_mode=row["pr_mode"],
        build_cmd=row["build_cmd"],
        test_cmd=row["test_cmd"],
        deploy_cmd=row["deploy_cmd"],
        smoke_cmd=row["smoke_cmd"],
        deploy_mode=row["deploy_mode"],
        deploy_on_task_labels=_loads(row["deploy_on_task_labels"], []),
        rollback_cmd=row["rollback_cmd"],
        enabled=bool(row["enabled"]),
    )


def _row_to_run(row: sqlite3.Row) -> PipelineRun:
    return PipelineRun(
        id=row["id"],
        squad_id=row["squad_id"],
        task_id=row["task_id"],
        status=row["status"],
        commit_sha=row["commit_sha"],
        branch=row["branch"],
        pr_url=row["pr_url"],
        logs=row["logs"],
        error=row["error"],
        created_at=row["created_at"],
        completed_at=row["completed_at"],
    )


class PipelineService:
    def __init__(self, db: _PipelineDB | None = None) -> None:
        self._db = db or _PipelineDB()
        self._bus = _load_bus()

    # ── Spec ──────────────────────────────────────────────────────────────────

    def set_pipeline(self, squad_id: str, spec: PipelineSpec, tenant_id: str = DEFAULT_TENANT_ID) -> PipelineSpec:
        spec.squad_id = squad_id
        with self._db.connect() as conn:
            conn.execute(
                """
                INSERT INTO pipeline_specs (
                    squad_id, tenant_id, repo, workdir, default_branch, feature_branch_prefix,
                    pr_mode, build_cmd, test_cmd, deploy_cmd, smoke_cmd,
                    deploy_mode, deploy_on_task_labels, rollback_cmd, enabled
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(squad_id) DO UPDATE SET
                    tenant_id = excluded.tenant_id,
                    repo = excluded.repo,
                    workdir = excluded.workdir,
                    default_branch = excluded.default_branch,
                    feature_branch_prefix = excluded.feature_branch_prefix,
                    pr_mode = excluded.pr_mode,
                    build_cmd = excluded.build_cmd,
                    test_cmd = excluded.test_cmd,
                    deploy_cmd = excluded.deploy_cmd,
                    smoke_cmd = excluded.smoke_cmd,
                    deploy_mode = excluded.deploy_mode,
                    deploy_on_task_labels = excluded.deploy_on_task_labels,
                    rollback_cmd = excluded.rollback_cmd,
                    enabled = excluded.enabled
                """,
                (
                    squad_id,
                    tenant_id,
                    spec.repo,
                    spec.workdir,
                    spec.default_branch,
                    spec.feature_branch_prefix,
                    spec.pr_mode,
                    spec.build_cmd,
                    spec.test_cmd,
                    spec.deploy_cmd,
                    spec.smoke_cmd,
                    spec.deploy_mode,
                    _dumps(spec.deploy_on_task_labels),
                    spec.rollback_cmd,
                    int(spec.enabled),
                ),
            )
        return spec

    def get_pipeline(self, squad_id: str, tenant_id: str | None = DEFAULT_TENANT_ID) -> PipelineSpec | None:
        with self._db.connect() as conn:
            if tenant_id is None:
                row = conn.execute(
                    "SELECT * FROM pipeline_specs WHERE squad_id = ?", (squad_id,)
                ).fetchone()
            else:
                row = conn.execute(
                    "SELECT * FROM pipeline_specs WHERE squad_id = ? AND tenant_id = ?", (squad_id, tenant_id)
                ).fetchone()
        return _row_to_spec(row) if row else None

    # ── Run ───────────────────────────────────────────────────────────────────

    def _save_run(self, run: PipelineRun, tenant_id: str = DEFAULT_TENANT_ID) -> PipelineRun:
        with self._db.connect() as conn:
            conn.execute(
                """
                INSERT INTO pipeline_runs (
                    id, tenant_id, squad_id, task_id, status, commit_sha, branch,
                    pr_url, logs, error, created_at, completed_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(id) DO UPDATE SET
                    tenant_id = excluded.tenant_id,
                    status = excluded.status,
                    commit_sha = excluded.commit_sha,
                    branch = excluded.branch,
                    pr_url = excluded.pr_url,
                    logs = excluded.logs,
                    error = excluded.error,
                    completed_at = excluded.completed_at
                """,
                (
                    run.id,
                    tenant_id,
                    run.squad_id,
                    run.task_id,
                    run.status,
                    run.commit_sha,
                    run.branch,
                    run.pr_url,
                    run.logs,
                    run.error,
                    run.created_at,
                    run.completed_at,
                ),
            )
        return run

    def _get_run(self, run_id: str, tenant_id: str | None = DEFAULT_TENANT_ID) -> PipelineRun | None:
        with self._db.connect() as conn:
            if tenant_id is None:
                row = conn.execute(
                    "SELECT * FROM pipeline_runs WHERE id = ?", (run_id,)
                ).fetchone()
            else:
                row = conn.execute(
                    "SELECT * FROM pipeline_runs WHERE id = ? AND tenant_id = ?", (run_id, tenant_id)
                ).fetchone()
        return _row_to_run(row) if row else None

    def _append_log(self, run: PipelineRun, line: str) -> None:
        run.logs += line if run.logs.endswith("\n") or not run.logs else "\n" + line

    def _resolve_cwd(self, spec: PipelineSpec) -> Path:
        local = _resolve_local_path(spec.repo)
        if not local:
            raise FileNotFoundError(
                f"Cannot resolve local path for repo '{spec.repo}'. "
                "Clone it first or check SOS_WORKSPACE_ROOT and ~/."
            )
        cwd = local / spec.workdir
        if not cwd.exists():
            raise FileNotFoundError(f"workdir '{cwd}' does not exist")
        return cwd

    def run_pipeline(
        self,
        squad_id: str,
        task_id: str,
        actor: str = "system",
        tenant_id: str | None = DEFAULT_TENANT_ID,
    ) -> PipelineRun:
        spec = self.get_pipeline(squad_id, tenant_id=tenant_id)
        if not spec:
            raise KeyError(f"No pipeline configured for squad '{squad_id}'")
        if not spec.enabled:
            raise ValueError(f"Pipeline for squad '{squad_id}' is disabled")

        run = PipelineRun(
            id=str(uuid.uuid4()),
            squad_id=squad_id,
            task_id=task_id,
            status="pending",
            created_at=_now(),
        )
        self._save_run(run, tenant_id=tenant_id if tenant_id is not None else DEFAULT_TENANT_ID)
        self._emit("pipeline.started", squad_id, actor, {"run_id": run.id, "task_id": task_id})

        try:
            cwd = self._resolve_cwd(spec)
        except FileNotFoundError as exc:
            run.status = "failed"
            run.error = str(exc)
            run.completed_at = _now()
            self._save_run(run, tenant_id=tenant_id if tenant_id is not None else DEFAULT_TENANT_ID)
            self._emit("pipeline.failed", squad_id, actor, {"run_id": run.id, "error": run.error})
            return run

        # ── test step ─────────────────────────────────────────────────────────
        if spec.test_cmd:
            run.status = "testing"
            self._save_run(run, tenant_id=tenant_id if tenant_id is not None else DEFAULT_TENANT_ID)
            self._append_log(run, f"[test] {spec.test_cmd}")
            try:
                rc, out = _run_step(spec.test_cmd, cwd)
            except subprocess.TimeoutExpired:
                rc, out = 1, "timeout after 120s"
            self._append_log(run, out)
            if rc != 0:
                run.status = "failed"
                run.error = f"test step failed (exit {rc})"
                run.completed_at = _now()
                self._save_run(run)
                self._emit("pipeline.failed", squad_id, actor, {"run_id": run.id, "error": run.error})
                return run

        # ── build step ────────────────────────────────────────────────────────
        if spec.build_cmd:
            run.status = "building"
            self._save_run(run, tenant_id=tenant_id if tenant_id is not None else DEFAULT_TENANT_ID)
            self._append_log(run, f"[build] {spec.build_cmd}")
            try:
                rc, out = _run_step(spec.build_cmd, cwd)
            except subprocess.TimeoutExpired:
                rc, out = 1, "timeout after 120s"
            self._append_log(run, out)
            if rc != 0:
                run.status = "failed"
                run.error = f"build step failed (exit {rc})"
                run.completed_at = _now()
                self._save_run(run)
                self._emit("pipeline.failed", squad_id, actor, {"run_id": run.id, "error": run.error})
                return run

        # ── deploy decision ───────────────────────────────────────────────────
        if spec.deploy_mode == "manual":
            run.status = "awaiting_approval"
            self._save_run(run, tenant_id=tenant_id if tenant_id is not None else DEFAULT_TENANT_ID)
            self._emit(
                "pipeline.approval_needed",
                squad_id,
                actor,
                {"run_id": run.id, "task_id": task_id},
            )
            return run

        # auto deploy
        return self._do_deploy(run, spec, cwd, actor, tenant_id=tenant_id)

    def approve_deploy(
        self,
        run_id: str,
        actor: str = "system",
        tenant_id: str | None = DEFAULT_TENANT_ID,
    ) -> PipelineRun:
        run = self._get_run(run_id, tenant_id=tenant_id)
        if not run:
            raise KeyError(f"Pipeline run not found: {run_id}")
        if run.status != "awaiting_approval":
            raise ValueError(f"Run '{run_id}' is not awaiting approval (status={run.status})")

        spec = self.get_pipeline(run.squad_id, tenant_id=tenant_id)
        if not spec:
            raise KeyError(f"No pipeline spec for squad '{run.squad_id}'")

        try:
            cwd = self._resolve_cwd(spec)
        except FileNotFoundError as exc:
            run.status = "failed"
            run.error = str(exc)
            run.completed_at = _now()
            self._save_run(run, tenant_id=tenant_id if tenant_id is not None else DEFAULT_TENANT_ID)
            self._emit("pipeline.failed", run.squad_id, actor, {"run_id": run.id, "error": run.error})
            return run

        return self._do_deploy(run, spec, cwd, actor, tenant_id=tenant_id)

    def _do_deploy(
        self,
        run: PipelineRun,
        spec: PipelineSpec,
        cwd: Path,
        actor: str,
        tenant_id: str | None = DEFAULT_TENANT_ID,
    ) -> PipelineRun:
        if spec.deploy_cmd:
            run.status = "deploying"
            self._save_run(run, tenant_id=tenant_id if tenant_id is not None else DEFAULT_TENANT_ID)
            self._append_log(run, f"[deploy] {spec.deploy_cmd}")
            try:
                rc, out = _run_step(spec.deploy_cmd, cwd)
            except subprocess.TimeoutExpired:
                rc, out = 1, "timeout after 120s"
            self._append_log(run, out)
            if rc != 0:
                run.status = "failed"
                run.error = f"deploy step failed (exit {rc})"
                run.completed_at = _now()
                self._save_run(run, tenant_id=tenant_id if tenant_id is not None else DEFAULT_TENANT_ID)
                self._emit("pipeline.failed", run.squad_id, actor, {"run_id": run.id, "error": run.error})
                return run

        # ── smoke step ────────────────────────────────────────────────────────
        if spec.smoke_cmd:
            run.status = "smoke"
            self._save_run(run, tenant_id=tenant_id if tenant_id is not None else DEFAULT_TENANT_ID)
            self._append_log(run, f"[smoke] {spec.smoke_cmd}")
            try:
                rc, out = _run_step(spec.smoke_cmd, cwd)
            except subprocess.TimeoutExpired:
                rc, out = 1, "timeout after 120s"
            self._append_log(run, out)
            if rc != 0:
                run.status = "failed"
                run.error = f"smoke step failed (exit {rc})"
                run.completed_at = _now()
                self._save_run(run, tenant_id=tenant_id if tenant_id is not None else DEFAULT_TENANT_ID)
                self._emit("pipeline.failed", run.squad_id, actor, {"run_id": run.id, "error": run.error})
                return run

        run.status = "succeeded"
        run.completed_at = _now()
        self._save_run(run, tenant_id=tenant_id if tenant_id is not None else DEFAULT_TENANT_ID)
        self._emit("pipeline.succeeded", run.squad_id, actor, {"run_id": run.id})
        return run

    def rollback(
        self,
        run_id: str,
        actor: str = "system",
        tenant_id: str | None = DEFAULT_TENANT_ID,
    ) -> PipelineRun:
        run = self._get_run(run_id, tenant_id=tenant_id)
        if not run:
            raise KeyError(f"Pipeline run not found: {run_id}")

        spec = self.get_pipeline(run.squad_id, tenant_id=tenant_id)
        if not spec:
            raise KeyError(f"No pipeline spec for squad '{run.squad_id}'")

        if not spec.rollback_cmd:
            raise ValueError(f"No rollback_cmd configured for squad '{run.squad_id}'")

        try:
            cwd = self._resolve_cwd(spec)
        except FileNotFoundError as exc:
            run.status = "failed"
            run.error = str(exc)
            run.completed_at = _now()
            self._save_run(run, tenant_id=tenant_id if tenant_id is not None else DEFAULT_TENANT_ID)
            return run

        self._append_log(run, f"[rollback] {spec.rollback_cmd}")
        try:
            rc, out = _run_step(spec.rollback_cmd, cwd)
        except subprocess.TimeoutExpired:
            rc, out = 1, "timeout after 120s"
        self._append_log(run, out)

        run.status = "rolled_back" if rc == 0 else "failed"
        if rc != 0:
            run.error = f"rollback step failed (exit {rc})"
        run.completed_at = _now()
        self._save_run(run, tenant_id=tenant_id if tenant_id is not None else DEFAULT_TENANT_ID)
        return run

    def list_runs(
        self,
        squad_id: str,
        limit: int = 20,
        tenant_id: str | None = DEFAULT_TENANT_ID,
    ) -> list[PipelineRun]:
        with self._db.connect() as conn:
            if tenant_id is None:
                rows = conn.execute(
                    "SELECT * FROM pipeline_runs WHERE squad_id = ? ORDER BY created_at DESC LIMIT ?",
                    (squad_id, limit),
                ).fetchall()
            else:
                rows = conn.execute(
                    "SELECT * FROM pipeline_runs WHERE squad_id = ? AND tenant_id = ? ORDER BY created_at DESC LIMIT ?",
                    (squad_id, tenant_id, limit),
                ).fetchall()
        return [_row_to_run(row) for row in rows]

    # ── Bus ───────────────────────────────────────────────────────────────────

    def _emit(self, event_type: str, squad_id: str, actor: str, payload: dict[str, Any]) -> None:
        if self._bus:
            try:
                self._bus.emit(event_type, squad_id, actor, payload)
            except Exception as exc:
                log.warn("Pipeline bus emit failed", error=str(exc), event_type=event_type)


def _load_bus() -> Any:
    """Lazy-load SquadBus to avoid import-time Redis errors."""
    try:
        from sos.services.squad.service import SquadBus
        return SquadBus()
    except Exception:
        return None
