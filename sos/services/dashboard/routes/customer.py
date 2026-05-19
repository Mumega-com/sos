"""GET /, GET /dashboard, GET /api/status."""
from __future__ import annotations

from fastapi import APIRouter, Request, Response
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse

from ..auth import _tenant_from_cookie
from ..config import COOKIE_NAME
from ..tenants import _agent_status, _fetch_memory, _fetch_tasks, _tenant_skills_and_usage
from ..templates.dashboard import _dashboard_html

router = APIRouter()


@router.get("/dashboard", response_class=HTMLResponse)
async def dashboard(request: Request) -> Response:
    cookie_val = request.cookies.get(COOKIE_NAME)
    tenant = _tenant_from_cookie(cookie_val)
    if not tenant:
        return RedirectResponse(url="/login", status_code=302)

    project = tenant.get("project")
    bus_token = tenant.get("token")
    agents = _agent_status(project)
    tasks = await _fetch_tasks(project)
    memory = await _fetch_memory(project, bus_token=bus_token)
    moat = _tenant_skills_and_usage(project)

    html = _dashboard_html(tenant, agents, tasks, memory, moat=moat)
    return HTMLResponse(html)


@router.get("/api/status")
async def api_status(request: Request) -> Response:
    cookie_val = request.cookies.get(COOKIE_NAME)
    tenant = _tenant_from_cookie(cookie_val)
    if not tenant:
        return JSONResponse({"error": "unauthorized"}, status_code=401)

    project = tenant.get("project")
    bus_token = tenant.get("token")
    agents = _agent_status(project)
    tasks = await _fetch_tasks(project)
    memory = await _fetch_memory(project, bus_token=bus_token)

    return JSONResponse({
        "tenant": tenant.get("label", ""),
        "project": project,
        "agents_online": agents["online"],
        "agents": agents["agents"],
        "task_count": len(tasks),
        "tasks": tasks,
        "memory_count": memory["count"],
    })
