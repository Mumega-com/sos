#!/usr/bin/env python3
"""
SOS MCP SSE Server — Persistent HTTP-based MCP transport for Claude Code.

Replaces the stdio MCP that disconnects mid-session.
All agents (kasra, mumega, codex) share this server.

Endpoints:
  GET  /sse       — Claude Code connects here (SSE stream)
  POST /messages  — Claude Code sends tool calls here
  GET  /health    — liveness check

Port: 6070 (env: SOS_MCP_PORT)
"""
from __future__ import annotations

import asyncio
import hmac
import json
import logging
import os
import subprocess
import sys
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from uuid import uuid4

import redis.asyncio as aioredis
import requests
from fastapi import FastAPI, HTTPException, Request, Response
from fastapi.responses import JSONResponse
from sse_starlette.sse import EventSourceResponse
from sos.services.squad.auth import SYSTEM_TOKEN as SQUAD_SYSTEM_TOKEN
from sos.services.squad.auth import _lookup_token as lookup_squad_token
from sos.services.squad.service import SquadDB
from sos.mcp.customer_tools import (
    BLOCKED_TOOLS,
    TOOL_MAPPING,
    get_customer_tools,
    is_customer_tool,
)
from sos.services.saas.marketplace import Marketplace

_marketplace = Marketplace()

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [sos-mcp-sse] %(levelname)s %(message)s",
)
log = logging.getLogger("sos_mcp_sse")

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------


def _load_secrets() -> None:
    for p in [str(Path.home() / ".env.secrets")]:
        if os.path.exists(p):
            with open(p) as f:
                for line in f:
                    line = line.strip()
                    if "=" in line and not line.startswith("#"):
                        k, v = line.split("=", 1)
                        os.environ.setdefault(k.strip(), v.strip())
    codex_cfg = Path.home() / ".codex" / "config.toml"
    if codex_cfg.exists():
        try:
            in_sos_env = False
            for line in codex_cfg.read_text().splitlines():
                line = line.strip()
                if line == "[mcp_servers.sos.env]":
                    in_sos_env = True
                    continue
                if line.startswith("[") and in_sos_env:
                    break
                if in_sos_env and "=" in line and not line.startswith("#"):
                    k, v = line.split("=", 1)
                    v = v.strip().strip('"')
                    os.environ.setdefault(k.strip(), v)
        except Exception:
            pass


_load_secrets()

REDIS_PASSWORD: str = os.environ.get("REDIS_PASSWORD", "")
MIRROR_URL: str = os.environ.get("MIRROR_URL", "http://localhost:8844")
MIRROR_TOKEN: str = os.environ.get("MIRROR_TOKEN", "")
PORT: int = int(os.environ.get("SOS_MCP_PORT", "6070"))

MIRROR_HEADERS = {
    "Authorization": f"Bearer {MIRROR_TOKEN}",
    "Content-Type": "application/json",
}
RATE_LIMIT_PER_MINUTE: int = int(os.environ.get("MCP_RATE_LIMIT_PER_MINUTE", "60"))
AUDIT_LOG_DIR = Path.home() / ".sos" / "logs"
MCP_AUDIT_LOG = AUDIT_LOG_DIR / "mcp_audit.jsonl"
BUS_TOKENS_PATH = Path.home() / "SOS" / "sos" / "bus" / "tokens.json"
CF_ACCOUNT = os.environ.get("CF_ACCOUNT_ID", "e39eaf94f33092c4efd029d94ae1e9dd")
CF_API_TOKEN = os.environ.get("CLOUDFLARE_API_TOKEN", "")
KV_NAMESPACE = os.environ.get("BUS_KV_NAMESPACE_ID", "05b010acf24f45ee96c2351dfb5a6dab")

# ---------------------------------------------------------------------------
# Redis (async)
# ---------------------------------------------------------------------------

_redis: aioredis.Redis | None = None


def _get_redis() -> aioredis.Redis:
    global _redis
    if _redis is None:
        url = f"redis://:{REDIS_PASSWORD}@localhost:6379/0" if REDIS_PASSWORD else "redis://localhost:6379/0"
        _redis = aioredis.from_url(url, decode_responses=True)
    return _redis


@dataclass
class MCPAuthContext:
    token: str
    tenant_id: str | None
    is_system: bool = False
    source: str = "unknown"

    @property
    def project_scope(self) -> str | None:
        return None if self.is_system else self.tenant_id

    agent_name: str = ""  # Explicit agent identity from token
    scope: str = ""  # "customer" for external customers; empty for internal agents

    @property
    def is_customer(self) -> bool:
        """True only for external customer tokens — gates tool visibility and access."""
        return self.scope == "customer"

    @property
    def agent_scope(self) -> str:
        if self.agent_name:
            return self.agent_name
        return AGENT_SELF if self.is_system else (self.tenant_id or AGENT_SELF)


# ---------------------------------------------------------------------------
# Stream helpers (mirrored from sos_mcp.py)
# ---------------------------------------------------------------------------

AGENT_SELF = "sos-mcp-sse"
PROJECT = os.environ.get("PROJECT", "")


def _scope_project(auth: MCPAuthContext | None) -> str | None:
    if auth and auth.project_scope:
        return auth.project_scope
    return PROJECT or None


def _prefix(project: str | None) -> str:
    return f"sos:stream:project:{project}" if project else "sos:stream:global"


def _agent_stream(agent: str, project: str | None) -> str:
    return f"{_prefix(project)}:agent:{agent}"


def _agent_channel(agent: str, project: str | None) -> str:
    if project:
        return f"sos:channel:project:{project}:agent:{agent}"
    return f"sos:channel:agent:{agent}"


def _legacy_stream(agent: str) -> str:
    return f"sos:stream:sos:channel:private:agent:{agent}"


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def sos_msg(msg_type: str, source: str, target: str, content: str) -> dict[str, Any]:
    msg: dict[str, Any] = {
        "id": str(uuid4()),
        "type": msg_type,
        "source": source,
        "target": target,
        "payload": json.dumps({"text": content}),
        "timestamp": now_iso(),
        "version": "1.0",
    }
    if PROJECT:
        msg["project"] = PROJECT
    return msg


def scoped_sos_msg(
    msg_type: str,
    source: str,
    target: str,
    content: str,
    project: str | None,
) -> dict[str, Any]:
    msg = sos_msg(msg_type, source, target, content)
    if project:
        msg["project"] = project
    elif "project" in msg and not PROJECT:
        msg.pop("project", None)
    return msg


# ---------------------------------------------------------------------------
# Mirror helpers (sync — run in thread pool for async context)
# ---------------------------------------------------------------------------


def mirror_get(path: str) -> Any:
    try:
        resp = requests.get(f"{MIRROR_URL}{path}", headers=MIRROR_HEADERS, timeout=5)
        resp.raise_for_status()
        return resp.json()
    except Exception:
        return {}


def mirror_post(path: str, body: dict[str, Any]) -> Any:
    try:
        resp = requests.post(f"{MIRROR_URL}{path}", headers=MIRROR_HEADERS, json=body, timeout=5)
        resp.raise_for_status()
        return resp.json()
    except Exception:
        return {}


def mirror_put(path: str, body: dict[str, Any]) -> Any:
    try:
        resp = requests.put(f"{MIRROR_URL}{path}", headers=MIRROR_HEADERS, json=body, timeout=5)
        resp.raise_for_status()
        return resp.json()
    except Exception:
        return {}


_squad_db = SquadDB()
_cloudflare_token_cache: dict[str, tuple[float, MCPAuthContext | None]] = {}


class _TokenCacheWithHotReload:
    """Token cache with automatic mtime-based reload.

    Stores tokens.json mtime and reloads the cache if the file changes.
    Includes a 30-second TTL to avoid filesystem hits on every request.
    """
    def __init__(self):
        self._cache: dict[str, MCPAuthContext] = {}
        self._mtime: float = 0
        self._last_check: float = 0
        self._check_interval: float = 30  # Check mtime every 30 seconds max

    def get(self) -> dict[str, MCPAuthContext]:
        """Get tokens, reloading if file changed or TTL expired."""
        now = time.monotonic()

        # Check if we should reload (at least 30 seconds since last check or file changed)
        if now - self._last_check >= self._check_interval:
            try:
                current_mtime = os.path.getmtime(BUS_TOKENS_PATH)
                if current_mtime != self._mtime:
                    log.info(f"tokens.json changed (mtime {self._mtime:.1f} -> {current_mtime:.1f}), reloading")
                    self._reload()
                    self._mtime = current_mtime
            except OSError:
                # File doesn't exist, keep current cache
                pass
            self._last_check = now

        return self._cache

    def _reload(self) -> None:
        """Reload tokens from file."""
        cache: dict[str, MCPAuthContext] = {}
        try:
            raw = json.loads(BUS_TOKENS_PATH.read_text())
            items = raw if isinstance(raw, list) else [raw]
            for item in items:
                token = item.get("token", "")
                if not token or not item.get("active"):
                    continue
                project = item.get("project") or None
                agent_name = item.get("agent", "")
                scope = item.get("scope", "")
                cache[token] = MCPAuthContext(
                    token=token,
                    tenant_id=project,
                    is_system=project is None,
                    source="bus_tokens",
                    agent_name=agent_name,
                    scope=scope,
                )
        except Exception as e:
            log.error(f"Failed to load tokens.json: {e}")
        self._cache = cache

    def invalidate(self) -> None:
        """Force immediate reload on next call."""
        self._last_check = 0
        self._mtime = 0


_local_token_cache = _TokenCacheWithHotReload()


def _system_tokens() -> set[str]:
    tokens = {token.strip() for token in os.environ.get("MCP_ACCESS_TOKENS", "").split(",") if token.strip()}
    if SQUAD_SYSTEM_TOKEN:
        tokens.add(SQUAD_SYSTEM_TOKEN)
    return tokens


def _load_bus_tokens() -> dict[str, MCPAuthContext]:
    """Load bus tokens with automatic hot-reload on file changes."""
    return _local_token_cache.get()


def _lookup_cloudflare_token(token: str) -> MCPAuthContext | None:
    cached = _cloudflare_token_cache.get(token)
    now = time.monotonic()
    if cached and now - cached[0] < 60:
        return cached[1]
    if not CF_API_TOKEN:
        _cloudflare_token_cache[token] = (now, None)
        return None
    url = (
        f"https://api.cloudflare.com/client/v4/accounts/{CF_ACCOUNT}"
        f"/storage/kv/namespaces/{KV_NAMESPACE}/values/token:{token}"
    )
    try:
        resp = requests.get(
            url,
            headers={"Authorization": f"Bearer {CF_API_TOKEN}"},
            timeout=5,
        )
        if resp.status_code == 404:
            ctx = None
        else:
            resp.raise_for_status()
            payload = json.loads(resp.text)
            project = payload.get("project") or None
            active = payload.get("active", True)
            agent_name = payload.get("agent", "")
            scope = payload.get("scope", "")
            if active and (project or agent_name):
                ctx = MCPAuthContext(
                    token=token, tenant_id=project,
                    is_system=project is None,
                    source="cloudflare_kv",
                    agent_name=agent_name,
                    scope=scope,
                )
            else:
                ctx = None
    except Exception:
        ctx = None
    _cloudflare_token_cache[token] = (now, ctx)
    return ctx


