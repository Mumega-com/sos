from __future__ import annotations

import asyncio
import dataclasses
import json
import os
import time
from dataclasses import asdict
from typing import Any, Optional

import httpx

from fastapi import Depends, FastAPI, HTTPException, Query
from fastapi.responses import JSONResponse
from fastapi.encoders import jsonable_encoder
from pydantic import BaseModel, Field

from sos import __version__
from sos.kernel.health import health_response
from sos.contracts.squad import (
    LoadingLevel,
    PipelineSpec,
    SkillDescriptor,
    SkillExecutionResult,
    SkillStatus,
    Squad,
    SquadEvent,
    SquadMember,
    SquadRole,
    SquadState,
    SquadStatus,
    SquadTask,
    SquadTier,
    TaskPriority,
    TaskStatus,
    TrustTier,
)
from fastapi import Header
from sos.services.squad.auth import AuthContext, create_api_key as _create_api_key, require_capability
from sos.services.squad.auth import _lookup_token as _squad_lookup_token
from sos.services.squad.service import SquadDB, LeagueService
from sos.services.squad import PipelineService, SquadService, SquadSkillService, SquadStateService, SquadTaskService
from sos.services.squad.tasks import ClaimTokenMismatchError, InsufficientFundsError, NotAllDoneError
from sos.services.squad.kpis import KPISnapshot, calculate_kpis
from sos.kernel.telemetry import init_tracing, instrument_fastapi
from sos.kernel.audit_chain import AuditChainEvent, emit_audit


init_tracing("squad")

app = FastAPI(title="SOS Squad Service", version=__version__)
instrument_fastapi(app)

_START_TIME = time.time()
_last_squad_health_status: str | None = None

squads = SquadService()
tasks = SquadTaskService()
skills = SquadSkillService()
state = SquadStateService(mirror_sync=False)
pipelines = PipelineService()
league = LeagueService()


def _json(value: Any) -> Any:
    return jsonable_encoder(value)


class SquadRoleIn(BaseModel):
    name: str
    skills: list[str] = Field(default_factory=list)
    schedule: str = ""
    description: str = ""
    fuel_grade: str = "diesel"


class SquadMemberIn(BaseModel):
    agent_id: str
    role: str
    joined_at: str = ""
    is_human: bool = False


class SquadIn(BaseModel):
    id: str
    name: str
    project: str
    objective: str
    tier: SquadTier = SquadTier.NOMAD
    status: SquadStatus = SquadStatus.DRAFT
    roles: list[SquadRoleIn] = Field(default_factory=list)
    members: list[SquadMemberIn] = Field(default_factory=list)
    kpis: list[str] = Field(default_factory=list)
    budget_cents_monthly: int = 0


class SquadTaskIn(BaseModel):
    id: str
    squad_id: str
    title: str
    description: str = ""
    status: TaskStatus = TaskStatus.BACKLOG
    priority: TaskPriority = TaskPriority.MEDIUM
    assignee: Optional[str] = None
    skill_id: Optional[str] = None
    project: str = ""
    labels: list[str] = Field(default_factory=list)
    blocked_by: list[str] = Field(default_factory=list)
    blocks: list[str] = Field(default_factory=list)
    inputs: dict[str, Any] = Field(default_factory=dict)
    result: dict[str, Any] = Field(default_factory=dict)
    token_budget: int = 0
    bounty: dict[str, Any] = Field(default_factory=dict)
    external_ref: Optional[str] = None
    attempt: int = 0


class SkillDescriptorIn(BaseModel):
    id: str
    name: str
    description: str
    input_schema: dict[str, Any] = Field(default_factory=dict)
    output_schema: dict[str, Any] = Field(default_factory=dict)
    labels: list[str] = Field(default_factory=list)
    keywords: list[str] = Field(default_factory=list)
    entrypoint: str
    skill_dir: str = ""
    required_inputs: list[str] = Field(default_factory=list)
    status: SkillStatus = SkillStatus.ACTIVE
    trust_tier: TrustTier = TrustTier.VENDOR
    loading_level: LoadingLevel = LoadingLevel.INSTRUCTIONS
    fuel_grade: str = "diesel"
    version: str = "1.0.0"
    deprecated_at: Optional[str] = None


class SquadStateIn(BaseModel):
    squad_id: str
    project: str
    data: dict[str, Any] = Field(default_factory=dict)
    version: int = 0


class ClaimIn(BaseModel):
    assignee: str
    attempt: int


class RouteIn(BaseModel):
    assignee: Optional[str] = None
    skill_id: Optional[str] = None
    reason: str


class CompleteIn(BaseModel):
    result: dict[str, Any] = Field(default_factory=dict)
    claim_token: Optional[str] = None


class FailIn(BaseModel):
    error: str


class EventIn(BaseModel):
    event_type: str
    actor: str
    payload: dict[str, Any] = Field(default_factory=dict)


class ExecuteSkillIn(BaseModel):
    task_id: str
    context: dict[str, Any] = Field(default_factory=dict)
    min_trust_tier: int = 1


class PipelineSpecIn(BaseModel):
    repo: str
    workdir: str = "."
    default_branch: str = "main"
    feature_branch_prefix: str = "squad/"
    pr_mode: str = "branch_pr"
    build_cmd: str = ""
    test_cmd: str = ""
    deploy_cmd: str = ""
    smoke_cmd: str = ""
    deploy_mode: str = "manual"
    deploy_on_task_labels: list[str] = Field(default_factory=lambda: ["deploy"])
    rollback_cmd: str = ""
    enabled: bool = True


class PipelineRunIn(BaseModel):
    task_id: str
    actor: str = "system"


class ApproveIn(BaseModel):
    actor: str = "system"


class RollbackIn(BaseModel):
    actor: str = "system"


def _to_squad(payload: SquadIn) -> Squad:
    return Squad(
        id=payload.id,
        name=payload.name,
        project=payload.project,
        objective=payload.objective,
        tier=payload.tier,
        status=payload.status,
        roles=[SquadRole(**role.model_dump()) for role in payload.roles],
        members=[SquadMember(**member.model_dump()) for member in payload.members],
        kpis=payload.kpis,
        budget_cents_monthly=payload.budget_cents_monthly,
    )


def _to_task(payload: SquadTaskIn) -> SquadTask:
    return SquadTask(**payload.model_dump())


@app.get("/health")
async def health() -> JSONResponse:
    """G71-canonical health check — disposable SQLite SELECT 1 ping.

    Returns 200 with db_reachable=True on healthy, 503 on unhealthy.
    Transition-only emit via _last_squad_health_status module-level state.
    """
    global _last_squad_health_status
    import sqlite3 as _sqlite3
    from sos.kernel.config import DB_PATH
    from sos.services.squad.tasks import SQUAD_INSTANCE_ID

    t0 = time.perf_counter()
    db_reachable = False
    db_reachable_ms = 0.0
    reason: str | None = None
    try:
        _conn = _sqlite3.connect(str(DB_PATH), timeout=1.0)
        _conn.execute("SELECT 1")
        _conn.close()
        db_reachable = True
    except Exception as exc:
        reason = str(exc)
    db_reachable_ms = round((time.perf_counter() - t0) * 1000, 2)

    # G71: pool exhaustion detection — check if DB responds within budget
    pool_healthy = db_reachable and db_reachable_ms < 500  # 500ms budget
    new_status = "healthy" if pool_healthy else "unhealthy"
    body: dict[str, Any] = {
        "status": new_status,
        "db_reachable": db_reachable,
        "instance_id": SQUAD_INSTANCE_ID,
        "db_reachable_ms": db_reachable_ms,
        "pool_stats": {
            "healthy": pool_healthy,
            "latency_budget_ms": 500,
            "actual_ms": db_reachable_ms,
        },
    }
    if reason:
        body["reason"] = reason

    if new_status != _last_squad_health_status:
        prev = _last_squad_health_status or "unknown"
        _last_squad_health_status = new_status
        try:
            from sos.observability.sprint_telemetry import emit_squad_health
            emit_squad_health(
                instance_id=SQUAD_INSTANCE_ID,
                prev_status=prev,
                new_status=new_status,
                db_reachable_ms=db_reachable_ms,
            )
        except Exception:
            pass

    status_code = 200 if db_reachable else 503
    return JSONResponse(status_code=status_code, content=body)


_SYSTEM_BEARER = os.getenv("SOS_SYSTEM_TOKEN", "sk-sos-system")


def _require_system_bearer(authorization: Optional[str]) -> None:
    if not authorization or not authorization.lower().startswith("bearer "):
        raise HTTPException(status_code=401, detail="missing_authorization")
    presented = authorization.split(" ", 1)[1].strip()
    if presented != _SYSTEM_BEARER:
        raise HTTPException(status_code=403, detail="system_bearer_required")


class AuthVerifyRequest(BaseModel):
    token: str


@app.post("/auth/verify")
async def auth_verify(
    payload: AuthVerifyRequest,
    authorization: Optional[str] = Header(default=None),
) -> dict[str, Any]:
    """Resolve a bearer against the squad api_keys table.

    Admin-gated: only callers presenting SOS_SYSTEM_TOKEN can verify tokens
    on another identity's behalf. Returns ``{ok: false}`` on miss so the
    shape is uniform for clients; SOSClientError is not raised for 401.
    """
    _require_system_bearer(authorization)
    ctx = _squad_lookup_token(payload.token, SquadDB())
    if ctx is None:
        return {"ok": False}
    return {
        "ok": True,
        "tenant_id": ctx.tenant_id,
        "is_system": ctx.is_system,
        "identity_type": ctx.identity.metadata.get("identity_type"),
        "identity_id": ctx.identity.id,
    }


class ApiKeyCreateRequest(BaseModel):
    tenant_id: str
    identity_type: str = "user"


@app.post("/api-keys")
async def api_keys_create(
    payload: ApiKeyCreateRequest,
    authorization: Optional[str] = Header(default=None),
) -> dict[str, Any]:
    """Mint a new api-key for ``tenant_id``. System-bearer gated."""
    _require_system_bearer(authorization)
    token, created_at = _create_api_key(
        payload.tenant_id, payload.identity_type, SquadDB()
    )
    return {"token": token, "tenant_id": payload.tenant_id, "created_at": created_at}


