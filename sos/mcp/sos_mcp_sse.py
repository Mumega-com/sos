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

    @property
    def agent_scope(self) -> str:
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
    resp = requests.get(f"{MIRROR_URL}{path}", headers=MIRROR_HEADERS, timeout=10)
    resp.raise_for_status()
    return resp.json()


def mirror_post(path: str, body: dict[str, Any]) -> Any:
    resp = requests.post(f"{MIRROR_URL}{path}", headers=MIRROR_HEADERS, json=body, timeout=10)
    resp.raise_for_status()
    return resp.json()


def mirror_put(path: str, body: dict[str, Any]) -> Any:
    resp = requests.put(f"{MIRROR_URL}{path}", headers=MIRROR_HEADERS, json=body, timeout=10)
    resp.raise_for_status()
    return resp.json()


_squad_db = SquadDB()
_cloudflare_token_cache: dict[str, tuple[float, MCPAuthContext | None]] = {}
_local_token_cache: dict[str, MCPAuthContext] = {}


def _system_tokens() -> set[str]:
    tokens = {token.strip() for token in os.environ.get("MCP_ACCESS_TOKENS", "").split(",") if token.strip()}
    if SQUAD_SYSTEM_TOKEN:
        tokens.add(SQUAD_SYSTEM_TOKEN)
    return tokens


def _load_bus_tokens() -> dict[str, MCPAuthContext]:
    global _local_token_cache
    if _local_token_cache:
        return _local_token_cache
    cache: dict[str, MCPAuthContext] = {}
    try:
        raw = json.loads(BUS_TOKENS_PATH.read_text())
        items = raw if isinstance(raw, list) else [raw]
        for item in items:
            token = item.get("token", "")
            if not token or not item.get("active"):
                continue
            project = item.get("project") or None
            cache[token] = MCPAuthContext(
                token=token,
                tenant_id=project,
                is_system=project is None,
                source="bus_tokens",
            )
    except Exception:
        return {}
    _local_token_cache = cache
    return cache


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
            if project and active:
                ctx = MCPAuthContext(token=token, tenant_id=project, is_system=False, source="cloudflare_kv")
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
            "name": "onboard",
            "description": "Get onboarding briefing for new agents joining the Mumega system. Call this first when connecting to learn the team, architecture, tools, and rules.",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "agent_name": {"type": "string", "description": "Your name (e.g. cyrus, hadi)"},
                },
                "required": [],
            },
        },
    ]


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
                        "text": f"[{AGENT_SELF} -> {to}] {text}",
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
            pattern = f"{_prefix(project_scope)}:agent:*"
            agents: set[str] = set()
            cursor = 0
            while True:
                cursor, keys = await r.scan(cursor, match=pattern, count=100)
                for k in keys:
                    agents.add(k.split(":")[-1])
                if cursor == 0:
                    break
            if auth.is_system and not project_scope:
                cursor = 0
                while True:
                    cursor, keys = await r.scan(
                        cursor, match="sos:stream:sos:channel:private:agent:*", count=100
                    )
                    for k in keys:
                        agents.add(k.split(":")[-1])
                    if cursor == 0:
                        break
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

        # --- onboard ---
        elif name == "onboard":
            agent_name = args.get("agent_name", "new-agent")
            # Get onboarding briefing from Mirror
            briefing = ""
            try:
                resp = requests.post(
                    f"{MIRROR_URL}/search",
                    json={"query": "onboarding team architecture getting-started", "top_k": 1, "agent_filter": "system"},
                    headers={"Authorization": f"Bearer {MIRROR_TOKEN}"},
                    timeout=10,
                )
                results = resp.json()
                if results:
                    first = results[0] if isinstance(results, list) else results.get("results", [{}])[0]
                    briefing = first.get("text", first.get("raw_text", ""))
            except Exception:
                pass

            if not briefing:
                briefing = """Welcome to Mumega. You are connecting to an AI operating system for businesses.

Team: Athena (queen, GPT-5.4), Kasra (builder, Opus), Mumega (orchestrator, Opus), Codex (infra, GPT-5.4)
Squads: seo, dev, outreach, content, ops — serve any project
Tools: send/inbox (async messaging), ask (sync agent call), peers (list agents), tasks, memory

Start by calling: peers (see who's online), then task_list (see current work).
Read docs at mumega-docs.pages.dev for full reference."""

            # Register arrival on the bus
            await r.xadd(
                "sos:stream:sos:channel:global",
                {"type": "agent_joined", "source": f"agent:{agent_name}", "payload": json.dumps({"text": f"{agent_name} has joined the team"})},
            )

            return _text(f"Welcome {agent_name}!\n\n{briefing}")

        else:
            return _text(f"Unknown tool: {name}")

    except Exception as e:
        log.exception("Tool %s failed", name)
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
        "response_types_supported": ["code"],
        "grant_types_supported": ["authorization_code"],
        "code_challenge_methods_supported": ["S256"],
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
    tokens = list(_valid_tokens())
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
        return _jsonrpc_ok(msg_id, {"tools": get_tools()})
    if method == "tools/call":
        tool_name = params.get("name", "")
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
