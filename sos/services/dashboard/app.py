"""Mumega Tenant Dashboard — customer-facing web UI.

Shows agent status, recent tasks, memory entries, analytics, and billing.
Runs on port 8090. Auth via sos.services.auth — single source of truth.
"""
from __future__ import annotations

import json
import logging
import os
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import httpx
import redis
from fastapi import Cookie, FastAPI, Form, Request, Response
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse

from sos.services.auth import verify_bearer as _auth_verify_bearer

logger = logging.getLogger("dashboard")

app = FastAPI(title="Mumega Dashboard", docs_url=None, redoc_url=None)

REDIS_PASSWORD = os.environ.get("REDIS_PASSWORD", "")
SQUAD_URL = os.environ.get("SQUAD_URL", "http://localhost:8060")
MIRROR_URL = os.environ.get("MIRROR_URL", "http://localhost:8844")
COOKIE_NAME = "mum_dash"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _auth_ctx_to_entry(token: str) -> dict[str, Any] | None:
    """Convert an AuthContext into the legacy dict shape the templates expect.

    Preserves the ``token`` key so the cookie round-trip and _tenant_from_cookie
    keep working without changes. Delegates all token verification to the
    canonical sos.services.auth module.
    """
    ctx = _auth_verify_bearer(f"Bearer {token}")
    if ctx is None:
        return None
    return {
        "token": token,
        "project": ctx.project,
        "tenant_slug": ctx.tenant_slug,
        "agent": ctx.agent,
        "label": ctx.label,
        "is_system": ctx.is_system,
        "is_admin": ctx.is_admin,
        "active": True,
    }


def _verify_token(token: str) -> dict[str, Any] | None:
    """Thin wrapper — delegates to sos.services.auth.verify_bearer.

    Kept for backwards compatibility: any caller that missed this migration
    still works unchanged. Internals now go through the canonical auth module.
    """
    return _auth_ctx_to_entry(token)


def _get_redis() -> redis.Redis:  # type: ignore[type-arg]
    return redis.Redis(
        host="localhost",
        port=6379,
        password=REDIS_PASSWORD,
        decode_responses=True,
    )


def _tenant_from_cookie(cookie_val: str | None) -> dict[str, Any] | None:
    if not cookie_val:
        return None
    try:
        data = json.loads(cookie_val)
        # Re-verify token is still active
        entry = _verify_token(data.get("token", ""))
        if entry:
            return data
    except Exception:
        pass
    return None


async def _fetch_tasks(project: str | None) -> list[dict[str, Any]]:
    try:
        params: dict[str, Any] = {"limit": 5}
        if project:
            params["squad"] = project
        async with httpx.AsyncClient(timeout=3) as client:
            resp = await client.get(f"{SQUAD_URL}/tasks", params=params)
            if resp.status_code == 200:
                data = resp.json()
                # Handle both list and dict-with-tasks responses
                if isinstance(data, list):
                    return data[:5]
                if isinstance(data, dict) and "tasks" in data:
                    return data["tasks"][:5]
    except Exception:
        logger.debug("Squad service unreachable", exc_info=True)
    return []


async def _fetch_memory(project: str | None, bus_token: str | None = None) -> dict[str, Any]:
    # Mirror exposes /stats (global count) and /recent/{agent} (scoped list).
    # /recent requires Bearer auth — customer tokens are auto-scoped to their project.
    try:
        headers = {"Authorization": f"Bearer {bus_token}"} if bus_token else {}
        async with httpx.AsyncClient(timeout=3) as client:
            agent = project or "river"
            recent_resp = await client.get(
                f"{MIRROR_URL}/recent/{agent}",
                params={"limit": 1},
                headers=headers,
            )
            entries: list[dict[str, Any]] = []
            if recent_resp.status_code == 200:
                data = recent_resp.json()
                entries = data.get("engrams", []) if isinstance(data, dict) else []

            count = len(entries)
            if project:
                count_resp = await client.get(
                    f"{MIRROR_URL}/recent/{project}",
                    params={"limit": 1000, "project": project},
                    headers=headers,
                )
                if count_resp.status_code == 200:
                    cdata = count_resp.json()
                    count = cdata.get("count", count) if isinstance(cdata, dict) else count
            else:
                stats_resp = await client.get(f"{MIRROR_URL}/stats")
                if stats_resp.status_code == 200:
                    count = stats_resp.json().get("total_engrams", count)

            return {
                "count": count,
                "latest": entries[0] if entries else None,
            }
    except Exception:
        logger.debug("Mirror unreachable", exc_info=True)
    return {"count": 0, "latest": None}


def _tenant_skills_and_usage(project: str | None) -> dict[str, Any]:
    """Tenant-scoped moat data for the customer dashboard.

    Reads the SkillCard registry + UsageLog and returns three summaries:
      - skills_invoked: skills this tenant has used (from invocations_by_tenant)
      - skills_authored: skills whose author_agent == agent:<slug>, with earnings
      - recent_usage: last 10 UsageLog events tenant-scoped
    Empty-state tolerant: any missing data returns a harmless empty list.
    """
    out: dict[str, Any] = {
        "skills_invoked": [],
        "skills_authored": [],
        "recent_usage": [],
        "total_spent_micros": 0,
        "total_earned_micros": 0,
    }
    if not project:
        return out

    # Skills registry
    try:
        from sos.skills.registry import Registry
        reg = Registry()
        cards = reg.list()
        author_uri = f"agent:{project}"
        for card in cards:
            earnings = card.earnings
            # Did this tenant invoke it?
            if earnings and earnings.invocations_by_tenant:
                if project in earnings.invocations_by_tenant:
                    out["skills_invoked"].append({
                        "id": card.id,
                        "name": card.name,
                        "author": card.author_agent,
                        "invocations": earnings.invocations_by_tenant[project],
                        "verification": card.verification.status if card.verification else "unverified",
                    })
            # Did this tenant author it?
            if card.author_agent == author_uri:
                out["skills_authored"].append({
                    "id": card.id,
                    "name": card.name,
                    "total_invocations": earnings.total_invocations if earnings else 0,
                    "total_earned_micros": earnings.total_earned_micros if earnings else 0,
                    "unique_tenants": len(earnings.invocations_by_tenant or {}) if earnings else 0,
                    "marketplace_listed": bool(card.commerce and card.commerce.marketplace_listed),
                })
                if earnings and earnings.total_earned_micros:
                    out["total_earned_micros"] += earnings.total_earned_micros
    except Exception:
        logger.debug("Registry read failed", exc_info=True)

    # UsageLog — tenant-scoped
    try:
        from sos.services.economy.usage_log import UsageLog
        log = UsageLog()
        events = log.read_all(tenant=project, limit=10)
        for e in events[::-1]:  # newest first
            out["recent_usage"].append({
                "occurred_at": e.occurred_at,
                "model": e.model,
                "endpoint": e.endpoint,
                "cost_micros": e.cost_micros,
            })
            out["total_spent_micros"] += e.cost_micros
    except Exception:
        logger.debug("UsageLog read failed", exc_info=True)

    return out


def _fmt_micros(micros: int) -> str:
    """Format integer micros as $X.XX (1 cent = 10_000 micros)."""
    if micros <= 0:
        return "$0.00"
    cents = micros / 10_000
    return f"${cents / 100:,.2f}"


def _agent_status(project: str | None) -> dict[str, Any]:
    try:
        r = _get_redis()
        # Check registry for agents associated with this project
        keys = r.keys("sos:registry:*")
        agents = []
        for key in keys:
            data = r.hgetall(key)
            if data:
                agents.append({
                    "name": key.split(":")[-1],
                    "status": data.get("status", "unknown"),
                    "last_seen": data.get("last_seen", ""),
                })
        if not agents:
            # Fallback: check bus:peers
            peers_raw = r.get("sos:peers")
            if peers_raw:
                try:
                    peers = json.loads(peers_raw)
                    for name, info in peers.items():
                        agents.append({
                            "name": name,
                            "status": "online",
                            "last_seen": info.get("last_seen", ""),
                        })
                except Exception:
                    pass
        return {"agents": agents, "online": sum(1 for a in agents if a.get("status") == "online")}
    except Exception:
        logger.debug("Redis unreachable", exc_info=True)
    return {"agents": [], "online": 0}


# ---------------------------------------------------------------------------
# HTML Templates (inline)
# ---------------------------------------------------------------------------