def _resolve_token_context(token: str) -> MCPAuthContext | None:
    if not token:
        return None
    if token in _system_tokens():
        return MCPAuthContext(token=token, tenant_id=None, is_system=True, source="system")
    local_bus = _load_bus_tokens().get(token)
    if local_bus:
        return local_bus
    squad_auth = lookup_squad_token(token, _squad_db)
    if squad_auth:
        return MCPAuthContext(
            token=token,
            tenant_id=squad_auth.tenant_id,
            is_system=squad_auth.is_system,
            source="squad_api_keys",
        )
    return _lookup_cloudflare_token(token)


def _require_same_tenant_agent(auth: MCPAuthContext, requested: str | None) -> str:
    if auth.is_system:
        return requested or AGENT_SELF
    # Per-agent tokens can send to any agent (they have verified identity)
    if auth.agent_name and requested:
        return requested
    tenant_agent = auth.agent_scope
    if requested and not hmac.compare_digest(requested, tenant_agent):
        raise HTTPException(status_code=403, detail="cross_tenant_agent_access")
    return tenant_agent


def _scoped_context_id(auth: MCPAuthContext, value: str | None) -> str:
    context_id = value or f"mcp-{int(datetime.now().timestamp())}"
    if auth.is_system or not auth.tenant_id:
        return context_id
    prefix = f"{auth.tenant_id}:"
    return context_id if context_id.startswith(prefix) else f"{prefix}{context_id}"


def _ensure_task_in_scope(task: dict[str, Any], auth: MCPAuthContext) -> None:
    if auth.is_system:
        return
    project = task.get("project")
    if project != auth.tenant_id:
        raise HTTPException(status_code=403, detail="cross_tenant_task_access")


# ---------------------------------------------------------------------------
# Tool definitions
# ---------------------------------------------------------------------------


def get_tools() -> list[dict[str, Any]]:
    scope = f" (project: {PROJECT})" if PROJECT else ""
    return [
        {
            "name": "ask",
            "description": "Ask an agent a question and get a direct response (via OpenClaw)",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "agent": {"type": "string", "description": "Agent name (e.g. athena, kasra, worker)"},
                    "message": {"type": "string", "description": "Question or task for the agent"},
                },
                "required": ["agent", "message"],
            },
        },
        {
            "name": "send",
            "description": f"Send async message to an agent{scope}",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "to": {"type": "string", "description": "Agent name"},
                    "text": {"type": "string", "description": "Message text"},
                },
                "required": ["to", "text"],
            },
        },
        {
            "name": "inbox",
            "description": f"Check agent inbox{scope}",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "agent": {"type": "string", "default": AGENT_SELF, "description": "Agent name"},
                    "limit": {"type": "integer", "default": 10},
                },
            },
        },
        {
            "name": "peers",
            "description": f"List agents on the bus{scope}",
            "inputSchema": {"type": "object", "properties": {}},
        },
        {
            "name": "broadcast",
            "description": f"Broadcast to all agents{scope}",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "text": {"type": "string", "description": "Message text"},
                    "squad": {"type": "string", "description": "Squad (omit for all)"},
                },
                "required": ["text"],
            },
        },
        {
            "name": "remember",
            "description": "Store a persistent memory",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "text": {"type": "string", "description": "Memory text to store"},
                    "context": {"type": "string", "description": "Context label (optional)"},
                },
                "required": ["text"],
            },
        },
        {
            "name": "recall",
            "description": "Semantic search across memories",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "Search query"},
                    "limit": {"type": "integer", "default": 5},
                },
                "required": ["query"],
            },
        },
        {
            "name": "search_code",
            "description": "Semantic search across synced code nodes (functions, classes, methods). Returns file paths and line numbers for matching code.",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "Natural language description of the code you're looking for"},
                    "repo": {"type": "string", "description": "Filter by repo name (e.g. torivers-staging-dev). Omit to search all repos."},
                    "kind": {"type": "string", "description": "Filter by node kind: function, class, method, etc."},
                    "top_k": {"type": "integer", "default": 5, "description": "Number of results to return"},
                },
                "required": ["query"],
            },
        },
        {
            "name": "memories",
            "description": "List recent memories",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "limit": {"type": "integer", "default": 10},
                },
            },
        },
        {
            "name": "task_create",
            "description": "Create a task",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "title": {"type": "string"},
                    "description": {"type": "string"},
                    "assignee": {"type": "string"},
                    "priority": {"type": "string", "enum": ["low", "medium", "high", "urgent"]},
                },
                "required": ["title"],
            },
        },
        {
            "name": "task_list",
            "description": "List tasks",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "status": {"type": "string"},
                    "assignee": {"type": "string"},
                    "limit": {"type": "integer", "default": 20},
                },
            },
        },
        {
            "name": "task_update",
            "description": "Update a task",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "task_id": {"type": "string"},
                    "status": {"type": "string"},
                    "notes": {"type": "string"},
                },
                "required": ["task_id"],
            },
        },
        {
            "name": "task_board",
            "description": "Prioritized task board — unified view across all projects. Returns scored + sorted tasks. Score = priority×10 + blocks×5 + staleness×2 + revenue_bonus.",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "project": {"type": "string", "description": "Filter by project (optional)"},
                    "agent": {"type": "string", "description": "Filter by assignee (optional)"},
                    "limit": {"type": "integer", "default": 20},
                    "status": {"type": "string", "default": "queued", "description": "Filter: queued, claimed, in_progress, blocked, all"},
                },
            },
        },
        {
            "name": "onboard",
            "description": "Onboard a new agent or customer. For agents: generates tokens, registers in Squad Service, sets up routing, announces on bus — full self-onboarding in one call. For customers (system token only): creates tokens, squad, genesis task.",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "agent_name": {"type": "string", "description": "Your name (required for agent onboarding)"},
                    "mode": {"type": "string", "description": "Mode: 'agent' (default) or 'customer'"},
                    "slug": {"type": "string", "description": "Customer slug (required for mode=customer)"},
                    "label": {"type": "string", "description": "Customer display name (required for mode=customer)"},
                    "email": {"type": "string", "description": "Customer email (optional, for mode=customer)"},
                    "model": {"type": "string", "description": "LLM model (claude, gpt, gemini, gemma) — agent mode"},
                    "role": {"type": "string", "description": "Agent role (builder, strategist, executor, researcher) — agent mode"},
                    "skills": {"type": "array", "items": {"type": "string"}, "description": "Skills this agent provides — agent mode"},
                    "routing": {"type": "string", "description": "How to wake this agent (mcp, tmux, openclaw) — agent mode"},
                },
                "required": [],
            },
        },
        {
            "name": "request",
            "description": "Request work from the Mumega system. Describe what you need in plain text. The system creates a task, routes it to the right squad, and agents start working.",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "description": {"type": "string", "description": "What you need done (e.g. 'SEO audit for my dental site', 'Build a landing page')"},
                    "priority": {"type": "string", "description": "Priority: low, medium, high (default: medium)"},
                },
                "required": ["description"],
            },
        },
        {
            "name": "status",
            "description": "System status — shows all agents with state (idle/busy/dead), running services, and task counts. Like 'sos ps' for the organism.",
            "inputSchema": {
                "type": "object",
                "properties": {},
            },
        },
    ]


# ---------------------------------------------------------------------------
# Agent Status Registry (Redis-backed)
# ---------------------------------------------------------------------------

KNOWN_AGENTS = {
    "kasra": {"type": "tmux", "model": "Claude Opus/Sonnet", "role": "Builder"},
    "mumega": {"type": "tmux", "model": "Claude Opus", "role": "Orchestrator"},
    "codex": {"type": "tmux", "model": "GPT-5.4", "role": "Infra + Security"},
    "mumcp": {"type": "tmux", "model": "Claude Sonnet", "role": "MumCP — WordPress + Elementor"},
    "mumega-web": {"type": "tmux", "model": "Claude Sonnet", "role": "Website"},
    "athena": {"type": "openclaw", "model": "GPT-5.4", "role": "Architecture Review"},
    "sol": {"type": "openclaw", "model": "Claude Opus", "role": "Content"},
    "worker": {"type": "openclaw", "model": "Haiku 4.5", "role": "Task Execution"},
    "dandan": {"type": "openclaw", "model": "OpenRouter free", "role": "DNU Lead"},
    "gemma": {"type": "openclaw", "model": "Gemma 4 31B", "role": "Bulk Tasks"},
    "mizan": {"type": "openclaw", "model": "Haiku", "role": "Business Agent"},
    "river": {"type": "tmux", "model": "Gemini 3.1 Pro", "role": "Oracle (dormant)"},
    "cyrus": {"type": "remote", "model": "Claude Code", "role": "Mac Frontend"},
    "antigravity": {"type": "remote", "model": "Gemini", "role": "Google IDE"},
}


async def _get_agent_statuses(r: aioredis.Redis) -> list[dict[str, Any]]:
    """Get status of all known agents from tmux + Redis registry."""
    statuses = []
    for name, info in KNOWN_AGENTS.items():
        status = "unknown"
        current_task = None

        if info["type"] == "tmux":
            # Check tmux session
            try:
                result = subprocess.run(
                    ["tmux", "has-session", "-t", name],
                    capture_output=True, timeout=3,
                )
                if result.returncode == 0:
                    # Check if at prompt (idle) or working (busy)
                    cap = subprocess.run(
                        ["tmux", "capture-pane", "-t", name, "-p"],
                        capture_output=True, text=True, timeout=3,
                    )
                    last_lines = " ".join(cap.stdout.strip().split("\n")[-3:]).lower()
                    if any(p in last_lines for p in ["❯", "›", "$ ", "waiting", "you:"]):
                        status = "idle"
                    else:
                        status = "busy"
                else:
                    status = "dead"
            except Exception:
                status = "dead"
        else:
            # OpenClaw / remote agents — check if they have recent bus activity
            try:
                stream = f"sos:stream:sos:channel:private:agent:{name}"
                msgs = await r.xrevrange(stream, count=1)
                if msgs:
                    last_ts = float(msgs[0][0].split("-")[0]) / 1000
                    age_min = (time.time() - last_ts) / 60
                    status = "active" if age_min < 60 else "idle"
                else:
                    stream2 = f"sos:stream:global:agent:{name}"
                    msgs2 = await r.xrevrange(stream2, count=1)
                    if msgs2:
                        last_ts = float(msgs2[0][0].split("-")[0]) / 1000
                        age_min = (time.time() - last_ts) / 60
                        status = "active" if age_min < 60 else "idle"
                    else:
                        status = "idle"
            except Exception:
                status = "unknown"

        statuses.append({
            "agent": name,
            "type": info["type"],
            "model": info["model"],
            "role": info["role"],
            "status": status,
        })
    return statuses