@app.post("/squads")
async def create_squad(
    payload: SquadIn,
    auth: AuthContext = Depends(require_capability("squads", "write")),
) -> dict[str, Any]:
    squad = _to_squad(payload)
    return {"squad": _json(squad), "response": _json(squads.create(squad, tenant_id=auth.tenant_scope or "default"))}


@app.get("/squads")
async def list_squads(
    status: SquadStatus | None = None,
    project: str | None = None,
    auth: AuthContext = Depends(require_capability("squads", "read")),
) -> list[dict[str, Any]]:
    return _json(squads.list(status=status, project=project, tenant_id=auth.tenant_scope))


@app.get("/squads/{squad_id}")
async def get_squad(
    squad_id: str,
    auth: AuthContext = Depends(require_capability("squads", "read")),
) -> dict[str, Any]:
    squad = squads.get(squad_id, tenant_id=auth.tenant_scope)
    if not squad:
        raise HTTPException(status_code=404, detail="squad_not_found")
    return _json(squad)


@app.patch("/squads/{squad_id}")
async def update_squad(
    squad_id: str,
    updates: dict[str, Any],
    auth: AuthContext = Depends(require_capability("squads", "write")),
) -> dict[str, Any]:
    try:
        squad = squads.update(squad_id, updates, tenant_id=auth.tenant_scope)
    except KeyError:
        raise HTTPException(status_code=404, detail="squad_not_found")
    return _json(squad)


@app.delete("/squads/{squad_id}")
async def delete_squad(
    squad_id: str,
    auth: AuthContext = Depends(require_capability("squads", "write")),
) -> dict[str, bool]:
    return {"deleted": squads.delete(squad_id, tenant_id=auth.tenant_scope)}


@app.post("/tasks")
async def create_task(
    payload: SquadTaskIn,
    auth: AuthContext = Depends(require_capability("tasks", "write")),
    idempotency_key: Optional[str] = Header(default=None, alias="Idempotency-Key"),
) -> dict[str, Any]:
    from sos.kernel.idempotency import with_idempotency

    async def _do() -> dict[str, Any]:
        task = _to_task(payload)
        result = {
            "task": _json(task),
            "response": _json(tasks.create(task, tenant_id=auth.tenant_scope or "default")),
        }
        asyncio.create_task(emit_audit(AuditChainEvent(
            stream_id="squad",
            actor_id=auth.identity.id,
            actor_type="agent" if auth.identity.type.value == "agent" else "human",
            action="created",
            resource=f"task:{task.id}",
            payload={"task_id": task.id, "squad_id": task.squad_id, "title": task.title,
                     "status": task.status.value, "assignee": task.assignee},
        )))
        return result

    return await with_idempotency(
        key=idempotency_key,
        tenant=auth.tenant_scope or "default",
        request_body=payload.model_dump(),
        fn=_do,
    )


@app.get("/tasks")
async def list_tasks(
    squad_id: str | None = None,
    status: TaskStatus | None = None,
    project_id: Optional[str] = None,
    auth: AuthContext = Depends(require_capability("tasks", "read")),
) -> list[dict[str, Any]]:
    return _json(tasks.list(squad_id=squad_id, status=status, project_id=project_id, tenant_id=auth.tenant_scope))


_PRIORITY_WEIGHTS = {
    TaskPriority.CRITICAL: 4,
    TaskPriority.HIGH: 3,
    TaskPriority.MEDIUM: 2,
    TaskPriority.LOW: 1,
}


def _score_task(task: SquadTask, now_ts: float) -> float:
    """Board-view score: ``priority*10 + blocks*5 + age_hours*2``.

    Stable ordering: tied scores fall back to creation order (oldest first).
    Missing / malformed ``created_at`` contributes 0 to the age term.
    """
    from datetime import datetime

    priority_term = _PRIORITY_WEIGHTS.get(task.priority, 2) * 10
    blocks_term = len(task.blocks) * 5
    age_hours = 0.0
    if task.created_at:
        try:
            created = datetime.fromisoformat(task.created_at.replace("Z", "+00:00"))
            age_hours = max(0.0, (now_ts - created.timestamp()) / 3600.0)
        except (ValueError, TypeError):
            age_hours = 0.0
    return float(priority_term + blocks_term + age_hours * 2)


# Closure-v1 Tier 1 §T1.4 — skill-based task routing
# Caller supplies no assignee → we pick the squad member whose role skills
# best cover the task's labels, ranked by squad conductance for those skills.
_DEFAULT_CONDUCTANCE = 0.5


def _auto_pick_assignee(
    squad: Squad | None, task: SquadTask
) -> tuple[Optional[str], Optional[str], float, list[str]]:
    """Return ``(assignee_id, skill_id, total_conductance, matched_skills)``.

    ``assignee_id`` is ``None`` when no member has any role-skill that
    matches a task label. ``skill_id`` names the single highest-conductance
    matching skill — callers store it on the task for downstream dispatch.
    Conductance defaults to ``_DEFAULT_CONDUCTANCE`` for skills missing
    from ``squad.conductance``.
    """
    if squad is None or not squad.members or not task.labels:
        return None, None, 0.0, []

    wanted = {lbl.lower() for lbl in task.labels}
    role_by_name = {role.name: role for role in squad.roles}

    best_member: Optional[str] = None
    best_total = -1.0
    best_matched: list[str] = []
    best_skill: Optional[str] = None
    for member in squad.members:
        role = role_by_name.get(member.role)
        if role is None:
            continue
        matched = [s for s in role.skills if s.lower() in wanted]
        if not matched:
            continue
        total = sum(
            squad.conductance.get(s, _DEFAULT_CONDUCTANCE) for s in matched
        )
        if total > best_total:
            best_total = total
            best_member = member.agent_id
            best_matched = matched
            # Top skill = highest-conductance matched skill (stable by
            # declaration order on tie).
            best_skill = max(
                matched,
                key=lambda s: squad.conductance.get(s, _DEFAULT_CONDUCTANCE),
            )
    if best_member is None:
        return None, None, 0.0, []
    return best_member, best_skill, best_total, best_matched


@app.get("/tasks/board")
async def tasks_board(
    squad: str,
    project_id: Optional[str] = None,
    auth: AuthContext = Depends(require_capability("tasks", "read")),
) -> dict[str, Any]:
    """Read-only board view — tasks grouped by status, scored for urgency.

    Per closure-v1 Tier 1 §T1.5: returns ``{squad, total, groups: {status: [task...]}}``
    where each task includes a ``score`` field and tasks are sorted highest
    score first within each group. Assignee resolves to the agent's card name
    when available, else falls back to the raw assignee id.
    """
    import time as _time

    now_ts = _time.time()
    items = tasks.list(squad_id=squad, status=None, project_id=project_id, tenant_id=auth.tenant_scope)

    groups: dict[str, list[dict[str, Any]]] = {}
    for task in items:
        scored = _json(task)
        scored["score"] = round(_score_task(task, now_ts), 2)
        scored["assignee_name"] = task.assignee or None
        status_key = (
            task.status.value if hasattr(task.status, "value") else str(task.status)
        )
        groups.setdefault(status_key, []).append(scored)

    for bucket in groups.values():
        bucket.sort(key=lambda t: (-t["score"], t.get("created_at") or ""))

    return {"squad": squad, "total": len(items), "groups": groups}


@app.get("/tasks/dedupe/{external_ref}")
async def dedupe_task(
    external_ref: str,
    ttl_seconds: int = 86400,
    auth: AuthContext = Depends(require_capability("tasks", "read")),
) -> dict[str, Any]:
    """G35: source-signal dedupe probe.

    Returns {"exists": true, "task_id": "...", "status": "..."} if a task with
    this external_ref is active (queued/claimed/in_flight) or was completed
    within ttl_seconds. Returns {"exists": false} if safe to emit.
    """
    task = tasks.get_by_external_ref(external_ref, ttl_seconds=ttl_seconds, tenant_id=auth.tenant_scope)
    if task:
        return {"exists": True, "task_id": task.id, "status": task.status.value}
    return {"exists": False}


@app.get("/tasks/{task_id}")
async def get_task(
    task_id: str,
    auth: AuthContext = Depends(require_capability("tasks", "read")),
) -> dict[str, Any]:
    task = tasks.get(task_id, tenant_id=auth.tenant_scope)
    if not task:
        raise HTTPException(status_code=404, detail="task_not_found")
    return _json(task)


@app.post("/tasks/{task_id}/claim")
async def claim_task(
    task_id: str,
    payload: ClaimIn,
    auth: AuthContext = Depends(require_capability("tasks", "write")),
) -> dict[str, Any]:
    try:
        claim = tasks.claim(task_id, payload.assignee, payload.attempt, tenant_id=auth.tenant_scope)
    except KeyError:
        raise HTTPException(status_code=404, detail="task_not_found")
    except InsufficientFundsError as exc:
        raise HTTPException(status_code=402, detail=str(exc))
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc))
    asyncio.create_task(emit_audit(AuditChainEvent(
        stream_id="squad",
        actor_id=payload.assignee,
        actor_type="agent",
        action="claimed",
        resource=f"task:{task_id}",
        payload={"task_id": task_id, "assignee": payload.assignee, "attempt": claim.attempt},
    )))
    return _json(claim)