LOGIN_HTML = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Mumega — Login</title>
<style>
*{margin:0;padding:0;box-sizing:border-box}
body{background:#0F172A;color:#E2E8F0;font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,sans-serif;display:flex;align-items:center;justify-content:center;min-height:100vh}
.login-box{background:#1E293B;border:1px solid #334155;border-radius:12px;padding:40px;width:100%;max-width:400px}
h1{font-size:1.5rem;margin-bottom:8px;color:#F8FAFC}
.subtitle{color:#94A3B8;font-size:0.875rem;margin-bottom:24px}
label{display:block;font-size:0.8rem;color:#94A3B8;margin-bottom:6px;margin-top:16px}
input{width:100%;padding:10px 12px;border:1px solid #334155;border-radius:8px;background:#0F172A;color:#F8FAFC;font-size:0.9rem;outline:none}
input:focus{border-color:#6366F1}
button{width:100%;padding:12px;margin-top:24px;background:#6366F1;color:#fff;border:none;border-radius:8px;font-size:0.95rem;cursor:pointer;font-weight:500}
button:hover{background:#4F46E5}
.error{color:#F87171;font-size:0.85rem;margin-top:12px}
.logo{font-size:2rem;margin-bottom:4px}
</style>
</head>
<body>
<div class="login-box">
  <div class="logo">&#9670;</div>
  <h1>Mumega</h1>
  <p class="subtitle">Tenant Dashboard</p>
  <form method="POST" action="/login">
    <label for="token">Access Token</label>
    <input type="password" id="token" name="token" placeholder="sk-bus-..." required>
    {error}
  </form>
  <button type="submit" onclick="this.closest('.login-box').querySelector('form').submit()">Sign In</button>
</div>
</body>
</html>"""


def _dashboard_html(tenant: dict[str, Any], agents: dict[str, Any], tasks: list[dict[str, Any]], memory: dict[str, Any], moat: dict[str, Any] | None = None) -> str:
    label = tenant.get("label", tenant.get("project", "Tenant"))
    project = tenant.get("project", "")
    moat = moat or {"skills_invoked": [], "skills_authored": [], "recent_usage": [], "total_spent_micros": 0, "total_earned_micros": 0}

    # Agent cards
    agent_rows = ""
    if agents["agents"]:
        for a in agents["agents"]:
            status_dot = '<span style="color:#34D399">&#9679;</span>' if a.get("status") == "online" else '<span style="color:#F87171">&#9679;</span>'
            last = a.get("last_seen", "—")
            if last and last != "—":
                try:
                    dt = datetime.fromisoformat(last)
                    last = dt.strftime("%b %d, %H:%M")
                except Exception:
                    pass
            agent_rows += f'<div class="agent-row">{status_dot} <strong>{a["name"]}</strong> <span class="muted">Last seen: {last}</span></div>'
    else:
        agent_rows = '<p class="muted">No agents registered yet</p>'

    # Task list
    task_rows = ""
    if tasks:
        for t in tasks:
            title = t.get("title", t.get("name", "Untitled"))
            status = t.get("status", "unknown")
            badge_color = {"done": "#34D399", "claimed": "#FBBF24", "open": "#60A5FA"}.get(status, "#94A3B8")
            task_rows += f'<li><span class="badge" style="background:{badge_color}">{status}</span> {title}</li>'
    else:
        task_rows = '<li class="muted">No tasks yet</li>'

    # Memory
    mem_count = memory.get("count", 0)
    mem_latest = memory.get("latest")
    mem_preview = "No entries yet"
    if mem_latest:
        content = mem_latest.get("content", mem_latest.get("text", ""))
        mem_preview = (content[:120] + "...") if len(content) > 120 else content

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{label} — Mumega Dashboard</title>
<style>
*{{margin:0;padding:0;box-sizing:border-box}}
body{{background:#0F172A;color:#E2E8F0;font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,sans-serif;padding:24px;max-width:1200px;margin:0 auto}}
header{{display:flex;align-items:center;justify-content:space-between;margin-bottom:32px;padding-bottom:16px;border-bottom:1px solid #1E293B}}
header h1{{font-size:1.4rem;color:#F8FAFC}}
header .meta{{color:#94A3B8;font-size:0.8rem}}
.logout{{color:#94A3B8;text-decoration:none;font-size:0.85rem;border:1px solid #334155;padding:6px 14px;border-radius:6px}}
.logout:hover{{color:#F8FAFC;border-color:#6366F1}}
.grid{{display:grid;grid-template-columns:repeat(auto-fit,minmax(280px,1fr));gap:20px}}
.card{{background:#1E293B;border:1px solid #334155;border-radius:12px;padding:24px}}
.card h3{{font-size:0.95rem;color:#94A3B8;text-transform:uppercase;letter-spacing:0.05em;margin-bottom:16px}}
.card ul{{list-style:none;padding:0}}
.card li{{padding:6px 0;border-bottom:1px solid #0F172A;font-size:0.9rem}}
.card li:last-child{{border:none}}
.badge{{display:inline-block;padding:2px 8px;border-radius:4px;font-size:0.7rem;color:#0F172A;font-weight:600;margin-right:8px;text-transform:uppercase}}
.agent-row{{padding:8px 0;border-bottom:1px solid #0F172A;font-size:0.9rem}}
.agent-row:last-child{{border:none}}
.muted{{color:#64748B;font-size:0.85rem}}
.stat{{font-size:2rem;font-weight:700;color:#F8FAFC;margin-bottom:4px}}
.preview{{color:#CBD5E1;font-size:0.85rem;margin-top:8px;line-height:1.5}}
.pill{{display:inline-block;background:#6366F1;color:#fff;padding:2px 10px;border-radius:999px;font-size:0.75rem;margin-left:8px}}
</style>
</head>
<body>
<header>
  <div>
    <h1>&#9670; {label}</h1>
    <span class="meta">{project or 'admin'} &middot; {datetime.now(timezone.utc).strftime("%b %d, %Y %H:%M UTC")}</span>
  </div>
  <a href="/logout" class="logout">Sign Out</a>
</header>

<div class="grid">
  <div class="card">
    <h3>Agents</h3>
    <div class="stat">{agents['online']}<span class="pill">online</span></div>
    {agent_rows}
  </div>

  <div class="card">
    <h3>Recent Tasks</h3>
    <ul>
      {task_rows}
    </ul>
  </div>

  <div class="card">
    <h3>Memory</h3>
    <div class="stat">{mem_count}</div>
    <p class="muted">entries stored</p>
    <p class="preview">{mem_preview}</p>
  </div>

  <div class="card">
    <h3>Analytics</h3>
    <p class="muted">Latest report</p>
    <div class="stat" style="font-size:1.2rem">Coming soon</div>
    <p class="preview">Analytics dashboard will show task throughput, memory growth, and agent uptime.</p>
  </div>

  <div class="card">
    <h3>Billing</h3>
    <p class="muted">Current plan</p>
    <div class="stat" style="font-size:1.2rem">Community</div>
    <p class="preview">$30/mo &middot; Includes agent access, task management, and memory storage.</p>
  </div>
</div>

<!-- Moat panels (SkillCard v1 + UsageLog — P1.1 customer surface) -->
<h2 style="font-size:1.1rem;color:#94A3B8;margin:32px 0 16px 0;text-transform:uppercase;letter-spacing:0.05em;font-weight:500">
  Your activity on Agent OS
</h2>
<div class="grid">

  <div class="card">
    <h3>Your Skills</h3>
    <p class="muted">Skills you've invoked</p>
    <div class="stat">{len(moat['skills_invoked'])}</div>
    {_moat_skills_invoked_html(moat['skills_invoked'])}
    <p class="muted" style="margin-top:12px;font-size:0.8rem">
      Total spent: <strong style="color:#F8FAFC">{_fmt_micros(moat['total_spent_micros'])}</strong>
    </p>
    <p class="preview" style="margin-top:8px;font-size:0.8rem">
      <a href="/marketplace" style="color:#A5B4FC;text-decoration:none">Browse more skills →</a>
    </p>
  </div>

  <div class="card">
    <h3>Your Earnings</h3>
    <p class="muted">Skills you've authored</p>
    <div class="stat" style="color:#34D399">{_fmt_micros(moat['total_earned_micros'])}</div>
    {_moat_skills_authored_html(moat['skills_authored'])}
  </div>

  <div class="card">
    <h3>Recent Usage</h3>
    <p class="muted">Last 10 calls</p>
    {_moat_recent_usage_html(moat['recent_usage'])}
  </div>

</div>

</body>
</html>"""


def _moat_skills_invoked_html(skills: list[dict[str, Any]]) -> str:
    if not skills:
        return '<p class="muted" style="margin-top:8px;font-size:0.85rem">No skill invocations yet. Browse the marketplace to start.</p>'
    rows = ""
    for s in skills[:5]:
        ver = s.get("verification", "unverified")
        ver_color = {"human_verified": "#34D399", "auto_verified": "#FBBF24", "disputed": "#F87171"}.get(ver, "#64748B")
        rows += f'<li><span style="color:{ver_color}">&#9679;</span> <strong>{s["name"]}</strong> <span class="muted">× {s["invocations"]}</span></li>'
    return f'<ul style="margin-top:12px">{rows}</ul>'


def _moat_skills_authored_html(skills: list[dict[str, Any]]) -> str:
    if not skills:
        return '<p class="muted" style="margin-top:8px;font-size:0.85rem">You haven\'t authored any skills yet. Publish to the marketplace to earn.</p>'
    rows = ""
    for s in skills[:5]:
        listed = "📢" if s.get("marketplace_listed") else ""
        rows += (
            f'<li><strong>{s["name"]}</strong> {listed} '
            f'<span class="muted">&middot; {s["total_invocations"]} calls '
            f'across {s["unique_tenants"]} tenants</span></li>'
        )
    return f'<ul style="margin-top:12px">{rows}</ul>'


def _moat_recent_usage_html(events: list[dict[str, Any]]) -> str:
    if not events:
        return '<p class="muted" style="margin-top:8px;font-size:0.85rem">No calls yet. Your first invocation shows up here.</p>'
    rows = ""
    for e in events[:10]:
        # parse occurred_at to friendly
        ts = e.get("occurred_at", "")
        try:
            dt = datetime.fromisoformat(ts.replace("Z", "+00:00"))
            ts = dt.strftime("%b %d, %H:%M")
        except Exception:
            pass
        cost = _fmt_micros(e.get("cost_micros", 0))
        model = e.get("model", "?")
        # truncate long model names
        if len(model) > 40:
            model = model[:37] + "..."
        rows += (
            f'<li style="font-size:0.8rem"><span class="muted">{ts}</span> '
            f'<code style="color:#A78BFA;font-size:0.75rem">{model}</code> '
            f'<strong style="color:#34D399">{cost}</strong></li>'
        )
    return f'<ul style="margin-top:8px">{rows}</ul>'


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

@app.get("/", response_class=RedirectResponse)
async def root() -> RedirectResponse:
    return RedirectResponse(url="/login", status_code=302)


@app.get("/login", response_class=HTMLResponse)
async def login_page() -> HTMLResponse:
    return HTMLResponse(LOGIN_HTML.replace("{error}", ""))


@app.post("/login")
async def login_submit(token: str = Form(...)) -> Response:
    entry = _verify_token(token)
    if not entry:
        html = LOGIN_HTML.replace("{error}", '<p class="error">Invalid or inactive token.</p>')
        return HTMLResponse(html, status_code=401)

    cookie_data = json.dumps({
        "token": token,
        "project": entry.get("project"),
        "label": entry.get("label", ""),
    })
    resp = RedirectResponse(url="/dashboard", status_code=303)
    resp.set_cookie(
        COOKIE_NAME,
        cookie_data,
        httponly=True,
        max_age=86400 * 7,
        samesite="lax",
    )
    return resp


@app.get("/logout")
async def logout() -> Response:
    resp = RedirectResponse(url="/login", status_code=302)
    resp.delete_cookie(COOKIE_NAME)
    return resp


@app.get("/dashboard", response_class=HTMLResponse)
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


@app.get("/api/status")
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


@app.get("/health")
async def health() -> JSONResponse:
    return JSONResponse({"status": "ok", "service": "dashboard", "port": 8090})


# ---------------------------------------------------------------------------
# SOS Operator Dashboard — `/sos` (Phase 0: static flow map)
# ---------------------------------------------------------------------------
#
# The operator view of the organism. Not customer-facing.
# Admin-scoped auth — any token without a `project` field (system scope).
# Phase 0 ships a hand-crafted flow-map page so operators and enterprise
# buyers have a single surface to see how SOS actually moves data + money.
# Phase 1+ swaps in live panels (agent grid, bus pulse, money pulse, etc.).
# ---------------------------------------------------------------------------


def _is_admin(tenant: dict[str, Any] | None) -> bool:
    """A tenant record is admin if it has no project scope (system token)."""
    if not tenant:
        return False
    return not tenant.get("project")


_SERVICE_MAP_PATH = Path(__file__).resolve().parent / "service_map.svg"
_service_map_cache: str | None = None


def _load_service_map_svg() -> str:
    """Read the service-map SVG once, cache in-process. Empty string on failure."""
    global _service_map_cache
    if _service_map_cache is None:
        try:
            _service_map_cache = _SERVICE_MAP_PATH.read_text(encoding="utf-8")
        except Exception:
            logger.exception("Failed to load service map SVG")
            _service_map_cache = ""
    return _service_map_cache


_SOS_FLOW_MAP_HTML = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>SOS Engine — Operator Dashboard</title>
<style>
*{margin:0;padding:0;box-sizing:border-box}
body{background:#0F172A;color:#E2E8F0;font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,sans-serif;padding:24px;max-width:1400px;margin:0 auto;min-height:100vh}
header{display:flex;align-items:center;justify-content:space-between;margin-bottom:32px;padding-bottom:16px;border-bottom:1px solid #1E293B}
header h1{font-size:1.4rem;color:#F8FAFC;font-weight:600}
header .meta{color:#94A3B8;font-size:0.8rem;margin-top:4px}
.logout{color:#94A3B8;text-decoration:none;font-size:0.85rem;border:1px solid #334155;padding:6px 14px;border-radius:6px}
.logout:hover{color:#F8FAFC;border-color:#6366F1}
nav{display:flex;gap:8px;margin-bottom:24px;font-size:0.85rem}
nav a{padding:6px 14px;border-radius:6px;color:#94A3B8;text-decoration:none;border:1px solid #1E293B}
nav a.active{background:#1E293B;color:#F8FAFC;border-color:#334155}
nav a:hover{color:#F8FAFC}
.subtitle{color:#64748B;font-size:0.9rem;margin-bottom:24px;line-height:1.5}
.flow-map{background:#1E293B;border:1px solid #334155;border-radius:12px;padding:32px;margin-bottom:24px;overflow-x:auto}
.legend{display:flex;flex-wrap:wrap;gap:16px;margin-top:16px;padding-top:16px;border-top:1px solid #0F172A}
.legend-item{display:flex;align-items:center;gap:8px;font-size:0.8rem;color:#94A3B8}
.legend-box{width:14px;height:14px;border-radius:3px}
.info-grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(300px,1fr));gap:16px}
.info-card{background:#1E293B;border:1px solid #334155;border-radius:10px;padding:20px}
.info-card h3{font-size:0.8rem;color:#94A3B8;text-transform:uppercase;letter-spacing:0.05em;margin-bottom:12px}
.info-card p{color:#CBD5E1;font-size:0.85rem;line-height:1.6}
.info-card ul{list-style:none;padding:0;margin-top:8px}
.info-card li{padding:4px 0;color:#CBD5E1;font-size:0.85rem}
.info-card code{color:#A78BFA;font-family:'SF Mono',Monaco,monospace;font-size:0.8rem}
svg text{font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,sans-serif}
.phase-tag{display:inline-block;background:#6366F1;color:#fff;padding:2px 10px;border-radius:999px;font-size:0.7rem;margin-left:8px;font-weight:500}
</style>
</head>
<body>
<header>
  <div>
    <h1>&#9670; SOS Engine <span class="phase-tag">Phase 0</span></h1>
    <div class="meta">Operator view of the organism &middot; {timestamp}</div>
  </div>
  <a href="/logout" class="logout">Sign Out</a>
</header>

<nav>
  <a href="/sos" class="active">Flow Map</a>
  <a href="/sos/agents" title="Phase 1">Agents</a>
  <a href="/sos/bus" title="Phase 1">Bus</a>
  <a href="/sos/money">Money</a>
  <a href="/sos/skills">Skills</a>
  <a href="/sos/incidents" title="Phase 2">Incidents</a>
  <a href="/sos/contracts" title="Phase 1">Contracts</a>
  <a href="/sos/providers" title="Phase 3">Providers</a>
</nav>

<p class="subtitle">
  The data-flow map: how messages, money, and memory move through the system.
  Each arrow is labeled with the primitive that carries it. Everything is
  read-only. Write operations happen through <code>sos status</code> CLI.
</p>

<div class="flow-map">
  {service_map_svg}
  <div class="legend">
    <div class="legend-item"><div class="legend-box" style="background:#6366F1"></div>Bus / v1 messages</div>
    <div class="legend-item"><div class="legend-box" style="background:#34D399"></div>Money / settlement / $MIND</div>
    <div class="legend-item"><div class="legend-box" style="background:#A78BFA"></div>Memory / telemetry</div>
    <div class="legend-item"><div class="legend-box" style="background:#FBBF24"></div>Squad / task coordination</div>
    <div class="legend-item"><div class="legend-box" style="background:#64748B"></div>External / HTTP</div>
    <div class="legend-item"><div class="legend-box" style="background:#F87171;border:1px dashed #F87171"></div>Debt / broken</div>
  </div>
  <div class="legend" style="margin-top:8px">
    <div class="legend-item"><div style="display:inline-block;width:14px;height:14px;border-radius:50%;background:#22D3EE;color:#0F172A;font-size:9px;font-weight:700;text-align:center;line-height:14px">C</div>&nbsp;Community (sos-community, Apache 2.0 — proposed)</div>
    <div class="legend-item"><div style="display:inline-block;width:14px;height:14px;border-radius:50%;background:#EAB308;color:#0F172A;font-size:9px;font-weight:700;text-align:center;line-height:14px">P</div>&nbsp;Proprietary core (Mumega commercial)</div>
    <div class="legend-item"><div style="display:inline-block;width:14px;height:14px;border-radius:50%;background:#64748B;color:#F8FAFC;font-size:9px;font-weight:700;text-align:center;line-height:14px">X</div>&nbsp;External / third-party</div>
  </div>
</div>
<div class="info-grid">
  <div class="info-card">
    <h3>Contracts (v0.4.0)</h3>
    <p>All 8 bus message types are JSON-Schema-validated at construction:</p>
    <ul>
      <li><code>announce</code>, <code>send</code>, <code>wake</code>, <code>ask</code></li>
      <li><code>task_created</code>, <code>task_claimed</code>, <code>task_completed</code></li>
      <li><code>agent_joined</code></li>
    </ul>
    <p style="margin-top:12px;">Strict enforcement rejects unknown types (<code>SOS-4004</code>).</p>
  </div>

  <div class="info-card">
    <h3>Economy primitives</h3>
    <p>Currency-agnostic ledger supports USD, $MIND, or operator-defined units:</p>
    <ul>
      <li><code>POST /usage</code> — tenant-scoped event ingest</li>
      <li><code>POST /credit</code>, <code>/debit</code>, <code>/transmute</code></li>
      <li><code>PricingEntry</code> — per-token + flat-per-call</li>
    </ul>
  </div>

  <div class="info-card">
    <h3>Not in Phase 0 yet</h3>
    <p>These panels ship in Phase 1-3 as plugins (same microkernel pattern as Inkwell):</p>
    <ul>
      <li>Live agent grid with heartbeat</li>
      <li>Bus pulse (msgs/sec, SSE sessions)</li>
      <li>Money pulse (MRR, settlements)</li>
      <li>Skill-moat panel (earnings per skill)</li>
      <li>Competitor release feed</li>
    </ul>
  </div>

  <div class="info-card">
    <h3>Boundary note</h3>
    <p>This dashboard shows what SOS is, not what Mumega sells on top.</p>
    <p style="margin-top:8px">Commercial overlays (Stripe USD invoicing, volume tiers, per-customer P&L) layer on the ledger without touching kernel code.</p>
    <p style="margin-top:8px;color:#64748B">The kernel stays substrate-agnostic — same code on RPi, VPS, Workers.</p>
  </div>
</div>

</body>
</html>"""


@app.get("/sos", response_class=HTMLResponse)
async def sos_dashboard_root(request: Request) -> Response:
    """SOS operator dashboard — admin-scoped only."""
    cookie_val = request.cookies.get(COOKIE_NAME)
    tenant = _tenant_from_cookie(cookie_val)
    if not tenant:
        return RedirectResponse(url="/login?next=/sos", status_code=302)
    if not _is_admin(tenant):
        return HTMLResponse(
            "<h1>403 — admin scope required</h1>"
            "<p>The SOS operator dashboard requires a system-scoped token "
            "(any token without a <code>project</code> field).</p>"
            "<p><a href=\"/dashboard\">Return to customer dashboard</a></p>",
            status_code=403,
        )
    html = _SOS_FLOW_MAP_HTML.replace(
        "{timestamp}",
        datetime.now(timezone.utc).strftime("%b %d, %Y %H:%M UTC"),
    ).replace("{service_map_svg}", _load_service_map_svg())
    return HTMLResponse(html)


# ---------------------------------------------------------------------------
# Public Marketplace — `/marketplace` (no auth required)
# ---------------------------------------------------------------------------

_MARKETPLACE_CSS = """
*{margin:0;padding:0;box-sizing:border-box}
body{background:#0F172A;color:#E2E8F0;font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,sans-serif;padding:24px;max-width:1400px;margin:0 auto;min-height:100vh}
header{margin-bottom:40px;padding-bottom:20px;border-bottom:1px solid #1E293B}
header h1{font-size:1.8rem;color:#F8FAFC;font-weight:700;margin-bottom:8px}
header .tagline{color:#94A3B8;font-size:1rem;line-height:1.6;max-width:700px}
.grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(320px,1fr));gap:20px;margin-bottom:48px}
.card{background:#1E293B;border:1px solid #334155;border-radius:12px;padding:24px;display:flex;flex-direction:column;gap:12px}
.card-name{font-size:1.1rem;font-weight:600;color:#F8FAFC}
.card-desc{color:#CBD5E1;font-size:0.88rem;line-height:1.6;flex:1}
.card-author{color:#94A3B8;font-size:0.8rem;font-family:'SF Mono',Monaco,monospace}
.card-price{color:#A5B4FC;font-size:0.85rem;font-family:'SF Mono',Monaco,monospace}
.badge{display:inline-block;padding:2px 10px;border-radius:999px;font-size:0.72rem;font-weight:600}
.badge-human{background:#064E3B;color:#34D399}
.badge-auto{background:#451A03;color:#FBBF24}
.badge-unverified{background:#1E293B;color:#64748B;border:1px solid #334155}
.badge-disputed{background:#450A0A;color:#F87171}
.earnings{color:#64748B;font-size:0.8rem;line-height:1.5}
.view-link{display:inline-block;margin-top:4px;color:#6366F1;font-size:0.85rem;text-decoration:none}
.view-link:hover{color:#A5B4FC;text-decoration:underline}
footer{border-top:1px solid #1E293B;padding-top:24px;color:#64748B;font-size:0.85rem;display:flex;gap:16px;flex-wrap:wrap}
footer a{color:#94A3B8;text-decoration:none}
footer a:hover{color:#F8FAFC}
"""


def _fmt_price(micros: int) -> str:
    """Format micros (1e-6 USD) as $N.NN per call."""
    cents = micros / 10000
    return f"${cents:.2f} per call"


def _earnings_line(earnings: Any | None) -> str:
    if earnings is None:
        return "No earnings data yet"
    total = (earnings.total_earned_micros or 0) / 10000
    invocations = earnings.total_invocations or 0
    tenants = len(earnings.invocations_by_tenant or {})
    return f"Earned ${total:.2f} across {invocations} invocations across {tenants} tenant{'s' if tenants != 1 else ''}"


def _verification_badge(verification: Any | None) -> str:
    if verification is None:
        return '<span class="badge badge-unverified">unverified</span>'
    status = verification.status
    if status == "human_verified":
        return '<span class="badge badge-human">human verified</span>'
    if status == "auto_verified":
        return '<span class="badge badge-auto">auto verified</span>'
    if status == "disputed":
        return '<span class="badge badge-disputed">disputed</span>'
    return '<span class="badge badge-unverified">unverified</span>'


def _marketplace_cards() -> list[Any]:
    """Return all SkillCards that are marketplace_listed == true."""
    try:
        from sos.skills.registry import Registry
        reg = Registry()
        return [
            c for c in reg.list()
            if c.commerce and c.commerce.marketplace_listed
        ]
    except Exception:
        logger.exception("Failed to load skill registry")
        return []


@app.get("/marketplace", response_class=HTMLResponse)
async def marketplace_index() -> HTMLResponse:
    """Public skill marketplace catalog — no auth required."""
    cards = _marketplace_cards()

    card_html = ""
    for c in cards:
        price_str = _fmt_price(c.commerce.price_per_call_micros) if c.commerce else "—"
        badge = _verification_badge(c.verification)
        earn = _earnings_line(c.earnings)
        card_html += f"""<div class="card">
  <div>
    <div class="card-name">{c.name}</div>
    <div style="margin-top:4px">{badge}</div>
  </div>
  <div class="card-desc">{c.description or ""}</div>
  <div class="card-author">{c.author_agent}</div>
  <div class="card-price">{price_str}</div>
  <div class="earnings">{earn}</div>
  <a href="/marketplace/skill/{c.id}" class="view-link">View card &rarr;</a>
</div>"""

    if not card_html:
        card_html = '<p style="color:#64748B;grid-column:1/-1">No skills listed yet.</p>'

    html = f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Mumega Skill Market</title>
<style>{_MARKETPLACE_CSS}</style>
</head>
<body>
<header>
  <h1>&#9670; Mumega Skill Market</h1>
  <p class="tagline">50 skills with receipts beats 18,000 uploads. Every skill here has earnings history, a named author, and verified outputs.</p>
</header>

<div class="grid">
{card_html}
</div>

<footer>
  <a href="/login">Sign In</a>
  <a href="/signup">Get Access</a>
</footer>
</body>
</html>"""
    return HTMLResponse(html)


@app.get("/marketplace/skill/{skill_id}", response_class=HTMLResponse)
async def marketplace_skill_detail(skill_id: str, format: str | None = None) -> Response:
    """Public skill detail page — no auth required.

    ?format=json returns the raw SkillCard as JSON.
    """
    try:
        from sos.skills.registry import Registry
        reg = Registry()
        card = reg.get(skill_id)
    except Exception:
        logger.exception("Failed to load registry for skill %s", skill_id)
        card = None

    if card is None:
        return HTMLResponse(
            f"""<!DOCTYPE html><html lang="en"><head><meta charset="utf-8">
<title>Skill Not Found</title>
<style>body{{background:#0F172A;color:#E2E8F0;font-family:sans-serif;padding:48px;text-align:center}}</style>
</head><body>
<h1 style="color:#F87171;font-size:2rem">404</h1>
<p style="margin-top:12px;color:#94A3B8">Skill <code style="color:#A5B4FC">{skill_id}</code> not found.</p>
<p style="margin-top:16px"><a href="/marketplace" style="color:#6366F1">Back to marketplace</a></p>
</body></html>""",
            status_code=404,
        )

    # JSON download
    if format == "json":
        return JSONResponse(card.model_dump(exclude_none=True))

    # --- Build detail HTML ---
    import json as _json

    def _json_block(obj: Any) -> str:
        escaped = _json.dumps(obj, indent=2, ensure_ascii=False).replace("</", "<\\/")
        return f'<pre style="background:#0F172A;border:1px solid #334155;border-radius:8px;padding:16px;font-size:0.78rem;color:#A5B4FC;overflow-x:auto;white-space:pre-wrap;margin-top:8px">{escaped}</pre>'

    badge = _verification_badge(card.verification)
    tags_html = ""
    if card.tags:
        for t in card.tags:
            tags_html += f'<span style="display:inline-block;background:#1E3A5F;color:#93C5FD;padding:2px 10px;border-radius:999px;font-size:0.72rem;margin:2px">{t}</span>'

    # Earnings section
    earn_section = ""
    if card.earnings:
        e = card.earnings
        total_usd = (e.total_earned_micros or 0) / 10000
        tenants_list = ""
        if e.invocations_by_tenant:
            for tid, cnt in e.invocations_by_tenant.items():
                tenants_list += f'<div style="display:flex;justify-content:space-between;padding:5px 0;border-bottom:1px solid #0F172A;font-size:0.85rem"><span style="color:#94A3B8">{tid}</span><span>{cnt} calls</span></div>'
        earn_section = f"""<section class="detail-section">
  <h2>Earnings</h2>
  <div class="row-pair"><span>Total earned</span><span style="color:#34D399">${total_usd:.2f} {e.currency}</span></div>
  <div class="row-pair"><span>Invocations</span><span>{e.total_invocations or 0}</span></div>
  <div class="row-pair"><span>Tenants</span><span>{len(e.invocations_by_tenant or {{}})}</span></div>
  {"<div style='margin-top:12px'>" + tenants_list + "</div>" if tenants_list else ""}
</section>"""

    # Lineage section
    lineage_section = ""
    if card.lineage:
        items = ""
        for le in card.lineage:
            items += f'<div style="padding:5px 0;border-bottom:1px solid #0F172A;font-size:0.85rem"><a href="/marketplace/skill/{le.parent_skill_id}" style="color:#6366F1">{le.parent_skill_id}</a> <span style="color:#64748B;margin-left:8px">{le.relation}</span></div>'
        lineage_section = f'<section class="detail-section"><h2>Lineage</h2>{items}</section>'

    # Commerce section
    commerce_section = ""
    if card.commerce:
        co = card.commerce
        price_str = _fmt_price(co.price_per_call_micros)
        split_html = ""
        if co.revenue_split:
            rs = co.revenue_split
            if rs.author is not None:
                split_html += f'<div class="row-pair"><span>Author share</span><span>{int((rs.author or 0)*100)}%</span></div>'
            if rs.operator is not None:
                split_html += f'<div class="row-pair"><span>Operator share</span><span>{int((rs.operator or 0)*100)}%</span></div>'
            if rs.network is not None:
                split_html += f'<div class="row-pair"><span>Network share</span><span>{int((rs.network or 0)*100)}%</span></div>'
        commerce_section = f"""<section class="detail-section">
  <h2>Commerce</h2>
  <div class="row-pair"><span>Price</span><span style="font-family:'SF Mono',Monaco,monospace;color:#A5B4FC">{price_str}</span></div>
  <div class="row-pair"><span>Currency</span><span>{co.currency}</span></div>
  {split_html}
</section>"""

    # Runtime section
    runtime_section = ""
    if card.runtime:
        rt = card.runtime
        runtime_section = f"""<section class="detail-section">
  <h2>Runtime</h2>
  <div class="row-pair"><span>Entry point</span><span style="font-family:'SF Mono',Monaco,monospace;color:#A5B4FC;font-size:0.82rem">{rt.entry_point or "—"}</span></div>
  <div class="row-pair"><span>Backend</span><span>{rt.backend or "—"}</span></div>
  <div class="row-pair"><span>Timeout</span><span>{rt.timeout_seconds or "—"}s</span></div>
  <div class="row-pair"><span>Memory</span><span>{rt.memory_mb or "—"} MB</span></div>
</section>"""

    # Verification section
    verif_section = ""
    if card.verification:
        vf = card.verification
        verified_by_html = ", ".join(
            f'<code style="color:#A5B4FC">{v}</code>' for v in (vf.verified_by or [])
        )
        verif_section = f"""<section class="detail-section">
  <h2>Verification</h2>
  <div class="row-pair"><span>Status</span><span>{badge}</span></div>
  {"<div class='row-pair'><span>Verified by</span><span>" + verified_by_html + "</span></div>" if verified_by_html else ""}
  {"<div class='row-pair'><span>Verified at</span><span>" + (vf.verified_at or "—") + "</span></div>" if vf.verified_at else ""}
</section>"""

    detail_css = _MARKETPLACE_CSS + """
.detail-section{background:#1E293B;border:1px solid #334155;border-radius:12px;padding:24px;margin-bottom:20px}
.detail-section h2{font-size:0.75rem;color:#64748B;text-transform:uppercase;letter-spacing:0.07em;margin-bottom:14px;font-weight:600}
.row-pair{display:flex;justify-content:space-between;align-items:flex-start;padding:6px 0;border-bottom:1px solid #0F172A;font-size:0.88rem;gap:16px}
.row-pair:last-child{border:none}
.row-pair>span:first-child{color:#94A3B8;flex-shrink:0}
"""

    html = f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{card.name} — Mumega Skill Market</title>
<style>{detail_css}</style>
</head>
<body>
<header>
  <div style="margin-bottom:4px"><a href="/marketplace" style="color:#64748B;font-size:0.85rem;text-decoration:none">&larr; Marketplace</a></div>
  <h1>{card.name}</h1>
  <p class="tagline" style="margin-top:8px">{card.description or ""}</p>
  <div style="margin-top:12px;display:flex;gap:8px;flex-wrap:wrap;align-items:center">
    {badge}
    {tags_html}
    <a href="/marketplace/skill/{card.id}?format=json" style="color:#64748B;font-size:0.78rem;border:1px solid #334155;padding:2px 10px;border-radius:6px;text-decoration:none">Download JSON</a>
  </div>
</header>

<section class="detail-section">
  <h2>Identity</h2>
  <div class="row-pair"><span>ID</span><span><code style="color:#A5B4FC;font-size:0.82rem">{card.id}</code></span></div>
  <div class="row-pair"><span>Version</span><span>{card.version}</span></div>
  <div class="row-pair"><span>Author</span><span><code style="color:#A5B4FC">{card.author_agent}</code></span></div>
  <div class="row-pair"><span>Created</span><span style="color:#94A3B8">{card.created_at}</span></div>
  {"<div class='row-pair'><span>Updated</span><span style='color:#94A3B8'>" + (card.updated_at or "—") + "</span></div>" if card.updated_at else ""}
  <div class="row-pair"><span>AI authored</span><span>{"Yes" if card.authored_by_ai else "No"}</span></div>
</section>

{earn_section}
{verif_section}
{lineage_section}
{commerce_section}
{runtime_section}

<section class="detail-section">
  <h2>Input Schema</h2>
  {_json_block(card.input_schema)}
</section>

<section class="detail-section">
  <h2>Output Schema</h2>
  {_json_block(card.output_schema)}
</section>

<footer>
  <a href="/marketplace">&larr; Back to marketplace</a>
  <a href="/login">Sign In</a>
  <a href="/signup">Get Access</a>
</footer>
</body>
</html>"""
    return HTMLResponse(html)


@app.get("/sos/service_map.svg")
async def sos_service_map_svg() -> Response:
    """Serve the raw service-map SVG for sharing / downloads (no auth)."""
    svg = _load_service_map_svg()
    if not svg:
        return Response(status_code=404, content="service map unavailable")
    return Response(content=svg, media_type="image/svg+xml")


@app.get("/sos/api/health")
async def sos_api_health() -> JSONResponse:
    """Public health endpoint for the operator dashboard."""
    return JSONResponse({
        "status": "ok",
        "service": "sos-engine-dashboard",
        "phase": 0,
        "version": __import__("sos").__version__ if hasattr(__import__("sos"), "__version__") else "unknown",
    })


# ---------------------------------------------------------------------------
# Phase 1 helpers
# ---------------------------------------------------------------------------

_SOS_KNOWN_UNITS = [
    "sos-mcp-sse",
    "bus-bridge",
    "dashboard",
    "sos-saas",
    "sos-squad",
    "calcifer",
    "agent-wake-daemon",
    "agent-lifecycle",
]


def _systemctl_status(unit: str) -> str:
    """Return 'active' or 'inactive' for a systemd user unit."""
    try:
        result = subprocess.run(
            ["systemctl", "--user", "is-active", unit],
            capture_output=True,
            text=True,
            timeout=2,
        )
        return result.stdout.strip() or "unknown"
    except subprocess.TimeoutExpired:
        return "timeout"
    except Exception:
        return "error"


def _proc_loadavg() -> str:
    try:
        text = Path("/proc/loadavg").read_text()
        parts = text.split()
        return " ".join(parts[:3])
    except Exception:
        return "—"


def _disk_usage() -> str:
    try:
        result = subprocess.run(
            ["df", "-h", "/"],
            capture_output=True,
            text=True,
            timeout=3,
        )
        lines = result.stdout.strip().splitlines()
        if len(lines) >= 2:
            cols = lines[1].split()
            # cols: Filesystem Size Used Avail Use% Mounted
            if len(cols) >= 5:
                return cols[4]  # Use%
    except Exception:
        pass
    return "—"


def _relative_time(ts_str: str) -> str:
    """Convert an ISO timestamp string to a human-readable relative time."""
    if not ts_str:
        return "—"
    try:
        dt = datetime.fromisoformat(ts_str)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        delta = datetime.now(timezone.utc) - dt
        secs = int(delta.total_seconds())
        if secs < 0:
            return "just now"
        if secs < 60:
            return f"{secs}s ago"
        if secs < 3600:
            return f"{secs // 60}m ago"
        if secs < 86400:
            return f"{secs // 3600}h ago"
        return f"{secs // 86400}d ago"
    except Exception:
        return ts_str


def _sos_nav(active: str) -> str:
    pages = [
        ("/sos", "Flow Map"),
        ("/sos/overview", "Overview"),
        ("/sos/agents", "Agents"),
        ("/sos/bus", "Bus"),
        ("/sos/money", "Money"),
        ("/sos/skills", "Skills"),
        ("/sos/incidents", "Incidents"),
        ("/sos/contracts", "Contracts"),
        ("/sos/providers", "Providers"),
    ]
    links = ""
    for href, label in pages:
        cls = ' class="active"' if label == active else ""
        links += f'<a href="{href}"{cls}>{label}</a>'
    return f'<nav style="display:flex;gap:8px;margin-bottom:24px;font-size:0.85rem">{links}</nav>'


_SOS_BASE_CSS = """
*{margin:0;padding:0;box-sizing:border-box}
body{background:#0F172A;color:#E2E8F0;font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,sans-serif;padding:24px;max-width:1400px;margin:0 auto;min-height:100vh}
header{display:flex;align-items:center;justify-content:space-between;margin-bottom:32px;padding-bottom:16px;border-bottom:1px solid #1E293B}
header h1{font-size:1.4rem;color:#F8FAFC;font-weight:600}
header .meta{color:#94A3B8;font-size:0.8rem;margin-top:4px}
.logout{color:#94A3B8;text-decoration:none;font-size:0.85rem;border:1px solid #334155;padding:6px 14px;border-radius:6px}
.logout:hover{color:#F8FAFC;border-color:#6366F1}
nav a{padding:6px 14px;border-radius:6px;color:#94A3B8;text-decoration:none;border:1px solid #1E293B}
nav a.active{background:#1E293B;color:#F8FAFC;border-color:#334155}
nav a:hover{color:#F8FAFC}
.grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(300px,1fr));gap:20px;margin-bottom:24px}
.card{background:#1E293B;border:1px solid #334155;border-radius:12px;padding:24px}
.card h3{font-size:0.75rem;color:#94A3B8;text-transform:uppercase;letter-spacing:0.07em;margin-bottom:16px}
.val{font-size:2rem;font-weight:700;color:#F8FAFC;margin-bottom:4px}
.val-sm{font-size:1.1rem;font-weight:600;color:#F8FAFC}
.muted{color:#64748B;font-size:0.82rem}
.row{display:flex;justify-content:space-between;align-items:center;padding:7px 0;border-bottom:1px solid #0F172A;font-size:0.85rem}
.row:last-child{border:none}
.dot-green{color:#34D399}
.dot-red{color:#F87171}
.dot-amber{color:#FBBF24}
.badge{display:inline-block;padding:2px 8px;border-radius:4px;font-size:0.7rem;font-weight:600;margin-left:6px}
.badge-green{background:#064E3B;color:#34D399}
.badge-red{background:#450A0A;color:#F87171}
.badge-amber{background:#451A03;color:#FBBF24}
"""


# ---------------------------------------------------------------------------
# GET /sos/overview — Phase 1 heartbeat tiles
# ---------------------------------------------------------------------------

@app.get("/sos/overview", response_class=HTMLResponse)
async def sos_overview(request: Request) -> Response:
    """SOS operator overview — live heartbeat tiles. Admin-scoped only."""
    cookie_val = request.cookies.get(COOKIE_NAME)
    tenant = _tenant_from_cookie(cookie_val)
    if not tenant:
        return RedirectResponse(url="/login?next=/sos/overview", status_code=302)
    if not _is_admin(tenant):
        return HTMLResponse("<h1>403 — admin scope required</h1>", status_code=403)

    now_str = datetime.now(timezone.utc).strftime("%b %d, %Y %H:%M UTC")

    # --- Bus health ---
    bus_stream_count = 0
    bus_total_msgs = 0
    bus_top_streams: list[tuple[str, int, str]] = []  # (key, count, last_ts)
    try:
        r = _get_redis()
        stream_keys = r.keys("sos:stream:*")
        bus_stream_count = len(stream_keys)
        stream_data: list[tuple[str, int, str]] = []
        for key in stream_keys:
            try:
                length = r.xlen(key)
                bus_total_msgs += length
                last_ts = "—"
                last_entries = r.xrevrange(key, count=1)
                if last_entries:
                    raw_id = last_entries[0][0]
                    ts_ms = int(raw_id.split("-")[0])
                    last_ts = datetime.fromtimestamp(ts_ms / 1000, tz=timezone.utc).strftime("%H:%M:%S")
                stream_data.append((key, length, last_ts))
            except Exception:
                stream_data.append((key, 0, "—"))
        stream_data.sort(key=lambda x: x[1], reverse=True)
        bus_top_streams = stream_data[:5]
    except Exception:
        pass

    # --- Registry ---
    reg_total = 0
    reg_active = 0
    try:
        r = _get_redis()
        reg_keys = r.keys("sos:registry:*")
        reg_total = len(reg_keys)
        cutoff = datetime.now(timezone.utc).timestamp() - 300
        for key in reg_keys:
            data = r.hgetall(key)
            ls = data.get("last_seen", "")
            if ls:
                try:
                    dt = datetime.fromisoformat(ls)
                    if dt.tzinfo is None:
                        dt = dt.replace(tzinfo=timezone.utc)
                    if dt.timestamp() >= cutoff:
                        reg_active += 1
                except Exception:
                    pass
    except Exception:
        pass

    # --- Redis INFO ---
    redis_mem = "—"
    redis_clients = "—"
    redis_keys = "—"
    try:
        r = _get_redis()
        info_mem = r.info("memory")
        redis_mem = info_mem.get("used_memory_human", "—")
        info_clients = r.info("clients")
        redis_clients = str(info_clients.get("connected_clients", "—"))
        info_ks = r.info("keyspace")
        db0 = info_ks.get("db0", {})
        redis_keys = str(db0.get("keys", "—")) if isinstance(db0, dict) else "—"
    except Exception:
        pass

    # --- Services ---
    svc_statuses: list[tuple[str, str]] = []
    for unit in _SOS_KNOWN_UNITS:
        status = _systemctl_status(unit)
        svc_statuses.append((unit, status))

    # --- Disk ---
    disk_used = _disk_usage()

    # --- Load ---
    load_avg = _proc_loadavg()

    # --- Build HTML ---
    # Bus card rows
    bus_stream_rows = ""
    for key, count, last_ts in bus_top_streams:
        short = key.replace("sos:stream:", "")
        bus_stream_rows += f'<div class="row"><span style="color:#A5B4FC;font-size:0.78rem;max-width:180px;overflow:hidden;text-overflow:ellipsis">{short}</span><span>{count} msgs &middot; {last_ts}</span></div>'
    if not bus_stream_rows:
        bus_stream_rows = '<div class="muted">No streams found</div>'

    # Services card rows
    svc_rows = ""
    for unit, status in svc_statuses:
        if status == "active":
            dot = '<span class="dot-green">&#9679;</span>'
            badge = '<span class="badge badge-green">active</span>'
        elif status in ("inactive", "failed"):
            dot = '<span class="dot-red">&#9679;</span>'
            badge = f'<span class="badge badge-red">{status}</span>'
        else:
            dot = '<span class="dot-amber">&#9679;</span>'
            badge = f'<span class="badge badge-amber">{status}</span>'
        svc_rows += f'<div class="row">{dot} {unit}{badge}</div>'

    nav_html = _sos_nav("Overview")

    html = f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>SOS Overview — Operator Dashboard</title>
<style>{_SOS_BASE_CSS}</style>
</head>
<body>
<header>
  <div>
    <h1>&#9670; SOS Engine &mdash; Overview</h1>
    <div class="meta">Live system heartbeat &middot; {now_str}</div>
  </div>
  <a href="/logout" class="logout">Sign Out</a>
</header>

{nav_html}

<div class="grid">

  <div class="card">
    <h3>Bus Health</h3>
    <div class="val">{bus_stream_count}</div>
    <div class="muted">streams &middot; {bus_total_msgs} total messages</div>
    <div style="margin-top:16px">{bus_stream_rows}</div>
  </div>

  <div class="card">
    <h3>Registry</h3>
    <div class="val">{reg_active}</div>
    <div class="muted">active (last 5 min) of {reg_total} registered</div>
  </div>

  <div class="card">
    <h3>Redis</h3>
    <div class="row"><span class="muted">Memory</span><span class="val-sm">{redis_mem}</span></div>
    <div class="row"><span class="muted">Clients</span><span class="val-sm">{redis_clients}</span></div>
    <div class="row"><span class="muted">Keys (db0)</span><span class="val-sm">{redis_keys}</span></div>
  </div>

  <div class="card">
    <h3>Services</h3>
    {svc_rows}
  </div>

  <div class="card">
    <h3>Disk</h3>
    <div class="val">{disk_used}</div>
    <div class="muted">used on /</div>
  </div>

  <div class="card">
    <h3>Load Average</h3>
    <div class="val-sm" style="font-size:1.6rem;margin-top:8px">{load_avg}</div>
    <div class="muted" style="margin-top:8px">1m &middot; 5m &middot; 15m</div>
  </div>

</div>
</body>
</html>"""
    return HTMLResponse(html)


# ---------------------------------------------------------------------------
# GET /sos/agents — Phase 1 agent registry table
# ---------------------------------------------------------------------------

@app.get("/sos/agents", response_class=HTMLResponse)
async def sos_agents(request: Request) -> Response:
    """SOS operator agents page — full registry table. Admin-scoped only."""
    cookie_val = request.cookies.get(COOKIE_NAME)
    tenant = _tenant_from_cookie(cookie_val)
    if not tenant:
        return RedirectResponse(url="/login?next=/sos/agents", status_code=302)
    if not _is_admin(tenant):
        return HTMLResponse("<h1>403 — admin scope required</h1>", status_code=403)

    now_str = datetime.now(timezone.utc).strftime("%b %d, %Y %H:%M UTC")
    cutoff = datetime.now(timezone.utc).timestamp() - 300

    agents: list[dict[str, Any]] = []
    try:
        r = _get_redis()
        keys = r.keys("sos:registry:*")
        for key in keys:
            data = r.hgetall(key)
            if not data:
                continue
            name = key.split(":")[-1]
            ls = data.get("last_seen", "")
            rel_time = _relative_time(ls)

            # Determine status
            explicit_status = data.get("status", "")
            if explicit_status == "online":
                status = "online"
            elif explicit_status in ("offline", "stopped"):
                status = "offline"
            elif ls:
                try:
                    dt = datetime.fromisoformat(ls)
                    if dt.tzinfo is None:
                        dt = dt.replace(tzinfo=timezone.utc)
                    status = "online" if dt.timestamp() >= cutoff else "stale"
                except Exception:
                    status = "unknown"
            else:
                status = "unknown"

            agents.append({
                "name": name,
                "role": data.get("role", data.get("type", "—")),
                "status": status,
                "last_seen_rel": rel_time,
                "project": data.get("project", data.get("scope", "admin")),
                "squads": data.get("squads", data.get("squad", "")),
                "raw": json.dumps(data, indent=2),
            })
    except Exception:
        pass

    # Sort: online first, then stale, then offline/unknown
    _order = {"online": 0, "stale": 1, "offline": 2, "unknown": 3}
    agents.sort(key=lambda a: (_order.get(a["status"], 9), a["name"]))

    def _status_badge(s: str) -> str:
        if s == "online":
            return '<span class="badge badge-green">online</span>'
        if s == "stale":
            return '<span class="badge badge-amber">stale</span>'
        if s == "offline":
            return '<span class="badge badge-red">offline</span>'
        return f'<span class="badge" style="background:#1E293B;color:#94A3B8">{s}</span>'

    rows = ""
    for i, ag in enumerate(agents):
        badge = _status_badge(ag["status"])
        squads_cell = ag["squads"] if ag["squads"] else '<span class="muted">—</span>'
        raw_escaped = ag["raw"].replace("</", "<\\/")
        rows += f"""<tr onclick="toggle({i})" style="cursor:pointer">
  <td style="padding:10px 12px;font-weight:500;color:#F8FAFC">{ag['name']}</td>
  <td style="padding:10px 12px;color:#CBD5E1">{ag['role']}</td>
  <td style="padding:10px 12px">{badge}</td>
  <td style="padding:10px 12px;color:#94A3B8">{ag['last_seen_rel']}</td>
  <td style="padding:10px 12px;color:#94A3B8">{ag['project']}</td>
  <td style="padding:10px 12px;color:#94A3B8">{squads_cell}</td>
</tr>
<tr id="expand-{i}" style="display:none">
  <td colspan="6" style="padding:0 12px 12px">
    <pre style="background:#0F172A;border:1px solid #334155;border-radius:8px;padding:16px;font-size:0.78rem;color:#A5B4FC;overflow-x:auto;white-space:pre-wrap">{ag['raw']}</pre>
  </td>
</tr>"""

    if not rows:
        rows = '<tr><td colspan="6" style="padding:24px;text-align:center;color:#64748B">No agents registered in sos:registry:*</td></tr>'

    nav_html = _sos_nav("Agents")

    html = f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>SOS Agents — Operator Dashboard</title>
<style>
{_SOS_BASE_CSS}
table{{width:100%;border-collapse:collapse;background:#1E293B;border:1px solid #334155;border-radius:12px;overflow:hidden}}
thead tr{{background:#0F172A}}
th{{padding:10px 12px;text-align:left;font-size:0.72rem;color:#64748B;text-transform:uppercase;letter-spacing:0.06em;font-weight:600;border-bottom:1px solid #334155}}
tbody tr:hover td{{background:#263449}}
</style>
</head>
<body>
<header>
  <div>
    <h1>&#9670; SOS Engine &mdash; Agents</h1>
    <div class="meta">Registry snapshot &middot; {now_str} &middot; {len(agents)} agents</div>
  </div>
  <a href="/logout" class="logout">Sign Out</a>
</header>

{nav_html}

<table>
  <thead>
    <tr>
      <th>Name</th>
      <th>Role / Type</th>
      <th>Status</th>
      <th>Last Seen</th>
      <th>Project Scope</th>
      <th>Squads</th>
    </tr>
  </thead>
  <tbody>
    {rows}
  </tbody>
</table>

<p class="muted" style="margin-top:16px">Click any row to expand the full registry payload.</p>

<script>
function toggle(i) {{
  var el = document.getElementById('expand-' + i);
  if (el) el.style.display = el.style.display === 'none' ? 'table-row' : 'none';
}}
</script>
</body>
</html>"""
    return HTMLResponse(html)


# ---------------------------------------------------------------------------
# Helpers for money/skills panels
# ---------------------------------------------------------------------------

def _fmt_micros(micros: int) -> str:
    """Format integer micros (1e-6 USD) as $X,XXX.XX string."""
    dollars = micros / 1_000_000
    return f"${dollars:,.2f}"


def _event_ts(ts_str: str) -> float:
    """Parse ISO timestamp to unix float; returns 0 on error."""
    try:
        dt = datetime.fromisoformat(ts_str)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.timestamp()
    except Exception:
        return 0.0


# ---------------------------------------------------------------------------
# GET /sos/money — Money Pulse panel (admin-scoped)
# ---------------------------------------------------------------------------

@app.get("/sos/money", response_class=HTMLResponse)
async def sos_money(request: Request) -> Response:
    """SOS money pulse panel — admin-scoped only."""
    cookie_val = request.cookies.get(COOKIE_NAME)
    tenant = _tenant_from_cookie(cookie_val)
    if not tenant:
        return RedirectResponse(url="/login?next=/sos/money", status_code=302)
    if not _is_admin(tenant):
        return HTMLResponse("<h1>403 — admin scope required</h1>", status_code=403)

    now_str = datetime.now(timezone.utc).strftime("%b %d, %Y %H:%M UTC")
    now_ts = datetime.now(timezone.utc).timestamp()

    # --- Load all events ---
    try:
        from sos.services.economy.usage_log import UsageLog
        ul = UsageLog()
        all_events = ul.read_all()
    except Exception:
        all_events = []

    nav_html = _sos_nav("Money")

    if not all_events:
        html = f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>SOS Money Pulse — Operator Dashboard</title>
<style>{_SOS_BASE_CSS}</style>
</head>
<body>
<header>
  <div>
    <h1>&#9670; SOS Engine &mdash; Money Pulse</h1>
    <div class="meta">Moat metric panel &middot; {now_str}</div>
  </div>
  <a href="/logout" class="logout">Sign Out</a>
</header>
{nav_html}
<div class="card"><h3>Usage Summary</h3><p class="muted">No data yet — run some model calls to populate this panel.</p></div>
</body>
</html>"""
        return HTMLResponse(html)

    # --- Section 1: Usage summary ---
    total_events = len(all_events)
    events_24h = sum(1 for e in all_events if (now_ts - _event_ts(e.received_at)) < 86400)
    events_7d = sum(1 for e in all_events if (now_ts - _event_ts(e.received_at)) < 86400 * 7)
    total_cost = sum(e.cost_micros for e in all_events)

    # Top 5 tenants by spend
    tenant_spend: dict[str, tuple[int, int]] = {}  # tenant -> (count, cost_micros)
    for ev in all_events:
        t = ev.tenant or "(none)"
        cnt, cost = tenant_spend.get(t, (0, 0))
        tenant_spend[t] = (cnt + 1, cost + ev.cost_micros)
    top_tenants = sorted(tenant_spend.items(), key=lambda x: x[1][1], reverse=True)[:5]

    # Top 5 models by count
    model_count: dict[str, int] = {}
    for ev in all_events:
        m = ev.model or "(none)"
        model_count[m] = model_count.get(m, 0) + 1
    top_models = sorted(model_count.items(), key=lambda x: x[1], reverse=True)[:5]

    # --- Section 2: AI-to-AI commerce ---
    ai2ai = [ev for ev in all_events if ev.metadata.get("ai_to_ai_commerce") is True]
    ai2ai_count = len(ai2ai)
    ai2ai_mind = sum(ev.cost_micros for ev in ai2ai)
    recent_ai2ai = sorted(ai2ai, key=lambda ev: ev.received_at, reverse=True)[:5]

    # --- Section 3: Settlement split totals ---
    total_author = 0
    total_operator_split = 0
    total_network = 0
    for ev in all_events:
        s = ev.metadata.get("settlement", {})
        if isinstance(s, dict):
            total_author += int(s.get("author", 0) or 0)
            total_operator_split += int(s.get("operator", 0) or 0)
            total_network += int(s.get("network", 0) or 0)

    # --- Build rows ---
    tenant_rows = ""
    for t_name, (t_cnt, t_cost) in top_tenants:
        tenant_rows += f'<div class="row"><span style="color:#A5B4FC">{t_name}</span><span>{t_cnt} events</span><span style="color:#34D399">{_fmt_micros(t_cost)}</span></div>'

    model_rows = ""
    for m_name, m_cnt in top_models:
        model_rows += f'<div class="row"><span style="color:#A5B4FC">{m_name}</span><span>{m_cnt}</span></div>'

    ai2ai_rows = ""
    if recent_ai2ai:
        for ev in recent_ai2ai:
            seller = ev.metadata.get("seller_skill", ev.endpoint or "—")
            buyer = ev.tenant or "—"
            payout = _fmt_micros(int(ev.metadata.get("author_payout_micros", 0) or 0))
            when = _relative_time(ev.received_at)
            ai2ai_rows += f'<div class="row"><span style="color:#A5B4FC;max-width:160px;overflow:hidden;text-overflow:ellipsis">{seller}</span><span>{buyer}</span><span style="color:#34D399">{payout}</span><span class="muted">{when}</span></div>'
    else:
        ai2ai_rows = '<div class="muted" style="padding:12px 0">No AI-to-AI transactions yet</div>'

    html = f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>SOS Money Pulse — Operator Dashboard</title>
<style>{_SOS_BASE_CSS}</style>
</head>
<body>
<header>
  <div>
    <h1>&#9670; SOS Engine &mdash; Money Pulse</h1>
    <div class="meta">Moat metric panel &middot; {now_str}</div>
  </div>
  <a href="/logout" class="logout">Sign Out</a>
</header>

{nav_html}

<div class="grid">

  <div class="card">
    <h3>All-Time Events</h3>
    <div class="val">{total_events:,}</div>
    <div class="muted">{events_24h:,} last 24h &middot; {events_7d:,} last 7d</div>
  </div>

  <div class="card">
    <h3>Total Spend (all tenants)</h3>
    <div class="val">{_fmt_micros(total_cost)}</div>
    <div class="muted">all-time &middot; cost_micros summed</div>
  </div>

  <div class="card">
    <h3>AI-to-AI Commerce</h3>
    <div class="val">{ai2ai_count:,}</div>
    <div class="muted">transactions &middot; <span style="color:#34D399">{_fmt_micros(ai2ai_mind)}</span> $MIND flowed</div>
  </div>

</div>

<div class="grid">

  <div class="card">
    <h3>Top 5 Tenants by Spend</h3>
    <div style="margin-top:8px">
      <div class="row" style="color:#64748B;font-size:0.75rem"><span>Tenant</span><span>Events</span><span>Spend</span></div>
      {tenant_rows}
    </div>
  </div>

  <div class="card">
    <h3>Top 5 Models by Usage</h3>
    <div style="margin-top:8px">
      <div class="row" style="color:#64748B;font-size:0.75rem"><span>Model</span><span>Calls</span></div>
      {model_rows}
    </div>
  </div>

  <div class="card">
    <h3>Settlement Split Totals</h3>
    <div class="row"><span class="muted">Authors</span><span style="color:#34D399">{_fmt_micros(total_author)}</span></div>
    <div class="row"><span class="muted">Operator</span><span style="color:#FBBF24">{_fmt_micros(total_operator_split)}</span></div>
    <div class="row"><span class="muted">Network / Pool</span><span style="color:#A5B4FC">{_fmt_micros(total_network)}</span></div>
  </div>

</div>

<div class="card" style="margin-bottom:24px">
  <h3>Recent AI-to-AI Transactions</h3>
  <div style="margin-top:8px">
    <div class="row" style="color:#64748B;font-size:0.75rem"><span>Seller Skill</span><span>Buyer Tenant</span><span>Author Payout</span><span>When</span></div>
    {ai2ai_rows}
  </div>
</div>

</body>
</html>"""
    return HTMLResponse(html)


# ---------------------------------------------------------------------------
# GET /sos/skills — Skill-moat panel (admin-scoped)
# ---------------------------------------------------------------------------

@app.get("/sos/skills", response_class=HTMLResponse)
async def sos_skills(request: Request) -> Response:
    """SOS skill-moat panel — admin-scoped only."""
    cookie_val = request.cookies.get(COOKIE_NAME)
    tenant = _tenant_from_cookie(cookie_val)
    if not tenant:
        return RedirectResponse(url="/login?next=/sos/skills", status_code=302)
    if not _is_admin(tenant):
        return HTMLResponse("<h1>403 — admin scope required</h1>", status_code=403)

    now_str = datetime.now(timezone.utc).strftime("%b %d, %Y %H:%M UTC")

    # --- Load skill cards ---
    try:
        from sos.skills.registry import Registry
        reg = Registry()
        cards = reg.list()
    except Exception:
        cards = []

    # Sort by total_earned_micros desc
    def _earned(c: Any) -> int:
        return (c.earnings.total_earned_micros or 0) if c.earnings else 0

    cards_sorted = sorted(cards, key=_earned, reverse=True)
    nav_html = _sos_nav("Skills")

    if not cards_sorted:
        html = f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>SOS Skills — Operator Dashboard</title>
<style>{_SOS_BASE_CSS}</style>
</head>
<body>
<header>
  <div>
    <h1>&#9670; SOS Engine &mdash; Skill Moat</h1>
    <div class="meta">Moat metric panel &middot; {now_str}</div>
  </div>
  <a href="/logout" class="logout">Sign Out</a>
</header>
{nav_html}
<div class="card"><h3>Skill Earnings Leaderboard</h3><p class="muted">No data yet — register skills via the Registry to populate this panel.</p></div>
</body>
</html>"""
        return HTMLResponse(html)

    # --- Leaderboard table rows ---
    lb_rows = ""
    for card in cards_sorted:
        e = card.earnings
        invocations = (e.total_invocations or 0) if e else 0
        earned = _fmt_micros((e.total_earned_micros or 0) if e else 0)
        by_tenant: dict[str, int] = (e.invocations_by_tenant or {}) if e else {}
        unique_t = len(by_tenant)
        ver_status = card.verification.status if card.verification else "unverified"
        ver_color = {
            "human_verified": "#34D399",
            "auto_verified": "#FBBF24",
            "unverified": "#64748B",
            "disputed": "#F87171",
        }.get(ver_status, "#64748B")
        mkt = "Y" if (card.commerce and card.commerce.marketplace_listed) else "N"
        mkt_color = "#34D399" if mkt == "Y" else "#64748B"
        author = card.author_agent.replace("agent:", "")
        lb_rows += f"""<tr>
  <td style="padding:10px 12px;font-weight:500;color:#F8FAFC">{card.name}</td>
  <td style="padding:10px 12px;color:#94A3B8">{author}</td>
  <td style="padding:10px 12px;text-align:right">{invocations:,}</td>
  <td style="padding:10px 12px;text-align:right;color:#34D399">{earned}</td>
  <td style="padding:10px 12px;text-align:right">{unique_t}</td>
  <td style="padding:10px 12px"><span style="color:{ver_color}">{ver_status}</span></td>
  <td style="padding:10px 12px;text-align:center;color:{mkt_color}">{mkt}</td>
</tr>"""

    # --- Cross-tenant reuse panel ---
    multi_tenant = [c for c in cards_sorted if c.earnings and c.earnings.invocations_by_tenant and len(c.earnings.invocations_by_tenant) > 1]
    moat_score = len(multi_tenant)
    total_skills = len(cards_sorted)

    # Bar charts — top 10 skills with tenant data
    bar_colors = ["#6366F1", "#34D399", "#FBBF24", "#F87171", "#A78BFA", "#60A5FA"]
    cards_with_tenants = sorted(
        [c for c in cards_sorted if c.earnings and c.earnings.invocations_by_tenant],
        key=lambda c: len((c.earnings.invocations_by_tenant or {}) if c.earnings else {}),  # type: ignore[union-attr]
        reverse=True,
    )[:10]

    reuse_rows = ""
    for card in cards_with_tenants:
        by_tenant = (card.earnings.invocations_by_tenant or {}) if card.earnings else {}  # type: ignore[union-attr]
        total_inv = sum(by_tenant.values()) or 1
        bar_cells = ""
        for i, (t_name, t_cnt) in enumerate(sorted(by_tenant.items(), key=lambda x: x[1], reverse=True)):
            pct = max(4, int(t_cnt / total_inv * 100))
            color = bar_colors[i % len(bar_colors)]
            bar_cells += f'<div title="{t_name}: {t_cnt}" style="height:20px;width:{pct}%;background:{color};display:inline-block;margin-right:2px;border-radius:3px"></div>'
        tenant_labels = " &middot; ".join(
            f'<span style="color:#94A3B8">{t}</span>({c})'
            for t, c in sorted(by_tenant.items(), key=lambda x: x[1], reverse=True)
        )
        reuse_rows += f"""<div style="padding:12px 0;border-bottom:1px solid #0F172A">
  <div style="display:flex;justify-content:space-between;margin-bottom:6px">
    <span style="font-weight:500;color:#F8FAFC">{card.name}</span>
    <span class="muted">{len(by_tenant)} tenants &middot; {sum(by_tenant.values())} total calls</span>
  </div>
  <div style="display:flex;align-items:center;gap:2px;margin-bottom:4px">{bar_cells}</div>
  <div style="font-size:0.78rem">{tenant_labels}</div>
</div>"""

    if not reuse_rows:
        reuse_rows = '<p class="muted">No cross-tenant invocation data yet.</p>'

    html = f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>SOS Skills — Operator Dashboard</title>
<style>
{_SOS_BASE_CSS}
table{{width:100%;border-collapse:collapse;background:#1E293B;border:1px solid #334155;border-radius:12px;overflow:hidden}}
thead tr{{background:#0F172A}}
th{{padding:10px 12px;text-align:left;font-size:0.72rem;color:#64748B;text-transform:uppercase;letter-spacing:0.06em;font-weight:600;border-bottom:1px solid #334155}}
th.num{{text-align:right}}
tbody tr:hover td{{background:#263449}}
</style>
</head>
<body>
<header>
  <div>
    <h1>&#9670; SOS Engine &mdash; Skill Moat</h1>
    <div class="meta">Moat metric panel &middot; {now_str} &middot; {total_skills} skills</div>
  </div>
  <a href="/logout" class="logout">Sign Out</a>
</header>

{nav_html}

<div class="grid" style="margin-bottom:24px">
  <div class="card">
    <h3>Total Skills</h3>
    <div class="val">{total_skills}</div>
    <div class="muted">in registry</div>
  </div>
  <div class="card">
    <h3>Multi-Tenant Skills</h3>
    <div class="val" style="color:#34D399">{moat_score}</div>
    <div class="muted">used by &gt;1 tenant &mdash; THE moat metric</div>
  </div>
</div>

<table style="margin-bottom:24px">
  <thead>
    <tr>
      <th>Name</th>
      <th>Author</th>
      <th class="num">Invocations</th>
      <th class="num">Earned</th>
      <th class="num">Unique Tenants</th>
      <th>Verification</th>
      <th style="text-align:center">Marketplace</th>
    </tr>
  </thead>
  <tbody>
    {lb_rows}
  </tbody>
</table>

<div class="card">
  <h3>Cross-Tenant Reuse (top 10 skills)</h3>
  <p class="muted" style="margin-bottom:16px">Bar width = fraction of invocations per tenant. Skills with wider spread have deeper moat.</p>
  {reuse_rows}
</div>

</body>
</html>"""
    return HTMLResponse(html)