def _get_service_statuses_sync() -> list[dict[str, str]]:
    """Check systemd service statuses (sync, runs in executor)."""
    services = [
        "sos-mcp-sse", "sos-squad", "sovereign-loop", "calcifer",
        "agent-wake-daemon", "bus-bridge", "openclaw-gateway",
        "kasra-agent-watchdog", "mumcp-agent-watchdog",
    ]
    statuses = []
    for svc in services:
        try:
            result = subprocess.run(
                ["systemctl", "--user", "is-active", f"{svc}.service"],
                capture_output=True, text=True, timeout=3,
                env={**os.environ, "XDG_RUNTIME_DIR": f"/run/user/{os.getuid()}"},
            )
            state = result.stdout.strip()
        except Exception:
            state = "unknown"
        statuses.append({"service": svc, "status": state})
    return statuses


def _get_systemd_health_sync() -> dict[str, str]:
    """Check health-critical systemd user units (sync, runs in executor)."""
    units = {
        "calcifer": "calcifer",
        "sentinel": "sentinel",
        "wake-daemon": "agent-wake-daemon",
        "mirror": "mirror",
    }
    result: dict[str, str] = {}
    env = {**os.environ, "XDG_RUNTIME_DIR": f"/run/user/{os.getuid()}"}
    for label, svc in units.items():
        try:
            proc = subprocess.run(
                ["systemctl", "--user", "is-active", f"{svc}.service"],
                capture_output=True, text=True, timeout=3, env=env,
            )
            result[label] = proc.stdout.strip() or "unknown"
        except Exception:
            result[label] = "unknown"
    return result


# ---------------------------------------------------------------------------
# Tool execution (async)
# ---------------------------------------------------------------------------


def _text(t: str) -> dict[str, Any]:
    return {"content": [{"type": "text", "text": t}]}