@app.post("/tasks/{task_id}/route")
async def route_task(
    task_id: str,
    payload: RouteIn,
    auth: AuthContext = Depends(require_capability("tasks", "write")),
) -> dict[str, Any]:
    """Route a task. When ``payload.assignee`` is omitted, the server picks
    the squad member whose role skills best cover the task's labels —
    ranked by ``squad.conductance`` for those skills (closure-v1 §T1.4).
    """
    assignee = payload.assignee
    skill_id = payload.skill_id
    reason = payload.reason
    if assignee is None:
        task_obj = tasks.get(task_id, tenant_id=auth.tenant_scope)
        if task_obj is None:
            raise HTTPException(status_code=404, detail="task_not_found")
        squad_obj = squads.get(task_obj.squad_id, tenant_id=auth.tenant_scope)
        picked, picked_skill, total, matched = _auto_pick_assignee(squad_obj, task_obj)
        if picked is not None:
            assignee = picked
            skill_id = skill_id or picked_skill
            auto_reason = f"auto:skills={','.join(matched)};conductance={total:.2f}"
            reason = f"{reason}; {auto_reason}" if reason else auto_reason
        else:
            reason = reason or "auto:no_skill_match"
    try:
        decision = tasks.route(task_id, assignee, skill_id, reason, tenant_id=auth.tenant_scope)
    except KeyError:
        raise HTTPException(status_code=404, detail="task_not_found")
    return _json(decision)


@app.post("/tasks/{task_id}/complete")
async def complete_task(
    task_id: str,
    payload: CompleteIn,
    auth: AuthContext = Depends(require_capability("tasks", "write")),
    idempotency_key: Optional[str] = Header(default=None, alias="Idempotency-Key"),
) -> dict[str, Any]:
    from sos.kernel.idempotency import with_idempotency

    async def _do() -> dict[str, Any]:
        try:
            task = tasks.complete(
                task_id, payload.result,
                tenant_id=auth.tenant_scope,
                claim_token=payload.claim_token,
            )
        except KeyError:
            raise HTTPException(status_code=404, detail="task_not_found")
        except ClaimTokenMismatchError as exc:
            raise HTTPException(status_code=409, detail=str(exc))
        except NotAllDoneError as exc:
            # done_when gate refused — client needs to tick the remaining
            # checks before retrying. 400 (bad request) not 409 (state
            # conflict): the task is in a valid state; the submission is
            # the thing that's short.
            raise HTTPException(status_code=400, detail=str(exc))
        # Check streak_30d and task-count badges after each completion
        if task.squad_id:
            from sos.services.squad.service import AchievementService
            AchievementService().check_and_award(task.squad_id)
        asyncio.create_task(emit_audit(AuditChainEvent(
            stream_id="squad",
            actor_id=auth.identity.id,
            actor_type="agent" if auth.identity.type.value == "agent" else "human",
            action="completed",
            resource=f"task:{task_id}",
            payload={"task_id": task_id, "squad_id": task.squad_id,
                     "assignee": task.assignee, "result_keys": list((payload.result or {}).keys())},
        )))
        return _json(task)

    body = {"task_id": task_id, "payload": payload.model_dump()}
    return await with_idempotency(
        key=idempotency_key,
        tenant=auth.tenant_scope or "default",
        request_body=body,
        fn=_do,
    )


@app.post("/tasks/{task_id}/fail")
async def fail_task(
    task_id: str,
    payload: FailIn,
    auth: AuthContext = Depends(require_capability("tasks", "write")),
) -> dict[str, Any]:
    try:
        task = tasks.fail(task_id, payload.error, tenant_id=auth.tenant_scope)
    except KeyError:
        raise HTTPException(status_code=404, detail="task_not_found")
    return _json(task)


@app.post("/skills")
async def register_skill(
    payload: SkillDescriptorIn,
    auth: AuthContext = Depends(require_capability("skills", "register")),
) -> dict[str, Any]:
    skill = skills.register(SkillDescriptor(**payload.model_dump()), tenant_id=auth.tenant_scope or "default")
    return _json(skill)


@app.get("/skills")
async def list_skills(
    status: SkillStatus | None = None,
    auth: AuthContext = Depends(require_capability("skills", "read")),
) -> list[dict[str, Any]]:
    return _json(skills.list(status=status, tenant_id=auth.tenant_scope))


@app.post("/skills/match")
async def match_skill(
    payload: SquadTaskIn,
    min_trust_tier: int = 1,
    auth: AuthContext = Depends(require_capability("skills", "read")),
) -> list[dict[str, Any]]:
    return _json(skills.match(_to_task(payload), min_trust_tier=min_trust_tier, tenant_id=auth.tenant_scope))


@app.post("/skills/execute")
async def execute_skill(
    payload: ExecuteSkillIn,
    auth: AuthContext = Depends(require_capability("skills", "execute")),
) -> dict[str, Any]:
    task = tasks.get(payload.task_id, tenant_id=auth.tenant_scope)
    if not task:
        raise HTTPException(status_code=404, detail="task_not_found")
    result: SkillExecutionResult = skills.execute(
        task,
        payload.context,
        min_trust_tier=payload.min_trust_tier,
        tenant_id=auth.tenant_scope,
    )
    return _json(result)


@app.get("/state/{squad_id}")
async def load_state(
    squad_id: str,
    auth: AuthContext = Depends(require_capability("state", "read")),
) -> dict[str, Any]:
    current = state.load(squad_id, tenant_id=auth.tenant_scope)
    if not current:
        raise HTTPException(status_code=404, detail="state_not_found")
    return _json(current)


@app.put("/state/{squad_id}")
async def save_state(
    squad_id: str,
    payload: SquadStateIn,
    auth: AuthContext = Depends(require_capability("state", "write")),
) -> dict[str, Any]:
    current = await state.save(
        SquadState(**payload.model_dump() | {"squad_id": squad_id}),
        tenant_id=auth.tenant_scope or "default",
    )
    return _json(current)


@app.get("/state/{squad_id}/events")
async def list_events(
    squad_id: str,
    limit: int = 50,
    auth: AuthContext = Depends(require_capability("state", "read")),
) -> list[dict[str, Any]]:
    return _json(state.list_events(squad_id, limit=limit, tenant_id=auth.tenant_scope))


@app.post("/state/{squad_id}/events")
async def append_event(
    squad_id: str,
    payload: EventIn,
    auth: AuthContext = Depends(require_capability("state", "write")),
) -> dict[str, Any]:
    event = await state.append_event(
        SquadEvent(squad_id=squad_id, event_type=payload.event_type, actor=payload.actor, payload=payload.payload),
        tenant_id=auth.tenant_scope or "default",
    )
    return _json(event)


# ── Pipeline routes ────────────────────────────────────────────────────────────

@app.put("/squads/{squad_id}/pipeline")
async def set_pipeline(
    squad_id: str,
    payload: PipelineSpecIn,
    auth: AuthContext = Depends(require_capability("pipeline", "write")),
) -> dict[str, Any]:
    spec = PipelineSpec(squad_id=squad_id, **payload.model_dump())
    return _json(pipelines.set_pipeline(squad_id, spec, tenant_id=auth.tenant_scope or "default"))


@app.get("/squads/{squad_id}/pipeline")
async def get_pipeline(
    squad_id: str,
    auth: AuthContext = Depends(require_capability("pipeline", "read")),
) -> dict[str, Any]:
    spec = pipelines.get_pipeline(squad_id, tenant_id=auth.tenant_scope)
    if not spec:
        raise HTTPException(status_code=404, detail="pipeline_not_found")
    return _json(spec)


@app.post("/squads/{squad_id}/pipeline/run")
async def trigger_pipeline_run(
    squad_id: str,
    payload: PipelineRunIn,
    auth: AuthContext = Depends(require_capability("pipeline", "execute")),
) -> dict[str, Any]:
    try:
        run = pipelines.run_pipeline(squad_id, payload.task_id, payload.actor, tenant_id=auth.tenant_scope)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    return _json(run)


@app.post("/pipeline-runs/{run_id}/approve")
async def approve_pipeline_run(
    run_id: str,
    payload: ApproveIn,
    auth: AuthContext = Depends(require_capability("pipeline", "execute")),
) -> dict[str, Any]:
    try:
        run = pipelines.approve_deploy(run_id, payload.actor, tenant_id=auth.tenant_scope)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc))
    return _json(run)


@app.post("/pipeline-runs/{run_id}/rollback")
async def rollback_pipeline_run(
    run_id: str,
    payload: RollbackIn,
    auth: AuthContext = Depends(require_capability("pipeline", "execute")),
) -> dict[str, Any]:
    try:
        run = pipelines.rollback(run_id, payload.actor, tenant_id=auth.tenant_scope)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    return _json(run)


@app.get("/squads/{squad_id}/pipeline/runs")
async def list_pipeline_runs(
    squad_id: str,
    limit: int = 20,
    auth: AuthContext = Depends(require_capability("pipeline", "read")),
) -> list[dict[str, Any]]:
    return _json(pipelines.list_runs(squad_id, limit=limit, tenant_id=auth.tenant_scope))


_MIRROR_URL: str = os.environ.get("MIRROR_URL", "http://localhost:8844")


class SquadMemoryIn(BaseModel):
    text: str
    agent_id: str = ""


@app.post("/squads/{squad_id}/memory")
async def store_squad_memory(
    squad_id: str,
    payload: SquadMemoryIn,
    auth: AuthContext = Depends(require_capability("squads", "read")),
) -> dict[str, Any]:
    squad = squads.get(squad_id, tenant_id=auth.tenant_scope)
    if not squad:
        raise HTTPException(status_code=404, detail="squad_not_found")
    project = f"squad:{squad_id}"
    context_id = f"squad:{squad_id}:{int(time.time())}"
    agent = payload.agent_id or "system"
    async with httpx.AsyncClient(timeout=5.0) as client:
        resp = await client.post(
            f"{_MIRROR_URL}/store",
            json={
                "agent": agent,
                "context_id": context_id,
                "text": payload.text,
                "project": project,
                "series": project,
            },
        )
    if resp.is_success:
        # Increment the per-squad memory counter so first_memory badge can trigger
        db = SquadDB()
        try:
            with db.connect() as conn:
                conn.execute(
                    """
                    INSERT INTO squad_memory_counts (squad_id, count)
                    VALUES (?, 1)
                    ON CONFLICT (squad_id) DO UPDATE SET count = count + 1
                    """,
                    (squad_id,),
                )
        except Exception as _mem_exc:
            log.warning("squad_memory_counts upsert failed", squad_id=squad_id, error=str(_mem_exc))
        # Check and award achievements (never blocks the response)
        from sos.services.squad.service import AchievementService
        AchievementService(db).check_and_award(squad_id)
    return {"stored": True, "squad_id": squad_id}


