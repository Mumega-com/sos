from __future__ import annotations

import os
import time
from dataclasses import asdict
from typing import Any, Optional

from fastapi import Depends, FastAPI, HTTPException
from fastapi.encoders import jsonable_encoder
from pydantic import BaseModel, Field

from sos import __version__
from sos.services._health import health_response
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
from sos.services.squad.auth import AuthContext, require_capability
from sos.services.squad import PipelineService, SquadService, SquadSkillService, SquadStateService, SquadTaskService


app = FastAPI(title="SOS Squad Service", version=__version__)

_START_TIME = time.time()

squads = SquadService()
tasks = SquadTaskService()
skills = SquadSkillService()
state = SquadStateService(mirror_sync=False)
pipelines = PipelineService()


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
async def health() -> dict[str, Any]:
    return health_response("squad", _START_TIME)


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
) -> dict[str, Any]:
    task = _to_task(payload)
    return {"task": _json(task), "response": _json(tasks.create(task, tenant_id=auth.tenant_scope or "default"))}


@app.get("/tasks")
async def list_tasks(
    squad_id: str | None = None,
    status: TaskStatus | None = None,
    auth: AuthContext = Depends(require_capability("tasks", "read")),
) -> list[dict[str, Any]]:
    return _json(tasks.list(squad_id=squad_id, status=status, tenant_id=auth.tenant_scope))


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
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc))
    return _json(claim)


@app.post("/tasks/{task_id}/route")
async def route_task(
    task_id: str,
    payload: RouteIn,
    auth: AuthContext = Depends(require_capability("tasks", "write")),
) -> dict[str, Any]:
    try:
        decision = tasks.route(task_id, payload.assignee, payload.skill_id, payload.reason, tenant_id=auth.tenant_scope)
    except KeyError:
        raise HTTPException(status_code=404, detail="task_not_found")
    return _json(decision)


@app.post("/tasks/{task_id}/complete")
async def complete_task(
    task_id: str,
    payload: CompleteIn,
    auth: AuthContext = Depends(require_capability("tasks", "write")),
) -> dict[str, Any]:
    try:
        task = tasks.complete(task_id, payload.result, tenant_id=auth.tenant_scope)
    except KeyError:
        raise HTTPException(status_code=404, detail="task_not_found")
    return _json(task)


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


if __name__ == "__main__":
    import uvicorn
    port = int(os.environ.get("SOS_SQUAD_PORT", "8060"))
    uvicorn.run(app, host="0.0.0.0", port=port)
