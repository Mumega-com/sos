"""Mumega Tenant Dashboard — customer-facing web UI.

Shows agent status, recent tasks, memory entries, analytics, and billing.
Runs on port 8090. Auth via bus tokens from tokens.json.
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import httpx
import redis
from fastapi import Cookie, FastAPI, Form, Request, Response
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse

try:
    import bcrypt
    _HAS_BCRYPT = True
except ImportError:
    _HAS_BCRYPT = False

logger = logging.getLogger("dashboard")

app = FastAPI(title="Mumega Dashboard", docs_url=None, redoc_url=None)

TOKENS_PATH = Path(__file__).resolve().parent.parent.parent / "bus" / "tokens.json"
REDIS_PASSWORD = os.environ.get("REDIS_PASSWORD", "")
SQUAD_URL = os.environ.get("SQUAD_URL", "http://localhost:8060")
MIRROR_URL = os.environ.get("MIRROR_URL", "http://localhost:8844")
COOKIE_NAME = "mum_dash"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _load_tokens() -> list[dict[str, Any]]:
    try:
        return json.loads(TOKENS_PATH.read_text())
    except Exception:
        logger.exception("Failed to load tokens")
        return []


def _verify_token(token: str) -> dict[str, Any] | None:
    if not token:
        return None
    sha_hash = hashlib.sha256(token.encode("utf-8")).hexdigest()
    token_bytes = token.encode("utf-8")
    for entry in _load_tokens():
        if not entry.get("active", True):
            continue
        # Post-SEC-001: raw tokens are never stored. Check token_hash (sha256)
        # and hash (bcrypt). Keep raw-token fallback for unmigrated entries.
        stored_token = entry.get("token") or ""
        if stored_token and stored_token == token:
            return entry
        token_hash = entry.get("token_hash") or ""
        if token_hash and token_hash == sha_hash:
            return entry
        bcrypt_hash = entry.get("hash") or ""
        if bcrypt_hash and _HAS_BCRYPT and bcrypt_hash.startswith(("$2a$", "$2b$", "$2y$")):
            try:
                if bcrypt.checkpw(token_bytes, bcrypt_hash.encode("utf-8")):
                    return entry
            except ValueError:
                continue
    return None


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


def _dashboard_html(tenant: dict[str, Any], agents: dict[str, Any], tasks: list[dict[str, Any]], memory: dict[str, Any]) -> str:
    label = tenant.get("label", tenant.get("project", "Tenant"))
    project = tenant.get("project", "")

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

</body>
</html>"""


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

    html = _dashboard_html(tenant, agents, tasks, memory)
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
  <a href="/sos/money" title="Phase 2">Money</a>
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
  <svg viewBox="0 0 1300 820" xmlns="http://www.w3.org/2000/svg" style="width:100%;height:auto;min-width:1100px">
    <defs>
      <marker id="arrow" viewBox="0 0 10 10" refX="10" refY="5" markerWidth="6" markerHeight="6" orient="auto">
        <path d="M 0 0 L 10 5 L 0 10 z" fill="#64748B"/>
      </marker>
      <marker id="arrow-green" viewBox="0 0 10 10" refX="10" refY="5" markerWidth="6" markerHeight="6" orient="auto">
        <path d="M 0 0 L 10 5 L 0 10 z" fill="#34D399"/>
      </marker>
      <marker id="arrow-purple" viewBox="0 0 10 10" refX="10" refY="5" markerWidth="6" markerHeight="6" orient="auto">
        <path d="M 0 0 L 10 5 L 0 10 z" fill="#A78BFA"/>
      </marker>
      <marker id="arrow-amber" viewBox="0 0 10 10" refX="10" refY="5" markerWidth="6" markerHeight="6" orient="auto">
        <path d="M 0 0 L 10 5 L 0 10 z" fill="#FBBF24"/>
      </marker>
    </defs>

    <!-- === CUSTOMER EDGE (top) === -->
    <g>
      <rect x="40" y="30" width="280" height="80" rx="10" fill="#1E293B" stroke="#475569" stroke-width="1.5"/>
      <text x="180" y="60" text-anchor="middle" fill="#F8FAFC" font-size="14" font-weight="600">Customer Edge</text>
      <text x="180" y="80" text-anchor="middle" fill="#94A3B8" font-size="11">CF Pages, Workers, Inkwell</text>
      <text x="180" y="96" text-anchor="middle" fill="#64748B" font-size="10">trop, gaf, dnu tenants</text>
    </g>

    <!-- === MCP GATEWAYS (top center) === -->
    <g>
      <rect x="380" y="30" width="280" height="80" rx="10" fill="#1E293B" stroke="#475569" stroke-width="1.5"/>
      <text x="520" y="60" text-anchor="middle" fill="#F8FAFC" font-size="14" font-weight="600">MCP Gateways</text>
      <text x="520" y="80" text-anchor="middle" fill="#94A3B8" font-size="11">sos_mcp_sse :6070 · bridge :6380</text>
      <text x="520" y="96" text-anchor="middle" fill="#64748B" font-size="10">tokens.json · v1 enforcement</text>
    </g>

    <!-- === ADAPTERS (top right) === -->
    <g>
      <rect x="720" y="30" width="280" height="80" rx="10" fill="#1E293B" stroke="#475569" stroke-width="1.5"/>
      <text x="860" y="60" text-anchor="middle" fill="#F8FAFC" font-size="14" font-weight="600">LLM Adapters</text>
      <text x="860" y="80" text-anchor="middle" fill="#94A3B8" font-size="11">Claude · Gemini · OpenAI</text>
      <text x="860" y="96" text-anchor="middle" fill="#64748B" font-size="10">PricingEntry (v0.4.0)</text>
    </g>

    <!-- === PROVIDERS (top far right) === -->
    <g>
      <rect x="1060" y="30" width="200" height="80" rx="10" fill="#0F172A" stroke="#334155" stroke-width="1" stroke-dasharray="4,2"/>
      <text x="1160" y="60" text-anchor="middle" fill="#CBD5E1" font-size="13" font-weight="500">Model Providers</text>
      <text x="1160" y="80" text-anchor="middle" fill="#64748B" font-size="11">Anthropic, Google,</text>
      <text x="1160" y="96" text-anchor="middle" fill="#64748B" font-size="11">OpenAI APIs</text>
    </g>

    <!-- === BUS (center — the heart) === -->
    <g>
      <rect x="380" y="220" width="540" height="140" rx="14" fill="#0F172A" stroke="#6366F1" stroke-width="2"/>
      <text x="650" y="255" text-anchor="middle" fill="#A5B4FC" font-size="17" font-weight="700">SOS Bus</text>
      <text x="650" y="277" text-anchor="middle" fill="#94A3B8" font-size="11">Redis Streams + PubSub</text>
      <text x="650" y="300" text-anchor="middle" fill="#CBD5E1" font-size="11">8 v1 message types: announce, send, wake, ask,</text>
      <text x="650" y="316" text-anchor="middle" fill="#CBD5E1" font-size="11">task_created, task_claimed, task_completed, agent_joined</text>
      <text x="650" y="340" text-anchor="middle" fill="#64748B" font-size="10">sos:stream:global:agent:{name} · sos:channel:{...}</text>
    </g>

    <!-- === AGENTS (center left) === -->
    <g>
      <rect x="40" y="220" width="280" height="140" rx="10" fill="#1E293B" stroke="#475569" stroke-width="1.5"/>
      <text x="180" y="255" text-anchor="middle" fill="#F8FAFC" font-size="14" font-weight="600">Agents</text>
      <text x="180" y="277" text-anchor="middle" fill="#94A3B8" font-size="11">tmux · openclaw · mcp</text>
      <text x="180" y="300" text-anchor="middle" fill="#CBD5E1" font-size="11">hadi, kasra, sos-dev, codex,</text>
      <text x="180" y="316" text-anchor="middle" fill="#CBD5E1" font-size="11">athena, gemini, sos-medic,</text>
      <text x="180" y="332" text-anchor="middle" fill="#CBD5E1" font-size="11">calcifer, wake-daemon, …</text>
    </g>

    <!-- === SQUADS (center right) === -->
    <g>
      <rect x="980" y="220" width="280" height="140" rx="10" fill="#1E293B" stroke="#475569" stroke-width="1.5"/>
      <text x="1120" y="255" text-anchor="middle" fill="#F8FAFC" font-size="14" font-weight="600">Squad Service</text>
      <text x="1120" y="277" text-anchor="middle" fill="#94A3B8" font-size="11">:8060 · tasks + skills</text>
      <text x="1120" y="300" text-anchor="middle" fill="#CBD5E1" font-size="11">mkt-lead, seo, research,</text>
      <text x="1120" y="316" text-anchor="middle" fill="#CBD5E1" font-size="11">content, ops, …</text>
      <text x="1120" y="340" text-anchor="middle" fill="#64748B" font-size="10">bounties + claims + settlement</text>
    </g>

    <!-- === MIRROR (bottom left) === -->
    <g>
      <rect x="40" y="440" width="280" height="100" rx="10" fill="#1E293B" stroke="#475569" stroke-width="1.5"/>
      <text x="180" y="472" text-anchor="middle" fill="#F8FAFC" font-size="14" font-weight="600">Mirror</text>
      <text x="180" y="492" text-anchor="middle" fill="#94A3B8" font-size="11">:8844 · memory + engrams</text>
      <text x="180" y="514" text-anchor="middle" fill="#CBD5E1" font-size="11">21k+ engrams</text>
      <text x="180" y="530" text-anchor="middle" fill="#64748B" font-size="10">Vector search + per-agent recall</text>
    </g>

    <!-- === ECONOMY (bottom center) === -->
    <g>
      <rect x="380" y="440" width="540" height="180" rx="12" fill="#1E293B" stroke="#34D399" stroke-width="2"/>
      <text x="650" y="475" text-anchor="middle" fill="#6EE7B7" font-size="16" font-weight="700">Economy</text>
      <text x="650" y="498" text-anchor="middle" fill="#94A3B8" font-size="11">Wallet · Ledger · UsageLog · $MIND</text>

      <rect x="410" y="510" width="150" height="50" rx="6" fill="#0F172A" stroke="#334155" stroke-width="1"/>
      <text x="485" y="530" text-anchor="middle" fill="#CBD5E1" font-size="11" font-weight="500">SovereignWallet</text>
      <text x="485" y="546" text-anchor="middle" fill="#64748B" font-size="10">per-user $MIND balance</text>

      <rect x="570" y="510" width="150" height="50" rx="6" fill="#0F172A" stroke="#334155" stroke-width="1"/>
      <text x="645" y="530" text-anchor="middle" fill="#CBD5E1" font-size="11" font-weight="500">WorkLedger</text>
      <text x="645" y="546" text-anchor="middle" fill="#64748B" font-size="10">squad bounty settlement</text>

      <rect x="730" y="510" width="170" height="50" rx="6" fill="#0F172A" stroke="#A78BFA" stroke-width="1"/>
      <text x="815" y="528" text-anchor="middle" fill="#DDD6FE" font-size="11" font-weight="500">UsageLog (new)</text>
      <text x="815" y="544" text-anchor="middle" fill="#64748B" font-size="10">POST /usage · micros</text>

      <text x="650" y="595" text-anchor="middle" fill="#64748B" font-size="10">PricingEntry table (per-token + flat-per-call)</text>
    </g>

    <!-- === $MIND/SOLANA (bottom right) === -->
    <g>
      <rect x="980" y="440" width="280" height="100" rx="10" fill="#1E293B" stroke="#FBBF24" stroke-width="1.5"/>
      <text x="1120" y="472" text-anchor="middle" fill="#FCD34D" font-size="14" font-weight="600">$MIND on Solana</text>
      <text x="1120" y="492" text-anchor="middle" fill="#94A3B8" font-size="11">devnet · transmute</text>
      <text x="1120" y="514" text-anchor="middle" fill="#CBD5E1" font-size="11">payout + QNFT proofs</text>
      <text x="1120" y="530" text-anchor="middle" fill="#64748B" font-size="10">Telegram approval gate</text>
    </g>

    <!-- === STRIPE (far bottom) === -->
    <g>
      <rect x="380" y="700" width="540" height="60" rx="8" fill="#0F172A" stroke="#6366F1" stroke-width="1.5"/>
      <text x="650" y="730" text-anchor="middle" fill="#A5B4FC" font-size="13" font-weight="600">Stripe → USD → $MIND conversion</text>
      <text x="650" y="748" text-anchor="middle" fill="#64748B" font-size="10">customer pays USD → treasury converts → wallet credit in $MIND</text>
    </g>

    <!-- === ARROWS === -->
    <!-- Customer Edge → MCP Gateways (HTTP) -->
    <line x1="320" y1="70" x2="380" y2="70" stroke="#64748B" stroke-width="2" marker-end="url(#arrow)"/>
    <text x="350" y="62" text-anchor="middle" fill="#94A3B8" font-size="9">HTTP Bearer</text>

    <!-- MCP Gateways → Adapters (execute) -->
    <line x1="660" y1="70" x2="720" y2="70" stroke="#64748B" stroke-width="2" marker-end="url(#arrow)"/>
    <text x="690" y="62" text-anchor="middle" fill="#94A3B8" font-size="9">execute</text>

    <!-- Adapters → Providers -->
    <line x1="1000" y1="70" x2="1060" y2="70" stroke="#64748B" stroke-width="2" marker-end="url(#arrow)"/>
    <text x="1030" y="62" text-anchor="middle" fill="#94A3B8" font-size="9">API</text>

    <!-- MCP Gateways → Bus (XADD) -->
    <line x1="520" y1="110" x2="520" y2="220" stroke="#6366F1" stroke-width="2" marker-end="url(#arrow)"/>
    <text x="540" y="170" fill="#A5B4FC" font-size="10">XADD v1 msg</text>

    <!-- Bus → MCP Gateways (xrevrange/inbox) -->
    <line x1="620" y1="220" x2="620" y2="110" stroke="#6366F1" stroke-width="2" stroke-dasharray="3,3" marker-end="url(#arrow)"/>
    <text x="636" y="170" fill="#A5B4FC" font-size="10">XREVRANGE</text>

    <!-- Bus ↔ Agents -->
    <line x1="380" y1="290" x2="320" y2="290" stroke="#6366F1" stroke-width="2" marker-end="url(#arrow)"/>
    <text x="350" y="282" text-anchor="middle" fill="#A5B4FC" font-size="10">wake pubsub</text>

    <line x1="320" y1="310" x2="380" y2="310" stroke="#6366F1" stroke-width="2" marker-end="url(#arrow)"/>
    <text x="350" y="326" text-anchor="middle" fill="#A5B4FC" font-size="10">publish v1</text>

    <!-- Bus ↔ Squads -->
    <line x1="920" y1="290" x2="980" y2="290" stroke="#FBBF24" stroke-width="2" marker-end="url(#arrow-amber)"/>
    <text x="950" y="282" text-anchor="middle" fill="#FCD34D" font-size="10">task_created</text>

    <line x1="980" y1="310" x2="920" y2="310" stroke="#FBBF24" stroke-width="2" marker-end="url(#arrow-amber)"/>
    <text x="950" y="326" text-anchor="middle" fill="#FCD34D" font-size="10">task_claimed/completed</text>

    <!-- Agents → Mirror (engram store) -->
    <line x1="180" y1="360" x2="180" y2="440" stroke="#A78BFA" stroke-width="2" marker-end="url(#arrow-purple)"/>
    <text x="206" y="408" fill="#C4B5FD" font-size="10">remember/recall</text>

    <!-- Mirror → Agents (recall back) — dashed -->
    <line x1="150" y1="440" x2="150" y2="360" stroke="#A78BFA" stroke-width="2" stroke-dasharray="3,3" marker-end="url(#arrow-purple)"/>

    <!-- Squads → Economy (settle) -->
    <line x1="1120" y1="360" x2="1120" y2="440" stroke="#34D399" stroke-width="2" marker-end="url(#arrow-green)"/>
    <text x="1146" y="408" fill="#6EE7B7" font-size="10">settle in $MIND</text>

    <!-- Bus → Economy (UsageLog events) -->
    <line x1="820" y1="360" x2="820" y2="440" stroke="#A78BFA" stroke-width="2" marker-end="url(#arrow-purple)"/>
    <text x="840" y="408" fill="#C4B5FD" font-size="10">usage event</text>

    <!-- Economy → $MIND/Solana -->
    <line x1="920" y1="490" x2="980" y2="490" stroke="#34D399" stroke-width="2" marker-end="url(#arrow-green)"/>
    <text x="950" y="482" text-anchor="middle" fill="#6EE7B7" font-size="10">transmute</text>

    <!-- Stripe → Economy -->
    <line x1="650" y1="700" x2="650" y2="620" stroke="#6366F1" stroke-width="2" marker-end="url(#arrow)"/>
    <text x="672" y="660" fill="#A5B4FC" font-size="10">USD → $MIND</text>

    <!-- Customer Edge → Economy (direct usage POST) -->
    <path d="M 180 110 Q 180 400 380 500" fill="none" stroke="#A78BFA" stroke-width="2" stroke-dasharray="5,4" marker-end="url(#arrow-purple)"/>
    <text x="210" y="450" fill="#C4B5FD" font-size="10">edge tenants</text>
    <text x="210" y="465" fill="#C4B5FD" font-size="10">POST /usage</text>
  </svg>

  <div class="legend">
    <div class="legend-item"><div class="legend-box" style="background:#6366F1"></div>Bus / v1 messages</div>
    <div class="legend-item"><div class="legend-box" style="background:#34D399"></div>Money / settlement / $MIND</div>
    <div class="legend-item"><div class="legend-box" style="background:#A78BFA"></div>Memory / telemetry</div>
    <div class="legend-item"><div class="legend-box" style="background:#FBBF24"></div>Squad / task coordination</div>
    <div class="legend-item"><div class="legend-box" style="background:#64748B"></div>External API / HTTP</div>
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
    )
    return HTMLResponse(html)


@app.get("/sos/api/health")
async def sos_api_health() -> JSONResponse:
    """Public health endpoint for the operator dashboard."""
    return JSONResponse({
        "status": "ok",
        "service": "sos-engine-dashboard",
        "phase": 0,
        "version": __import__("sos").__version__ if hasattr(__import__("sos"), "__version__") else "unknown",
    })