@app.get("/squads/{squad_id}/memory")
async def search_squad_memory(
    squad_id: str,
    q: str = "",
    limit: int = 20,
    auth: AuthContext = Depends(require_capability("squads", "read")),
) -> dict[str, Any]:
    squad = squads.get(squad_id, tenant_id=auth.tenant_scope)
    if not squad:
        raise HTTPException(status_code=404, detail="squad_not_found")
    project = f"squad:{squad_id}"
    async with httpx.AsyncClient(timeout=5.0) as client:
        if q:
            resp = await client.post(
                f"{_MIRROR_URL}/search",
                json={"query": q, "top_k": limit, "project": project},
            )
            memories = resp.json() if resp.is_success else []
        else:
            resp = await client.get(
                f"{_MIRROR_URL}/recent/{project}",
                params={"limit": limit},
            )
            data = resp.json() if resp.is_success else {}
            memories = data.get("engrams", [])
    return {"memories": memories, "squad_id": squad_id}


# ── KPI routes ────────────────────────────────────────────────────────────────

@app.get("/squads/{squad_id}/kpis")
async def get_squad_kpis(
    squad_id: str,
    auth: AuthContext = Depends(require_capability("squads", "read")),
) -> dict[str, Any]:
    """Return a live KPISnapshot for the squad over the last 7 days."""
    squad = squads.get(squad_id, tenant_id=auth.tenant_scope)
    if not squad:
        raise HTTPException(status_code=404, detail="squad_not_found")
    snapshot = await calculate_kpis(squad_id)
    return _json(dataclasses.asdict(snapshot))


@app.get("/squads/{squad_id}/kpis/history")
async def get_squad_kpis_history(
    squad_id: str,
    days: int = 30,
    auth: AuthContext = Depends(require_capability("squads", "read")),
) -> list[dict[str, Any]]:
    """Return daily KPI snapshots from the diagnostics_snapshots table (newest first).

    Returns an empty list when the table does not exist yet.
    """
    squad = squads.get(squad_id, tenant_id=auth.tenant_scope)
    if not squad:
        raise HTTPException(status_code=404, detail="squad_not_found")
    from sos.services.squad.service import SquadDB as _SquadDB
    import json as _json_mod
    from datetime import datetime, timedelta, timezone

    cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
    db = _SquadDB()
    try:
        with db.connect() as conn:
            rows = conn.execute(
                """
                SELECT payload_json, recorded_at
                FROM diagnostics_snapshots
                WHERE squad_id = ? AND recorded_at >= ?
                ORDER BY recorded_at DESC
                """,
                (squad_id, cutoff),
            ).fetchall()
        return [_json_mod.loads(r["payload_json"]) for r in rows]
    except Exception:
        # Table does not exist yet — return empty list rather than 500
        return []


# ── Achievement routes ───────────────────────────────────────────

@app.get("/squads/{squad_id}/achievements")
async def get_squad_achievements(
    squad_id: str,
    auth: AuthContext = Depends(require_capability("squads", "read")),
) -> dict[str, Any]:
    from sos.services.squad.service import AchievementService
    squad = squads.get(squad_id, tenant_id=auth.tenant_scope)
    if not squad:
        raise HTTPException(status_code=404, detail="squad_not_found")
    service = AchievementService()
    achievements = service.get_achievements(squad_id)
    return {
        "achievements": [
            {
                "id": a.id,
                "badge": a.badge,
                "name": a.name,
                "description": a.description,
                "earned_at": a.earned_at,
            }
            for a in achievements
        ]
    }


# ── League routes ─────────────────────────────────────────────────────────────

_MUMEGA_ADMIN_TOKEN: str = os.environ.get("MUMEGA_ADMIN_TOKEN", "")


def _require_admin_token(x_admin_token: Optional[str] = Header(default=None)) -> None:
    """Dependency: require X-Admin-Token == MUMEGA_ADMIN_TOKEN."""
    if not _MUMEGA_ADMIN_TOKEN:
        raise HTTPException(status_code=503, detail="admin_token_not_configured")
    if x_admin_token != _MUMEGA_ADMIN_TOKEN:
        raise HTTPException(status_code=403, detail="admin_token_required")


class LeagueSeasonIn(BaseModel):
    name: str
    start_date: str
    end_date: str
    tenant_id: Optional[str] = None


@app.get("/league")
async def get_league(
    x_tenant_id: Optional[str] = Header(default=None),
) -> dict[str, Any]:
    """Return the current active season and its full league table.

    Pass ``X-Tenant-ID`` header to scope the league to a specific tenant.
    Omit the header for the global (no-tenant) league.
    """
    season = league.get_current_season(tenant_id=x_tenant_id)
    if season is None:
        season = league.ensure_active_season(tenant_id=x_tenant_id)
    table = league.get_league_table(season["id"])
    return {"season": season, "table": table}


@app.get("/league/seasons")
async def list_league_seasons(
    x_tenant_id: Optional[str] = Header(default=None),
) -> list[dict[str, Any]]:
    """List all seasons for the given tenant scope, newest first.

    Pass ``X-Tenant-ID`` header to scope to a specific tenant.
    Omit for the global (no-tenant) seasons.
    """
    return league.list_seasons(tenant_id=x_tenant_id)


@app.post("/league/seasons")
async def create_league_season(
    payload: LeagueSeasonIn,
    x_tenant_id: Optional[str] = Header(default=None),
    _admin: None = Depends(_require_admin_token),
) -> dict[str, Any]:
    """Create a new league season. Admin only (X-Admin-Token header).

    ``tenant_id`` from the request body takes precedence; falls back to
    the ``X-Tenant-ID`` header if body field is omitted.
    """
    effective_tenant_id = payload.tenant_id if payload.tenant_id is not None else x_tenant_id
    return league.create_season(
        name=payload.name,
        start_date=payload.start_date,
        end_date=payload.end_date,
        tenant_id=effective_tenant_id,
    )


@app.post("/league/snapshot")
async def trigger_league_snapshot(
    x_tenant_id: Optional[str] = Header(default=None),
    _admin: None = Depends(_require_admin_token),
) -> dict[str, Any]:
    """Snapshot KPIs and recalculate tiers for the current active season. Admin only.

    Pass ``X-Tenant-ID`` header to snapshot a specific tenant's season.
    Omit for the global season.
    """
    season = league.get_current_season(tenant_id=x_tenant_id)
    if season is None:
        raise HTTPException(status_code=404, detail="no_active_season")
    scores = await league.snapshot_league_scores(season["id"])
    return {"season_id": season["id"], "snapshotted": len(scores), "scores": scores}


# ── Daily KPI snapshot cron ───────────────────────────────────────────────────

async def _daily_kpi_snapshot() -> None:
    """Runs once per day: compute KPIs for all active squads and push to Inkwell."""
    from sos.contracts.squad import SquadStatus as _SquadStatus

    active_squads = squads.list(status=_SquadStatus.ACTIVE, tenant_id=None)
    inkwell_url = os.environ.get("SITE_URL", "")
    token = os.environ.get("MUMEGA_TOKEN", "")

    for squad in active_squads:
        try:
            snapshot = await calculate_kpis(squad.id)
            if inkwell_url and token:
                async with httpx.AsyncClient(timeout=10.0) as client:
                    await client.post(
                        f"{inkwell_url}/api/dashboard/squads/{squad.id}/kpis/snapshot",
                        json=dataclasses.asdict(snapshot),
                        headers={"Authorization": f"Bearer {token}"},
                    )
        except Exception:
            pass  # never fail the cron loop


async def _kpi_cron_loop() -> None:
    """Background task: sleep until 00:05 UTC, run snapshot, repeat daily."""
    import math
    from datetime import datetime, timezone

    while True:
        now = datetime.now(timezone.utc)
        # Next 00:05 UTC
        target = now.replace(hour=0, minute=5, second=0, microsecond=0)
        if target <= now:
            target = target.replace(day=target.day + 1)
        wait_seconds = (target - now).total_seconds()
        await asyncio.sleep(wait_seconds)
        await _daily_kpi_snapshot()


async def _league_weekly_snapshot_loop() -> None:
    """Background task: every Monday at 01:00 UTC, snapshot league scores."""
    from datetime import datetime, timezone

    while True:
        now = datetime.now(timezone.utc)
        # Next Monday 01:00 UTC
        days_until_monday = (7 - now.weekday()) % 7  # 0 if today is Monday
        target = now.replace(hour=1, minute=0, second=0, microsecond=0)
        if days_until_monday > 0:
            target = target.replace(day=target.day + days_until_monday)
        elif target <= now:
            # It's Monday but we've already passed 01:00 — skip to next Monday
            target = target.replace(day=target.day + 7)
        wait_seconds = (target - now).total_seconds()
        await asyncio.sleep(wait_seconds)
        try:
            season = league.get_current_season()
            if season is not None:
                await league.snapshot_league_scores(season["id"])
        except Exception:
            pass  # never fail the cron loop


async def _league_daily_season_loop() -> None:
    """Background task: every day at 00:01 UTC, ensure an active season exists."""
    from datetime import datetime, timezone

    while True:
        now = datetime.now(timezone.utc)
        # Next 00:01 UTC
        target = now.replace(hour=0, minute=1, second=0, microsecond=0)
        if target <= now:
            target = target.replace(day=target.day + 1)
        wait_seconds = (target - now).total_seconds()
        await asyncio.sleep(wait_seconds)
        try:
            league.ensure_active_season()
        except Exception:
            pass  # never fail the cron loop


_STALE_CLAIM_REAP_INTERVAL_S: int = int(os.environ.get("STALE_CLAIM_REAP_INTERVAL_S", "30"))