async def handle_tool(name: str, args: dict[str, Any], auth: MCPAuthContext) -> dict[str, Any]:
    loop = asyncio.get_event_loop()
    r = _get_redis()
    project_scope = _scope_project(auth)
    agent_scope = auth.agent_scope

    # Log tool invocation
    await _publish_log("info", "mcp", f"tool:{name} by {agent_scope}", agent=agent_scope)

    # Capability gate — restrict dangerous tools for non-system tokens
    SYSTEM_ONLY_TOOLS = {"onboard"}  # customer onboard mode requires system token
    WRITE_TOOLS = {"send", "broadcast", "remember", "task_create", "task_update", "request"}
    READ_TOOLS = {"inbox", "peers", "recall", "memories", "task_list", "status", "search_code"}

    if name in SYSTEM_ONLY_TOOLS and not auth.is_system:
        # onboard tool handles its own mode check, but log the attempt
        pass

    # Rate limit write operations more strictly for tenant tokens
    if name in WRITE_TOOLS and not auth.is_system:
        now = time.monotonic()
        write_key = f"write:{auth.token}"
        started_at, count = _token_windows.get(write_key, (now, 0))
        if now - started_at >= 60:
            started_at, count = now, 0
        count += 1
        _token_windows[write_key] = (started_at, count)
        if count > 30:  # 30 writes/min for tenant tokens (vs 60 total)
            await _publish_log("warn", "mcp", f"write rate limit hit by {agent_scope}", agent=agent_scope)
            return _text("Rate limit: too many write operations. Try again in a minute.")

    try:
        # --- ask ---
        if name == "ask":
            agent = _require_same_tenant_agent(auth, args.get("agent"))
            message = args["message"]

            def _run_openclaw() -> str:
                result = subprocess.run(
                    ["/usr/local/bin/openclaw", "agent", "--agent", agent, "-m", message, "--json"],
                    capture_output=True,
                    text=True,
                    timeout=120,
                )
                if result.returncode != 0:
                    return f"OpenClaw error: {result.stderr[:200]}"
                try:
                    data = json.loads(result.stdout)
                    payloads = data.get("result", {}).get("payloads", [])
                    reply = "\n".join(p.get("text", "") for p in payloads if p.get("text"))
                    return f"[{agent}]: {reply}" if reply else f"[{agent}]: (no response)"
                except json.JSONDecodeError:
                    return f"[{agent}]: {result.stdout[:500]}"

            text = await loop.run_in_executor(None, _run_openclaw)
            return _text(text)

        # --- send ---
        elif name == "send":
            to = _require_same_tenant_agent(auth, args.get("to"))
            text = args["text"]
            stream = _agent_stream(to, project_scope)
            msg = scoped_sos_msg("chat", f"agent:{agent_scope}", f"agent:{to}", text, project_scope)
            mid = await r.xadd(stream, msg)
            await r.publish(_agent_channel(to, project_scope), json.dumps(msg))
            await r.publish(f"sos:wake:{to}", json.dumps(msg))
            try:
                await loop.run_in_executor(
                    None,
                    mirror_post,
                    "/store",
                    {
                        "text": f"[{agent_scope} -> {to}] {text}",
                        "agent": agent_scope,
                        "project": project_scope,
                        "context_id": _scoped_context_id(auth, f"msg_{mid}"),
                    },
                )
            except Exception:
                pass
            return _text(f"Sent to {to} (id: {mid})")

        # --- inbox ---
        elif name == "inbox":
            agent = _require_same_tenant_agent(auth, args.get("agent"))
            limit = args.get("limit", 10)
            stream = _agent_stream(agent, project_scope)
            entries = await r.xrevrange(stream, count=limit)
            if not entries and auth.is_system and not project_scope:
                entries = await r.xrevrange(_legacy_stream(agent), count=limit)
            if not entries:
                return _text(f"No messages for {agent}.")
            lines = []
            for mid, data in entries:
                payload = json.loads(data.get("payload", "{}"))
                lines.append(
                    f"[{data.get('timestamp', '?')}] {data.get('source', '?')}: {payload.get('text', '')}"
                )
            return _text("\n".join(lines))

        # --- peers ---
        elif name == "peers":
            agents: set[str] = set()
            # Project-scoped tokens only see agents in their project
            pattern = f"{_prefix(project_scope)}:agent:*"
            cursor = 0
            while True:
                cursor, keys = await r.scan(cursor, match=pattern, count=100)
                for k in keys:
                    agents.add(k.split(":")[-1])
                if cursor == 0:
                    break
            # System tokens with no project scope see global agents only
            # (not every project's agents — that doesn't scale to 1M squads)
            if auth.is_system and not project_scope:
                # Also check legacy stream pattern
                cursor = 0
                while True:
                    cursor, keys = await r.scan(
                        cursor, match="sos:stream:sos:channel:private:agent:*", count=100
                    )
                    for k in keys:
                        agents.add(k.split(":")[-1])
                    if cursor == 0:
                        break
            # Filter out internal system agents from non-system callers
            internal_agents = {"sos-mcp-sse", "sos-squad", "sovereign-loop", "calcifer",
                               "lifecycle", "task-poller", "wake-daemon"}
            if not auth.is_system:
                agents -= internal_agents
            scope = f"project:{project_scope}" if project_scope else "global"
            return _text(
                f"Agents ({scope}): {', '.join(sorted(agents))}" if agents else "No agents found."
            )

        # --- broadcast ---
        elif name == "broadcast":
            text = args["text"]
            squad = args.get("squad")
            if squad:
                stream = f"{_prefix(project_scope)}:squad:{squad}"
                channel = f"sos:channel:{'project:' + project_scope + ':' if project_scope else ''}squad:{squad}"
            else:
                stream = f"{_prefix(project_scope)}:broadcast"
                channel = f"sos:channel:{'project:' + project_scope + ':' if project_scope else ''}global"
            msg = scoped_sos_msg("broadcast", f"agent:{agent_scope}", channel, text, project_scope)
            mid = await r.xadd(stream, msg)
            await r.publish(channel, json.dumps(msg))
            return _text(f"Broadcast to {channel} (id: {mid})")

        # --- remember ---
        elif name == "remember":
            ctx = _scoped_context_id(auth, args.get("context"))
            await loop.run_in_executor(
                None,
                mirror_post,
                "/store",
                {
                    "text": args["text"],
                    "agent": agent_scope,
                    "project": project_scope,
                    "context_id": ctx,
                },
            )
            return _text(f"Stored: {ctx}")

        # --- recall ---
        elif name == "recall":
            results = await loop.run_in_executor(
                None,
                mirror_post,
                "/search",
                {
                    "query": args["query"],
                    "top_k": args.get("limit", 5),
                    "agent_filter": agent_scope,
                    "project": project_scope,
                },
            )
            if not results:
                return _text("No matching memories.")
            lines = []
            for i, e in enumerate(results, 1):
                text = (e.get("raw_data", {}) or {}).get("text", e.get("context_id", "?"))
                lines.append(f"{i}. [{e.get('timestamp', '?')[:10]}] {str(text)[:200]}")
            return _text("\n".join(lines))

        # --- search_code ---
        elif name == "search_code":
            results = await loop.run_in_executor(
                None,
                mirror_post,
                "/code/search",
                {
                    "query": args["query"],
                    "top_k": args.get("top_k", 5),
                    "repo": args.get("repo"),
                    "kind": args.get("kind"),
                },
            )
            if not results:
                return _text("No matching code nodes found.")
            lines = []
            for i, r in enumerate(results, 1):
                loc = f"{r.get('file_path', '?')}:{r.get('line_start', '?')}"
                sig = r.get("signature") or r.get("name", "?")
                sim = r.get("similarity", 0)
                lines.append(f"{i}. [{r.get('kind')}] {sig}\n   {loc} (score: {sim:.2f})")
            return _text("\n".join(lines))

        # --- memories ---
        elif name == "memories":
            data = await loop.run_in_executor(
                None,
                mirror_get,
                f"/recent/{agent_scope}?limit={args.get('limit', 10)}"
                + (f"&project={project_scope}" if project_scope else ""),
            )
            engrams = data.get("engrams", [])
            if not engrams:
                return _text("No memories yet.")
            lines = []
            for i, e in enumerate(engrams, 1):
                text = (e.get("raw_data", {}) or {}).get("text", e.get("context_id", "?"))
                lines.append(f"{i}. [{e.get('timestamp', '?')[:10]}] {str(text)[:200]}")
            return _text("\n".join(lines))

        # --- task_create ---
        elif name == "task_create":
            await loop.run_in_executor(
                None,
                mirror_post,
                "/tasks",
                {
                    "title": args["title"],
                    "description": args.get("description", ""),
                    "assignee": _require_same_tenant_agent(auth, args.get("assignee")),
                    "priority": args.get("priority", "medium"),
                    "status": "pending",
                    "agent": agent_scope,
                    "project": project_scope,
                },
            )
            return _text(f"Task created: {args['title']}")

        # --- task_list ---
        elif name == "task_list":
            params = f"?limit={args.get('limit', 20)}"
            if args.get("status"):
                params += f"&status={args['status']}"
            assignee = _require_same_tenant_agent(auth, args.get("assignee"))
            if assignee:
                params += f"&agent={assignee}"
            if project_scope:
                params += f"&project={project_scope}"
            result = await loop.run_in_executor(None, mirror_get, f"/tasks{params}")
            tasks = result if isinstance(result, list) else result.get("tasks", [])
            if project_scope:
                tasks = [t for t in tasks if t.get("project") == project_scope]
            if not tasks:
                return _text("No tasks found.")
            lines = []
            for t in tasks:
                lines.append(
                    f"[{t.get('status', '?')}] {t.get('title', '?')} -> {t.get('assignee', '?')}"
                )
            return _text("\n".join(lines))

        # --- task_update ---
        elif name == "task_update":
            task = await loop.run_in_executor(None, mirror_get, f"/tasks/{args['task_id']}")
            _ensure_task_in_scope(task, auth)
            body: dict[str, Any] = {"task_id": args["task_id"]}
            if args.get("status"):
                body["status"] = args["status"]
            if args.get("notes"):
                body["notes"] = args["notes"]
            await loop.run_in_executor(None, mirror_put, f"/tasks/{args['task_id']}", body)
            return _text(f"Task {args['task_id']} updated")

        # --- task_board (prioritized unified view) ---
        elif name == "task_board":
            REVENUE_PROJECTS = {"dentalnearyou", "dnu", "gaf", "viamar", "stemminds", "pecb", "digid", "torivers"}
            PRIORITY_W = {"critical": 4, "urgent": 4, "high": 3, "medium": 2, "low": 1}

            # Pull from Squad Service (primary) and Mirror (secondary)
            all_tasks: list[dict] = []
            try:
                squad_resp = await loop.run_in_executor(
                    None,
                    lambda: requests.get(f"{SQUAD_SERVICE_URL}/tasks", headers={"Authorization": f"Bearer {SQUAD_SYSTEM_TOKEN}"}, timeout=5).json(),
                )
                squad_tasks = squad_resp if isinstance(squad_resp, list) else squad_resp.get("tasks", [])
                for t in squad_tasks:
                    t["_source"] = "squad"
                all_tasks.extend(squad_tasks)
            except Exception:
                pass
            try:
                mirror_resp = await loop.run_in_executor(None, mirror_get, "/tasks?limit=100")
                mirror_tasks = mirror_resp if isinstance(mirror_resp, list) else mirror_resp.get("tasks", [])
                squad_ids = {t.get("id") for t in all_tasks}
                for t in mirror_tasks:
                    if t.get("id") not in squad_ids:
                        t["_source"] = "mirror"
                        all_tasks.append(t)
            except Exception:
                pass

            # Filter
            status_filter = args.get("status", "queued")
            if status_filter != "all":
                all_tasks = [t for t in all_tasks if t.get("status") == status_filter]
            if args.get("project"):
                all_tasks = [t for t in all_tasks if t.get("project") == args["project"]]
            if args.get("agent"):
                all_tasks = [t for t in all_tasks if t.get("assignee") == args["agent"] or t.get("agent") == args["agent"]]
            if project_scope and not auth.is_system:
                all_tasks = [t for t in all_tasks if t.get("project") == project_scope]

            # Score
            def _score(t: dict) -> int:
                p = str(t.get("priority", "medium")).lower()
                blocks = len(t.get("blocks") or t.get("blocks_json") or [])
                updated = t.get("updated_at", "")
                staleness = 0
                if updated:
                    try:
                        from datetime import datetime as dt
                        age = (dt.now(timezone.utc) - dt.fromisoformat(updated.replace("Z", "+00:00"))).days
                        staleness = min(age, 30)
                    except Exception:
                        pass
                project = str(t.get("project", ""))
                revenue = 20 if project in REVENUE_PROJECTS else 0
                return PRIORITY_W.get(p, 1) * 10 + blocks * 5 + staleness * 2 + revenue

            for t in all_tasks:
                t["_score"] = _score(t)
            all_tasks.sort(key=lambda t: t["_score"], reverse=True)

            limit = args.get("limit", 20)
            all_tasks = all_tasks[:limit]

            if not all_tasks:
                return _text(f"No {status_filter} tasks found.")

            lines = [f"### Task Board ({status_filter}) — {len(all_tasks)} tasks\n"]
            lines.append(f"{'Score':>5} | {'Priority':>8} | {'Project':<14} | {'Agent':<10} | Title")
            lines.append(f"{'─'*5} | {'─'*8} | {'─'*14} | {'─'*10} | {'─'*30}")
            for t in all_tasks:
                agent = t.get("assignee") or t.get("agent") or "—"
                lines.append(
                    f"{t['_score']:>5} | {str(t.get('priority') or 'med'):>8} | {str(t.get('project') or '—'):<14} | {str(agent):<10} | {str(t.get('title') or '?')[:50]}"
                )
            return _text("\n".join(lines))

        # --- onboard ---
        elif name == "onboard":
            mode = args.get("mode", "agent")

            # --- Customer onboarding (system token only) ---
            if mode == "customer":
                if not auth.is_system:
                    return _text("Error: customer onboarding requires system token")
                slug = args.get("slug", "").strip().lower()
                label = args.get("label", "").strip()
                email = args.get("email", "").strip()
                if not slug or not label:
                    return _text("Error: slug and label required for customer onboarding")
                if not slug.replace("-", "").isalnum():
                    return _text("Error: slug must be lowercase alphanumeric with hyphens")
                result = await _onboard_customer(slug, label, email)
                if result.get("status") == "duplicate":
                    return _text(f"Customer '{slug}' already exists")
                return _text(
                    f"Customer onboarded: {label} ({slug})\n\n"
                    f"Bus token: {result['bus_token']}\n"
                    f"Mirror token: {result['mirror_token']}\n"
                    f"Squad token: {result.get('squad_token', 'n/a')}\n"
                    f"MCP SSE: {result['mcp_sse_url']}\n"
                    f"MCP HTTP: {result['mcp_http_url']}\n"
                    f"Project dir: {result['project_dir']}"
                )

            # --- Agent onboarding (full self-join) ---
            agent_name = args.get("agent_name", "new-agent")
            agent_model = args.get("model", "unknown")
            agent_role = args.get("role", "executor")
            agent_skills = args.get("skills", [])
            agent_routing = args.get("routing", "mcp")

            from sos.agents.join import AgentJoinService

            join_service = AgentJoinService()
            join_result = await join_service.join(
                name=agent_name,
                model=agent_model,
                role=agent_role,
                skills=agent_skills if isinstance(agent_skills, list) else [],
                routing=agent_routing,
            )

            # Clear MCP token cache so new token is recognized immediately
            _local_token_cache.invalidate()

            if not join_result.success:
                return _text(
                    f"Onboarding failed for '{agent_name}': "
                    + "; ".join(join_result.errors)
                )

            lines = [
                f"Welcome {join_result.name}!",
                "",
                f"Bus token: {join_result.bus_token}",
                f"Mirror token: {join_result.mirror_token}",
                f"MCP SSE: {join_result.mcp_url}",
                f"MCP HTTP: https://mcp.mumega.com/mcp/{join_result.bus_token}",
                f"Routing: {join_result.routing}",
                f"Skills registered: {', '.join(join_result.skills_registered) if join_result.skills_registered else 'none'}",
            ]
            if join_result.errors:
                lines.append("")
                lines.append("Warnings: " + "; ".join(join_result.errors))
            lines.append("")
            lines.append("--- MCP config (paste into your settings) ---")
            lines.append(json.dumps({
                "mcpServers": {
                    "mumega": {"url": join_result.mcp_url}
                }
            }, indent=2))
            lines.append("")
            lines.append(join_result.team_briefing)

            return _text("\n".join(lines))

        # --- request ---
        elif name == "request":
            description = args.get("description", "").strip()
            if not description:
                return _text("Error: description required")
            priority = args.get("priority", "medium").lower()
            if priority not in ("low", "medium", "high", "critical"):
                priority = "medium"

            # Determine project scope from auth
            project = auth.tenant_id or "mumega"

            # Auto-detect squad by keywords
            desc_lower = description.lower()
            squad_type = "dev"  # default
            labels = ["customer-request"]
            if any(kw in desc_lower for kw in ["seo", "audit", "meta", "schema", "ranking", "search"]):
                squad_type = "seo"
                labels.append("seo")
            elif any(kw in desc_lower for kw in ["content", "blog", "write", "article", "post", "social"]):
                squad_type = "content"
                labels.append("content")
            elif any(kw in desc_lower for kw in ["outreach", "lead", "email", "sales", "crm"]):
                squad_type = "outreach"
                labels.append("outreach")
            elif any(kw in desc_lower for kw in ["deploy", "monitor", "incident", "server", "infra"]):
                squad_type = "ops"
                labels.append("ops")
            else:
                labels.append("dev")

            squad_id = f"{project}-{squad_type}"
            task_id = f"{project}-req-{datetime.now(timezone.utc).strftime('%Y%m%d%H%M%S')}"

            # Create task in Squad Service
            try:
                resp = requests.post(
                    f"{SQUAD_SERVICE_URL}/tasks",
                    json={
                        "id": task_id,
                        "squad_id": squad_id,
                        "title": description[:120],
                        "description": description,
                        "project": project,
                        "priority": priority,
                        "labels": labels,
                        "status": "backlog",
                    },
                    headers={"Authorization": f"Bearer {SQUAD_SYSTEM_TOKEN}"},
                    timeout=5,
                )
                if resp.status_code >= 400:
                    # Squad might not exist yet — create it and retry
                    requests.post(
                        f"{SQUAD_SERVICE_URL}/squads",
                        json={"id": squad_id, "name": f"{project} {squad_type}", "project": project,
                              "objective": f"{squad_type} work for {project}", "status": "active"},
                        headers={"Authorization": f"Bearer {SQUAD_SYSTEM_TOKEN}"},
                        timeout=5,
                    )
                    requests.post(
                        f"{SQUAD_SERVICE_URL}/tasks",
                        json={
                            "id": task_id, "squad_id": squad_id,
                            "title": description[:120], "description": description,
                            "project": project, "priority": priority,
                            "labels": labels, "status": "backlog",
                        },
                        headers={"Authorization": f"Bearer {SQUAD_SYSTEM_TOKEN}"},
                        timeout=5,
                    )
            except Exception as e:
                return _text(f"Error creating task: {e}")

            # Store in Mirror
            try:
                requests.post(
                    f"{MIRROR_URL}/engrams",
                    json={"text": f"Customer request from {project}: {description}", "agent": project,
                          "context_id": task_id},
                    headers=MIRROR_HEADERS, timeout=5,
                )
            except Exception:
                pass

            return _text(
                f"Request received! Task created: {task_id}\n"
                f"Squad: {squad_id}\n"
                f"Priority: {priority}\n"
                f"Status: backlog — will be picked up by the next available agent.\n"
                f"Check progress with: task_list"
            )

        # --- status (sos ps) ---
        elif name == "status":
            agent_statuses = await _get_agent_statuses(r)
            svc_statuses = await asyncio.get_event_loop().run_in_executor(None, _get_service_statuses_sync)

            # Task counts from Squad Service
            task_counts = {}
            try:
                resp = requests.get(
                    f"{SQUAD_SERVICE_URL}/tasks?limit=500",
                    headers={"Authorization": f"Bearer {SQUAD_SYSTEM_TOKEN}"},
                    timeout=5,
                )
                if resp.ok:
                    tasks = resp.json()
                    from collections import Counter
                    task_counts = dict(Counter(t.get("status", "?") for t in tasks))
            except Exception:
                pass

            lines = ["# SOS Status\n"]

            # Agents
            lines.append("## Agents")
            for a in sorted(agent_statuses, key=lambda x: x["status"]):
                icon = {"idle": "🟢", "busy": "🔵", "active": "🟡", "dead": "🔴"}.get(a["status"], "⚪")
                lines.append(f"{icon} **{a['agent']}** ({a['model']}) — {a['role']} [{a['status']}]")

            # Services
            lines.append("\n## Services")
            for s in svc_statuses:
                icon = "🟢" if s["status"] == "active" else "🔴"
                lines.append(f"{icon} {s['service']}: {s['status']}")

            # Tasks
            if task_counts:
                lines.append("\n## Tasks")
                for status, count in sorted(task_counts.items()):
                    lines.append(f"- {status}: {count}")

            return _text("\n".join(lines))

        # --- browse_marketplace ---
        elif name == "browse_marketplace":
            results = _marketplace.browse(
                category=args.get("category"),
                query=args.get("query"),
            )
            if not results:
                text = "No listings found. The marketplace is just getting started!"
            else:
                lines = [f"Found {len(results)} listings:\n"]
                for r in results:
                    price = f"${r['price_cents'] / 100:.0f}/{r['price_model']}"
                    lines.append(f"- **{r['title']}** ({r['category']}) — {price}")
                    lines.append(f"  {r['description'][:100]}")
                    lines.append(f"  ID: {r['id']} | {r['subscriber_count']} subscribers")
                    lines.append("")
                text = "\n".join(lines)
            return _text(text)

        # --- subscribe ---
        elif name == "subscribe":
            result = _marketplace.subscribe(project_scope or agent_scope, args["listing_id"])
            text = result.get("message") or result.get("error", "Unknown error")
            return _text(text)

        # --- my_subscriptions ---
        elif name == "my_subscriptions":
            subs = _marketplace.my_subscriptions(project_scope or agent_scope)
            if not subs:
                text = "No active subscriptions."
            else:
                lines = ["Your subscriptions:\n"]
                for s in subs:
                    lines.append(
                        f"- {s['title']} ({s['category']}) — ${s['price_cents'] / 100:.0f}/{s['price_model']}"
                    )
                text = "\n".join(lines)
            return _text(text)

        # --- create_listing ---
        elif name == "create_listing":
            result = _marketplace.create_listing(
                seller_tenant=project_scope or agent_scope,
                title=args["title"],
                description=args["description"],
                category=args["category"],
                listing_type=args.get("listing_type", "squad"),
                price_cents=args["price_cents"],
                tags=args.get("tags", []),
            )
            text = (
                f"Listed: {result['title']} (ID: {result['listing_id']})"
                if result.get("success")
                else result.get("error", "Failed")
            )
            return _text(text)

        # --- my_earnings ---
        elif name == "my_earnings":
            earnings = _marketplace.my_earnings(project_scope or agent_scope)
            lines = [
                f"Total MRR: ${earnings['total_mrr_cents'] / 100:.0f}",
                f"Platform fee (5%): ${earnings['platform_fee_cents'] / 100:.0f}",
                f"Net earnings: ${earnings['net_earnings_cents'] / 100:.0f}\n",
            ]
            for listing in earnings["listings"]:
                lines.append(
                    f"- {listing['title']}: {listing['subscriber_count']} subscribers × ${listing['price_cents'] / 100:.0f}"
                )
            text = "\n".join(lines)
            return _text(text)

        else:
            return _text(f"Unknown tool: {name}")

    except Exception as e:
        log.exception("Tool %s failed", name)
        await _publish_log("error", "mcp", f"tool:{name} failed: {e}", agent=agent_scope)
        return _text(f"Error: {e}")


