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
