from __future__ import annotations

import asyncio
import json
import os
import re
import sqlite3
import uuid as _uuid
from dataclasses import asdict
from typing import Any

import httpx

from sos.contracts.done_check import DoneCheck, all_done
from sos.contracts.messages import TaskCompletedMessage, TaskCompletedPayload
from sos.contracts.squad import RoutingDecision, SquadTask, TaskClaim, TaskPriority, TaskStatus
from sos.kernel import Response, ResponseStatus
from sos.observability.logging import get_logger
from sos.services.squad.service import DEFAULT_TENANT_ID, SquadBus, SquadDB, now_iso


log = get_logger("squad_tasks")


class NotAllDoneError(ValueError):
    """Raised when a SquadTask's ``done_when`` gate is not satisfied on /complete.

    Distinct from ValueError so the app layer can map it to HTTP 400
    (client mis-submitted) rather than 409 (concurrent-state conflict).
    """


# --- Bus envelope helpers -----------------------------------------------------
# Squad emits v1 TaskCompletedMessage on the global squad stream so that health
# (conductance) and journeys (milestones) consumers can react without squad
# reaching into either service in-process. See P0-04, P0-05 in the 2026-04-17
# structural audit.

_SOURCE_AGENT_RE = re.compile(r"^[a-z][a-z0-9-]*$")
_CONTRACT_TASK_STATUS = {"done", "failed", "cancelled", "timeout"}


def _normalize_agent_source(agent_name: str | None) -> str:
    """Coerce an arbitrary agent name into a v1-valid `source` envelope field.

    The BusMessage envelope requires ``source`` to match
    ``^agent:[a-z][a-z0-9-]*$``. Squad task assignees come from user-supplied
    data so we normalize rather than reject: lowercase, non-alnum becomes ``-``,
    leading non-letters get an ``agent-`` prefix, empty falls back to ``squad``.
    """
    if not agent_name:
        return "agent:squad"
    slug = re.sub(r"[^a-z0-9-]+", "-", agent_name.strip().lower()).strip("-")
    if not slug:
        return "agent:squad"
    if not slug[0].isalpha():
        slug = f"agent-{slug}"
    return f"agent:{slug}" if _SOURCE_AGENT_RE.match(slug) else "agent:squad"


def _coerce_contract_status(status: TaskStatus) -> str:
    """Map squad's TaskStatus enum onto the contract's Literal type.

    Squad's enum has more terminal states (canceled/failed/done) plus
    intermediate ones. For task.completed we only ever emit terminal states;
    this collapses spelling differences (e.g. ``canceled`` → ``cancelled``).
    """
    raw = status.value if hasattr(status, "value") else str(status)
    if raw in _CONTRACT_TASK_STATUS:
        return raw
    if raw == "canceled":
        return "cancelled"
    if raw in {"failed"}:
        return "failed"
    # Any other terminal state is treated as success on the contract side.
    return "done"