# ---------------------------------------------------------------------------
# Session registry: session_id -> asyncio.Queue for SSE push
# ---------------------------------------------------------------------------

_sessions: dict[str, asyncio.Queue[dict[str, Any] | None]] = {}
_session_auth: dict[str, MCPAuthContext] = {}
_token_windows: dict[str, tuple[float, int]] = {}


def _token_label(token: str) -> str:
    return token[-8:] if token else "anonymous"


def _append_audit(token: str, tool_name: str, success: bool) -> None:
    AUDIT_LOG_DIR.mkdir(parents=True, exist_ok=True)
    with MCP_AUDIT_LOG.open("a", encoding="utf-8") as fh:
        fh.write(
            json.dumps(
                {
                    "timestamp": now_iso(),
                    "token_last8": _token_label(token),
                    "tool": tool_name,
                    "status": "success" if success else "fail",
                    "tenant_id": _resolve_token_context(token).tenant_id if _resolve_token_context(token) else None,
                }
            )
            + "\n"
        )


def _tool_result_failed(result: dict[str, Any]) -> bool:
    try:
        text = result["content"][0]["text"]
    except Exception:
        return False
    return isinstance(text, str) and text.startswith("Error:")


def _enforce_rate_limit(token: str) -> None:
    if not token:
        raise HTTPException(status_code=401, detail="missing_token")
    now = time.monotonic()
    started_at, count = _token_windows.get(token, (now, 0))
    if now - started_at >= 60:
        started_at, count = now, 0
    count += 1
    _token_windows[token] = (started_at, count)
    if count > RATE_LIMIT_PER_MINUTE:
        raise HTTPException(status_code=429, detail="rate_limit_exceeded")


# ---------------------------------------------------------------------------
# FastAPI app
# ---------------------------------------------------------------------------

app = FastAPI(title="SOS MCP SSE", version="2.0.0")

