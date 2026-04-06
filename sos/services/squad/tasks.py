from __future__ import annotations

import json
import sqlite3
from dataclasses import asdict
from typing import Any

from sos.contracts.squad import RoutingDecision, SquadTask, TaskClaim, TaskPriority, TaskStatus
from sos.kernel import Response, ResponseStatus
from sos.observability.logging import get_logger
from sos.services.squad.service import DEFAULT_TENANT_ID, SquadBus, SquadDB, now_iso


log = get_logger("squad_tasks")


def _loads(value: str | None, fallback: Any) -> Any:
    return json.loads(value) if value else fallback


def _dumps(value: Any) -> str:
    return json.dumps(value)


def row_to_task(row: sqlite3.Row) -> SquadTask:
    return SquadTask(
        id=row["id"],
        squad_id=row["squad_id"],
        title=row["title"],
        description=row["description"],
        status=TaskStatus(row["status"]),
        priority=TaskPriority(row["priority"]),
        assignee=row["assignee"],
        skill_id=row["skill_id"],
        project=row["project"],
        labels=_loads(row["labels_json"], []),
        blocked_by=_loads(row["blocked_by_json"], []),
        blocks=_loads(row["blocks_json"], []),
        inputs=_loads(row["inputs_json"], {}),
        result=_loads(row["result_json"], {}),
        token_budget=row["token_budget"],
        bounty=_loads(row["bounty_json"], {}),
        external_ref=row["external_ref"],
        created_at=row["created_at"],
        updated_at=row["updated_at"],
        completed_at=row["completed_at"],
        claimed_at=row["claimed_at"],
        attempt=row["attempt"],
    )