def _emit_task_completed(task: SquadTask, actor: str, completed_at: str) -> None:
    """Publish a v1 TaskCompletedMessage to the per-squad global stream.

    Downstream consumers (health, journeys) pick up from
    ``sos:stream:global:squad:<squad_id>`` via SCAN, so we xadd to the same
    shape SquadBus.emit already uses.

    ``result`` carries fields outside the v1 envelope — ``agent_addr``,
    ``labels``, ``reward_mind``, ``squad_id``, ``bounty_id`` — so consumers
    can do their work without extra round-trips.
    """
    import redis as _redis  # local import keeps the module importable without redis
    agent_name = task.assignee or actor or "squad"
    source = _normalize_agent_source(agent_name)

    reward = 0.0
    try:
        if task.bounty and task.bounty.get("reward"):
            reward = float(task.bounty.get("reward") or 0.0)
    except (TypeError, ValueError):
        reward = 0.0

    result_extras: dict[str, Any] = {
        "agent_addr": agent_name,
        "labels": list(task.labels or []),
        "reward_mind": reward,
        "squad_id": task.squad_id,
    }
    if task.bounty and task.bounty.get("bounty_id"):
        result_extras["bounty_id"] = task.bounty["bounty_id"]
    if task.project:
        result_extras["project"] = task.project
    # Fold the original result dict in underneath so nothing is lost.
    if isinstance(task.result, dict):
        for k, v in task.result.items():
            result_extras.setdefault(k, v)

    try:
        message = TaskCompletedMessage(
            source=source,
            target="sos:channel:tasks",
            timestamp=completed_at,
            message_id=str(_uuid.uuid4()),
            payload=TaskCompletedPayload(
                task_id=task.id,
                status=_coerce_contract_status(task.status),  # type: ignore[arg-type]
                completed_at=completed_at,
                result=result_extras,
            ),
        )
    except Exception as exc:
        log.warn("task.completed envelope construction failed", task_id=task.id, error=str(exc))
        return

    stream = f"sos:stream:global:squad:{task.squad_id}"
    try:
        pw = os.environ.get("REDIS_PASSWORD", "")
        host = os.environ.get("REDIS_HOST", "127.0.0.1")
        port = int(os.environ.get("REDIS_PORT", "6379"))
        r = _redis.Redis(host=host, port=port, password=pw or None, decode_responses=True)
        r.xadd(stream, message.to_redis_fields(), maxlen=1000)
    except Exception as exc:
        log.warn("task.completed xadd failed", task_id=task.id, error=str(exc))


def _loads(value: str | None, fallback: Any) -> Any:
    return json.loads(value) if value else fallback


def _dumps(value: Any) -> str:
    return json.dumps(value)


def _task_to_bus_payload(task: SquadTask) -> dict[str, Any]:
    """asdict(task) with ``done_when`` rendered as plain dicts.

    Plain ``asdict`` on SquadTask keeps pydantic DoneCheck instances as-is,
    which breaks the downstream ``json.dumps`` inside ``SquadBus.emit``.
    Every bus emit that wants to round-trip a full task payload must go
    through this helper.
    """
    payload = asdict(task)
    payload["done_when"] = [
        item.model_dump() if isinstance(item, DoneCheck) else dict(item)
        for item in (task.done_when or [])
    ]
    return payload


def _rehydrate_done_when(raw: Any) -> list[DoneCheck]:
    """Coerce the stored JSON list into DoneCheck instances.

    Rows written before migration 0002 parse as ``[]`` (DB default).
    Malformed entries are dropped rather than raising — an operator
    fixing a bad row shouldn't lock out every /complete attempt.
    """
    if not raw:
        return []
    items = raw if isinstance(raw, list) else []
    out: list[DoneCheck] = []
    for item in items:
        if isinstance(item, DoneCheck):
            out.append(item)
            continue
        if isinstance(item, dict):
            try:
                out.append(DoneCheck(**item))
            except Exception as exc:  # pydantic.ValidationError et al
                log.warn("done_when rehydrate skipped malformed entry", error=str(exc))
    return out


def row_to_task(row: sqlite3.Row) -> SquadTask:
    # ``done_when_json`` column was added in Alembic 0002. Access via dict-
    # style lookup guarded by the row's key set so pre-migration test rows
    # (if any survive) don't KeyError here.
    done_when_raw = row["done_when_json"] if "done_when_json" in row.keys() else None
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
        done_when=_rehydrate_done_when(_loads(done_when_raw, [])),
        created_at=row["created_at"],
        updated_at=row["updated_at"],
        completed_at=row["completed_at"],
        claimed_at=row["claimed_at"],
        attempt=row["attempt"],
    )


# ── Deterministic Cost Estimation ─────────────────────────────────────────
# Ported from cli/mumega/core/task_hierarchy.py — every task gets a budget
# BEFORE it runs. No surprises.

# Token estimates by priority (proxy for complexity)
PRIORITY_TOKEN_BUDGET = {
    "critical": 20000,  # advanced — needs Opus
    "high":     8000,   # complex — needs Sonnet
    "medium":   3000,   # moderate — Flash is fine
    "low":      1500,   # simple — Haiku
}