async def _stale_claim_reaper_loop() -> None:
    """Sprint 006 A.3 / G53: orphan-task recovery for dual-instance Squad HA.

    Every STALE_CLAIM_REAP_INTERVAL_S seconds, check for tasks claimed by
    dead processes and reset them to BACKLOG.  The initial sweep runs once
    at startup to reclaim any tasks this process held before a kill-9 +
    systemd auto-restart.
    """
    import logging as _logging
    _log = _logging.getLogger(__name__)
    # Startup sweep: reclaim any tasks held by dead processes (incl. prior PID of this process)
    try:
        reset = tasks.reap_stale_claims(tenant_id=None)
        if reset:
            _log.info("squad.startup_reap: reset %d stale claimed task(s)", reset)
    except Exception as exc:
        _log.warning("squad.startup_reap failed (non-fatal): %s", exc)
    # Periodic sweep
    while True:
        await asyncio.sleep(_STALE_CLAIM_REAP_INTERVAL_S)
        try:
            reset = tasks.reap_stale_claims(tenant_id=None)
            if reset:
                _log.info("squad.periodic_reap: reset %d stale claimed task(s)", reset)
        except Exception as exc:
            _log.warning("squad.periodic_reap failed (non-fatal): %s", exc)


@app.on_event("startup")
async def _start_kpi_cron() -> None:
    asyncio.create_task(_kpi_cron_loop())
    asyncio.create_task(_league_weekly_snapshot_loop())
    asyncio.create_task(_league_daily_season_loop())
    asyncio.create_task(_stale_claim_reaper_loop())


# ---------------------------------------------------------------------------
# Project Sessions & Members API
# ---------------------------------------------------------------------------
from sos.services.squad.service import DEFAULT_TENANT_ID, now_iso
from sos.services.squad.sessions import (
    ProjectSessionService,
    SessionAlreadyClosedError,
    SessionNotFoundError,
)
from sos.services.squad.members import (
    InsufficientRoleError,
    MemberNotFoundError,
    ProjectMemberService,
    lookup_sos_token,
    role_satisfies,
    DEFAULT_CUSTOMER_ROLE,
)

_session_svc = ProjectSessionService()
_member_svc = ProjectMemberService()


def _require_project_role(min_role: str, project_id_param: str = "project_id"):
    """FastAPI dependency: validates SOS bus token + project scope + minimum role.

    min_role: "observer" | "member" | "owner"
    """
    from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

    bearer = HTTPBearer(auto_error=False)

    async def _dep(
        project_id: str,
        credentials: Optional[HTTPAuthorizationCredentials] = Depends(bearer),
    ) -> dict:
        raw_token = credentials.credentials if credentials else ""
        if not raw_token:
            raise HTTPException(status_code=401, detail="missing_authorization")

        token_rec = lookup_sos_token(raw_token)
        if token_rec is None:
            raise HTTPException(status_code=401, detail="invalid_token")

        # Project scope check — token must be scoped to this project
        token_project = token_rec.get("project")
        if token_project and token_project != project_id:
            raise HTTPException(status_code=403, detail="token_project_mismatch")

        # Role check
        token_role = token_rec.get("role", DEFAULT_CUSTOMER_ROLE)
        if not role_satisfies(token_role, min_role):
            raise HTTPException(
                status_code=403,
                detail=f"insufficient_role: need {min_role}, have {token_role}",
            )
        return token_rec

    return _dep


class CheckinRequest(BaseModel):
    agent_id: str
    context: dict = Field(default_factory=dict)


class CheckoutRequest(BaseModel):
    reason: str = "explicit"


class AddMemberRequest(BaseModel):
    agent_id: str
    role: str = "member"


@app.post("/projects/{project_id}/checkin")
async def project_checkin(
    project_id: str,
    body: CheckinRequest,
    token_rec: dict = Depends(_require_project_role("member")),
):
    """Open or resume a session for this agent+project. Idempotent."""
    try:
        result = _session_svc.checkin(
            project_id=project_id,
            agent_id=body.agent_id,
            context=body.context,
        )
        return result
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))


@app.post("/sessions/{session_id}/checkout")
async def session_checkout(
    session_id: str,
    body: CheckoutRequest,
    authorization: Optional[str] = Header(default=None),
):
    """Close a session explicitly."""
    raw_token = (authorization or "").removeprefix("Bearer ").strip()
    if not raw_token:
        raise HTTPException(status_code=401, detail="missing_authorization")
    token_rec = lookup_sos_token(raw_token)
    if token_rec is None:
        raise HTTPException(status_code=401, detail="invalid_token")
    token_role = token_rec.get("role", DEFAULT_CUSTOMER_ROLE)
    if not role_satisfies(token_role, "member"):
        raise HTTPException(status_code=403, detail="insufficient_role")
    try:
        return _session_svc.checkout(session_id, reason=body.reason)
    except SessionNotFoundError:
        raise HTTPException(status_code=404, detail="session_not_found")
    except SessionAlreadyClosedError:
        raise HTTPException(status_code=409, detail="session_already_closed")


@app.post("/sessions/{session_id}/heartbeat")
async def session_heartbeat(
    session_id: str,
    authorization: Optional[str] = Header(default=None),
):
    """Keep a session alive."""
    raw_token = (authorization or "").removeprefix("Bearer ").strip()
    if not raw_token:
        raise HTTPException(status_code=401, detail="missing_authorization")
    token_rec = lookup_sos_token(raw_token)
    if token_rec is None:
        raise HTTPException(status_code=401, detail="invalid_token")
    token_role = token_rec.get("role", DEFAULT_CUSTOMER_ROLE)
    if not role_satisfies(token_role, "member"):
        raise HTTPException(status_code=403, detail="insufficient_role")
    try:
        _session_svc.heartbeat(session_id)
        return {"status": "ok"}
    except SessionNotFoundError:
        raise HTTPException(status_code=404, detail="session_not_found")


@app.get("/projects/{project_id}/sessions")
async def list_project_sessions(
    project_id: str,
    limit: int = 50,
    offset: int = 0,
    token_rec: dict = Depends(_require_project_role("observer")),
):
    """List sessions for a project (observer+)."""
    sessions = _session_svc.list_sessions(project_id, limit=limit, offset=offset)
    return {"sessions": sessions, "count": len(sessions)}


@app.get("/sessions/{session_id}")
async def get_session(
    session_id: str,
    authorization: Optional[str] = Header(default=None),
):
    """Session detail + events. Requires observer+ token scoped to this project."""
    raw_token = (authorization or "").removeprefix("Bearer ").strip()
    if not raw_token:
        raise HTTPException(status_code=401, detail="missing_authorization")
    token_rec = lookup_sos_token(raw_token)
    if token_rec is None:
        raise HTTPException(status_code=401, detail="invalid_token")
    try:
        session = _session_svc.get_session(session_id)
    except SessionNotFoundError:
        raise HTTPException(status_code=404, detail="session_not_found")
    # Project scope check on the fetched session
    token_project = token_rec.get("project")
    if token_project and token_project != session["project_id"]:
        raise HTTPException(status_code=403, detail="token_project_mismatch")
    token_role = token_rec.get("role", DEFAULT_CUSTOMER_ROLE)
    if not role_satisfies(token_role, "observer"):
        raise HTTPException(status_code=403, detail="insufficient_role")
    return session


@app.post("/projects/{project_id}/members")
async def add_project_member(
    project_id: str,
    body: AddMemberRequest,
    token_rec: dict = Depends(_require_project_role("owner")),
):
    """Add or update a member in the project. Requires owner token."""
    try:
        return _member_svc.add_member(project_id, body.agent_id, role=body.role)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))


@app.get("/projects/{project_id}/members")
async def list_project_members(
    project_id: str,
    token_rec: dict = Depends(_require_project_role("observer")),
):
    """List project members."""
    return {"members": _member_svc.list_members(project_id)}


@app.delete("/projects/{project_id}/members/{agent_id}")
async def remove_project_member(
    project_id: str,
    agent_id: str,
    token_rec: dict = Depends(_require_project_role("owner")),
):
    """Remove a member from the project. Requires owner token."""
    try:
        _member_svc.remove_member(project_id, agent_id)
        return {"status": "removed", "agent_id": agent_id}
    except MemberNotFoundError:
        raise HTTPException(status_code=404, detail="member_not_found")


# ---------------------------------------------------------------------------
# Project Resources API
# ---------------------------------------------------------------------------

class AddResourceRequest(BaseModel):
    resource_type: str           # 'repo', 'domain', 'analytics', etc.
    url: Optional[str] = None
    local_path: Optional[str] = None
    meta: dict = {}