class SquadTaskService:
    def __init__(self, db: SquadDB | None = None, bus: SquadBus | None = None):
        self.db = db or SquadDB()
        self.bus = bus or SquadBus()

    def create(self, task: SquadTask, actor: str = "system", tenant_id: str = DEFAULT_TENANT_ID) -> Response:
        timestamp = now_iso()
        if not task.created_at:
            task.created_at = timestamp
        task.updated_at = timestamp
        with self.db.connect() as conn:
            conn.execute(
                """
                INSERT INTO squad_tasks (
                    id, tenant_id, squad_id, title, description, status, priority, assignee, skill_id, project,
                    labels_json, blocked_by_json, blocks_json, inputs_json, result_json, token_budget,
                    bounty_json, external_ref, created_at, updated_at, completed_at, claimed_at, attempt
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    task.id,
                    tenant_id,
                    task.squad_id,
                    task.title,
                    task.description,
                    task.status.value,
                    task.priority.value,
                    task.assignee,
                    task.skill_id,
                    task.project,
                    _dumps(task.labels),
                    _dumps(task.blocked_by),
                    _dumps(task.blocks),
                    _dumps(task.inputs),
                    _dumps(task.result),
                    task.token_budget,
                    _dumps(task.bounty),
                    task.external_ref,
                    task.created_at,
                    task.updated_at,
                    task.completed_at,
                    task.claimed_at,
                    task.attempt,
                ),
            )
        self.bus.emit("task.created", task.squad_id, actor, asdict(task))
        return Response(message_id=task.id, status=ResponseStatus.SUCCESS, data={"task": asdict(task)})

    def get(self, task_id: str, tenant_id: str | None = DEFAULT_TENANT_ID) -> SquadTask | None:
        with self.db.connect() as conn:
            if tenant_id is None:
                row = conn.execute("SELECT * FROM squad_tasks WHERE id = ?", (task_id,)).fetchone()
            else:
                row = conn.execute("SELECT * FROM squad_tasks WHERE id = ? AND tenant_id = ?", (task_id, tenant_id)).fetchone()
        return row_to_task(row) if row else None

    def list(
        self,
        squad_id: str | None = None,
        status: TaskStatus | None = None,
        tenant_id: str | None = DEFAULT_TENANT_ID,
    ) -> list[SquadTask]:
        query = "SELECT * FROM squad_tasks WHERE 1=1"
        params: list[Any] = []
        if tenant_id is not None:
            query += " AND tenant_id = ?"
            params.append(tenant_id)
        if squad_id:
            query += " AND squad_id = ?"
            params.append(squad_id)
        if status:
            query += " AND status = ?"
            params.append(status.value)
        query += " ORDER BY updated_at DESC"
        with self.db.connect() as conn:
            rows = conn.execute(query, params).fetchall()
        return [row_to_task(row) for row in rows]

    def score_task(self, task: SquadTask) -> float:
        impact = float(task.inputs.get("impact", 1))
        urgency = float(task.inputs.get("urgency", 1))
        cost = float(task.inputs.get("cost", 1) or 1)
        unblock_value = max(len(task.blocks), 1)
        return impact * urgency * unblock_value / cost

    def route(
        self,
        task_id: str,
        assignee: str | None,
        skill_id: str | None,
        reason: str,
        actor: str = "router",
        tenant_id: str | None = DEFAULT_TENANT_ID,
    ) -> RoutingDecision:
        task = self.get(task_id, tenant_id=tenant_id)
        if not task:
            raise KeyError(f"Task not found: {task_id}")
        decision = RoutingDecision(
            task_id=task.id,
            skill_id=skill_id,
            assignee=assignee,
            reason=reason,
            score=self.score_task(task),
        )
        task.assignee = assignee
        task.skill_id = skill_id
        task.status = TaskStatus.QUEUED
        task.updated_at = now_iso()
        with self.db.connect() as conn:
            conn.execute(
                "UPDATE squad_tasks SET assignee = ?, skill_id = ?, status = ?, updated_at = ? WHERE id = ? AND tenant_id = ?",
                (assignee, skill_id, task.status.value, task.updated_at, task.id, tenant_id if tenant_id is not None else DEFAULT_TENANT_ID),
            )
        self.bus.emit("task.routed", task.squad_id, actor, asdict(decision))
        return decision

    def claim(
        self,
        task_id: str,
        assignee: str,
        attempt: int,
        actor: str | None = None,
        tenant_id: str | None = DEFAULT_TENANT_ID,
    ) -> TaskClaim:
        claimed_at = now_iso()
        with self.db.connect() as conn:
            if tenant_id is None:
                row = conn.execute("SELECT * FROM squad_tasks WHERE id = ?", (task_id,)).fetchone()
            else:
                row = conn.execute("SELECT * FROM squad_tasks WHERE id = ? AND tenant_id = ?", (task_id, tenant_id)).fetchone()
            if not row:
                raise KeyError(f"Task not found: {task_id}")
            task = row_to_task(row)
            if task.attempt != attempt:
                raise ValueError(f"Claim attempt mismatch for {task_id}: expected {task.attempt}, got {attempt}")
            if task.status not in {TaskStatus.BACKLOG, TaskStatus.QUEUED}:
                raise ValueError(f"Task {task_id} is not claimable from status {task.status.value}")
            new_attempt = task.attempt + 1
            updated = conn.execute(
                """
                UPDATE squad_tasks
                SET assignee = ?, status = ?, claimed_at = ?, updated_at = ?, attempt = ?
                WHERE id = ? AND tenant_id = ? AND attempt = ? AND status IN (?, ?)
                """,
                (
                    assignee,
                    TaskStatus.CLAIMED.value,
                    claimed_at,
                    claimed_at,
                    new_attempt,
                    task_id,
                    tenant_id if tenant_id is not None else DEFAULT_TENANT_ID,
                    attempt,
                    TaskStatus.BACKLOG.value,
                    TaskStatus.QUEUED.value,
                ),
            )
            if updated.rowcount != 1:
                raise ValueError(f"Task {task_id} was claimed concurrently")
        claim = TaskClaim(task_id=task_id, assignee=assignee, claimed_at=claimed_at, attempt=new_attempt)
        self.bus.emit("task.claimed", task.squad_id, actor or assignee, asdict(claim))
        return claim

    def complete(
        self,
        task_id: str,
        result: dict[str, Any],
        actor: str = "system",
        tenant_id: str | None = DEFAULT_TENANT_ID,
    ) -> SquadTask:
        task = self.get(task_id, tenant_id=tenant_id)
        if not task:
            raise KeyError(f"Task not found: {task_id}")
        timestamp = now_iso()
        task.result = result
        task.status = TaskStatus.DONE
        task.completed_at = timestamp
        task.updated_at = timestamp
        with self.db.connect() as conn:
            conn.execute(
                "UPDATE squad_tasks SET result_json = ?, status = ?, completed_at = ?, updated_at = ? WHERE id = ? AND tenant_id = ?",
                (_dumps(task.result), task.status.value, task.completed_at, task.updated_at, task.id, tenant_id if tenant_id is not None else DEFAULT_TENANT_ID),
            )
        self.bus.emit("task.completed", task.squad_id, actor, {"task_id": task.id, "result": result})
        return task

    def fail(
        self,
        task_id: str,
        error: str,
        actor: str = "system",
        tenant_id: str | None = DEFAULT_TENANT_ID,
    ) -> SquadTask:
        task = self.get(task_id, tenant_id=tenant_id)
        if not task:
            raise KeyError(f"Task not found: {task_id}")
        task.result = {"error": error}
        task.status = TaskStatus.FAILED
        task.updated_at = now_iso()
        with self.db.connect() as conn:
            conn.execute(
                "UPDATE squad_tasks SET result_json = ?, status = ?, updated_at = ? WHERE id = ? AND tenant_id = ?",
                (_dumps(task.result), task.status.value, task.updated_at, task.id, tenant_id if tenant_id is not None else DEFAULT_TENANT_ID),
            )
        self.bus.emit("task.failed", task.squad_id, actor, {"task_id": task.id, "error": error})
        return task