# CORS for Claude.ai connector and other browser-based clients
from fastapi.middleware.cors import CORSMiddleware
app.add_middleware(
    CORSMiddleware,
    allow_origins=["https://claude.ai", "https://www.claude.ai", "https://chatgpt.com", "https://chat.openai.com", "*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
    expose_headers=["*"],
)


@app.options("/{path:path}")
async def options_handler(path: str):
    """Handle CORS preflight for all paths."""
    return Response(
        status_code=204,
        headers={
            "Access-Control-Allow-Origin": "*",
            "Access-Control-Allow-Methods": "GET, POST, OPTIONS",
            "Access-Control-Allow-Headers": "Content-Type, Authorization",
        },
    )


def _request_bearer_token(request: Request) -> str:
    auth = request.headers.get("authorization", "").strip()
    if auth.lower().startswith("bearer "):
        return auth[7:].strip()
    return ""


def _require_auth(request: Request, token: str | None = None) -> MCPAuthContext:
    candidate = token or _request_bearer_token(request) or request.query_params.get("token", "").strip()
    context = _resolve_token_context(candidate)
    if not context:
        raise HTTPException(status_code=401, detail="invalid token")
    return context


@app.get("/.well-known/oauth-authorization-server")
async def oauth_discovery() -> JSONResponse:
    """OAuth discovery for ChatGPT/external MCP clients."""
    base = "https://mcp.mumega.com"
    return JSONResponse({
        "issuer": base,
        "authorization_endpoint": f"{base}/oauth/authorize",
        "token_endpoint": f"{base}/oauth/token",
        "registration_endpoint": f"{base}/oauth/register",
        "response_types_supported": ["code"],
        "grant_types_supported": ["authorization_code"],
        "code_challenge_methods_supported": ["S256"],
    })


@app.post("/oauth/register")
async def oauth_register(request: Request) -> JSONResponse:
    """Dynamic client registration — auto-approves any client."""
    body = await request.json()
    client_id = f"client-{uuid4().hex[:12]}"
    return JSONResponse({
        "client_id": client_id,
        "client_secret": client_id,
        "client_name": body.get("client_name", "unknown"),
        "redirect_uris": body.get("redirect_uris", []),
        "grant_types": ["authorization_code"],
        "response_types": ["code"],
        "token_endpoint_auth_method": "client_secret_post",
    })


@app.get("/oauth/authorize")
async def oauth_authorize(request: Request) -> Response:
    """OAuth authorize — auto-approves with token from query or generates one."""
    redirect_uri = request.query_params.get("redirect_uri", "")
    state = request.query_params.get("state", "")
    if redirect_uri:
        sep = "&" if "?" in redirect_uri else "?"
        return Response(
            status_code=302,
            headers={"Location": f"{redirect_uri}{sep}code=mumega-auth-ok&state={state}"},
        )
    return JSONResponse({"code": "mumega-auth-ok"})


@app.post("/oauth/token")
async def oauth_token(request: Request) -> JSONResponse:
    """OAuth token exchange — returns the system MCP access token."""
    tokens = list(_system_tokens())
    access_token = tokens[0] if tokens else "no-token-configured"
    return JSONResponse({
        "access_token": access_token,
        "token_type": "Bearer",
        "expires_in": 86400,
    })


@app.get("/health")
async def health() -> JSONResponse:
    r = _get_redis()
    try:
        await r.ping()
        redis_ok = True
    except Exception:
        redis_ok = False
    return JSONResponse(
        {
            "status": "ok",
            "redis": redis_ok,
            "port": PORT,
            "sessions": len(_sessions),
        }
    )


@app.get("/health/full")
async def health_full() -> JSONResponse:
    """Full organism health — one URL to rule them all.

    Checks Redis, Mirror, Squad, Dashboard, MCP SSE, systemd units,
    agent registry, tenant registry, kernel services, and flywheel score.
    All checks run in parallel with 3-second timeouts.
    """
    import httpx

    t0 = time.monotonic()

    # ------------------------------------------------------------------
    # Parallel async checks
    # ------------------------------------------------------------------

    async def _check_redis() -> dict[str, Any]:
        r = _get_redis()
        start = time.monotonic()
        try:
            await asyncio.wait_for(r.ping(), timeout=3.0)
            return {"status": "healthy", "latency_ms": round((time.monotonic() - start) * 1000)}
        except Exception as exc:
            return {"status": "critical", "error": str(exc)}

    async def _check_http(name: str, url: str) -> dict[str, Any]:
        start = time.monotonic()
        try:
            async with httpx.AsyncClient(timeout=3.0) as client:
                resp = await client.get(url)
                latency = round((time.monotonic() - start) * 1000)
                if resp.status_code < 400:
                    return {"status": "healthy", "latency_ms": latency}
                return {"status": "degraded", "latency_ms": latency, "http": resp.status_code}
        except Exception as exc:
            return {"status": "down", "error": str(exc)}

    async def _get_kernel_info(r: aioredis.Redis) -> dict[str, Any]:
        try:
            svc_keys = await asyncio.wait_for(r.keys("sos:kernel:services:*"), timeout=3.0)
            return {"registered_services": len(svc_keys)}
        except Exception:
            return {"registered_services": 0}

    async def _get_online_agents(r: aioredis.Redis) -> dict[str, Any]:
        try:
            reg_keys = await asyncio.wait_for(r.keys("sos:registry:*"), timeout=3.0)
            names = [k.split(":")[-1] for k in reg_keys]
            return {"online": sorted(names), "total": len(names)}
        except Exception:
            return {"online": [], "total": 0}

    async def _get_flywheel() -> dict[str, Any]:
        try:
            async with httpx.AsyncClient(timeout=3.0) as client:
                resp = await client.get(
                    f"{MIRROR_URL}/memories",
                    headers=MIRROR_HEADERS,
                    params={"query": "feedback loop score", "limit": "1"},
                )
                if resp.ok:
                    data = resp.json()
                    items = data if isinstance(data, list) else data.get("results", [])
                    if items:
                        item = items[0]
                        meta = item.get("metadata", {})
                        return {
                            "last_feedback": meta.get("date", item.get("created_at", "unknown")),
                            "effectiveness": meta.get("effectiveness", None),
                        }
        except Exception:
            pass
        return {"last_feedback": None, "effectiveness": None}

    # Fire everything in parallel
    r = _get_redis()
    (
        redis_result,
        mirror_result,
        squad_result,
        dashboard_result,
        kernel_info,
        agents_info,
        flywheel_info,
        systemd_statuses,
    ) = await asyncio.gather(
        _check_redis(),
        _check_http("mirror", "http://localhost:8844/"),
        _check_http("squad", "http://localhost:8060/health"),
        _check_http("dashboard", "http://localhost:8090/health"),
        _get_kernel_info(r),
        _get_online_agents(r),
        _get_flywheel(),
        asyncio.get_event_loop().run_in_executor(None, _get_systemd_health_sync),
    )

    # ------------------------------------------------------------------
    # Tenants from disk
    # ------------------------------------------------------------------
    tenants_info: dict[str, Any] = {"active": [], "total": 0}
    tenants_path = Path.home() / ".sos" / "tenants.json"
    try:
        if tenants_path.exists():
            tdata = json.loads(tenants_path.read_text())
            names = [k for k in tdata if not k.startswith("_")]
            tenants_info = {"active": sorted(names), "total": len(names)}
    except Exception:
        pass

    # ------------------------------------------------------------------
    # Overall status
    # ------------------------------------------------------------------
    services = {
        "redis": redis_result,
        "mirror": mirror_result,
        "squad": squad_result,
        "dashboard": dashboard_result,
        "mcp_sse": {"status": "healthy"},
    }

    critical_down = redis_result.get("status") != "healthy"
    degraded_count = sum(
        1 for k, v in services.items()
        if v.get("status") not in ("healthy",) and k not in ("redis", "mcp_sse")
    )

    if critical_down:
        overall = "critical"
    elif degraded_count >= 3:
        overall = "critical"
    elif degraded_count >= 1:
        overall = "degraded"
    else:
        overall = "healthy"

    elapsed_ms = round((time.monotonic() - t0) * 1000)

    return JSONResponse({
        "status": overall,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "elapsed_ms": elapsed_ms,
        "services": services,
        "agents": agents_info,
        "tenants": tenants_info,
        "kernel": kernel_info,
        "systemd": systemd_statuses,
        "flywheel": flywheel_info,
    })


# ---------------------------------------------------------------------------
# Public Organism Vitals — live data for mumega.com homepage
# ---------------------------------------------------------------------------

@app.get("/api/organism")
async def organism_vitals() -> JSONResponse:
    """Real-time organism vitals for the public mumega.com homepage.

    Returns sanitized, public-safe data. No tokens, no secrets, no internal paths.
    Fetched by the homepage every 60 seconds to show the organism is alive.
    """
    from pathlib import Path as _Path
    import glob

    vitals: dict[str, Any] = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }

    # Agent count from registry
    try:
        from sos.kernel.agent_registry import get_all_agents
        all_agents = get_all_agents()
        vitals["agents_total"] = len(all_agents)
    except Exception:
        vitals["agents_total"] = 0

    # Active agents from lifecycle state files
    try:
        state_dir = _Path.home() / ".sos" / "state"
        active = 0
        last_activity = ""
        for sf in state_dir.glob("*.json"):
            try:
                state = json.loads(sf.read_text())
                if state.get("last_seen_state") in ("busy", "idle"):
                    active += 1
                seen = state.get("last_seen_at", "")
                if seen > last_activity:
                    last_activity = seen
            except Exception:
                pass
        vitals["agents_active"] = active
        vitals["last_activity"] = last_activity
    except Exception:
        vitals["agents_active"] = 0
        vitals["last_activity"] = ""

    # Tasks from Squad Service
    try:
        import httpx
        async with httpx.AsyncClient(timeout=3) as client:
            resp = await client.get(
                "http://localhost:8060/tasks",
                headers={"Authorization": f"Bearer {os.environ.get('SOS_SYSTEM_TOKEN', '')}"},
            )
            if resp.status_code == 200:
                tasks = resp.json()
                if isinstance(tasks, dict):
                    tasks = tasks.get("tasks", [])
                done = sum(1 for t in tasks if t.get("status") == "done")
                vitals["tasks_completed"] = done
                vitals["tasks_total"] = len(tasks)
            else:
                vitals["tasks_completed"] = 0
    except Exception:
        vitals["tasks_completed"] = 0

    # Bounties from bounty board
    try:
        bounties_dir = _Path.home() / ".mumega" / "bounties"
        if bounties_dir.exists():
            bounty_files = list(bounties_dir.glob("*.json"))
            open_bounties = 0
            for bf in bounty_files:
                try:
                    bd = json.loads(bf.read_text())
                    if bd.get("status") == "open":
                        open_bounties += 1
                except Exception:
                    pass
            vitals["bounties_open"] = open_bounties
            vitals["bounties_total"] = len(bounty_files)
    except Exception:
        vitals["bounties_open"] = 0

    # Treasury balance
    try:
        treasury_dir = _Path.home() / ".sos" / "treasury"
        total_mind = 0.0
        if treasury_dir.exists():
            for balance_file in treasury_dir.glob("*/balance.json"):
                try:
                    bal = json.loads(balance_file.read_text())
                    total_mind += bal.get("balance_mind", 0)
                except Exception:
                    pass
        vitals["treasury_mind"] = total_mind
    except Exception:
        vitals["treasury_mind"] = 0

    # Services healthy count
    try:
        r = _get_redis()
        await r.ping()
        vitals["redis"] = True
    except Exception:
        vitals["redis"] = False

    vitals["services_count"] = 7  # calcifer, lifecycle, output-capture, wake-daemon, mcp-sse, squad, mirror

    return JSONResponse(vitals, headers={"Access-Control-Allow-Origin": "*"})


# ---------------------------------------------------------------------------
# Customer Onboarding — Reproduction Organ
# ---------------------------------------------------------------------------

SIGNUP_SECRET = os.environ.get("SIGNUP_SECRET", "")
MIRROR_KEYS_PATH = Path.home() / "mirror" / "tenant_keys.json"
CUSTOMERS_DIR = Path.home() / ".mumega" / "customers"
SQUAD_SERVICE_URL = "http://localhost:8060"


def _atomic_json_append(path: Path, entry: dict, dedup_key: str, dedup_value: str) -> bool:
    """Atomically append an entry to a JSON array file. Returns False if duplicate."""
    import tempfile
    data = json.loads(path.read_text()) if path.exists() else []
    for item in data:
        if item.get(dedup_key) == dedup_value:
            return False
    data.append(entry)
    tmp = tempfile.NamedTemporaryFile(mode="w", dir=str(path.parent), suffix=".tmp", delete=False)
    try:
        json.dump(data, tmp, indent=2)
        tmp.close()
        os.rename(tmp.name, str(path))
    except Exception:
        os.unlink(tmp.name)
        raise
    return True


def _scaffold_customer_dir(slug: str, label: str, bus_token: str, mirror_token: str) -> Path:
    """Create customer project directory with configs."""
    proj_dir = CUSTOMERS_DIR / slug
    claude_dir = proj_dir / ".claude"
    claude_dir.mkdir(parents=True, exist_ok=True)

    (proj_dir / "CLAUDE.md").write_text(f"""# {label}

## Connection
This project is connected to Mumega SOS.
- Agent: `{slug}`
- Memory: scoped to {slug} namespace
- Bus: project-isolated messaging

## Tools
All tools are available via the `sos` MCP:
- `send` / `inbox` / `peers` / `broadcast` — team messaging
- `remember` / `recall` / `memories` — persistent memory
- `task_create` / `task_list` / `task_update` — task management
""")

    (claude_dir / "settings.json").write_text(json.dumps({
        "mcpServers": {
            "sos": {
                "type": "stdio",
                "command": "node",
                "args": ["$HOME/sos-remote.js"],
                "env": {
                    "SOS_TOKEN": bus_token,
                    "MIRROR_TOKEN": mirror_token,
                    "AGENT": slug,
                },
            }
        }
    }, indent=2))

    (proj_dir / ".env").write_text(f"""# {label} — Mumega Connection
SOS_TOKEN={bus_token}
MIRROR_TOKEN={mirror_token}
AGENT={slug}
""")

    (proj_dir / ".gitignore").write_text(".env\nnode_modules/\n.claude/settings.local.json\n")

    (proj_dir / "README.md").write_text(f"""# {label}

## Setup
```bash
curl -o ~/sos-remote.js https://bus.mumega.com/sdk/remote.js
cd {slug}
claude
```

## MCP Connection (for Antigravity / Claude.ai / external agents)
SSE: `https://mcp.mumega.com/sse/{bus_token}`
HTTP: `https://mcp.mumega.com/mcp/{bus_token}`
""")
    return proj_dir