@app.post("/projects/{project_id}/resources")
async def add_project_resource(
    project_id: str,
    req: AddResourceRequest,
    token_rec: dict = Depends(_require_project_role("member")),
):
    """Register a resource (repo, domain, etc.) with the project."""
    from uuid import uuid4
    resource_id = str(uuid4())
    added_at = now_iso()
    with SquadDB().connect() as conn:
        conn.execute(
            """
            INSERT INTO project_resources
                (id, project_id, tenant_id, resource_type, url, local_path, meta_json, added_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                resource_id, project_id, DEFAULT_TENANT_ID,
                req.resource_type, req.url, req.local_path,
                json.dumps(req.meta), added_at,
            ),
        )
    return {"id": resource_id, "project_id": project_id, "added_at": added_at}


@app.get("/projects/{project_id}/resources")
async def list_project_resources(
    project_id: str,
    token_rec: dict = Depends(_require_project_role("observer")),
):
    """List all resources registered to a project."""
    with SquadDB().connect() as conn:
        rows = conn.execute(
            """
            SELECT id, resource_type, url, local_path, meta_json, added_at
            FROM project_resources
            WHERE project_id = ? AND tenant_id = ?
            ORDER BY added_at DESC
            """,
            (project_id, DEFAULT_TENANT_ID),
        ).fetchall()
    return {
        "resources": [
            {**dict(r), "meta": json.loads(r["meta_json"])}
            for r in rows
        ]
    }


# ---------------------------------------------------------------------------
# Customer Intake API
# ---------------------------------------------------------------------------

import hmac as _hmac
from sos.services.squad.intake import (
    CustomerIntakeService,
    IntakeNotFoundError,
    IntakeStatusError,
    MintFailedError,
    validate_initial_roles,
)

_intake_svc = CustomerIntakeService()
_GHL_WEBHOOK_SECRET = os.getenv("GHL_WEBHOOK_SECRET", "")


class IntakeCreateRequest(BaseModel):
    customer_name: str
    customer_slug: str
    domain: Optional[str] = None
    repo_url: Optional[str] = None
    icp: Optional[str] = None
    okrs_json: str = "[]"
    cause_draft: Optional[str] = None
    descriptor_draft: Optional[str] = None
    initial_roles_json: str = '["advisor","intern"]'
    source: str = "direct"
    ghl_contact_id: Optional[str] = None


class IntakeUpdateRequest(BaseModel):
    cause_draft: Optional[str] = None
    descriptor_draft: Optional[str] = None
    initial_roles_json: Optional[str] = None
    domain: Optional[str] = None
    repo_url: Optional[str] = None
    icp: Optional[str] = None
    okrs_json: Optional[str] = None


@app.post("/customers/intake")
async def create_intake(
    req: IntakeCreateRequest,
    authorization: Optional[str] = Header(default=None),
) -> dict[str, Any]:
    """Create a customer intake record. System bearer required."""
    _require_system_bearer(authorization)
    try:
        validate_initial_roles(req.initial_roles_json)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail={"error": "invalid_initial_roles", "detail": str(exc)})
    try:
        intake = _intake_svc.create_intake(
            customer_name=req.customer_name,
            customer_slug=req.customer_slug,
            domain=req.domain,
            repo_url=req.repo_url,
            icp=req.icp,
            okrs_json=req.okrs_json,
            cause_draft=req.cause_draft,
            descriptor_draft=req.descriptor_draft,
            initial_roles_json=req.initial_roles_json,
            source=req.source,
            ghl_contact_id=req.ghl_contact_id,
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc))
    return intake


@app.get("/customers/{intake_id}")
async def get_intake(
    intake_id: str,
    authorization: Optional[str] = Header(default=None),
) -> dict[str, Any]:
    """Get a customer intake by ID. System bearer required."""
    _require_system_bearer(authorization)
    try:
        return _intake_svc.get_intake(intake_id)
    except IntakeNotFoundError:
        raise HTTPException(status_code=404, detail="intake_not_found")


@app.get("/customers")
async def list_intakes(
    status: Optional[str] = None,
    limit: int = 50,
    offset: int = 0,
    authorization: Optional[str] = Header(default=None),
) -> dict[str, Any]:
    """List customer intakes, optionally filtered by status. System bearer required."""
    _require_system_bearer(authorization)
    intakes = _intake_svc.list_intakes(status=status, limit=limit, offset=offset)
    return {"intakes": intakes, "count": len(intakes)}


@app.patch("/customers/{intake_id}")
async def update_intake(
    intake_id: str,
    req: IntakeUpdateRequest,
    authorization: Optional[str] = Header(default=None),
) -> dict[str, Any]:
    """Update editable fields of a pending intake. System bearer required."""
    _require_system_bearer(authorization)
    updates = req.model_dump(exclude_none=True)
    if "initial_roles_json" in updates:
        try:
            validate_initial_roles(updates["initial_roles_json"])
        except ValueError as exc:
            raise HTTPException(status_code=422, detail={"error": "invalid_initial_roles", "detail": str(exc)})
    try:
        return _intake_svc.update_intake(intake_id, updates)
    except IntakeNotFoundError:
        raise HTTPException(status_code=404, detail="intake_not_found")
    except IntakeStatusError as exc:
        raise HTTPException(status_code=409, detail=str(exc))


@app.post("/customers/{intake_id}/approve")
async def approve_intake(
    intake_id: str,
    authorization: Optional[str] = Header(default=None),
) -> dict[str, Any]:
    """Approve an intake. System bearer ONLY — project owner tokens are rejected."""
    _require_system_bearer(authorization)
    try:
        return _intake_svc.approve(intake_id, approver_agent_id="system")
    except IntakeNotFoundError:
        raise HTTPException(status_code=404, detail="intake_not_found")
    except IntakeStatusError as exc:
        raise HTTPException(status_code=409, detail=str(exc))


@app.post("/customers/{intake_id}/reject")
async def reject_intake(
    intake_id: str,
    authorization: Optional[str] = Header(default=None),
) -> dict[str, Any]:
    """Reject an intake. System bearer ONLY."""
    _require_system_bearer(authorization)
    try:
        return _intake_svc.reject(intake_id)
    except IntakeNotFoundError:
        raise HTTPException(status_code=404, detail="intake_not_found")
    except IntakeStatusError as exc:
        raise HTTPException(status_code=409, detail=str(exc))


@app.post("/customers/{intake_id}/mint")
async def mint_intake(
    intake_id: str,
    authorization: Optional[str] = Header(default=None),
) -> dict[str, Any]:
    """Trigger knight mint for an approved intake. System bearer required."""
    _require_system_bearer(authorization)
    try:
        result = _intake_svc.mint(intake_id)
        return result
    except IntakeNotFoundError:
        raise HTTPException(status_code=404, detail="intake_not_found")
    except IntakeStatusError as exc:
        raise HTTPException(status_code=409, detail=str(exc))
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc))
    except MintFailedError as exc:
        raise HTTPException(
            status_code=500,
            detail={"error": "mint_failed", "detail": str(exc)},
        )


@app.post("/customers/{intake_id}/seed-roles")
async def seed_roles(
    intake_id: str,
    authorization: Optional[str] = Header(default=None),
) -> dict[str, Any]:
    """Retry role seeding for a minted intake. System bearer required."""
    _require_system_bearer(authorization)
    try:
        return _intake_svc.seed_roles(intake_id)
    except IntakeNotFoundError:
        raise HTTPException(status_code=404, detail="intake_not_found")
    except IntakeStatusError as exc:
        raise HTTPException(status_code=409, detail=str(exc))


@app.post("/webhooks/ghl/lead")
async def ghl_lead_webhook(
    payload: dict[str, Any],
    x_ghl_secret: Optional[str] = Header(default=None, alias="X-GHL-Secret"),
) -> dict[str, Any]:
    """Receive GHL lead webhook and create a pending intake. Verified by X-GHL-Secret."""
    if not _GHL_WEBHOOK_SECRET:
        raise HTTPException(status_code=503, detail="ghl_webhook_not_configured")
    presented = (x_ghl_secret or "").encode()
    expected = _GHL_WEBHOOK_SECRET.encode()
    if not _hmac.compare_digest(presented, expected):
        raise HTTPException(status_code=401, detail="invalid_ghl_secret")
    try:
        intake = _intake_svc.create_from_ghl(payload)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc))
    return {"status": "created", "intake_id": intake["id"], "customer_slug": intake["customer_slug"]}


# =============================================================================
# Section 1A — Role Registry (roles, permissions, assignments)
# =============================================================================

from sos.services.squad.roles import RoleService, RoleNotFoundError, RoleDuplicateError, RolePrivilegeError

_role_svc = RoleService()


class _RoleCreate(BaseModel):
    name: str
    description: Optional[str] = None


class _PermissionBody(BaseModel):
    permission: str


class _AssignBody(BaseModel):
    assignee_id: str
    assignee_type: str = "agent"
    assigned_by: str


@app.post("/projects/{project_id}/roles")
async def create_project_role(
    project_id: str,
    body: _RoleCreate,
    authorization: Optional[str] = Header(default=None),
) -> dict[str, Any]:
    """Create a named role for a project. Owner-level auth."""
    auth = _squad_lookup_token(_parse_bearer(authorization), SquadDB())
    if not auth:
        raise HTTPException(status_code=401, detail="invalid_token")
    try:
        return _role_svc.create_role(
            project_id, body.name,
            tenant_id=auth.tenant_scope or "default",
            description=body.description,
        )
    except RoleDuplicateError as exc:
        raise HTTPException(status_code=409, detail=str(exc))


@app.get("/projects/{project_id}/roles")
async def list_project_roles(
    project_id: str,
    authorization: Optional[str] = Header(default=None),
) -> dict[str, Any]:
    auth = _squad_lookup_token(_parse_bearer(authorization), SquadDB())
    if not auth:
        raise HTTPException(status_code=401, detail="invalid_token")
    roles = _role_svc.list_roles(project_id, tenant_id=auth.tenant_scope or "default")
    return {"roles": roles}


@app.post("/roles/{role_id}/permissions")
async def add_role_permission(
    role_id: str,
    body: _PermissionBody,
    authorization: Optional[str] = Header(default=None),
) -> dict[str, Any]:
    _squad_lookup_token(_parse_bearer(authorization), SquadDB()) or _raise_401()
    try:
        return _role_svc.add_permission(role_id, body.permission)
    except RoleNotFoundError:
        raise HTTPException(status_code=404, detail="role_not_found")


@app.delete("/roles/{role_id}/permissions/{permission}")
async def remove_role_permission(
    role_id: str,
    permission: str,
    authorization: Optional[str] = Header(default=None),
) -> dict[str, Any]:
    _squad_lookup_token(_parse_bearer(authorization), SquadDB()) or _raise_401()
    _role_svc.remove_permission(role_id, permission)
    return {"deleted": True}


@app.post("/roles/{role_id}/assignments")
async def assign_role(
    role_id: str,
    body: _AssignBody,
    authorization: Optional[str] = Header(default=None),
) -> dict[str, Any]:
    auth = _squad_lookup_token(_parse_bearer(authorization), SquadDB())
    if not auth:
        _raise_401()
    caller_id = auth.identity.id if auth.identity else "system"
    try:
        return _role_svc.assign_role(
            role_id, body.assignee_id,
            assignee_type=body.assignee_type,
            assigned_by=body.assigned_by,
            caller_id=caller_id,
        )
    except RoleNotFoundError:
        raise HTTPException(status_code=404, detail="role_not_found")
    except RolePrivilegeError as exc:
        raise HTTPException(status_code=403, detail=str(exc))


@app.delete("/roles/{role_id}/assignments/{assignee_id}")
async def revoke_role_assignment(
    role_id: str,
    assignee_id: str,
    authorization: Optional[str] = Header(default=None),
) -> dict[str, Any]:
    _squad_lookup_token(_parse_bearer(authorization), SquadDB()) or _raise_401()
    _role_svc.revoke_assignment(role_id, assignee_id)
    return {"revoked": True}


@app.get("/roles/{role_id}/assignments")
async def list_role_assignments(
    role_id: str,
    authorization: Optional[str] = Header(default=None),
) -> dict[str, Any]:
    _squad_lookup_token(_parse_bearer(authorization), SquadDB()) or _raise_401()
    assignments = _role_svc.list_assignments(role_id)
    return {"assignments": assignments}


@app.get("/agents/{agent_id}/roles")
async def get_agent_roles(
    agent_id: str,
    authorization: Optional[str] = Header(default=None),
) -> dict[str, Any]:
    _squad_lookup_token(_parse_bearer(authorization), SquadDB()) or _raise_401()
    roles = _role_svc.get_agent_roles(agent_id)
    return {"agent_id": agent_id, "roles": roles}


@app.get("/me/roles")
async def get_my_roles(
    authorization: Optional[str] = Header(default=None),
) -> dict[str, Any]:
    auth = _squad_lookup_token(_parse_bearer(authorization), SquadDB())
    if not auth:
        raise HTTPException(status_code=401, detail="invalid_token")
    roles = _role_svc.get_token_roles(auth.tenant_id or "")
    return {"tenant_id": auth.tenant_id, "roles": roles}


# =============================================================================
# Section 3 — Structured Records (contacts, partners, opportunities, referrals)
# =============================================================================

from sos.services.squad.records import (
    ContactsService, PartnersService, OpportunitiesService, ReferralsService,
    RecordNotFoundError, RecordConflictError,
)

_contacts_svc = ContactsService()
_partners_svc = PartnersService()
_opps_svc = OpportunitiesService()
_refs_svc = ReferralsService()


def _parse_bearer(authorization: Optional[str]) -> str:
    if not authorization:
        return ""
    return authorization.replace("Bearer ", "").replace("bearer ", "").strip()


def _raise_401() -> None:
    raise HTTPException(status_code=401, detail="invalid_token")


def _workspace(auth: Any) -> str:
    return auth.tenant_scope or "default"


def _actor(auth: Any) -> str:
    return auth.identity.id if auth.identity else "system"


# ── Contacts ──────────────────────────────────────────────────────────────


class _ContactCreate(BaseModel):
    first_name: str
    last_name: str
    external_id: Optional[str] = None
    email: Optional[str] = None
    phone: Optional[str] = None
    title: Optional[str] = None
    org_id: Optional[str] = None
    visibility_tier: str = "firm_internal"
    engagement_status: str = "prospect"
    source: Optional[str] = None
    next_action: Optional[str] = None
    notes_ref: Optional[str] = None
    notes: Optional[str] = None
    owner_id: str = "system"


class _ContactUpdate(BaseModel):
    first_name: Optional[str] = None
    last_name: Optional[str] = None
    email: Optional[str] = None
    phone: Optional[str] = None
    title: Optional[str] = None
    org_id: Optional[str] = None
    visibility_tier: Optional[str] = None
    engagement_status: Optional[str] = None
    source: Optional[str] = None
    next_action: Optional[str] = None
    notes_ref: Optional[str] = None
    notes: Optional[str] = None
    owner_id: Optional[str] = None


class _TouchBody(BaseModel):
    note: Optional[str] = None


@app.post("/contacts")
async def create_contact(
    body: _ContactCreate,
    authorization: Optional[str] = Header(default=None),
) -> dict[str, Any]:
    auth = _squad_lookup_token(_parse_bearer(authorization), SquadDB())
    if not auth:
        _raise_401()
    try:
        return _contacts_svc.create(
            _workspace(auth), body.first_name, body.last_name,
            **{k: v for k, v in body.model_dump().items() if k not in ("first_name", "last_name")},
            actor=_actor(auth),
        )
    except RecordConflictError as exc:
        raise HTTPException(status_code=409, detail=str(exc))


@app.get("/contacts")
async def list_contacts(
    owner_id: Optional[str] = None,
    org_id: Optional[str] = None,
    status: Optional[str] = None,
    archived: bool = False,
    tier: Optional[str] = None,
    authorization: Optional[str] = Header(default=None),
) -> dict[str, Any]:
    auth = _squad_lookup_token(_parse_bearer(authorization), SquadDB())
    if not auth:
        _raise_401()
    contacts = _contacts_svc.list(
        _workspace(auth), owner_id=owner_id, org_id=org_id,
        status=status, include_archived=archived, tier=tier,
    )
    return {"contacts": contacts}


@app.get("/contacts/by-email/{email}")
async def get_contact_by_email(
    email: str,
    authorization: Optional[str] = Header(default=None),
) -> dict[str, Any]:
    auth = _squad_lookup_token(_parse_bearer(authorization), SquadDB())
    if not auth:
        _raise_401()
    contact = _contacts_svc.get_by_email(_workspace(auth), email)
    if not contact:
        raise HTTPException(status_code=404, detail="not_found")
    return contact


@app.get("/contacts/{contact_id}")
async def get_contact(
    contact_id: str,
    authorization: Optional[str] = Header(default=None),
) -> dict[str, Any]:
    auth = _squad_lookup_token(_parse_bearer(authorization), SquadDB())
    if not auth:
        _raise_401()
    try:
        return _contacts_svc.get(contact_id, _workspace(auth))
    except RecordNotFoundError:
        raise HTTPException(status_code=404, detail="not_found")


@app.patch("/contacts/{contact_id}")
async def update_contact(
    contact_id: str,
    body: _ContactUpdate,
    authorization: Optional[str] = Header(default=None),
) -> dict[str, Any]:
    auth = _squad_lookup_token(_parse_bearer(authorization), SquadDB())
    if not auth:
        _raise_401()
    try:
        return _contacts_svc.update(
            contact_id, _workspace(auth), _actor(auth),
            **{k: v for k, v in body.model_dump().items() if v is not None},
        )
    except RecordNotFoundError:
        raise HTTPException(status_code=404, detail="not_found")


@app.post("/contacts/{contact_id}/touch")
async def touch_contact(
    contact_id: str,
    body: _TouchBody,
    authorization: Optional[str] = Header(default=None),
) -> dict[str, Any]:
    auth = _squad_lookup_token(_parse_bearer(authorization), SquadDB())
    if not auth:
        _raise_401()
    try:
        return _contacts_svc.touch(contact_id, _workspace(auth), _actor(auth), note=body.note)
    except RecordNotFoundError:
        raise HTTPException(status_code=404, detail="not_found")


@app.delete("/contacts/{contact_id}")
async def delete_contact(
    contact_id: str,
    authorization: Optional[str] = Header(default=None),
) -> dict[str, Any]:
    auth = _squad_lookup_token(_parse_bearer(authorization), SquadDB())
    if not auth:
        _raise_401()
    try:
        return _contacts_svc.soft_delete(contact_id, _workspace(auth), _actor(auth))
    except RecordNotFoundError:
        raise HTTPException(status_code=404, detail="not_found")


# ── Partners ──────────────────────────────────────────────────────────────


class _PartnerCreate(BaseModel):
    name: str
    type: str
    external_id: Optional[str] = None
    website_url: Optional[str] = None
    hq_country: Optional[str] = None
    primary_contact_id: Optional[str] = None
    parent_partner_id: Optional[str] = None
    revenue_split_pct: Optional[float] = None
    visibility_tier: str = "firm_internal"
    engagement_status: str = "prospect"
    notes: Optional[str] = None
    inkwell_page_slug: Optional[str] = None
    onboarded_at: Optional[str] = None


class _PartnerUpdate(BaseModel):
    name: Optional[str] = None
    type: Optional[str] = None
    website_url: Optional[str] = None
    hq_country: Optional[str] = None
    primary_contact_id: Optional[str] = None
    revenue_split_pct: Optional[float] = None
    visibility_tier: Optional[str] = None
    engagement_status: Optional[str] = None
    notes: Optional[str] = None
    active: Optional[int] = None


@app.post("/partners")
async def create_partner(
    body: _PartnerCreate,
    authorization: Optional[str] = Header(default=None),
) -> dict[str, Any]:
    auth = _squad_lookup_token(_parse_bearer(authorization), SquadDB())
    if not auth:
        _raise_401()
    try:
        return _partners_svc.create(
            _workspace(auth), body.name, body.type,
            **{k: v for k, v in body.model_dump().items() if k not in ("name", "type")},
            actor=_actor(auth),
        )
    except RecordConflictError as exc:
        raise HTTPException(status_code=409, detail=str(exc))


@app.get("/partners")
async def list_partners(
    type: Optional[str] = None,
    active_only: bool = True,
    status: Optional[str] = None,
    authorization: Optional[str] = Header(default=None),
) -> dict[str, Any]:
    auth = _squad_lookup_token(_parse_bearer(authorization), SquadDB())
    if not auth:
        _raise_401()
    partners = _partners_svc.list(_workspace(auth), type=type, active_only=active_only, status=status)
    return {"partners": partners}


@app.get("/partners/{partner_id}")
async def get_partner(
    partner_id: str,
    authorization: Optional[str] = Header(default=None),
) -> dict[str, Any]:
    auth = _squad_lookup_token(_parse_bearer(authorization), SquadDB())
    if not auth:
        _raise_401()
    try:
        return _partners_svc.get(partner_id, _workspace(auth))
    except RecordNotFoundError:
        raise HTTPException(status_code=404, detail="not_found")


@app.patch("/partners/{partner_id}")
async def update_partner(
    partner_id: str,
    body: _PartnerUpdate,
    authorization: Optional[str] = Header(default=None),
) -> dict[str, Any]:
    auth = _squad_lookup_token(_parse_bearer(authorization), SquadDB())
    if not auth:
        _raise_401()
    try:
        return _partners_svc.update(
            partner_id, _workspace(auth), _actor(auth),
            **{k: v for k, v in body.model_dump().items() if v is not None},
        )
    except RecordNotFoundError:
        raise HTTPException(status_code=404, detail="not_found")


@app.get("/partners/{partner_id}/contacts")
async def get_partner_contacts(
    partner_id: str,
    authorization: Optional[str] = Header(default=None),
) -> dict[str, Any]:
    auth = _squad_lookup_token(_parse_bearer(authorization), SquadDB())
    if not auth:
        _raise_401()
    contacts = _partners_svc.get_contacts(partner_id, _workspace(auth))
    return {"contacts": contacts}


@app.get("/partners/{partner_id}/opportunities")
async def get_partner_opportunities(
    partner_id: str,
    authorization: Optional[str] = Header(default=None),
) -> dict[str, Any]:
    auth = _squad_lookup_token(_parse_bearer(authorization), SquadDB())
    if not auth:
        _raise_401()
    opps = _partners_svc.get_opportunities(partner_id, _workspace(auth))
    return {"opportunities": opps}


# ── Opportunities ─────────────────────────────────────────────────────────


class _OppCreate(BaseModel):
    name: str
    type: str
    external_id: Optional[str] = None
    partner_id: Optional[str] = None
    primary_contact_id: Optional[str] = None
    stage: str = "prospect"
    estimated_value: Optional[float] = None
    estimated_close_at: Optional[str] = None
    owner_id: str = "system"
    notes_ref: Optional[str] = None
    notes: Optional[str] = None


class _OppUpdate(BaseModel):
    name: Optional[str] = None
    type: Optional[str] = None
    partner_id: Optional[str] = None
    primary_contact_id: Optional[str] = None
    estimated_value: Optional[float] = None
    estimated_close_at: Optional[str] = None
    close_reason: Optional[str] = None
    owner_id: Optional[str] = None
    notes_ref: Optional[str] = None
    notes: Optional[str] = None


class _StageTransition(BaseModel):
    stage: str


@app.post("/opportunities")
async def create_opportunity(
    body: _OppCreate,
    authorization: Optional[str] = Header(default=None),
) -> dict[str, Any]:
    auth = _squad_lookup_token(_parse_bearer(authorization), SquadDB())
    if not auth:
        _raise_401()
    try:
        return _opps_svc.create(
            _workspace(auth), body.name, body.type,
            **{k: v for k, v in body.model_dump().items() if k not in ("name", "type")},
            actor=_actor(auth),
        )
    except RecordConflictError as exc:
        raise HTTPException(status_code=409, detail=str(exc))


@app.get("/opportunities")
async def list_opportunities(
    stage: Optional[str] = None,
    partner_id: Optional[str] = None,
    owner_id: Optional[str] = None,
    archived: bool = False,
    authorization: Optional[str] = Header(default=None),
) -> dict[str, Any]:
    auth = _squad_lookup_token(_parse_bearer(authorization), SquadDB())
    if not auth:
        _raise_401()
    opps = _opps_svc.list(
        _workspace(auth), stage=stage, partner_id=partner_id,
        owner_id=owner_id, include_archived=archived,
    )
    return {"opportunities": opps}


@app.get("/opportunities/pipeline-summary")
async def pipeline_summary(
    authorization: Optional[str] = Header(default=None),
) -> dict[str, Any]:
    auth = _squad_lookup_token(_parse_bearer(authorization), SquadDB())
    if not auth:
        _raise_401()
    return {"pipeline": _opps_svc.pipeline_summary(_workspace(auth))}


@app.get("/opportunities/{opp_id}")
async def get_opportunity(
    opp_id: str,
    authorization: Optional[str] = Header(default=None),
) -> dict[str, Any]:
    auth = _squad_lookup_token(_parse_bearer(authorization), SquadDB())
    if not auth:
        _raise_401()
    try:
        return _opps_svc.get(opp_id, _workspace(auth))
    except RecordNotFoundError:
        raise HTTPException(status_code=404, detail="not_found")


@app.patch("/opportunities/{opp_id}/stage")
async def transition_opportunity_stage(
    opp_id: str,
    body: _StageTransition,
    authorization: Optional[str] = Header(default=None),
) -> dict[str, Any]:
    auth = _squad_lookup_token(_parse_bearer(authorization), SquadDB())
    if not auth:
        _raise_401()
    try:
        return _opps_svc.transition_stage(opp_id, _workspace(auth), body.stage, _actor(auth))
    except RecordNotFoundError:
        raise HTTPException(status_code=404, detail="not_found")


@app.patch("/opportunities/{opp_id}")
async def update_opportunity(
    opp_id: str,
    body: _OppUpdate,
    authorization: Optional[str] = Header(default=None),
) -> dict[str, Any]:
    auth = _squad_lookup_token(_parse_bearer(authorization), SquadDB())
    if not auth:
        _raise_401()
    try:
        return _opps_svc.update(
            opp_id, _workspace(auth), _actor(auth),
            **{k: v for k, v in body.model_dump().items() if v is not None},
        )
    except RecordNotFoundError:
        raise HTTPException(status_code=404, detail="not_found")


# ── Referrals ─────────────────────────────────────────────────────────────


class _ReferralCreate(BaseModel):
    source_id: str
    source_type: str
    target_id: str
    target_type: str
    relationship: str
    strength: str = "moderate"
    context: Optional[str] = None
    referred_at: Optional[str] = None
    notes: Optional[str] = None


class _ReferralUpdate(BaseModel):
    strength: Optional[str] = None
    context: Optional[str] = None
    notes: Optional[str] = None
    referred_at: Optional[str] = None


@app.post("/referrals")
async def create_referral(
    body: _ReferralCreate,
    authorization: Optional[str] = Header(default=None),
) -> dict[str, Any]:
    auth = _squad_lookup_token(_parse_bearer(authorization), SquadDB())
    if not auth:
        _raise_401()
    try:
        return _refs_svc.create(
            _workspace(auth), body.source_id, body.source_type,
            body.target_id, body.target_type, body.relationship,
            strength=body.strength, context=body.context,
            referred_at=body.referred_at, notes=body.notes,
            actor=_actor(auth),
        )
    except RecordConflictError as exc:
        raise HTTPException(status_code=409, detail=str(exc))


@app.get("/referrals")
async def list_referrals(
    source_id: Optional[str] = None,
    source_type: Optional[str] = None,
    target_id: Optional[str] = None,
    target_type: Optional[str] = None,
    authorization: Optional[str] = Header(default=None),
) -> dict[str, Any]:
    auth = _squad_lookup_token(_parse_bearer(authorization), SquadDB())
    if not auth:
        _raise_401()
    refs = _refs_svc.list(
        _workspace(auth), source_id=source_id, source_type=source_type,
        target_id=target_id, target_type=target_type,
    )
    return {"referrals": refs}


@app.get("/referrals/network/{entity_id}")
async def referral_network(
    entity_id: str,
    hops: int = Query(default=2, ge=1, le=5),
    authorization: Optional[str] = Header(default=None),
) -> dict[str, Any]:
    auth = _squad_lookup_token(_parse_bearer(authorization), SquadDB())
    if not auth:
        _raise_401()
    return _refs_svc.network(entity_id, _workspace(auth), hops=hops)


@app.patch("/referrals/{ref_id}")
async def update_referral(
    ref_id: str,
    body: _ReferralUpdate,
    authorization: Optional[str] = Header(default=None),
) -> dict[str, Any]:
    auth = _squad_lookup_token(_parse_bearer(authorization), SquadDB())
    if not auth:
        _raise_401()
    try:
        return _refs_svc.update(
            ref_id, _workspace(auth), _actor(auth),
            **{k: v for k, v in body.model_dump().items() if v is not None},
        )
    except RecordNotFoundError:
        raise HTTPException(status_code=404, detail="not_found")


@app.delete("/referrals/{ref_id}")
async def delete_referral(
    ref_id: str,
    authorization: Optional[str] = Header(default=None),
) -> dict[str, Any]:
    auth = _squad_lookup_token(_parse_bearer(authorization), SquadDB())
    if not auth:
        _raise_401()
    _refs_svc.delete(ref_id, _workspace(auth), _actor(auth))
    return {"deleted": True}


# ── GHL sync ──────────────────────────────────────────────────────────────


@app.post("/integrations/ghl/sync-contact")
async def ghl_sync_contact(
    payload: dict[str, Any],
    authorization: Optional[str] = Header(default=None),
) -> dict[str, Any]:
    """Upsert contact from GHL lead payload. Keyed by email."""
    auth = _squad_lookup_token(_parse_bearer(authorization), SquadDB())
    if not auth:
        _raise_401()
    email = payload.get("email") or payload.get("contact", {}).get("email")
    first_name = payload.get("firstName") or payload.get("contact", {}).get("firstName", "Unknown")
    last_name = payload.get("lastName") or payload.get("contact", {}).get("lastName", "")
    ghl_id = payload.get("id") or payload.get("contact", {}).get("id")
    ws = _workspace(auth)
    actor = _actor(auth)

    if email:
        existing = _contacts_svc.get_by_email(ws, email)
        if existing:
            return {"status": "existing", "contact_id": existing["id"]}

    try:
        contact = _contacts_svc.create(
            ws, first_name, last_name,
            email=email,
            external_id=ghl_id,
            source="ghl",
            owner_id=actor,
            actor=actor,
        )
        return {"status": "created", "contact_id": contact["id"]}
    except RecordConflictError:
        existing = _contacts_svc.get_by_email(ws, email) if email else None
        return {"status": "conflict", "contact_id": existing["id"] if existing else None}


if __name__ == "__main__":
    import uvicorn
    port = int(os.environ.get("SOS_SQUAD_PORT", "8060"))
    uvicorn.run(app, host="0.0.0.0", port=port)