# Model selection by priority (cheapest that can do the job)
PRIORITY_MODEL = {
    "critical": "opus",
    "high":     "sonnet",
    "medium":   "flash",
    "low":      "haiku",
}

# Real API cost per 1M tokens
MODEL_COST_PER_M = {
    "opus":    {"input": 15.00, "output": 75.00},
    "sonnet":  {"input": 3.00,  "output": 15.00},
    "haiku":   {"input": 0.25,  "output": 1.25},
    "flash":   {"input": 0.10,  "output": 0.40},
    "gpt-5.4": {"input": 2.50,  "output": 10.00},
}

# Fuel grade = which model tier
FUEL_GRADE_MODEL = {
    "diesel":   "flash",    # $0.009/task
    "regular":  "haiku",    # $0.025/task
    "premium":  "sonnet",   # $0.30/task
    "aviation": "opus",     # $1.50/task
    "codex":    "gpt-5.4",  # $0.225/task
}

# Internal cost (what it costs US in tokens)
FUEL_COST_CENTS = {
    "diesel":   0,     # free (Gemini Flash)
    "regular":  3,     # ~$0.025
    "premium":  30,    # ~$0.30
    "aviation": 150,   # ~$1.50
    "codex":    23,    # ~$0.225
}

# ── Agent Billable Rates ──────────────────────────────────────────────────────
# What we CHARGE the customer per task. Not token cost — VALUE of the work.
# Rate = expertise + codebase knowledge + reliability + model cost
# Pay-per-use: customer pays per completed task, not monthly subscription.

AGENT_RATE_CENTS_PER_TASK = {
    # Elders — deep codebase knowledge, architectural decisions
    "kasra":      2500,    # $25/task — architect, built the system, knows every file
    "athena":     3000,    # $30/task — queen, root gatekeeper, quality gate
    "codex":      2000,    # $20/task — infra + security specialist

    # Specialists — domain expertise
    "sol":        1500,    # $15/task — content quality, cosmic voice
    "mumega":     2000,    # $20/task — platform orchestrator

    # Workers — commodity execution
    "worker":      500,    # $5/task — cheap task execution (Haiku)
    "gemma":       200,    # $2/task — free model, bulk work
    "dandan":      500,    # $5/task — project lead (cheap model)

    # New agents — building expertise
    "trop":       1000,    # $10/task — learning the codebase
    "river":      1500,    # $15/task — oracle, strategy

    # Human
    "hadi":      20000,    # $200/task — founder, final decisions
}

# Margin: billable rate - fuel cost = gross margin per task
# Example: kasra does a critical task
#   fuel cost: $1.50 (opus)
#   billable:  $25.00
#   margin:    $23.50 (94%)

# Agent markup multiplier on token cost
# billable = internal_cost * MARKUP
# 10x across the board — simple, consistent, easy to explain
AGENT_MARKUP_DEFAULT = 10

# Override only where truly different
AGENT_MARKUP = {
    "hadi": 100,    # founder time is 100x token cost
}

def get_markup(agent: str) -> int:
    return AGENT_MARKUP.get(agent, AGENT_MARKUP_DEFAULT)


def estimate_task_budget(priority: str, fuel_grade: str | None = None) -> dict:
    """Estimate token budget and cost for a task BEFORE execution."""
    priority = priority.lower()
    tokens = PRIORITY_TOKEN_BUDGET.get(priority, 3000)
    model = PRIORITY_MODEL.get(priority, "flash")

    if fuel_grade:
        model = FUEL_GRADE_MODEL.get(fuel_grade, model)

    rates = MODEL_COST_PER_M.get(model, MODEL_COST_PER_M["flash"])
    # Assume 70% input, 30% output split
    input_tokens = int(tokens * 0.7)
    output_tokens = int(tokens * 0.3)
    cost_usd = (input_tokens / 1_000_000 * rates["input"]) + (output_tokens / 1_000_000 * rates["output"])
    cost_cents = max(1, int(cost_usd * 100))

    return {
        "token_budget": tokens,
        "model": model,
        "fuel_grade": fuel_grade or PRIORITY_MODEL.get(priority, "diesel"),
        "estimated_cost_cents": cost_cents,
        "input_tokens": input_tokens,
        "output_tokens": output_tokens,
    }