async def _onboard_customer(slug: str, label: str, email: str) -> dict[str, Any]:
    """Full customer onboarding orchestrator. Returns tokens and status."""
    import secrets as _secrets
    import hashlib

    timestamp = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    # 1. Generate tokens
    mirror_token = f"sk-mumega-{slug}-{_secrets.token_hex(8)}"
    bus_token = f"sk-bus-{slug}-{_secrets.token_hex(8)}"
    mirror_hash = hashlib.sha256(mirror_token.encode()).hexdigest()

    # 2. Store Mirror tenant key (atomic)
    mirror_added = _atomic_json_append(
        MIRROR_KEYS_PATH,
        {"key": mirror_token, "key_hash": mirror_hash, "agent_slug": slug,
         "created_at": timestamp, "active": True, "label": label},
        dedup_key="agent_slug", dedup_value=slug,
    )

    # 3. Store Bus token (atomic) — scope="customer" gates tool visibility
    bus_added = _atomic_json_append(
        BUS_TOKENS_PATH,
        {"token": bus_token, "token_hash": "", "project": slug, "agent": slug,
         "label": label, "active": True, "created_at": timestamp, "scope": "customer"},
        dedup_key="project", dedup_value=slug,
    )

    if not mirror_added or not bus_added:
        return {"error": f"Customer '{slug}' already exists", "status": "duplicate"}

    # 4. Clear MCP token cache so new tokens are recognized immediately
    _local_token_cache.invalidate()

    # 5. Create Squad API key
    squad_token = ""
    try:
        from sos.services.squad.auth import create_api_key
        squad_token, _ = create_api_key(slug, "user", _squad_db)
    except Exception as e:
        log.warning("Squad API key creation failed: %s", e)

    # 6. Scaffold customer directory
    proj_dir = _scaffold_customer_dir(slug, label, bus_token, mirror_token)

    # 7. Create default squad via Squad Service
    try:
        requests.post(
            f"{SQUAD_SERVICE_URL}/squads",
            json={"id": f"{slug}-dev", "name": f"{label} Dev Squad", "project": slug,
                  "objective": f"Development and delivery for {label}",
                  "status": "active"},
            headers={"Authorization": f"Bearer {SQUAD_SYSTEM_TOKEN}"},
            timeout=5,
        )
    except Exception as e:
        log.warning("Default squad creation failed: %s", e)

    # 8. Dispatch genesis task
    try:
        task_id = f"{slug}-genesis-{datetime.now(timezone.utc).strftime('%Y%m%d%H%M%S')}"
        requests.post(
            f"{SQUAD_SERVICE_URL}/tasks",
            json={"id": task_id, "squad_id": f"{slug}-dev",
                  "title": f"Welcome {label} — initial audit", "project": slug,
                  "description": f"Run initial audit for {label}. Check site health, identify quick wins.",
                  "priority": "high", "labels": ["onboarding", "audit"], "status": "backlog"},
            headers={"Authorization": f"Bearer {SQUAD_SYSTEM_TOKEN}"},
            timeout=5,
        )
    except Exception as e:
        log.warning("Genesis task creation failed: %s", e)

    # 9. Register in Mirror
    try:
        requests.post(
            f"{MIRROR_URL}/engrams",
            json={"text": f"Customer onboarded: {label} ({slug}), email: {email}, date: {timestamp}",
                  "agent": "system", "context_id": f"onboard-{slug}"},
            headers=MIRROR_HEADERS, timeout=5,
        )
    except Exception:
        pass

    # 10. Announce on bus
    try:
        r = _get_redis()
        await r.publish("sos:wake:kasra", json.dumps({
            "source": "system", "text": f"New customer onboarded: {label} ({slug})",
        }))
        await _publish_log("info", "onboarding", f"Customer onboarded: {label} ({slug})")
    except Exception:
        pass

    mcp_sse_url = f"https://mcp.mumega.com/sse/{bus_token}"
    mcp_http_url = f"https://mcp.mumega.com/mcp/{bus_token}"

    return {
        "status": "ok",
        "slug": slug,
        "label": label,
        "bus_token": bus_token,
        "mirror_token": mirror_token,
        "squad_token": squad_token,
        "mcp_sse_url": mcp_sse_url,
        "mcp_http_url": mcp_http_url,
        "project_dir": str(proj_dir),
        "setup_instructions": f"Connect any MCP client to: {mcp_sse_url}",
    }


@app.post("/api/v1/customers/signup")
async def customer_signup(request: Request) -> JSONResponse:
    """Customer onboarding endpoint. Creates tokens, squad, genesis task."""
    # Auth: signup secret OR system token
    secret = request.headers.get("x-signup-secret", "")
    bearer = _request_bearer_token(request)
    if secret and SIGNUP_SECRET and secret == SIGNUP_SECRET:
        pass  # OK
    elif bearer and bearer in _system_tokens():
        pass  # OK
    else:
        raise HTTPException(status_code=401, detail="unauthorized")

    body = await request.json()
    slug = body.get("slug", "").strip().lower()
    label = body.get("label", "").strip()
    email = body.get("email", "").strip()

    if not slug or not label:
        raise HTTPException(status_code=400, detail="slug and label required")
    if not slug.replace("-", "").isalnum():
        raise HTTPException(status_code=400, detail="slug must be lowercase alphanumeric with hyphens")

    result = await _onboard_customer(slug, label, email)

    if result.get("status") == "duplicate":
        raise HTTPException(status_code=409, detail=result["error"])

    return JSONResponse(result)


# ---------------------------------------------------------------------------
# Stripe Webhook — Auto-provision tenant on payment
# ---------------------------------------------------------------------------

@app.post("/webhook/stripe")
async def stripe_webhook(request: Request) -> JSONResponse:
    """Stripe webhook endpoint. Verifies signature, provisions tenant on checkout."""
    from sos.services.billing.webhook import stripe_webhook_handler
    return await stripe_webhook_handler(request)


# ---------------------------------------------------------------------------
# OAuth Callbacks — Per-tenant integration connections
# ---------------------------------------------------------------------------

@app.get("/oauth/ghl/callback")
async def ghl_oauth_callback(request: Request) -> Response:
    """Handle GHL OAuth callback after tenant grants access.

    Query params: code, tenant (passed via state or custom param).
    """
    from sos.services.integrations.oauth import TenantIntegrations

    code = request.query_params.get("code", "")
    tenant = request.query_params.get("tenant", "")

    if not code or not tenant:
        raise HTTPException(status_code=400, detail="code and tenant required")

    integrations = TenantIntegrations(tenant)
    result = await integrations.handle_ghl_callback(code)

    # TODO: Redirect to dashboard with success message once dashboard exists
    return JSONResponse({
        "status": "connected",
        "provider": "ghl",
        "tenant": tenant,
        "location_id": result.get("location_id", ""),
    })


@app.get("/oauth/google/callback")
async def google_oauth_callback(request: Request) -> Response:
    """Handle Google OAuth callback after tenant grants access.

    Query params: code, state (contains tenant:service).
    """
    from sos.services.integrations.oauth import TenantIntegrations

    code = request.query_params.get("code", "")
    state = request.query_params.get("state", "")

    if not code or not state:
        raise HTTPException(status_code=400, detail="code and state required")

    # State format: "tenant_name:service" (e.g. "viamar:analytics")
    parts = state.split(":", 1)
    if len(parts) != 2:
        raise HTTPException(status_code=400, detail="invalid state format, expected tenant:service")

    tenant, service = parts[0], parts[1]

    if service not in ("analytics", "search_console", "ads"):
        raise HTTPException(status_code=400, detail=f"unknown service: {service}")

    integrations = TenantIntegrations(tenant)
    result = await integrations.handle_google_callback(code, service)  # type: ignore[arg-type]

    # TODO: Redirect to dashboard with success message once dashboard exists
    return JSONResponse({
        "status": "connected",
        "provider": f"google_{service}",
        "tenant": tenant,
    })


async def _publish_log(level: str, service: str, message: str, agent: str = "") -> None:
    """Publish a log entry to the unified log stream."""
    try:
        r = _get_redis()
        await r.xadd("sos:stream:logs", {
            "level": level,
            "service": service,
            "agent": agent,
            "message": message,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }, maxlen=10000)
    except Exception:
        pass


@app.post("/api/skills/install")
async def install_skill(request: Request) -> JSONResponse:
    """Install a skill from a GitHub SKILL.md URL or local path."""
    auth = _require_auth(request)
    if not auth.is_system:
        raise HTTPException(status_code=403, detail="system token required")

    body = await request.json()
    source = body.get("source", "").strip()  # GitHub URL or local path
    if not source:
        raise HTTPException(status_code=400, detail="source required (GitHub URL or local path)")

    skill_content = ""

    # Fetch from GitHub
    if source.startswith("http"):
        try:
            # Convert GitHub page URL to raw URL
            raw_url = source.replace("github.com", "raw.githubusercontent.com").replace("/blob/", "/")
            if not raw_url.endswith("SKILL.md"):
                raw_url = raw_url.rstrip("/") + "/SKILL.md"
            resp = requests.get(raw_url, timeout=10)
            resp.raise_for_status()
            skill_content = resp.text
        except Exception as e:
            raise HTTPException(status_code=400, detail=f"Failed to fetch: {e}")

    # Read from local path
    elif source.startswith("/"):
        skill_path = Path(source)
        if skill_path.is_dir():
            skill_path = skill_path / "SKILL.md"
        if not skill_path.exists():
            raise HTTPException(status_code=404, detail=f"Not found: {skill_path}")
        skill_content = skill_path.read_text()

    else:
        # Assume it's a skill name in our local skills dir
        local = Path.home() / "SOS" / "sos" / "skills" / source / "SKILL.md"
        if local.exists():
            skill_content = local.read_text()
        else:
            raise HTTPException(status_code=404, detail=f"Skill not found: {source}")

    # Parse SKILL.md YAML frontmatter
    import yaml
    if "---" in skill_content:
        parts = skill_content.split("---", 2)
        if len(parts) >= 3:
            try:
                meta = yaml.safe_load(parts[1])
            except Exception:
                raise HTTPException(status_code=400, detail="Invalid SKILL.md YAML")
        else:
            raise HTTPException(status_code=400, detail="Invalid SKILL.md format")
    else:
        raise HTTPException(status_code=400, detail="SKILL.md must have YAML frontmatter")

    # Register in Squad Service
    skill_payload = {
        "id": meta.get("name", source.split("/")[-1]),
        "name": meta.get("name", "unknown"),
        "description": meta.get("description", ""),
        "labels": meta.get("labels", []),
        "keywords": meta.get("keywords", []),
        "entrypoint": meta.get("entrypoint", ""),
        "fuel_grade": meta.get("fuel_grade", "diesel"),
        "trust_tier": meta.get("trust_tier", 1),
        "version": meta.get("version", "1.0.0"),
        "input_schema": meta.get("input_schema", {}),
        "output_schema": meta.get("output_schema", {}),
        "status": "active",
    }

    try:
        resp = requests.post(
            f"{SQUAD_SERVICE_URL}/skills",
            json=skill_payload,
            headers={"Authorization": f"Bearer {SQUAD_SYSTEM_TOKEN}"},
            timeout=5,
        )
        if resp.status_code >= 400:
            return JSONResponse({"status": "error", "detail": resp.text}, status_code=resp.status_code)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Registration failed: {e}")

    await _publish_log("info", "skills", f"Installed skill: {meta.get('name', source)}")

    return JSONResponse({
        "status": "ok",
        "skill": meta.get("name"),
        "version": meta.get("version", "1.0.0"),
        "labels": meta.get("labels", []),
        "source": source,
    })


@app.get("/api/skills")
async def list_skills(request: Request) -> JSONResponse:
    """List all installed skills."""
    try:
        resp = requests.get(
            f"{SQUAD_SERVICE_URL}/skills",
            headers={"Authorization": f"Bearer {SQUAD_SYSTEM_TOKEN}"},
            timeout=5,
        )
        return JSONResponse(resp.json() if resp.ok else [])
    except Exception:
        return JSONResponse([])


@app.get("/api/config")
async def get_config(request: Request) -> JSONResponse:
    """Unified config viewer — shows all system configuration (secrets masked)."""
    auth = _require_auth(request)
    if not auth.is_system:
        raise HTTPException(status_code=403, detail="system token required")

    def _mask(val: str) -> str:
        if not val or len(val) < 8:
            return "***"
        return val[:6] + "..." + val[-4:]

    # Collect config from all sources
    config: dict[str, Any] = {}

    # 1. Environment (.env.secrets)
    env_keys = {}
    secrets_path = Path.home() / ".env.secrets"
    if secrets_path.exists():
        for line in secrets_path.read_text().splitlines():
            line = line.strip()
            if "=" in line and not line.startswith("#"):
                k, v = line.split("=", 1)
                env_keys[k.strip()] = _mask(v.strip())
    config["secrets"] = env_keys

    # 2. Services
    config["services"] = {
        "mcp_sse": {"port": PORT, "url": f"http://localhost:{PORT}"},
        "squad": {"port": 8060, "url": SQUAD_SERVICE_URL},
        "mirror": {"port": 8844, "url": MIRROR_URL, "status": "disabled"},
        "redis": {"port": 6379},
        "openclaw": {"port": 18789},
    }

    # 3. Agents (from KNOWN_AGENTS)
    config["agents"] = {name: {"type": info["type"], "model": info["model"], "role": info["role"]} for name, info in KNOWN_AGENTS.items()}

    # 4. Bus tokens (count only, not values)
    try:
        bus_tokens = json.loads(BUS_TOKENS_PATH.read_text())
        config["bus_tokens"] = {
            "count": len(bus_tokens),
            "projects": [t.get("project", "?") for t in bus_tokens if t.get("active")],
        }
    except Exception:
        config["bus_tokens"] = {"count": 0, "projects": []}

    # 5. Organisms
    org_dir = Path.home() / ".mumega" / "organisms"
    if org_dir.exists():
        config["organisms"] = [f.stem for f in org_dir.glob("*.yaml")]
    else:
        config["organisms"] = []

    # 6. Skills count
    try:
        resp = requests.get(f"{SQUAD_SERVICE_URL}/skills", headers={"Authorization": f"Bearer {SQUAD_SYSTEM_TOKEN}"}, timeout=3)
        config["skills_count"] = len(resp.json()) if resp.ok else 0
    except Exception:
        config["skills_count"] = 0

    # 7. Feature flags
    config["features"] = {
        "capabilities_enforced": os.environ.get("SOS_REQUIRE_CAPABILITIES", "0") == "1",
        "mirror_enabled": False,
        "rate_limit_per_min": RATE_LIMIT_PER_MINUTE,
    }

    return JSONResponse(config)


@app.get("/api/logs")
async def get_logs(
    service: str | None = None,
    level: str | None = None,
    agent: str | None = None,
    limit: int = 50,
) -> JSONResponse:
    """Unified log viewer — query logs from Redis stream."""
    r = _get_redis()
    try:
        entries = await r.xrevrange("sos:stream:logs", count=min(limit, 500))
    except Exception:
        return JSONResponse({"logs": [], "error": "Redis unavailable"})

    logs = []
    for mid, data in entries:
        if service and data.get("service") != service:
            continue
        if level and data.get("level") != level:
            continue
        if agent and data.get("agent") != agent:
            continue
        logs.append({
            "id": mid,
            "level": data.get("level", "info"),
            "service": data.get("service", "?"),
            "agent": data.get("agent", ""),
            "message": data.get("message", ""),
            "timestamp": data.get("timestamp", ""),
        })
        if len(logs) >= limit:
            break

    return JSONResponse({"logs": logs, "count": len(logs)})


@app.get("/sse/{token}")
async def sse_endpoint_with_token(token: str, request: Request) -> EventSourceResponse:
    """SSE endpoint with token-based auth (for Claude.ai connectors)."""
    # TODO: path-token auth is deprecated because tokens in URLs leak into access logs,
    # browser history, and proxies. Prefer Authorization: Bearer for new clients.
    _require_auth(request, token)
    return await sse_endpoint(request, token=token)


@app.get("/sse")
async def sse_endpoint(request: Request, token: str | None = None) -> EventSourceResponse:
    """
    MCP SSE transport: client connects here and receives a session endpoint,
    then sends JSON-RPC requests to POST /messages?session_id=<id>.
    """
    resolved_token = token or _request_bearer_token(request) or request.query_params.get("token", "").strip()
    auth = _require_auth(request, resolved_token)
    session_id = str(uuid4())
    queue: asyncio.Queue[dict[str, Any] | None] = asyncio.Queue()
    _sessions[session_id] = queue
    _session_auth[session_id] = auth

    # Use public URL if behind nginx proxy, otherwise localhost
    public_base = os.environ.get("MCP_PUBLIC_URL", "")
    if public_base:
        messages_url = f"{public_base}/messages?session_id={session_id}"
    elif request.headers.get("x-forwarded-proto") == "https":
        messages_url = f"https://{request.headers.get('host', 'mcp.mumega.com')}/messages?session_id={session_id}"
    else:
        messages_url = f"http://localhost:{PORT}/messages?session_id={session_id}"
    log.info("SSE client connected, session=%s", session_id)

    async def event_generator():
        try:
            # First event: tell the client where to POST requests
            yield {"event": "endpoint", "data": messages_url}

            while True:
                if await request.is_disconnected():
                    break
                try:
                    msg = await asyncio.wait_for(queue.get(), timeout=15.0)
                    if msg is None:
                        break
                    yield {"event": "message", "data": json.dumps(msg)}
                except asyncio.TimeoutError:
                    # keepalive ping
                    yield {"event": "ping", "data": ""}
        finally:
            _sessions.pop(session_id, None)
            _session_auth.pop(session_id, None)
            log.info("SSE client disconnected, session=%s", session_id)

    return EventSourceResponse(event_generator())


@app.post("/messages")
async def messages_endpoint(request: Request) -> Response:
    """
    Receive JSON-RPC requests from the MCP client.
    Dispatch tool calls and push responses back via SSE.
    """
    session_id = request.query_params.get("session_id", "")
    queue = _sessions.get(session_id)
    auth = _session_auth.get(session_id) or _require_auth(request)
    _enforce_rate_limit(auth.token)

    try:
        body = await request.json()
    except Exception:
        return Response(status_code=400, content="Invalid JSON")

    resp = await _process_jsonrpc(body, session_id=session_id, auth=auth)
    if resp is None:
        return Response(status_code=202)

    # Push response to SSE stream if session is active
    if queue is not None:
        await queue.put(resp)
    else:
        # Fallback: return response directly (stateless clients)
        return JSONResponse(resp)

    return Response(status_code=202)


@app.get("/mcp")
async def mcp_info(request: Request) -> JSONResponse:
    _require_auth(request)
    return JSONResponse(
        {
            "name": "sos",
            "transport": "streamable-http",
            "endpoint": "/mcp",
            "protocolVersion": "2024-11-05",
            "capabilities": {"tools": {"listChanged": False}},
        }
    )


@app.get("/mcp/{token}")
async def mcp_info_with_token(token: str, request: Request) -> JSONResponse:
    _require_auth(request, token)
    return JSONResponse(
        {
            "name": "sos",
            "transport": "streamable-http",
            "endpoint": "/mcp",
            "protocolVersion": "2024-11-05",
            "capabilities": {"tools": {"listChanged": False}},
        }
    )


@app.post("/mcp")
async def mcp_endpoint(request: Request) -> Response:
    auth = _require_auth(request, _request_bearer_token(request))
    _enforce_rate_limit(auth.token)
    return await _streamable_http_response(request, auth)


@app.post("/mcp/{token}")
async def mcp_endpoint_with_token(token: str, request: Request) -> Response:
    # TODO: path-token auth is deprecated because tokens in URLs leak into access logs,
    # browser history, and proxies. Prefer Authorization: Bearer for new clients.
    auth = _require_auth(request, token)
    _enforce_rate_limit(auth.token)
    return await _streamable_http_response(request, auth)


async def _streamable_http_response(request: Request, auth: MCPAuthContext) -> Response:
    try:
        body = await request.json()
    except Exception:
        return Response(status_code=400, content="Invalid JSON")

    resp = await _process_jsonrpc(body, session_id=None, auth=auth)
    if resp is None:
        return Response(status_code=202)
    return JSONResponse(resp)


async def _process_jsonrpc(
    body: dict[str, Any],
    session_id: str | None,
    auth: MCPAuthContext,
) -> dict[str, Any] | None:
    method = body.get("method", "")
    msg_id = body.get("id")
    params = body.get("params", {})

    log.info("session=%s method=%s id=%s", session_id or "-", method, msg_id)

    if method == "initialize":
        return _jsonrpc_ok(
            msg_id,
            {
                "protocolVersion": "2024-11-05",
                "capabilities": {"tools": {"listChanged": False}},
                "serverInfo": {"name": "sos", "version": "2.1.0"},
            },
        )
    if method == "notifications/initialized":
        return None
    if method == "tools/list":
        # Customer tokens see only the 8 curated customer tools
        if auth.is_customer:
            return _jsonrpc_ok(msg_id, {"tools": get_customer_tools()})
        return _jsonrpc_ok(msg_id, {"tools": get_tools()})
    if method == "tools/call":
        tool_name = params.get("name", "")
        # Customer token gating: block admin tools, resolve customer names to internal names
        if auth.is_customer:
            if tool_name in BLOCKED_TOOLS:
                log.warning(
                    "customer %s attempted blocked tool %s",
                    auth.tenant_id,
                    tool_name,
                )
                return _jsonrpc_err(msg_id, f"Tool not available: {tool_name}")
            if not is_customer_tool(tool_name):
                log.warning(
                    "customer %s attempted unknown tool %s",
                    auth.tenant_id,
                    tool_name,
                )
                return _jsonrpc_err(msg_id, f"Tool not available: {tool_name}")
            # Resolve customer-facing name to internal SOS tool name
            internal_name = TOOL_MAPPING.get(tool_name, tool_name)
            tool_name = internal_name
        tool_result = await handle_tool(tool_name, params.get("arguments", {}), auth)
        _append_audit(auth.token, tool_name, not _tool_result_failed(tool_result))
        return _jsonrpc_ok(msg_id, tool_result)
    if method == "ping":
        return _jsonrpc_ok(msg_id, {})
    return _jsonrpc_err(msg_id, f"Unknown method: {method}")


def _jsonrpc_ok(msg_id: Any, result: Any) -> dict[str, Any]:
    return {"jsonrpc": "2.0", "id": msg_id, "result": result}


def _jsonrpc_err(msg_id: Any, message: str) -> dict[str, Any]:
    return {"jsonrpc": "2.0", "id": msg_id, "error": {"code": -32000, "message": message}}


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import uvicorn

    log.info("Starting SOS MCP SSE server on port %d", PORT)
    uvicorn.run(
        "sos.mcp.sos_mcp_sse:app",
        host="0.0.0.0",
        port=PORT,
        log_level="info",
        access_log=False,
    )