async def _write_squad_memory_for_task(task: SquadTask, squad_id: str) -> None:
    """Fire-and-forget: persist task completion as a squad memory in Mirror."""
    try:
        result_summary = ""
        if task.result:
            result_summary = str(task.result)[:200]
        text = f"Task '{task.title}' completed by {task.assignee or 'squad'}."
        if result_summary:
            text += f" Result: {result_summary}"
        mirror_url = os.environ.get("MIRROR_URL", "http://localhost:8844")
        async with httpx.AsyncClient(timeout=5.0) as client:
            await client.post(
                f"{mirror_url}/store",
                json={
                    "agent": task.assignee or "system",
                    "context_id": f"task:{task.id}:done",
                    "text": text,
                    "project": f"squad:{squad_id}",
                    "series": f"squad:{squad_id}",
                },
            )
    except Exception:
        pass  # fire-and-forget, never fail the main flow


class SquadTaskService:
    def __init__(self, db: SquadDB | None = None, bus: SquadBus | None = None):
        self.db = db or SquadDB()
        self.bus = bus or SquadBus()

    def create(self, task: SquadTask, actor: str = "system", tenant_id: str = DEFAULT_TENANT_ID) -> Response:
        timestamp = now_iso()
        if not task.created_at:
            task.created_at = timestamp
        task.updated_at = timestamp

        # Auto-estimate budget if not set
        if task.token_budget <= 0:
            fuel = task.inputs.get("fuel_grade")
            budget = estimate_task_budget(task.priority.value, fuel)
            task.token_budget = budget["token_budget"]
            task.inputs["fuel_grade"] = budget["fuel_grade"]
            task.inputs["estimated_cost_cents"] = budget["estimated_cost_cents"]
            task.inputs["model"] = budget["model"]
        done_when_serialized = [
            item.model_dump() if isinstance(item, DoneCheck) else dict(item)
            for item in (task.done_when or [])
        ]
        with self.db.connect() as conn:
            conn.execute(
                """
                INSERT INTO squad_tasks (
                    id, tenant_id, squad_id, title, description, status, priority, assignee, skill_id, project,
                    labels_json, blocked_by_json, blocks_json, inputs_json, result_json, token_budget,
                    bounty_json, external_ref, done_when_json, created_at, updated_at, completed_at, claimed_at, attempt
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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
                    _dumps(done_when_serialized),
                    task.created_at,
                    task.updated_at,
                    task.completed_at,
                    task.claimed_at,
                    task.attempt,
                ),
            )
        bus_payload = _task_to_bus_payload(task)
        self.bus.emit("task.created", task.squad_id, actor, bus_payload)
        return Response(message_id=task.id, status=ResponseStatus.SUCCESS, data={"task": bus_payload})

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
        project_id: str | None = None,
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
        if project_id:
            query += " AND project = ?"
            params.append(project_id)
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
        # Structured done_when gate — T1.3 Part 2 (task #270).
        # Empty list is vacuously True, preserving pre-migration behaviour.
        # Refuse BEFORE any state mutation or bus emission.
        if not all_done(task.done_when):
            pending = [
                (c.id if isinstance(c, DoneCheck) else c.get("id"))
                for c in task.done_when
                if not (c.done if isinstance(c, DoneCheck) else bool(c.get("done")))
            ]
            raise NotAllDoneError(
                f"done_when not satisfied for task {task.id}: pending={pending}"
            )
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

        # v1 TaskCompletedMessage on the global squad stream for health +
        # journeys consumers. Replaces in-process reach into
        # sos.services.health.calcifer.conductance_update (P0-04) and
        # sos.services.journeys.tracker.JourneyTracker (P0-05). Fire-and-forget;
        # consumers are idempotent on message_id and fail-open.
        _emit_task_completed(task, actor, task.completed_at or timestamp)

        # Deliver result to customer project agent via bus
        project = task.project
        if project:
            try:
                import redis as _redis
                import os as _os
                _pw = _os.environ.get("REDIS_PASSWORD", "")
                _r = _redis.Redis(host="localhost", port=6379, password=_pw, decode_responses=True)
                result_summary = str(result.get("result", result.get("summary", "")))[:300]
                delivery_msg = json.dumps({
                    "text": f"Task completed: {task.title}\nResult: {result_summary}",
                    "source": "agent:system",
                    "task_id": task.id,
                })
                # Write to both project-scoped stream (MCP inbox) and legacy stream (wake daemon)
                _r.xadd(f"sos:stream:project:{project}:agent:{project}", {
                    "type": "delivery",
                    "source": "system",
                    "target": f"agent:{project}",
                    "payload": delivery_msg,
                })
                _r.publish(f"sos:wake:{project}", json.dumps({
                    "source": "system",
                    "text": f"Task done: {task.title}",
                }))
            except Exception as e:
                log.warning(f"Delivery notification failed for {project}: {e}")

        # Wire 4: Bounty completion → Treasury payout
        if task.bounty and task.bounty.get("reward"):
            try:
                import sys as _sys
                from pathlib import Path as _Path
                _sys.path.insert(0, str(_Path.home()))
                from sovereign.bounty_board import BountyBoard
                import asyncio as _asyncio

                board = BountyBoard()
                bounty_id = task.bounty.get("bounty_id")
                agent_addr = task.assignee or actor

                if bounty_id:
                    # Bounty already on board — submit and pay
                    async def _payout():
                        await board.submit_solution(bounty_id, proof_url=task.result.get("summary", "completed"))
                        payout_msg = await board.approve_and_pay(bounty_id)
                        log.info(f"Wire 4 payout for {task.id}: {payout_msg}")

                        # Wire 6: conductance update moved to the health
                        # consumer listening on task.completed (P0-04).

                        # If pending approval (>100 MIND), notify via bus for Telegram relay
                        if "Pending" in payout_msg:
                            reward = float(task.bounty.get("reward", 0))
                            try:
                                _r = _redis.Redis(host="localhost", port=6379, password=_pw, decode_responses=True)
                                _r.xadd("sos:stream:global:agent:hadi", {
                                    "type": "approval_request",
                                    "source": "wire4-treasury",
                                    "data": json.dumps({
                                        "text": (
                                            f"Bounty payout needs approval:\n"
                                            f"Task: {task.title}\n"
                                            f"Agent: {agent_addr}\n"
                                            f"Amount: {reward:.0f} MIND\n"
                                            f"Bounty: {bounty_id}\n\n"
                                            f"Reply: approve {bounty_id}"
                                        ),
                                        "source": "wire4-treasury",
                                    }),
                                }, maxlen=500)
                                _r.publish("sos:wake:hadi", json.dumps({
                                    "source": "wire4-treasury",
                                    "text": f"Approval needed: {reward:.0f} MIND for {task.title[:40]}",
                                }))
                            except Exception:
                                pass

                    try:
                        loop = _asyncio.get_running_loop()
                        loop.create_task(_payout())
                    except RuntimeError:
                        _asyncio.run(_payout())
                else:
                    # No bounty_id — task has bounty value but wasn't posted to board
                    # Record the payout obligation in the ledger
                    reward = float(task.bounty["reward"])
                    log.info(
                        "Wire 4: Task %s has %s MIND bounty but no bounty_id on board. "
                        "Recording payout obligation for %s.",
                        task.id, reward, agent_addr,
                    )
            except Exception as exc:
                log.warning(f"Wire 4 bounty payout failed (non-blocking) for {task.id}: {exc}")

        # Journey milestone auto-evaluation moved to the journeys bus consumer
        # listening on task.completed (P0-05). No in-process reach.

        # ── Economy: wallet charge + conductance update + transaction log ──
        # Two costs tracked: internal (fuel) and billable (customer-facing)
        try:
            squad_id = task.squad_id
            fuel_grade = task.inputs.get("fuel_grade") or result.get("fuel_grade", "diesel")
            agent_name = task.assignee or actor or "worker"

            # Internal cost (what it costs us in tokens)
            internal_cost_cents = FUEL_COST_CENTS.get(fuel_grade, 0)

            # Billable = internal cost × 10x markup
            # If internal cost is 0 (free model), use flat per-task rate instead
            markup = get_markup(agent_name)
            if internal_cost_cents > 0:
                billable_cents = internal_cost_cents * markup
            else:
                # Free model — use flat rate from AGENT_RATE_CENTS_PER_TASK
                billable_cents = AGENT_RATE_CENTS_PER_TASK.get(agent_name, 200)

            margin_cents = billable_cents - internal_cost_cents
            cost_cents = billable_cents
            _tid = tenant_id if tenant_id is not None else DEFAULT_TENANT_ID

            with self.db.connect() as conn:
                # Charge wallet
                if cost_cents > 0:
                    conn.execute(
                        "UPDATE squad_wallets SET balance_cents = balance_cents - ?, total_spent_cents = total_spent_cents + ?, updated_at = ? WHERE squad_id = ? AND tenant_id = ?",
                        (cost_cents, cost_cents, timestamp, squad_id, _tid),
                    )

                # Record transaction (billable amount)
                import uuid as _uuid
                conn.execute(
                    "INSERT INTO squad_transactions (id, squad_id, tenant_id, type, amount_cents, counterparty, reason, task_id, created_at) VALUES (?, ?, ?, 'spend', ?, ?, ?, ?, ?)",
                    (str(_uuid.uuid4())[:8], squad_id, _tid, billable_cents,
                     agent_name, f"Task: {task.title[:60]} | fuel:{fuel_grade} internal:{internal_cost_cents}c margin:{margin_cents}c",
                     task.id, timestamp),
                )

                # Update conductance — dG/dt = |F|^γ - αG
                for label in (task.labels or []):
                    squad_row = conn.execute(
                        "SELECT conductance_json FROM squads WHERE id = ? AND tenant_id = ?",
                        (squad_id, _tid),
                    ).fetchone()
                    if squad_row:
                        conductance = json.loads(squad_row[0] or "{}")
                        G = conductance.get(label, 0.1)
                        gamma = 0.5  # non-linearity: sqrt of value
                        value = max(cost_cents, 1)  # flow = value of work done
                        G_new = min(G + abs(value) ** gamma / 100, 1.0)  # cap at 1.0
                        conductance[label] = round(G_new, 4)
                        conn.execute(
                            "UPDATE squads SET conductance_json = ?, updated_at = ? WHERE id = ? AND tenant_id = ?",
                            (json.dumps(conductance), timestamp, squad_id, _tid),
                        )

                # Update goal progress
                conn.execute(
                    """UPDATE squad_goals SET
                        progress = MIN(1.0, progress + 0.05),
                        updated_at = ?
                    WHERE squad_id = ? AND tenant_id = ? AND status = 'active'""",
                    (timestamp, squad_id, _tid),
                )

            log.info(f"Economy: squad={squad_id} agent={agent_name} billable={billable_cents}c internal={internal_cost_cents}c margin={margin_cents}c")
        except Exception as exc:
            log.debug(f"Economy update skipped: {exc}")

        # Squad shared memory — fire-and-forget, never blocks the main flow
        try:
            loop = asyncio.get_running_loop()
            loop.create_task(_write_squad_memory_for_task(task, task.squad_id))
        except RuntimeError:
            pass  # No running event loop (sync context) — skip silently

        # Achievement check — fire-and-forget, never blocks task completion
        try:
            from sos.services.squad.service import AchievementService as _AchievementService
            _AchievementService(db=self.db).check_and_award(task.squad_id)
        except Exception:
            pass

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
