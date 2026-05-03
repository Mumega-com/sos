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
import hashlib
import hmac
import json
import logging
import os
import subprocess
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urlencode
from uuid import uuid4

import redis.asyncio as aioredis
import requests
from fastapi import FastAPI, HTTPException, Request, Response
from fastapi.responses import JSONResponse
from sse_starlette.sse import EventSourceResponse
from sos.clients.billing import AsyncBillingClient
from sos.clients.integrations import AsyncIntegrationsClient
from sos.clients.saas import AsyncSaasClient, SaasClient
from sos.bus import envelope as bus_envelope
from sos.clients.squad import SquadClient
from sos.contracts.messages import SendMessage
from sos.kernel.bus import enforce_scope
from sos.mcp.customer_tools import (
    BLOCKED_TOOLS,
    CUSTOMER_TOOLS,
    IDENTITY_TOOLS,
    TOOL_MAPPING,
    get_tools_for_role,
    get_tools_for_tier,
    is_customer_tool,
    is_tool_allowed_for_role,
    is_tool_allowed_for_tier,
)
from sos.kernel.auth import verify_bearer as _auth_verify_bearer
from sos.kernel.audit_chain import AuditChainEvent, emit_audit as _emit_audit

# ---------------------------------------------------------------------------
# Mirror kernel — direct import (no HTTP to :8844)
# PYTHONPATH=/home/mumega is set in sos-mcp-sse.service so this import works.
# psycopg2 is sync — all calls must be wrapped in run_in_executor.
# ---------------------------------------------------------------------------
import sys as _sys
import concurrent.futures as _futures

_sys.path.insert(0, "/home/mumega")
from mirror.kernel.db import get_db as _get_mirror_db  # noqa: E402
from mirror.kernel.embeddings import get_embedding as _get_mirror_embedding  # noqa: E402

try:
    _mirror_db = _get_mirror_db()  # singleton connection pool
except Exception as _e:
    import logging as _logging
    _logging.getLogger(__name__).warning("Mirror DB unavailable at startup: %s — recall will return empty", _e)
    _mirror_db = None
_mirror_executor = _futures.ThreadPoolExecutor(
    max_workers=4, thread_name_prefix="mirror-db"
)

# Squad system token now resolves from env — same pattern as agents/join
# after v0.4.6 P1-05. Default mirrors sos.services.squad.auth.SYSTEM_TOKEN
# so existing deployments keep working without env changes.
SQUAD_SYSTEM_TOKEN = os.environ.get("SOS_SQUAD_SYSTEM_TOKEN") or os.environ.get(
    "SOS_SYSTEM_TOKEN", "sk-sos-system"
)

_squad_client = SquadClient(token=SQUAD_SYSTEM_TOKEN)
_saas_client = SaasClient()
_async_saas_client = AsyncSaasClient()
_async_billing_client = AsyncBillingClient()
_async_integrations_client = AsyncIntegrationsClient()


def _audit_tool_call(
    tenant: str,
    tool: str,
    actor: str = "",
    ip: str = "",
    details: dict | None = None,
) -> None:
    """Fire-and-forget audit write. Never blocks the request path.

    Uses the async client from the running event loop when available;
    falls back to the sync client if called outside an async context.
    """
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        loop = None
    if loop is not None:
        loop.create_task(
            _async_saas_client.log_tool_call(tenant, tool, actor=actor, ip=ip, details=details)
        )
        return
    try:
        _saas_client.log_tool_call(tenant, tool, actor=actor, ip=ip, details=details)
    except Exception as exc:
        log.warning("audit log_tool_call failed: %s", exc)


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
# F-17: admin endpoints (e.g. /admin/outbox/status) require admin-typed token.
MIRROR_ADMIN_TOKEN: str = os.environ.get("MIRROR_ADMIN_TOKEN", "")
PORT: int = int(os.environ.get("SOS_MCP_PORT", "6070"))

MIRROR_HEADERS = {
    "Authorization": f"Bearer {MIRROR_TOKEN}",
    "Content-Type": "application/json",
}
MIRROR_ADMIN_HEADERS = {
    "Authorization": f"Bearer {MIRROR_ADMIN_TOKEN}",
    "Content-Type": "application/json",
}
RATE_LIMIT_PER_MINUTE: int = int(os.environ.get("MCP_RATE_LIMIT_PER_MINUTE", "60"))

# WARN-S013-005 fix: single module-level constant (was duplicated in handle_tool body).
# All write-path tools — rate-limited + audit-emitted.
MCP_WRITE_TOOLS: frozenset[str] = frozenset({
    "send", "broadcast", "remember", "squad_remember",
    "task_create", "task_update", "request",
})

# WARN-S013-004 fix: module-level sync Redis client for _enforce_rate_limit.
# Creating a new client per call was fine on localhost but pressure point at scale.
import redis as _redis_sync_mod
_sync_redis = _redis_sync_mod.Redis(
    host="localhost",
    port=6379,
    password=REDIS_PASSWORD,
    decode_responses=True,
    socket_keepalive=True,
)
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
        url = (
            f"redis://:{REDIS_PASSWORD}@localhost:6379/0"
            if REDIS_PASSWORD
            else "redis://localhost:6379/0"
        )
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
        # S016 Track A — sign_in() pins active_project for the session;
        # it overrides the token-default tenant_id when set.
        if self.is_system:
            return None
        return self.active_project or self.tenant_id

    agent_name: str = ""  # Explicit agent identity from token
    scope: str = ""  # "customer" for external customers; empty for internal agents
    plan: str | None = None  # starter | growth | scale | None (system)
    role: str = "admin"  # admin | editor | viewer
    dev_mode: bool = False  # LOCK-TENANT-C: True while knight is activating (first-call window)
    # S016 Track A — per-session active project (set by sign_in, cleared by sign_out).
    # When None, project_scope falls back to tenant_id (token-default project).
    active_project: str | None = None
    # S016 Track A — BYOA identity from Inkwell D1 (lazy-loaded on first sign_in).
    identity_id: str | None = None
    # S017 G2 — IdP-confirmed identity fields, set on the worker_oauth path
    # when the dispatcher passes X-Email / X-Email-Verified / X-Agent-Identity-Id.
    # /v2/me surfaces these to inkwell-api /oauth-complete which gates the
    # portal-account bridge on email_verified === true (§2.7).
    email: str | None = None
    email_verified: bool = False
    agent_identity_id: str | None = None

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


# S018 Track E — read agent's specialist slugs from mumega.com/agents/<a>/specialists.yml.
# Best-effort: never raises. Missing or malformed file => empty list.
_SPECIALISTS_REPO_ROOT = Path(
    os.getenv("MUMEGA_COM_REPO", "/home/mumega/mumega.com")
)


def _read_specialist_slugs(agent: str) -> list[str]:
    path = _SPECIALISTS_REPO_ROOT / "agents" / agent / "specialists.yml"
    if not path.exists():
        return []
    try:
        txt = path.read_text(encoding="utf-8")
    except Exception:
        return []
    slugs: list[str] = []
    in_list = False
    for raw in txt.splitlines():
        line = raw.rstrip()
        stripped = line.lstrip()
        if not stripped or stripped.startswith("#"):
            continue
        if stripped.startswith("specialists:"):
            in_list = True
            continue
        if not in_list:
            continue
        if line.startswith("  - "):
            kv = line[4:].split(":", 1)
            if len(kv) == 2 and kv[0].strip() == "slug":
                slugs.append(kv[1].strip().strip('"').strip("'"))
        elif not line.startswith(" "):
            in_list = False
    return slugs


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
    return bus_envelope.build(
        msg_type=msg_type,
        source=source,
        target=target,
        text=content,
        project=PROJECT or None,
    )


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
                    log.info(
                        f"tokens.json changed (mtime {self._mtime:.1f} -> {current_mtime:.1f}), reloading"
                    )
                    self._reload()
                    self._mtime = current_mtime
            except OSError:
                # File doesn't exist, keep current cache
                pass
            self._last_check = now

        return self._cache

    def _reload(self) -> None:
        """Reload tokens from file. Cache is keyed by SHA-256 token hash."""
        cache: dict[str, MCPAuthContext] = {}
        try:
            raw = json.loads(BUS_TOKENS_PATH.read_text())
            items = raw if isinstance(raw, list) else [raw]
            for item in items:
                if not item.get("active"):
                    continue
                # Prefer stored token_hash; fall back to hashing raw token for
                # entries that haven't been migrated yet.
                stored_hash = item.get("token_hash", "")
                # Normalize: strip "sha256:" prefix so lookup keys are always plain hex
                if stored_hash.startswith("sha256:"):
                    stored_hash = stored_hash[len("sha256:"):]
                raw_token = item.get("token", "")
                if stored_hash:
                    hash_key = stored_hash
                elif raw_token:
                    hash_key = hashlib.sha256(raw_token.encode()).hexdigest()
                else:
                    continue
                project = item.get("project") or None
                agent_name = item.get("agent", "")
                scope = item.get("scope", "")
                plan = item.get("plan") or None
                role = item.get("role", "admin")
                cache[hash_key] = MCPAuthContext(
                    token=hash_key,  # store hash, never the raw token
                    tenant_id=project,
                    is_system=project is None,
                    source="bus_tokens",
                    agent_name=agent_name,
                    scope=scope,
                    plan=plan,
                    role=role,
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
    tokens = {
        token.strip()
        for token in os.environ.get("MCP_ACCESS_TOKENS", "").split(",")
        if token.strip()
    }
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
            plan = payload.get("plan") or None
            role = payload.get("role", "admin")
            if active and (project or agent_name):
                ctx = MCPAuthContext(
                    token=token,
                    tenant_id=project,
                    is_system=project is None,
                    source="cloudflare_kv",
                    agent_name=agent_name,
                    scope=scope,
                    plan=plan,
                    role=role,
                )
            else:
                ctx = None
    except Exception:
        ctx = None
    _cloudflare_token_cache[token] = (now, ctx)
    return ctx


def _resolve_token_context(token: str) -> MCPAuthContext | None:
    """Resolve a raw token string to an MCPAuthContext.

    For the bus-token path, this now delegates to sos.services.auth.verify_bearer
    (single source of truth).  The URL-based /sse/<token> flow constructs a
    synthetic ``Authorization: Bearer <token>`` header and calls verify_bearer,
    which handles env-var system tokens, sha256 token_hash, bcrypt, and raw token
    fallback — without any direct tokens.json reads here.

    Lookup order:
      1. MCP_ACCESS_TOKENS / SQUAD_SYSTEM_TOKEN env vars (system path, no file I/O)
      2. sos.services.auth.verify_bearer (bus tokens via canonical auth module)
      3. Squad API keys DB (squad_api_keys table)
      4. Cloudflare KV (edge-provisioned tokens)
    """
    if not token:
        return None
    # 1. Env-var system tokens checked first — fast path, no file I/O.
    if token in _system_tokens():
        return MCPAuthContext(token=token, tenant_id=None, is_system=True, source="system")
    # 2. Bus tokens via canonical auth module — replaces direct _load_bus_tokens() lookup.
    auth_ctx = _auth_verify_bearer(f"Bearer {token}")
    if auth_ctx is not None:
        # Map AuthContext → MCPAuthContext, preserving all attributes read by handlers.
        token_hash = hashlib.sha256(token.encode()).hexdigest()
        # Try to pull richer metadata (scope, plan, role) from the local cache built
        # by _TokenCacheWithHotReload so we don't lose those fields.
        local_bus = _load_bus_tokens().get(token_hash)
        if local_bus:
            # S016 Track A — BYOA lookups need the raw token to send to inkwell-api,
            # which hashes it server-side. The cache stores token=hash for safety, so
            # we COPY the cached context and overwrite .token with the raw candidate.
            # Mutating the cached object directly would poison subsequent requests.
            from dataclasses import replace as _replace
            return _replace(local_bus, token=token)
        # Fallback: construct MCPAuthContext from AuthContext alone.
        return MCPAuthContext(
            token=token,
            tenant_id=auth_ctx.project,
            is_system=auth_ctx.is_system,
            source="bus_tokens",
            agent_name=auth_ctx.agent or "",
            role="admin" if auth_ctx.is_admin else "viewer",
        )
    # 3. Squad API keys (resolved over HTTP via SquadClient — was an
    #    in-process DB lookup before v0.4.7 P1-01).
    try:
        squad_auth = _squad_client.verify_token(token)
    except Exception:
        squad_auth = None
    if squad_auth and squad_auth.get("ok"):
        return MCPAuthContext(
            token=token,
            tenant_id=squad_auth.get("tenant_id"),
            is_system=bool(squad_auth.get("is_system")),
            source="squad_api_keys",
        )
    # 4. Cloudflare KV.
    return _lookup_cloudflare_token(token)


def _require_same_tenant_agent(auth: MCPAuthContext, requested: str | None) -> str:
    if auth.is_system:
        return requested or AGENT_SELF
    # REMOVED 2026-04-26 (S013 P0 BLOCK-1, Athena adversarial): per-agent bypass.
    # All non-system tokens MUST go through hmac.compare_digest(requested, tenant_agent).
    # DO NOT re-add a presence-check shortcut. agent_name being truthy is a string
    # check, not cryptographic binding. If cross-agent send becomes a legitimate
    # requirement, add an explicit `scope = "cross_agent"` capability claim in the
    # token, never an implicit bypass on agent_name. — Athena gate-keeper 2026-04-26
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
                    "agent": {
                        "type": "string",
                        "description": "Agent name (e.g. athena, kasra, worker)",
                    },
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
            "name": "squad_remember",
            "description": "Store a memory scoped to a specific squad",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "squad_id": {"type": "string", "description": "Squad identifier"},
                    "text": {"type": "string", "description": "Memory text to store"},
                    "agent_id": {"type": "string", "description": "Agent storing the memory (optional)", "default": ""},
                },
                "required": ["squad_id", "text"],
            },
        },
        {
            "name": "squad_recall",
            "description": "Semantic search across memories scoped to a specific squad",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "squad_id": {"type": "string", "description": "Squad identifier"},
                    "query": {"type": "string", "description": "Search query"},
                    "limit": {"type": "integer", "default": 10},
                },
                "required": ["squad_id", "query"],
            },
        },
        {
            "name": "search_code",
            "description": "Semantic search across synced code nodes (functions, classes, methods). Returns file paths and line numbers for matching code.",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "Natural language description of the code you're looking for",
                    },
                    "repo": {
                        "type": "string",
                        "description": "Filter by repo name (e.g. torivers-staging-dev). Omit to search all repos.",
                    },
                    "kind": {
                        "type": "string",
                        "description": "Filter by node kind: function, class, method, etc.",
                    },
                    "top_k": {
                        "type": "integer",
                        "default": 5,
                        "description": "Number of results to return",
                    },
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
                    "status": {
                        "type": "string",
                        "default": "queued",
                        "description": "Filter: queued, claimed, in_progress, blocked, all",
                    },
                },
            },
        },
        {
            "name": "onboard",
            "description": "Onboard a new agent or customer. For agents: generates tokens, registers in Squad Service, sets up routing, announces on bus — full self-onboarding in one call. For customers (system token only): creates tokens, squad, genesis task.",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "agent_name": {
                        "type": "string",
                        "description": "Your name (required for agent onboarding)",
                    },
                    "mode": {
                        "type": "string",
                        "description": "Mode: 'agent' (default) or 'customer'",
                    },
                    "slug": {
                        "type": "string",
                        "description": "Customer slug (required for mode=customer)",
                    },
                    "label": {
                        "type": "string",
                        "description": "Customer display name (required for mode=customer)",
                    },
                    "email": {
                        "type": "string",
                        "description": "Customer email (optional, for mode=customer)",
                    },
                    "model": {
                        "type": "string",
                        "description": "LLM model (claude, gpt, gemini, gemma) — agent mode",
                    },
                    "role": {
                        "type": "string",
                        "description": "Agent role (builder, strategist, executor, researcher) — agent mode",
                    },
                    "skills": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "Skills this agent provides — agent mode",
                    },
                    "routing": {
                        "type": "string",
                        "description": "How to wake this agent (mcp, tmux, openclaw) — agent mode",
                    },
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
                    "description": {
                        "type": "string",
                        "description": "What you need done (e.g. 'SEO audit for my dental site', 'Build a landing page')",
                    },
                    "priority": {
                        "type": "string",
                        "description": "Priority: low, medium, high (default: medium)",
                    },
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
        {
            "name": "outbox_status",
            "description": "Aggregate outbox/queue health across substrates (Mirror receipts, SOS bus events, Inkwell-incoming). S024 F-17. Returns per-substrate {kind, pending, in_flight, dlq} with `kind` ∈ real|best_effort|not_configured|error. Pages a substrate as 'real' only if it durably persists pending work. Use this to diagnose audit-write backlog or DLQ growth.",
            "inputSchema": {
                "type": "object",
                "properties": {},
            },
        },
        {
            "name": "code_mode",
            "description": "Execute a Python snippet in a restricted sandbox with pre-bound SOS tools exposed as `tools.<name>(...)`. Returns the final expression's value plus captured stdout. Intended for token-efficient tool-call batching — the Cloudflare Code Mode pattern.",
            "inputSchema": {
                "type": "object",
                "required": ["code"],
                "properties": {
                    "code": {
                        "type": "string",
                        "description": "Python snippet to execute. Last expression becomes the return value. Available names: `tools` (SimpleNamespace) and a small allowlist of builtins (int, str, list, dict, ...). Imports are blocked.",
                    },
                    "timeout_s": {
                        "type": "number",
                        "minimum": 0.1,
                        "maximum": 10.0,
                        "default": 5.0,
                    },
                },
            },
        },
    ]


# ---------------------------------------------------------------------------
# Agent Status Registry (Redis-backed)
# ---------------------------------------------------------------------------

KNOWN_AGENTS = {
    "kasra": {"type": "tmux", "model": "Claude Opus/Sonnet", "role": "Builder"},
    "loom": {"type": "tmux", "model": "Claude Opus 4.7", "role": "SOS Protocol Custodian — bus, MCP, sessions, tokens, memory scoping, minting authority (v1)"},
    "mumega": {"type": "tmux", "model": "Claude Opus", "role": "Orchestrator"},
    "codex": {"type": "tmux", "model": "GPT-5.4", "role": "Infra + Security"},
    "mumcp": {"type": "tmux", "model": "Claude Sonnet", "role": "MumCP — WordPress + Elementor"},
    "mumega-web": {"type": "tmux", "model": "Claude Sonnet", "role": "Website"},
    "athena": {"type": "tmux", "model": "Claude Sonnet", "role": "Architecture Review"},
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

        if info["type"] == "tmux":
            # Check tmux session
            try:
                result = subprocess.run(
                    ["tmux", "has-session", "-t", name],
                    capture_output=True,
                    timeout=3,
                )
                if result.returncode == 0:
                    # Check if at prompt (idle) or working (busy)
                    cap = subprocess.run(
                        ["tmux", "capture-pane", "-t", name, "-p"],
                        capture_output=True,
                        text=True,
                        timeout=3,
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

        statuses.append(
            {
                "agent": name,
                "type": info["type"],
                "model": info["model"],
                "role": info["role"],
                "status": status,
            }
        )
    return statuses


def _get_service_statuses_sync() -> list[dict[str, str]]:
    """Check systemd service statuses (sync, runs in executor)."""
    services = [
        "sos-mcp-sse",
        "sos-squad",
        "sovereign-loop",
        "calcifer",
        "agent-wake-daemon",
        "bus-bridge",
        "openclaw-gateway",
        "kasra-agent-watchdog",
        "mumcp-agent-watchdog",
    ]
    statuses = []
    for svc in services:
        try:
            result = subprocess.run(
                ["systemctl", "--user", "is-active", f"{svc}.service"],
                capture_output=True,
                text=True,
                timeout=3,
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
                capture_output=True,
                text=True,
                timeout=3,
                env=env,
            )
            result[label] = proc.stdout.strip() or "unknown"
        except Exception:
            result[label] = "unknown"
    return result


# ---------------------------------------------------------------------------
# Outbox status aggregator (S024 F-17)
# ---------------------------------------------------------------------------
#
# Aggregates audit-write outbox health across the substrates that ought to
# have one. Pages each substrate as one of:
#
#   - real:           durable pending row backed by a transactional store;
#                     numbers are authoritative.
#   - best_effort:    in-process queue without crash-survival; numbers are
#                     a snapshot, not a guarantee.
#   - not_configured: the substrate has no outbox today; reports zeros so
#                     F-17 dashboards don't false-page.
#   - error:          known-configured outbox failed to respond; numbers
#                     are the last-known shape (zeros) and `last_error`
#                     surfaces the failure mode.
#
# Only Mirror is `real` today (F-16 landed mig 052 + NativeSqlOutbox). SOS
# bus and Inkwell-incoming are explicit `not_configured` placeholders so
# the contract is named — when their outboxes ship they replace the
# placeholder branch with a real query.


OUTBOX_ALERT_THRESHOLDS = {
    "dlq_count": 10,
    "pending_count": 1000,
    "stale_pending_seconds": 3600,
}


def _mirror_outbox_status_sync() -> dict[str, Any]:
    """Query mirror's /admin/outbox/status and project to the F-17 component
    schema (per v0.5 brief §6.6: `dlq_count`, `pending_count`, `backend`).
    Intentionally swallows exceptions and emits `backend='error'` so a
    Mirror outage doesn't break the aggregator."""
    if not MIRROR_ADMIN_TOKEN:
        return {
            "dlq_count": 0,
            "pending_count": 0,
            "backend": "not_configured",
            "last_error": "MIRROR_ADMIN_TOKEN not set in SOS MCP env",
        }
    try:
        resp = requests.get(
            f"{MIRROR_URL}/admin/outbox/status",
            headers=MIRROR_ADMIN_HEADERS,
            timeout=5,
        )
        resp.raise_for_status()
        data = resp.json() or {}
    except Exception as exc:
        return {
            "dlq_count": 0,
            "pending_count": 0,
            "backend": "error",
            "last_error": f"{type(exc).__name__}: {exc}",
        }

    if not data.get("enabled"):
        return {
            "dlq_count": 0,
            "pending_count": 0,
            "backend": "not_configured",
            "last_error": "MIRROR_OUTBOX_ENABLED=0 (F-16 build present, flag off)",
        }
    return {
        "dlq_count": int(data.get("dlq_count", 0)),
        "pending_count": int(data.get("pending_count", 0)),
        "backend": data.get("backend") or "unknown",
    }


def _sos_outbox_status_sync() -> dict[str, Any]:
    """SOS bus has no audit-write outbox today (Redis stream is the
    primary store; receipt durability is owned by downstream consumers).
    Reported as `best_effort` per v0.5 brief F-17 placeholder contract."""
    return {
        "dlq_count": 0,
        "pending_count": 0,
        "backend": "best_effort",
        "last_error": "SOS bus outbox not implemented; carry tracked in S024 Track F P2",
    }


def _inkwell_outbox_status_sync() -> dict[str, Any]:
    """Inkwell-incoming outbox (CF Queues) is `not_configured` today;
    ships as part of S025+ ingestion-hardening scope per brief §6.7."""
    return {
        "dlq_count": 0,
        "pending_count": 0,
        "backend": "not_configured",
        "last_error": "Inkwell-incoming outbox not implemented; deferred to S025+ per brief §6.7",
    }


def _aggregate_outbox_status_sync() -> dict[str, Any]:
    """Returns the F-17 contract shape (v0.5 brief §6.6):

      {
        "components": {
          "mirror": {dlq_count, pending_count, backend},
          "sos": {...},
          "inkwell_incoming": {...},
        },
        "alert_thresholds": {dlq_count, pending_count, stale_pending_seconds},
      }
    """
    return {
        "components": {
            "mirror": _mirror_outbox_status_sync(),
            "sos": _sos_outbox_status_sync(),
            "inkwell_incoming": _inkwell_outbox_status_sync(),
        },
        "alert_thresholds": dict(OUTBOX_ALERT_THRESHOLDS),
    }


# ---------------------------------------------------------------------------
# Tool execution (async)
# ---------------------------------------------------------------------------


def _text(t: str) -> dict[str, Any]:
    return {"content": [{"type": "text", "text": t}]}


# Safe, read-only tools exposed inside code_mode's `tools` namespace. Keep this
# set narrow — every name here is callable from a client-supplied snippet.
_CODE_MODE_SAFE_TOOLS: frozenset[str] = frozenset(
    {"status", "peers", "memories", "recall", "search_code", "task_board", "task_list"}
)


def _make_code_mode_sync_wrapper(tool_name: str, auth: MCPAuthContext) -> Any:
    """Build a sync callable that forwards kwargs to ``handle_tool(tool_name, ...)``.

    Runs the coroutine to completion using the event loop the helper itself
    is running on. If we're in an event loop (normal case — ``handle_tool``
    is async), schedule via ``run_coroutine_threadsafe`` and block the
    snippet's worker thread until done.
    """

    def _sync(**kw: Any) -> Any:
        coro = handle_tool(tool_name, kw, auth)
        try:
            loop = asyncio.get_event_loop()
        except RuntimeError:
            return asyncio.run(coro)
        import concurrent.futures  # noqa: PLC0415 - local import keeps hot path cold

        fut = asyncio.run_coroutine_threadsafe(coro, loop)
        try:
            return fut.result(timeout=10.0)
        except concurrent.futures.TimeoutError:
            return {"error": "tool_call_timeout"}

    return _sync


async def _handle_code_mode(args: dict[str, Any], auth: MCPAuthContext) -> dict[str, Any]:
    """Execute a Python snippet via ``sos.mcp.code_mode.execute_snippet``.

    Exposes a narrow, read-only slice of ``handle_tool`` as the ``tools``
    namespace. Empty ``code`` is rejected. The return shape is a standard
    MCP content block wrapping a JSON body (``value``, ``stdout``,
    ``stderr``, ``duration_ms``, ``token_estimate``).
    """
    from sos.mcp.code_mode import execute_snippet  # noqa: PLC0415

    code = str(args.get("code", ""))
    if not code.strip():
        return _text("error: empty code")
    timeout_s = float(args.get("timeout_s", 5.0))

    tools_map: dict[str, Any] = {
        tn: _make_code_mode_sync_wrapper(tn, auth) for tn in _CODE_MODE_SAFE_TOOLS
    }

    result = await execute_snippet(code=code, tools=tools_map, timeout_s=timeout_s)
    payload = {
        "value": repr(result["value"]),
        "stdout": result["stdout"],
        "stderr": result["stderr"],
        "duration_ms": result["duration_ms"],
        "token_estimate": result["token_estimate"],
    }
    return _text(json.dumps(payload))


# ---------------------------------------------------------------------------
# S016 Track A — BYOA identity helpers
# ---------------------------------------------------------------------------

INKWELL_API_URL = os.environ.get("INKWELL_API_URL", "https://mumega.com")
INTERNAL_API_SECRET = os.environ.get("INTERNAL_API_SECRET", "")


async def _inkwell_lookup_connection(token: str) -> dict[str, Any] | None:
    """Look up a BYOA identity + memberships by raw connection token.

    Calls Inkwell's POST /api/agents/connections/lookup which hashes the token
    server-side and returns the identity row joined with the connection.
    Returns None on lookup miss or any non-2xx.
    """
    if not INTERNAL_API_SECRET:
        log.warning("BYOA lookup disabled — INTERNAL_API_SECRET unset")
        return None
    import httpx
    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            resp = await client.post(
                f"{INKWELL_API_URL}/api/agents/connections/lookup",
                headers={"Authorization": f"Bearer {INTERNAL_API_SECRET}"},
                json={"token": token},
            )
            if resp.status_code == 200:
                return resp.json()
            if resp.status_code == 404:
                return None
            log.warning("BYOA lookup unexpected status=%s", resp.status_code)
            return None
    except Exception as exc:  # noqa: BLE001 — network/timeout — fail closed-ish
        log.warning("BYOA lookup error: %s", exc)
        return None


def _memberships_from_lookup(info: dict[str, Any] | None) -> list[dict[str, Any]]:
    """Extract the memberships array from a /connections/lookup response."""
    if not info:
        return []
    raw = info.get("memberships") or []
    return raw if isinstance(raw, list) else []


async def _push_tools_list_changed(session_id: str | None) -> None:
    """Notify the client that the available tool list has changed.

    MCP spec: notifications/tools/list_changed has no params. The client should
    re-call tools/list to pick up the new set. Safe to call when session_id
    is None (Streamable HTTP) — it's a no-op.
    """
    if not session_id:
        return
    queue = _sessions.get(session_id)
    if not queue:
        return
    await queue.put({
        "jsonrpc": "2.0",
        "method": "notifications/tools/list_changed",
        "params": {},
    })


async def _handle_my_profile(auth: MCPAuthContext) -> dict[str, Any]:
    info = await _inkwell_lookup_connection(auth.token)
    if not info or not info.get("identity"):
        return _text(
            "Your token isn't bound to a BYOA identity yet. "
            "Sign in via mumega.com/dashboard/login to mint your identity."
        )
    ident = info["identity"]
    return _text(json.dumps({
        "identity_id": ident.get("id"),
        "name": ident.get("person_name"),
        "email": ident.get("person_email"),
        "qnft_url": ident.get("qnft_url"),
        "google_id": bool(ident.get("google_id")),
        "github_id": bool(ident.get("github_id")),
        "active_project": auth.active_project,
        "default_project": auth.tenant_id,
    }, indent=2))


async def _handle_list_projects(auth: MCPAuthContext) -> dict[str, Any]:
    info = await _inkwell_lookup_connection(auth.token)
    if not info or not info.get("identity"):
        return _text("No BYOA identity bound to this token.")
    auth.identity_id = info["identity"]["id"]
    memberships = _memberships_from_lookup(info)
    if not memberships:
        return _text("You don't have access to any projects yet.")
    rows = [
        {"project": m.get("project_id"), "role": m.get("role")}
        for m in memberships
    ]
    return _text(json.dumps({"projects": rows, "count": len(rows)}, indent=2))


async def _handle_sign_in(
    auth: MCPAuthContext,
    args: dict[str, Any],
    session_id: str | None,
) -> dict[str, Any]:
    project = str(args.get("project") or "").strip()
    if not project:
        return _text("sign_in requires a project slug. Use list_projects to see options.")
    info = await _inkwell_lookup_connection(auth.token)
    if not info or not info.get("identity"):
        return _text("No BYOA identity bound to this token.")
    auth.identity_id = info["identity"]["id"]
    memberships = _memberships_from_lookup(info)
    allowed = {m.get("project_id") for m in memberships}
    if project not in allowed:
        return _text(
            f"You don't have access to project '{project}'. "
            f"Available: {sorted(p for p in allowed if p)}"
        )
    auth.active_project = project
    role_for_project = next(
        (m.get("role") for m in memberships if m.get("project_id") == project),
        "viewer",
    )
    auth.role = role_for_project or "viewer"
    if session_id:
        _session_signed_in.add(session_id)
        await _push_tools_list_changed(session_id)
    return _text(json.dumps({
        "ok": True,
        "active_project": project,
        "role": auth.role,
    }, indent=2))


async def _handle_sign_out(
    auth: MCPAuthContext,
    session_id: str | None,
) -> dict[str, Any]:
    auth.active_project = None
    if session_id:
        _session_signed_in.discard(session_id)
        await _push_tools_list_changed(session_id)
    return _text(json.dumps({"ok": True, "signed_out": True}, indent=2))


async def _handle_invite(
    auth: MCPAuthContext,
    args: dict[str, Any],
) -> dict[str, Any]:
    """S016 Track B — admin generates an invite code for the active project.

    POSTs to inkwell-api /api/invites with INTERNAL_API_SECRET; returns the
    join URL the admin can share. Owner/admin role is enforced by ROLE_TOOLS
    upstream (tools/list filtering); we double-check here in case a stale
    client calls invite without listing first.
    """
    if not auth.active_project:
        return _text(
            "Sign in to a project first. invite() always generates codes for "
            "the project you're currently signed into."
        )
    if auth.role not in ("admin", "owner"):
        return _text(
            f"invite() is admin/owner only. Your role on {auth.active_project} is "
            f"'{auth.role or 'viewer'}'."
        )
    if not INTERNAL_API_SECRET:
        return _text("invite() unavailable — INTERNAL_API_SECRET not configured.")
    if not auth.identity_id:
        # Backfill identity_id from connection lookup so created_by is non-null.
        info = await _inkwell_lookup_connection(auth.token)
        if info and info.get("identity"):
            auth.identity_id = info["identity"].get("id")
    if not auth.identity_id:
        return _text("Cannot determine your identity — re-sign in.")

    role = str(args.get("role") or "member").strip()
    if role not in ("viewer", "member", "admin", "owner"):
        return _text(f"role must be one of viewer/member/admin/owner. Got: {role}")
    try:
        max_uses = int(args.get("max_uses") or 1)
    except (TypeError, ValueError):
        max_uses = 1
    max_uses = max(1, min(max_uses, 100))

    expires_at: str | None = None
    expires_in_hours = args.get("expires_in_hours")
    if expires_in_hours is not None:
        try:
            hours = int(expires_in_hours)
            if hours > 0:
                from datetime import datetime, timedelta, timezone
                expires_at = (
                    datetime.now(timezone.utc) + timedelta(hours=hours)
                ).isoformat().replace("+00:00", "Z")
        except (TypeError, ValueError):
            pass

    payload = {
        "project_id": auth.active_project,
        "role": role,
        "max_uses": max_uses,
        "created_by": auth.identity_id,
        "expires_at": expires_at,
    }

    import httpx
    try:
        async with httpx.AsyncClient(timeout=8.0) as client:
            resp = await client.post(
                f"{INKWELL_API_URL}/api/invites",
                headers={"Authorization": f"Bearer {INTERNAL_API_SECRET}"},
                json=payload,
            )
    except Exception as exc:  # noqa: BLE001
        log.warning("invite() POST failed: %s", exc)
        return _text(f"Invite creation failed: {exc}")

    if resp.status_code != 201:
        return _text(
            f"Invite creation failed: HTTP {resp.status_code} {resp.text[:200]}"
        )

    data = resp.json()
    invite = data.get("invite") or {}
    code = invite.get("code")
    if not code:
        return _text(f"Inkwell returned no code: {data}")
    join_url = f"https://mcp.mumega.com/join/{code}"
    return _text(json.dumps({
        "ok": True,
        "code": code,
        "join_url": join_url,
        "project": auth.active_project,
        "role": role,
        "max_uses": max_uses,
        "expires_at": expires_at,
        "share": (
            f"You've been invited to {auth.active_project} as {role}. "
            f"Sign in: {join_url}"
        ),
    }, indent=2))


async def handle_tool(
    name: str,
    args: dict[str, Any],
    auth: MCPAuthContext,
    session_id: str | None = None,
) -> dict[str, Any]:
    loop = asyncio.get_event_loop()
    r = _get_redis()
    project_scope = _scope_project(auth)
    agent_scope = auth.agent_scope

    # S016 Track A — BYOA identity tools (no tenant-write side effects).
    # Dispatched before WRITE_TOOLS rate-limit so unsigned-in users aren't gated.
    if name == "my_profile":
        return await _handle_my_profile(auth)
    if name == "list_projects":
        return await _handle_list_projects(auth)
    if name == "sign_in":
        return await _handle_sign_in(auth, args, session_id)
    if name == "sign_out":
        return await _handle_sign_out(auth, session_id)
    if name == "invite":
        return await _handle_invite(auth, args)

    # Log tool invocation
    await _publish_log("info", "mcp", f"tool:{name} by {agent_scope}", agent=agent_scope)

    # WARN-3 fix (LOCK-MCP-4): emit to audit chain for all write tools.
    # Read tools (inbox/peers/recall) excluded for volume; all WRITE_TOOLS emitted.
    # Fire-and-forget — never blocks the tool call path.
    if name in MCP_WRITE_TOOLS:
        asyncio.create_task(_emit_audit(AuditChainEvent(
            stream_id="mcp",
            actor_id=auth.agent_scope,
            actor_type="agent" if not auth.is_customer else "human",
            action=f"mcp.{name}",
            resource=f"tool:{name}",
            payload={
                "tenant_id": auth.tenant_id,
                "token_prefix": auth.token[:12] if auth.token else "",
                "tool": name,
            },
        )))

    # Capability gate — restrict dangerous tools for non-system tokens
    SYSTEM_ONLY_TOOLS = {"onboard"}  # customer onboard mode requires system token
    # outbox_status reads cross-substrate operator state (Mirror DLQ depth,
    # last_error response text from upstream). Not for tenant tokens.
    # Adversarial-gate hardening: BLOCK-P1-5 (G_S024_F16_F17_kasra_001).
    STRICT_SYSTEM_ONLY_TOOLS = {"outbox_status"}
    WRITE_TOOLS = MCP_WRITE_TOOLS  # module-level constant (WARN-S013-005)
    # Tools classified as read-only — kept as a documented contract, even
    # though flow below only branches on SYSTEM_ONLY_TOOLS/WRITE_TOOLS.
    READ_TOOLS = {  # noqa: F841
        "inbox",
        "peers",
        "recall",
        "memories",
        "task_list",
        "status",
        "search_code",
        "outbox_status",
    }

    if name in SYSTEM_ONLY_TOOLS and not auth.is_system:
        # onboard tool handles its own mode check, but log the attempt
        pass

    # Strict gate — unlike SYSTEM_ONLY_TOOLS above, this set actually denies.
    # outbox_status surfaces operator-only data (DLQ depths, upstream
    # error-text echoes). A tenant token must never reach the dispatch
    # branch.
    if name in STRICT_SYSTEM_ONLY_TOOLS and not auth.is_system:
        return _text(
            f"Tool `{name}` is restricted to system tokens. "
            "Contact your operator for outbox/queue health visibility."
        )

    # LOCK-TENANT-B + LOCK-TENANT-C: dev-tenant activation window for OAuth customers.
    #
    # On every worker_oauth call: enqueue knight mint (idempotent — SET NX inside).
    # On write attempts: block until knight is ready (sos:knight:ready:{tenant_id} set).
    # Enforcement is here at middleware, NOT at caller trust — LOCK-TENANT-C requires it.
    #
    # Production tables guarded: engrams, principals, gtm.principal_state, audit_events.
    # All these are written via the WRITE_TOOLS set (send/broadcast/remember/task_create/...).
    # Read tools pass through; the customer gets immediate value while the knight activates.
    if auth.source == "worker_oauth" and auth.tenant_id and not auth.is_system:
        # LOCK-TENANT-B: fire-and-forget enqueue — never blocks reads
        asyncio.create_task(_ensure_knight_enqueued(r, auth.tenant_id, auth.agent_name))
        # LOCK-TENANT-C: gate writes until knight is activated
        if name in WRITE_TOOLS:
            knight_ready = await r.exists(f"sos:knight:ready:{auth.tenant_id}")
            if not knight_ready:
                return _text(
                    "Your workspace is activating — usually completes within 60 seconds. "
                    "Read tools are available now (try `get_briefing` or `list_signals`). "
                    "Write access (send, remember, task_create) unlocks automatically "
                    "once your knight is ready. "
                    "[starter: https://mcp.mumega.com/upgrade?tier=starter]"
                )

    # Rate limit write operations more strictly for tenant tokens.
    # WARN-1 fix: Redis sliding window — process-local _token_windows dict was
    # bypassable by concurrent connections (single-instance lucky today, latent at scale).
    # WARN-S013-006 fix: key on _rate_key(auth) — worker_oauth contexts share token hash.
    if name in WRITE_TOOLS and not auth.is_system:
        write_rkey = f"sos:rate:write:{_rate_key(auth)}"
        write_count = await r.incr(write_rkey)
        if write_count == 1:
            await r.expire(write_rkey, 60)
        if write_count > 30:  # 30 writes/min for tenant tokens
            await _publish_log(
                "warn", "mcp", f"write rate limit hit by {agent_scope}", agent=agent_scope
            )
            return _text("Rate limit: too many write operations. Try again in a minute.")

    try:
        # --- code_mode ---
        if name == "code_mode":
            return await _handle_code_mode(args, auth)

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
            # System tokens (internal agents connecting via MCP env token) route
            # to the global agent stream with synthetic scope so enforce_scope
            # passes. Tenant tokens still require project scope per v0.9.1.
            if auth.is_system:
                effective_project = project_scope  # None unless PROJECT env set
                effective_tenant = "sos"
            elif not project_scope or not auth.tenant_id:
                return _text(
                    "error: SOS-4005 send requires tenant+project scope; "
                    "system tokens must use a scoped sub-token"
                )
            else:
                effective_project = project_scope
                effective_tenant = auth.tenant_id
            stream = _agent_stream(to, effective_project)
            # v0.4.0-beta.1: v1 "send" message with structured payload. Builds via
            # Pydantic model so all schema invariants (source pattern, target pattern,
            # ISO timestamp, UUID message_id, payload.text max length, content_type
            # enum) are enforced on construction — before any XADD.
            try:
                sendmsg = SendMessage(
                    source=f"agent:{agent_scope}",
                    target=f"agent:{to}",
                    timestamp=SendMessage.now_iso(),
                    message_id=str(uuid4()),
                    payload={"text": text, "content_type": "text/plain"},
                )
            except Exception as ve:
                log.error(f"SendMessage construction failed: {ve}")
                return _text(f"error: SOS-4001 {ve}")
            msg = sendmsg.to_redis_fields()
            msg["tenant_id"] = effective_tenant
            msg["project"] = effective_project or "sos"
            # Pydantic already validated on construction above — no second enforce()
            # pass because the Redis-field shape (payload as JSON string) is not
            # re-parseable by Pydantic without from_redis_fields() (which would be
            # wasted cycles). Validation happened at SendMessage(...) ingress.
            # Scope guard (Phase 2 / W1): defense-in-depth check that both
            # fields landed on the wire envelope before XADD. Cheap and
            # raises on regression.
            enforce_scope(msg)
            mid = await r.xadd(stream, msg)
            await r.publish(_agent_channel(to, effective_project), json.dumps(msg))
            await r.publish(f"sos:wake:{to}", json.dumps(msg))
            # mirror_post("/store", ...) removed — mirror_bus_consumer subscribes
            # to sos:stream:* and writes engrams asynchronously off the stream.
            return _text(f"Sent to {to} (id: {mid})")

        # --- inbox ---
        elif name == "inbox":
            agent = _require_same_tenant_agent(auth, args.get("agent"))
            limit = args.get("limit", 10)
            # Always read BOTH project-scoped and global streams and merge.
            # Root cause: senders with different project scopes land in different
            # streams. A project-scoped token (e.g. Loom/project:sos) would miss
            # messages sent by global-scoped agents (e.g. Kasra/no-project).
            # Fix: collect from project stream + global stream, deduplicate by
            # message ID, sort descending by Redis stream ID (which is ms timestamp),
            # return top `limit` entries.
            seen_ids: set = set()
            all_entries: list = []
            streams_to_check = []
            if project_scope:
                streams_to_check.append(_agent_stream(agent, project_scope))
            streams_to_check.append(_agent_stream(agent, None))  # global always
            streams_to_check.append(_legacy_stream(agent))       # legacy fallback
            for s in streams_to_check:
                try:
                    batch = await r.xrevrange(s, count=limit)
                    for mid, data in batch:
                        if mid not in seen_ids:
                            seen_ids.add(mid)
                            all_entries.append((mid, data))
                except Exception:
                    pass
            # Sort descending by stream ID (lexicographic on Redis IDs = chronological)
            all_entries.sort(key=lambda x: x[0], reverse=True)
            all_entries = all_entries[:limit]
            if not all_entries:
                return _text(f"No messages for {agent}.")
            lines = []
            for mid, data in all_entries:
                parsed = bus_envelope.parse(data)
                lines.append(
                    f"[{data.get('timestamp', '?')}] {parsed['source'] or '?'}: {parsed['text']}"
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
            internal_agents = {
                "sos-mcp-sse",
                "sos-squad",
                "sovereign-loop",
                "calcifer",
                "lifecycle",
                "task-poller",
                "wake-daemon",
            }
            if not auth.is_system:
                agents -= internal_agents
            scope = f"project:{project_scope}" if project_scope else "global"
            sorted_agents = sorted(agents)
            if not sorted_agents:
                return _text("No agents found.")
            # S018 Track E — surface each agent's loadable specialist slugs
            # from agents/<agent>/specialists.yml in mumega.com (absent =
            # empty array). Reads are best-effort; missing/malformed YAML
            # never errors the peers response.
            lines = [f"Agents ({scope}):"]
            for a in sorted_agents:
                slugs = _read_specialist_slugs(a)
                if slugs:
                    lines.append(f"  - {a} (specialists: {', '.join(slugs)})")
                else:
                    lines.append(f"  - {a} (specialists: -)")
            return _text("\n".join(lines))

        # --- broadcast ---
        elif name == "broadcast":
            text = args["text"]
            squad = args.get("squad")
            if squad:
                stream = f"{_prefix(project_scope)}:squad:{squad}"
                channel = f"sos:channel:{'project:' + project_scope + ':' if project_scope else ''}squad:{squad}"
            else:
                stream = f"{_prefix(project_scope)}:broadcast"
                channel = (
                    f"sos:channel:{'project:' + project_scope + ':' if project_scope else ''}global"
                )
            # v0.4.0: broadcast uses v1 "send" type with a channel target.
            try:
                bmsg = SendMessage(
                    source=f"agent:{agent_scope}",
                    target=channel,
                    timestamp=SendMessage.now_iso(),
                    message_id=str(uuid4()),
                    payload={"text": text, "content_type": "text/plain"},
                )
            except Exception as ve:
                log.error(f"broadcast SendMessage construction failed: {ve}")
                return _text(f"error: SOS-4001 {ve}")
            msg = bmsg.to_redis_fields()
            if project_scope:
                msg["project"] = project_scope
            mid = await r.xadd(stream, msg)
            await r.publish(channel, json.dumps(msg))
            return _text(f"Broadcast to {channel} (id: {mid})")

        # --- remember ---
        elif name == "remember":
            ctx = _scoped_context_id(auth, args.get("context"))
            text_to_store = args["text"]

            # Write directly to Mirror DB with embedding (synchronous, immediate readback)
            if _mirror_db is not None:
                try:
                    from uuid import uuid4 as _uuid4
                    from datetime import datetime, timezone

                    embedding = await loop.run_in_executor(
                        _mirror_executor,
                        lambda: [float(x) for x in _get_mirror_embedding(text_to_store)],
                    )

                    engram = {
                        "id": str(_uuid4()),
                        "context_id": ctx,
                        "timestamp": datetime.now(timezone.utc).isoformat(),
                        "series": "memory",
                        "raw_data": json.dumps({"text": text_to_store, "source": "mcp-remember"}),
                        "embedding": embedding,
                        "project": project_scope or "",
                        "workspace_id": project_scope or agent_scope,
                        "owner_type": "agent",
                        "owner_id": agent_scope,
                        "memory_tier": "working",
                        "core_concepts": [args.get("context", "memory")],
                    }

                    await loop.run_in_executor(
                        _mirror_executor,
                        lambda: _mirror_db.upsert_engram(engram),
                    )
                except Exception as exc:
                    log.warning("Direct Mirror write failed (falling back to bus): %s", exc)

            # Also publish to bus stream for any consumers
            try:
                from uuid import uuid4 as _uuid4

                rem_msg = {
                    "type": "send",
                    "source": f"agent:{agent_scope}",
                    "target": f"agent:{agent_scope}",
                    "timestamp": SendMessage.now_iso(),
                    "message_id": str(_uuid4()),
                    "payload": json.dumps(
                        {
                            "text": text_to_store,
                            "content_type": "text/plain",
                            "remember": True,
                        }
                    ),
                }
                if project_scope:
                    rem_msg["project"] = project_scope
                await r.xadd(_agent_stream(agent_scope, project_scope), rem_msg)
            except Exception:
                pass
            return _text(f"Stored: {ctx}")

        # --- recall ---
        elif name == "recall":
            # Phase 2: read from Mirror kernel directly — no HTTP to :8844
            if _mirror_db is None:
                return _text("Mirror DB unavailable — recall disabled")
            query_text = args["query"]
            limit = int(args.get("limit", 5))

            embedding = await loop.run_in_executor(
                _mirror_executor,
                lambda: [float(x) for x in _get_mirror_embedding(query_text)],
            )
            rows = await loop.run_in_executor(
                _mirror_executor,
                lambda: _mirror_db.search_engrams(
                    embedding=embedding,
                    threshold=0.5,
                    limit=limit,
                    project=project_scope,
                    workspace_id=project_scope,  # enforces tenant isolation
                ),
            )
            if not rows:
                return _text("No matching memories.")
            lines = []
            for i, e in enumerate(rows, 1):
                # mirror_match_engrams_v2 returns: context_id, series, raw_data, ts, similarity
                # Text lives in raw_data JSONB or falls back to context_id.
                raw = e.get("raw_data") or {}
                text = raw.get("text", "") or str(e.get("context_id", "?"))
                ts = str(e.get("ts", "?"))[:10]
                lines.append(f"{i}. [{ts}] {str(text)[:200]}")
            return _text("\n".join(lines))

        # --- squad_remember ---
        elif name == "squad_remember":
            squad_id = args["squad_id"]
            text = args["text"]
            # LOCK-MCP-2: bind to calling agent — caller-supplied agent_id is ignored.
            # Cross-agent squad attribution requires an explicit squad membership check.
            agent_id = auth.agent_scope
            context_id = f"squad:{squad_id}:{int(time.time())}"
            project = f"squad:{squad_id}"
            await loop.run_in_executor(
                None,
                mirror_post,
                "/store",
                {
                    "agent": agent_id,
                    "context_id": context_id,
                    "text": text,
                    "project": project,
                },
            )
            return _text(json.dumps({"stored": True, "squad_id": squad_id}))

        # --- squad_recall ---
        elif name == "squad_recall":
            squad_id = args["squad_id"]
            results = await loop.run_in_executor(
                None,
                mirror_post,
                "/search",
                {
                    "query": args["query"],
                    "top_k": args.get("limit", 10),
                    "project": f"squad:{squad_id}",
                },
            )
            if not results:
                return _text("No matching squad memories.")
            lines = []
            for i, e in enumerate(results, 1):
                mem_text = (e.get("raw_data", {}) or {}).get("text", e.get("context_id", "?"))
                lines.append(f"{i}. [{e.get('timestamp', '?')[:10]}] {str(mem_text)[:200]}")
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
            # Redirected from Mirror (retired /tasks) → Squad Service (:8060)
            await loop.run_in_executor(
                None,
                lambda: requests.post(
                    f"{SQUAD_SERVICE_URL}/tasks",
                    headers={"Authorization": f"Bearer {SQUAD_SYSTEM_TOKEN}"},
                    json={
                        "title": args["title"],
                        "description": args.get("description", ""),
                        "assignee": _require_same_tenant_agent(auth, args.get("assignee")),
                        "priority": args.get("priority", "medium"),
                        "agent": agent_scope,
                        "project": project_scope or args.get("project"),
                    },
                    timeout=10,
                ),
            )
            return _text(f"Task created: {args['title']}")

        # --- task_list ---
        elif name == "task_list":
            requested_limit = max(0, min(int(args.get("limit", 20)), 500))
            params: dict[str, Any] = {"limit": requested_limit}
            if args.get("status"):
                params["status"] = args["status"]
            assignee = args.get("assignee")
            if assignee:
                assignee = _require_same_tenant_agent(auth, assignee)
                params["assignee"] = assignee
            if project_scope:
                params["project"] = project_scope
            query = f"?{urlencode(params)}"
            # Redirected from Mirror (retired /tasks) → Squad Service (:8060)
            result = await loop.run_in_executor(
                None,
                lambda: requests.get(
                    f"{SQUAD_SERVICE_URL}/tasks{query}",
                    headers={"Authorization": f"Bearer {SQUAD_SYSTEM_TOKEN}"},
                    timeout=5,
                ).json(),
            )
            tasks = result if isinstance(result, list) else result.get("tasks", [])
            if project_scope:
                tasks = [t for t in tasks if t.get("project") == project_scope]
            if assignee:
                tasks = [
                    t
                    for t in tasks
                    if t.get("assignee") == assignee or t.get("agent") == assignee
                ]
            tasks = tasks[:requested_limit]
            if not tasks:
                return _text("No tasks found.")
            lines = []
            for t in tasks:
                lines.append(
                    f"[{t.get('status', '?')}] {t.get('title', '?')} -> {t.get('assignee', t.get('agent', '?'))}"
                )
            return _text("\n".join(lines))

        # --- task_update ---
        elif name == "task_update":
            # Redirected from Mirror (retired /tasks) → Squad Service (:8060)
            task = await loop.run_in_executor(
                None,
                lambda: requests.get(
                    f"{SQUAD_SERVICE_URL}/tasks/{args['task_id']}",
                    headers={"Authorization": f"Bearer {SQUAD_SYSTEM_TOKEN}"},
                    timeout=5,
                ).json(),
            )
            _ensure_task_in_scope(task, auth)
            body: dict[str, Any] = {}
            if args.get("status"):
                body["status"] = args["status"]
            if args.get("notes"):
                body["notes"] = args["notes"]
            await loop.run_in_executor(
                None,
                lambda: requests.put(
                    f"{SQUAD_SERVICE_URL}/tasks/{args['task_id']}",
                    headers={"Authorization": f"Bearer {SQUAD_SYSTEM_TOKEN}"},
                    json=body,
                    timeout=5,
                ),
            )
            return _text(f"Task {args['task_id']} updated")

        # --- task_board (prioritized unified view) ---
        elif name == "task_board":
            REVENUE_PROJECTS = {
                "dentalnearyou",
                "dnu",
                "gaf",
                "viamar",
                "stemminds",
                "pecb",
                "digid",
                "torivers",
            }
            PRIORITY_W = {"critical": 4, "urgent": 4, "high": 3, "medium": 2, "low": 1}

            # Pull exclusively from Squad Service — Mirror /tasks is retired (410).
            all_tasks: list[dict] = []
            try:
                squad_resp = await loop.run_in_executor(
                    None,
                    lambda: requests.get(
                        f"{SQUAD_SERVICE_URL}/tasks",
                        headers={"Authorization": f"Bearer {SQUAD_SYSTEM_TOKEN}"},
                        timeout=5,
                    ).json(),
                )
                squad_tasks = (
                    squad_resp if isinstance(squad_resp, list) else squad_resp.get("tasks", [])
                )
                for t in squad_tasks:
                    t["_source"] = "squad"
                all_tasks.extend(squad_tasks)
            except Exception:
                pass
            # Mirror /tasks secondary fetch removed — Mirror retired its /tasks
            # endpoints (410 Gone). Squad Service is the single source of truth.

            # Filter
            status_filter = args.get("status", "queued")
            if status_filter != "all":
                all_tasks = [t for t in all_tasks if t.get("status") == status_filter]
            if args.get("project"):
                all_tasks = [t for t in all_tasks if t.get("project") == args["project"]]
            if args.get("agent"):
                all_tasks = [
                    t
                    for t in all_tasks
                    if t.get("assignee") == args["agent"] or t.get("agent") == args["agent"]
                ]
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

                        age = (
                            dt.now(timezone.utc) - dt.fromisoformat(updated.replace("Z", "+00:00"))
                        ).days
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
            lines.append(
                f"{'Score':>5} | {'Priority':>8} | {'Project':<14} | {'Agent':<10} | Title"
            )
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
                    f"Onboarding failed for '{agent_name}': " + "; ".join(join_result.errors)
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
            lines.append(
                json.dumps({"mcpServers": {"mumega": {"url": join_result.mcp_url}}}, indent=2)
            )
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
            if any(
                kw in desc_lower for kw in ["seo", "audit", "meta", "schema", "ranking", "search"]
            ):
                squad_type = "seo"
                labels.append("seo")
            elif any(
                kw in desc_lower for kw in ["content", "blog", "write", "article", "post", "social"]
            ):
                squad_type = "content"
                labels.append("content")
            elif any(kw in desc_lower for kw in ["outreach", "lead", "email", "sales", "crm"]):
                squad_type = "outreach"
                labels.append("outreach")
            elif any(
                kw in desc_lower for kw in ["deploy", "monitor", "incident", "server", "infra"]
            ):
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
                        json={
                            "id": squad_id,
                            "name": f"{project} {squad_type}",
                            "project": project,
                            "objective": f"{squad_type} work for {project}",
                            "status": "active",
                        },
                        headers={"Authorization": f"Bearer {SQUAD_SYSTEM_TOKEN}"},
                        timeout=5,
                    )
                    requests.post(
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
            except Exception as e:
                return _text(f"Error creating task: {e}")

            # Store in Mirror
            try:
                requests.post(
                    f"{MIRROR_URL}/engrams",
                    json={
                        "text": f"Customer request from {project}: {description}",
                        "agent": project,
                        "context_id": task_id,
                    },
                    headers=MIRROR_HEADERS,
                    timeout=5,
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
            svc_statuses = await asyncio.get_event_loop().run_in_executor(
                None, _get_service_statuses_sync
            )

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
                icon = {"idle": "🟢", "busy": "🔵", "active": "🟡", "dead": "🔴"}.get(
                    a["status"], "⚪"
                )
                lines.append(
                    f"{icon} **{a['agent']}** ({a['model']}) — {a['role']} [{a['status']}]"
                )

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

        # --- outbox_status (S024 F-17) ---
        elif name == "outbox_status":
            agg = await asyncio.get_event_loop().run_in_executor(
                None, _aggregate_outbox_status_sync
            )
            backend_icon = {
                "native": "🟢",        # Mirror NativeSqlOutbox — durable
                "memory": "🟡",        # in-process — best_effort fallback
                "best_effort": "🟡",   # SOS bus today
                "not_configured": "⚪",
                "disabled": "⚪",      # Mirror flag off
                "error": "🔴",
            }
            thresholds = agg["alert_thresholds"]
            lines = ["# Outbox Status\n"]
            for sub_name, info in agg["components"].items():
                backend = info.get("backend", "unknown")
                icon = backend_icon.get(backend, "❓")
                pending = info.get("pending_count", 0)
                dlq = info.get("dlq_count", 0)
                # Inline alert flagging — surface threshold breaches plainly.
                alerts = []
                if dlq >= thresholds["dlq_count"]:
                    alerts.append(f"⚠ DLQ ≥ {thresholds['dlq_count']}")
                if pending >= thresholds["pending_count"]:
                    alerts.append(f"⚠ pending ≥ {thresholds['pending_count']}")
                line = (
                    f"{icon} **{sub_name}** [{backend}] — "
                    f"pending={pending} dlq={dlq}"
                )
                if alerts:
                    line += "  " + "  ".join(alerts)
                if info.get("last_error"):
                    line += f"\n    {info['last_error']}"
                lines.append(line)
            lines.append("\n```json")
            lines.append(json.dumps(agg, indent=2))
            lines.append("```")
            return _text("\n".join(lines))

        # --- browse_marketplace ---
        elif name == "browse_marketplace":
            results = await _async_saas_client.browse_marketplace(
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
            result = await _async_saas_client.subscribe_marketplace(
                project_scope or agent_scope, args["listing_id"]
            )
            text = result.get("message") or result.get("error", "Unknown error")
            return _text(text)

        # --- my_subscriptions ---
        elif name == "my_subscriptions":
            subs = await _async_saas_client.my_subscriptions(project_scope or agent_scope)
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
            result = await _async_saas_client.create_listing(
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
            earnings = await _async_saas_client.my_earnings(project_scope or agent_scope)
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

        # --- notification_settings ---
        elif name == "notification_settings":
            tenant_slug = project_scope or agent_scope
            prefs_updates: dict[str, Any] = {}
            for key in ("email", "telegram", "webhook", "in_app"):
                if args.get(key) is not None:
                    prefs_updates[key] = args.get(key)

            existing = await _async_saas_client.get_notification_preferences(tenant_slug)
            existing.update(prefs_updates)
            await _async_saas_client.set_notification_preferences(tenant_slug, existing)
            text = (
                f"Notification settings updated for {tenant_slug}:\n"
                f"- Email: {'enabled' if existing.get('email') else 'disabled'}\n"
                f"- Telegram: {'enabled' if existing.get('telegram') else 'disabled'}\n"
                f"- Webhook: {existing.get('webhook') or 'not configured'}\n"
                f"- In-app: {'enabled' if existing.get('in_app') else 'disabled'}"
            )
            return _text(text)

        # --- dashboard (customer: "dashboard" → mapped: "get_dashboard") ---
        elif name == "get_dashboard":
            period = args.get("period", "7d")
            tenant_slug = project_scope or agent_scope
            try:
                resp = requests.get(
                    f"http://localhost:8075/tenants/{tenant_slug}/stats",
                    timeout=5,
                )
                if resp.status_code == 200:
                    stats = resp.json()
                    text = (
                        f"Dashboard for {tenant_slug} ({period}):\n"
                        f"- Plan: {stats.get('plan', 'unknown')}\n"
                        f"- Status: {stats.get('status', 'active')}\n"
                        f"- Domain: {stats.get('domain', 'not configured')}\n"
                    )
                    if stats.get("tasks_total") is not None:
                        text += f"- Tasks: {stats.get('tasks_done', 0)}/{stats.get('tasks_total', 0)} completed\n"
                    if stats.get("memories_count") is not None:
                        text += f"- Memories: {stats.get('memories_count', 0)} stored\n"
                else:
                    text = f"Dashboard for {tenant_slug}: active tenant on {stats.get('plan', 'starter') if resp.status_code == 200 else 'starter'} plan. Detailed stats coming soon."
            except Exception:
                text = f"Dashboard for {tenant_slug}: active tenant. Detailed stats endpoint not yet configured."
            return _text(text)

        # --- publish (customer: "publish" → mapped: "publish_content") ---
        elif name == "publish_content":
            title = args.get("title", "")
            content = args.get("content", "")
            slug = args.get("slug", "")
            status = args.get("status", "draft")
            tags = args.get("tags", [])
            tenant_slug = project_scope or agent_scope

            if not title or not content:
                return _text("Error: title and content are required.")

            # Auto-generate slug from title if not provided
            if not slug:
                slug = title.lower().replace(" ", "-")[:60]
                import re as _re
                slug = _re.sub(r"[^a-z0-9-]", "", slug)

            text = (
                f"Content prepared for publishing:\n"
                f"- Title: {title}\n"
                f"- Slug: /{slug}\n"
                f"- Status: {status}\n"
                f"- Tags: {', '.join(tags) if tags else 'none'}\n"
                f"- Tenant: {tenant_slug}\n\n"
                f"Content publishing to Inkwell is being wired. "
                f"For now, the content has been saved to memory. "
                f"Use the dashboard to view and publish."
            )
            # Store as memory so content isn't lost
            try:
                await _remember(
                    f"[draft:{slug}] {title}\n\n{content[:500]}",
                    context=f"publish-draft-{slug}",
                    auth=auth,
                )
                text += "\n\nDraft saved to memory."
            except Exception:
                pass
            return _text(text)

        # --- sell (customer: "sell" → mapped: "create_checkout") ---
        elif name == "create_checkout":
            product_name = args.get("product_name", "")
            price_cents = args.get("price_cents", 0)
            currency = args.get("currency", "usd")
            description = args.get("description", "")

            if not product_name or not price_cents:
                return _text("Error: product_name and price_cents are required.")

            price_display = f"${price_cents / 100:.2f} {currency.upper()}"
            text = (
                f"Payment link for '{product_name}':\n"
                f"- Price: {price_display}\n"
                f"- Description: {description or 'No description'}\n\n"
                f"Stripe checkout integration is being wired. "
                f"For now, create a payment link at stripe.com/dashboard."
            )
            return _text(text)

        # --- my_site (customer: "my_site" → mapped: "site_info") ---
        elif name == "site_info":
            tenant_slug = project_scope or agent_scope
            try:
                resp = requests.get(
                    f"http://localhost:8075/tenants/{tenant_slug}",
                    timeout=5,
                )
                if resp.status_code == 200:
                    info = resp.json()
                    domain = info.get("domain") or info.get("subdomain") or f"{tenant_slug}.mumega.com"
                    text = (
                        f"Your site: {tenant_slug}\n"
                        f"- URL: https://{domain}\n"
                        f"- Plan: {info.get('plan', 'starter')}\n"
                        f"- Status: {info.get('status', 'active')}\n"
                    )
                    if info.get("label"):
                        text += f"- Label: {info['label']}\n"
                else:
                    text = f"Your site: {tenant_slug}.mumega.com (details unavailable)"
            except Exception:
                text = f"Your site: {tenant_slug}.mumega.com\nSite info endpoint not yet configured."
            return _text(text)

        # --- request_squad ---
        elif name == "request_squad":
            squad_type = args.get("type", "support")
            task_text = args.get("task", "")
            urgency = args.get("urgency", "normal")
            project_id = project_scope or agent_scope

            priority_map = {"low": "low", "normal": "medium", "high": "high"}
            task_id = f"{project_id}-squad-{datetime.now(timezone.utc).strftime('%Y%m%d%H%M%S')}"
            # Squad ID is the global squad for this type — any member can claim
            squad_id = squad_type

            # 1. Post bounty to Squad Service task board — any agent in that squad can claim
            try:
                resp = requests.post(
                    f"{SQUAD_SERVICE_URL}/tasks",
                    json={
                        "id": task_id,
                        "squad_id": squad_id,
                        "title": f"[{squad_type.upper()}] {task_text[:100]}",
                        "description": task_text,
                        "project": project_id,
                        "priority": priority_map.get(urgency, "medium"),
                        "labels": ["bounty", "squad-request", squad_type, f"tenant:{project_id}"],
                        "status": "backlog",
                    },
                    headers={"Authorization": f"Bearer {SQUAD_SYSTEM_TOKEN}"},
                    timeout=5,
                )
                if resp.status_code >= 400:
                    # Squad may not exist yet — auto-create then retry
                    requests.post(
                        f"{SQUAD_SERVICE_URL}/squads",
                        json={
                            "id": squad_id,
                            "name": f"{squad_type} squad",
                            "objective": f"Handle {squad_type} requests",
                            "status": "active",
                        },
                        headers={"Authorization": f"Bearer {SQUAD_SYSTEM_TOKEN}"},
                        timeout=5,
                    )
                    requests.post(
                        f"{SQUAD_SERVICE_URL}/tasks",
                        json={
                            "id": task_id,
                            "squad_id": squad_id,
                            "title": f"[{squad_type.upper()}] {task_text[:100]}",
                            "description": task_text,
                            "project": project_id,
                            "priority": priority_map.get(urgency, "medium"),
                            "labels": ["bounty", "squad-request", squad_type, f"tenant:{project_id}"],
                            "status": "backlog",
                        },
                        headers={"Authorization": f"Bearer {SQUAD_SYSTEM_TOKEN}"},
                        timeout=5,
                    )
            except Exception as e:
                log.warning("request_squad task creation failed: %s", e)

            # 2. Broadcast to squad channel — agents subscribed to sos:squad:{type}
            #    pick it up and claim if they're a member. No direct-to-agent routing.
            try:
                bounty_event = json.dumps({
                    "type": "bounty.posted",
                    "squad": squad_type,
                    "task_id": task_id,
                    "project": project_id,
                    "priority": priority_map.get(urgency, "medium"),
                    "title": task_text[:100],
                })
                await r.publish(f"sos:squad:{squad_type}", bounty_event)
            except Exception as e:
                log.warning("request_squad bounty broadcast failed: %s", e)

            return _text(
                f"Squad request sent. A {squad_type} specialist will join your project shortly. "
                f"You'll see them appear in squad_status once they've loaded your context.\n"
                f"Task ID: {task_id}"
            )

        # --- squad_status ---
        elif name == "squad_status":
            project_id = project_scope or agent_scope
            try:
                resp = requests.get(
                    f"{SQUAD_SERVICE_URL}/projects/{project_id}/presence",
                    headers={"Authorization": f"Bearer {SQUAD_SYSTEM_TOKEN}"},
                    timeout=5,
                )
                if resp.status_code == 200:
                    data = resp.json()
                    agents = data.get("agents", [])
                    if not agents:
                        text = "No squad members currently active on your project."
                    else:
                        lines = [f"Active squad members ({len(agents)}):"]
                        for a in agents:
                            lines.append(
                                f"- {a['agent_id']} ({a['role']}) — joined {a.get('joined_at', 'recently')}"
                            )
                            if a.get("reason"):
                                lines.append(f"  Working on: {a['reason']}")
                        text = "\n".join(lines)
                else:
                    text = "Squad status temporarily unavailable."
            except Exception:
                text = "Squad status temporarily unavailable."
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
# S016 Track A — per-session "signed in" flag.
# Connection auth.tenant_id stays the personal default project; sign_in("foo")
# mutates auth.active_project to override scope. Tracking the flag separately
# lets Step 5's tools/list filter on "is signed in" without a None-vs-set
# ambiguity. Cleared on sign_out and on session disconnect.
_session_signed_in: set[str] = set()
# REMOVED 2026-04-26 (S013 WARN-1, Athena adversarial): process-local rate-limit dict.
# _token_windows was bypassable via concurrent connections (single INCR per-process,
# multiple processes = no shared state). Replaced with Redis INCR + EXPIRE in both
# _enforce_rate_limit and handle_tool write-rate path. — Kasra 2026-04-26


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
                    "tenant_id": (
                        _resolve_token_context(token).tenant_id
                        if _resolve_token_context(token)
                        else None
                    ),
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


def _rate_key(auth: "MCPAuthContext") -> str:
    """WARN-S013-006 fix: derive a per-customer rate-limit identifier.

    worker_oauth contexts all share the same auth.token hash (sha256 of SOS_INTERNAL_TOKEN).
    Keying rate limits on that shared hash would make Customer A deplete Customer B's bucket.
    Use tenant_id for worker_oauth — it is unique per customer and is NOT a secret.
    All other sources key on auth.token (a sha256-derived opaque string).
    """
    if auth.source == "worker_oauth" and auth.tenant_id:
        return auth.tenant_id
    return auth.token


def _enforce_rate_limit(auth: "MCPAuthContext") -> None:
    # WARN-1 fix: Redis sliding window (module-level client, WARN-S013-004 fix).
    # WARN-S013-006 fix: key on _rate_key(auth) not raw token.
    key_id = _rate_key(auth)
    if not key_id:
        raise HTTPException(status_code=401, detail="missing_token")
    rkey = f"sos:rate:all:{key_id}"
    count = _sync_redis.incr(rkey)
    if count == 1:
        _sync_redis.expire(rkey, 60)
    if count > RATE_LIMIT_PER_MINUTE:
        raise HTTPException(status_code=429, detail="rate_limit_exceeded")


async def _ensure_knight_enqueued(r: Any, tenant_id: str, agent_name: str) -> None:
    """LOCK-TENANT-B: enqueue knight mint on first worker_oauth call. Idempotent.

    Uses Redis SET NX so concurrent retries (network replay) don't double-enqueue.
    The knight service processes sos:stream:knight:mint and sets
    sos:knight:ready:{tenant_id} when activation completes.
    sos:knight:mint_lock expires after 5 min to allow retry if the service crashed.
    """
    ready_key = f"sos:knight:ready:{tenant_id}"
    if await r.exists(ready_key):
        return  # already activated — fast path
    lock_key = f"sos:knight:mint_lock:{tenant_id}"
    acquired = await r.set(lock_key, "1", nx=True, ex=300)
    if acquired:
        await r.xadd("sos:stream:knight:mint", {
            "tenant_id": tenant_id,
            "agent_name": agent_name,
        })
        await _publish_log("info", "mcp", f"knight_mint_enqueued:{tenant_id}", agent="system")


# ---------------------------------------------------------------------------
# FastAPI app
# ---------------------------------------------------------------------------

app = FastAPI(title="SOS MCP SSE", version="2.0.0")

# CORS for Claude.ai connector and other browser-based clients.
# Late import on purpose — depends on `app` being defined above.
from fastapi.middleware.cors import CORSMiddleware  # noqa: E402

app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "https://claude.ai",
        "https://www.claude.ai",
        "https://chatgpt.com",
        "https://chat.openai.com",
        "*",
    ],
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


_SOS_INTERNAL_TOKEN: str = os.environ.get("SOS_INTERNAL_TOKEN", "")


def _require_auth(request: Request, token: str | None = None) -> MCPAuthContext:
    candidate = (
        token or _request_bearer_token(request) or request.query_params.get("token", "").strip()
    )

    # LOCK-MCP-4 / S013 P0 BLOCK-1 fix (2026-04-26, Athena adversarial):
    # Worker-proxied OAuth customer requests arrive with Bearer=SOS_INTERNAL_TOKEN
    # AND tenant context injected as X-Tenant-Id / X-Agent-Name / X-Tier headers.
    # Without this check, _resolve_token_context would match the internal token →
    # is_system=True → customer gets system god-mode (all scope checks bypassed).
    # Detect this path explicitly: internal token + tenant header = OAuth customer,
    # NOT a system agent. Construct MCPAuthContext from headers; is_system stays False.
    # DO NOT remove this check or merge the two paths — the Worker comment says exactly
    # "VPS uses tenant headers not the OAuth JWT" and this is the VPS side of that contract.
    if (
        _SOS_INTERNAL_TOKEN
        and candidate == _SOS_INTERNAL_TOKEN
        and request.headers.get("X-Tenant-Id")
    ):
        tenant_id = request.headers.get("X-Tenant-Id", "")
        agent_name = request.headers.get("X-Agent-Name", "")
        tier = request.headers.get("X-Tier", "free")
        # S017 G2 — bridge fields. Absent on pre-G2 dispatcher tokens; in that
        # case the inkwell-api bridge gate falls through to legacy S016.
        email_header = request.headers.get("X-Email") or None
        email_verified_header = request.headers.get("X-Email-Verified", "").lower()
        email_verified = email_verified_header == "true"
        agent_identity_id_header = request.headers.get("X-Agent-Identity-Id") or None
        return MCPAuthContext(
            token=hashlib.sha256(candidate.encode()).hexdigest()[:16],  # never store raw
            tenant_id=tenant_id,
            is_system=False,
            source="worker_oauth",
            agent_name=agent_name,
            scope="customer",
            plan=tier,
            email=email_header,
            email_verified=email_verified,
            agent_identity_id=agent_identity_id_header,
        )

    context = _resolve_token_context(candidate)
    if not context:
        raise HTTPException(status_code=401, detail="invalid token")
    return context


@app.get("/.well-known/oauth-authorization-server")
async def oauth_discovery() -> JSONResponse:
    """OAuth discovery — advertises the Cloudflare Worker OAuth endpoints (not VPS stubs).

    S013-B (2026-04-26): /authorize, /token, /register are handled by the
    workers-oauth-provider in the mcp-dispatcher Worker, NOT by this VPS server.
    Discovery document correctly points to those Worker-level paths.
    """
    base = "https://mcp.mumega.com"
    return JSONResponse(
        {
            "issuer": base,
            "authorization_endpoint": f"{base}/authorize",    # Worker: OAuthProvider
            "token_endpoint": f"{base}/token",                # Worker: OAuthProvider
            "registration_endpoint": f"{base}/register",      # Worker: DCR (LOCK-OAuth-D)
            "response_types_supported": ["code"],
            "grant_types_supported": ["authorization_code", "refresh_token"],
            "code_challenge_methods_supported": ["S256"],      # LOCK-OAuth-A: S256 only
            "token_endpoint_auth_methods_supported": ["none"],
        }
    )

# REMOVED 2026-04-26 (S013 P0 BLOCK-2, Athena adversarial): VPS stub OAuth endpoints.
#
# /oauth/register — auto-approved DCR: any caller got a valid client_id. Shodan-reachable.
# /oauth/authorize — auto-approved auth: redirected with code=mumega-auth-ok to any URI.
# /oauth/token — CRITICAL: leaked a live system MCP access token from _system_tokens() to
#                any caller who reached the VPS (direct IP, DNS, misconfigured nginx).
#
# All three were prototype stubs that were never meant to reach production OAuth flow.
# Real OAuth is handled entirely by the mcp-dispatcher Cloudflare Worker via
# workers-oauth-provider (@cloudflare/workers-oauth-provider). The Worker's OAuthProvider
# wraps /authorize, /token, /register — VPS never participates in the OAuth handshake.
#
# DO NOT restore these endpoints for "testing". Use the Worker dev environment instead:
#   npx wrangler dev workers/mcp-dispatcher/
# If you need a VPS-side stub for integration testing, add a TEST-only route gated by
# os.environ.get("SOS_TEST_MODE") and stripped before production deploy.


@app.get("/me")
async def me(
    request: Request,
    client_id: str | None = None,  # query param from npm CLI (W3)
) -> JSONResponse:
    """Tenant profile endpoint — called by @mumega/mcp after token exchange.

    S013 v0.2: replaces the /dcr-bind + /internal/oauth-dcr-register pattern.
    One call does three things:
      1. Returns { tenant_id, tier, agent_name } from worker_oauth context
      2. Persists dcr_client_id if client_id query param provided (W3 — LOCK-AUDIT-1)
      3. Drives `npx @mumega/mcp status` — live tier from server, not cached stale value

    LOCK-AUDIT-1: one DCR client per tenant enforced via UNIQUE(dcr_client_id).
    Called as GET /v2/me?client_id=... → Worker validates OAuth token → here.
    """
    auth = _require_auth(request)
    if auth.source != "worker_oauth" or not auth.tenant_id:
        raise HTTPException(status_code=403, detail="worker_oauth_required")

    # W3: persist DCR client_id if provided (fire-once, idempotent)
    if client_id:
        try:
            from mirror.kernel.db import get_db
            db = get_db()
            db.execute(  # type: ignore[attr-defined]
                """
                UPDATE oauth_tenants
                SET dcr_client_id = %s, updated_at = NOW()
                WHERE tenant_id = %s
                  AND (dcr_client_id IS NULL OR dcr_client_id = %s)
                """,
                client_id, auth.tenant_id, client_id,
            )
        except Exception as exc:
            if "unique" not in str(exc).lower() and "duplicate" not in str(exc).lower():
                log.warning("dcr_client_id write failed for %s: %s", auth.tenant_id, exc)
            # Non-fatal — dcr_client_id is telemetry, never block profile response

    # Derive slug from agent_name ("{slug}-knight" convention — set at tenant provision time)
    agent_name = auth.agent_name or ""
    slug = agent_name[:-7] if agent_name.endswith("-knight") else ""

    response_body: dict[str, object] = {
        "tenant_id": auth.tenant_id,
        "tier": auth.plan or "free",
        "agent_name": agent_name,
        "slug": slug,
    }
    # S017 G2 — surface IdP verification fields to inkwell-api /oauth-complete.
    # Absent on pre-G2 dispatcher tokens (legacy customers); inkwell-api treats
    # absent fields as "skip bridge" so legacy auth continues working.
    # Brief: agents/loom/briefs/kasra-s017-g2-portal-unification.md (v0.4) §2.7
    if auth.email is not None:
        response_body["email"] = auth.email
    # Always include email_verified when we know it (positive OR negative),
    # so inkwell-api can distinguish "IdP said unverified" from "pre-G2 token".
    # Pre-G2 tokens leave the field absent (auth.email is None).
    if auth.email is not None:
        response_body["email_verified"] = bool(auth.email_verified)
    if auth.agent_identity_id is not None:
        response_body["agent_identity_id"] = auth.agent_identity_id

    return JSONResponse(response_body)


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
        1
        for k, v in services.items()
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

    return JSONResponse(
        {
            "status": overall,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "elapsed_ms": elapsed_ms,
            "services": services,
            "agents": agents_info,
            "tenants": tenants_info,
            "kernel": kernel_info,
            "systemd": systemd_statuses,
            "flywheel": flywheel_info,
        }
    )


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

    vitals["services_count"] = (
        7  # calcifer, lifecycle, output-capture, wake-daemon, mcp-sse, squad, mirror
    )

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

    (claude_dir / "settings.json").write_text(
        json.dumps(
            {
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
            },
            indent=2,
        )
    )

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
        {
            "key": mirror_token,
            "key_hash": mirror_hash,
            "agent_slug": slug,
            "created_at": timestamp,
            "active": True,
            "label": label,
        },
        dedup_key="agent_slug",
        dedup_value=slug,
    )

    # 3. Store Bus token (atomic) — scope="customer" gates tool visibility
    bus_added = _atomic_json_append(
        BUS_TOKENS_PATH,
        {
            "token": bus_token,
            "token_hash": "",
            "project": slug,
            "agent": slug,
            "label": label,
            "active": True,
            "created_at": timestamp,
            "scope": "customer",
        },
        dedup_key="project",
        dedup_value=slug,
    )

    if not mirror_added or not bus_added:
        return {"error": f"Customer '{slug}' already exists", "status": "duplicate"}

    # 4. Clear MCP token cache so new tokens are recognized immediately
    _local_token_cache.invalidate()

    # 5. Create Squad API key (over HTTP via SquadClient — was an
    # in-process create_api_key call before v0.4.7 P1-01).
    squad_token = ""
    try:
        result = _squad_client.create_api_key(slug, role="user")
        squad_token = result.get("token", "") if isinstance(result, dict) else ""
    except Exception as e:
        log.warning("Squad API key creation failed: %s", e)

    # 6. Scaffold customer directory
    proj_dir = _scaffold_customer_dir(slug, label, bus_token, mirror_token)

    # 7. Create default squad via Squad Service
    try:
        requests.post(
            f"{SQUAD_SERVICE_URL}/squads",
            json={
                "id": f"{slug}-dev",
                "name": f"{label} Dev Squad",
                "project": slug,
                "objective": f"Development and delivery for {label}",
                "status": "active",
            },
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
            json={
                "id": task_id,
                "squad_id": f"{slug}-dev",
                "title": f"Welcome {label} — initial audit",
                "project": slug,
                "description": f"Run initial audit for {label}. Check site health, identify quick wins.",
                "priority": "high",
                "labels": ["onboarding", "audit"],
                "status": "backlog",
            },
            headers={"Authorization": f"Bearer {SQUAD_SYSTEM_TOKEN}"},
            timeout=5,
        )
    except Exception as e:
        log.warning("Genesis task creation failed: %s", e)

    # 9. Register in Mirror
    try:
        requests.post(
            f"{MIRROR_URL}/engrams",
            json={
                "text": f"Customer onboarded: {label} ({slug}), email: {email}, date: {timestamp}",
                "agent": "system",
                "context_id": f"onboard-{slug}",
            },
            headers=MIRROR_HEADERS,
            timeout=5,
        )
    except Exception:
        pass

    # 10. Announce on bus
    try:
        r = _get_redis()
        await r.publish(
            "sos:wake:kasra",
            json.dumps(
                {
                    "source": "system",
                    "text": f"New customer onboarded: {label} ({slug})",
                }
            ),
        )
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
        raise HTTPException(
            status_code=400, detail="slug must be lowercase alphanumeric with hyphens"
        )

    result = await _onboard_customer(slug, label, email)

    if result.get("status") == "duplicate":
        raise HTTPException(status_code=409, detail=result["error"])

    return JSONResponse(result)


# ---------------------------------------------------------------------------
# Stripe Webhook — Auto-provision tenant on payment
# ---------------------------------------------------------------------------


@app.post("/webhook/stripe")
async def stripe_webhook(request: Request) -> Response:
    """Stripe webhook proxy — forwards the raw request to the billing
    service so Stripe signature verification (HMAC over the raw bytes)
    happens inside billing, not in-process here. Body + headers pass
    through unchanged.
    """
    raw_body = await request.body()
    try:
        billing_resp = await _async_billing_client.forward_stripe_webhook(
            raw_body, dict(request.headers)
        )
    except Exception as exc:
        log.exception("billing webhook proxy failed")
        return JSONResponse({"status": "error", "detail": str(exc)}, status_code=502)
    return Response(
        content=billing_resp.content,
        status_code=billing_resp.status_code,
        media_type=billing_resp.headers.get("content-type"),
    )


# ---------------------------------------------------------------------------
# OAuth Callbacks — Per-tenant integration connections
# ---------------------------------------------------------------------------


@app.get("/oauth/ghl/callback")
async def ghl_oauth_callback(request: Request) -> Response:
    """Handle GHL OAuth callback after tenant grants access.

    Query params: code, tenant (passed via state or custom param).
    Proxies to integrations service — MCP no longer touches
    TenantIntegrations directly (v0.4.7 Phase 4, R2 closure).
    """
    code = request.query_params.get("code", "")
    tenant = request.query_params.get("tenant", "")

    if not code or not tenant:
        raise HTTPException(status_code=400, detail="code and tenant required")

    try:
        result = await _async_integrations_client.handle_ghl_callback(tenant, code)
    except Exception as exc:
        log.exception("integrations ghl callback proxy failed")
        raise HTTPException(status_code=502, detail=f"integrations unavailable: {exc}") from exc

    # TODO: Redirect to dashboard with success message once dashboard exists
    return JSONResponse(
        {
            "status": "connected",
            "provider": "ghl",
            "tenant": tenant,
            "location_id": result.get("location_id", ""),
        }
    )


@app.get("/oauth/google/callback")
async def google_oauth_callback(request: Request) -> Response:
    """Handle Google OAuth callback after tenant grants access.

    Query params: code, state (contains tenant:service).
    Proxies to integrations service — MCP no longer touches
    TenantIntegrations directly (v0.4.7 Phase 4, R2 closure).
    """
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

    try:
        await _async_integrations_client.handle_google_callback(tenant, code, service)
    except Exception as exc:
        log.exception("integrations google callback proxy failed")
        raise HTTPException(status_code=502, detail=f"integrations unavailable: {exc}") from exc

    # TODO: Redirect to dashboard with success message once dashboard exists
    return JSONResponse(
        {
            "status": "connected",
            "provider": f"google_{service}",
            "tenant": tenant,
        }
    )


async def _publish_log(level: str, service: str, message: str, agent: str = "") -> None:
    """Publish a log entry to the unified log stream."""
    try:
        r = _get_redis()
        await r.xadd(
            "sos:stream:logs",
            {
                "level": level,
                "service": service,
                "agent": agent,
                "message": message,
                "timestamp": datetime.now(timezone.utc).isoformat(),
            },
            maxlen=10000,
        )
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
            raw_url = source.replace("github.com", "raw.githubusercontent.com").replace(
                "/blob/", "/"
            )
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
            return JSONResponse(
                {"status": "error", "detail": resp.text}, status_code=resp.status_code
            )
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Registration failed: {e}")

    await _publish_log("info", "skills", f"Installed skill: {meta.get('name', source)}")

    return JSONResponse(
        {
            "status": "ok",
            "skill": meta.get("name"),
            "version": meta.get("version", "1.0.0"),
            "labels": meta.get("labels", []),
            "source": source,
        }
    )


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
    config["agents"] = {
        name: {"type": info["type"], "model": info["model"], "role": info["role"]}
        for name, info in KNOWN_AGENTS.items()
    }

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
        resp = requests.get(
            f"{SQUAD_SERVICE_URL}/skills",
            headers={"Authorization": f"Bearer {SQUAD_SYSTEM_TOKEN}"},
            timeout=3,
        )
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
        logs.append(
            {
                "id": mid,
                "level": data.get("level", "info"),
                "service": data.get("service", "?"),
                "agent": data.get("agent", ""),
                "message": data.get("message", ""),
                "timestamp": data.get("timestamp", ""),
            }
        )
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
    resolved_token = (
        token or _request_bearer_token(request) or request.query_params.get("token", "").strip()
    )
    auth = _require_auth(request, resolved_token)
    session_id = str(uuid4())
    queue: asyncio.Queue[dict[str, Any] | None] = asyncio.Queue()
    _sessions[session_id] = queue
    _session_auth[session_id] = auth

    # S016 Track A Step 6 — Auto sign-in via ?project= query param.
    # If the SSE client opens with ?project=foo and the token is BYOA-customer,
    # invoke the sign_in handler inline so the session lands already signed-in.
    # Failure (no identity, no membership) is silent — client can sign_in
    # manually via tools/call. Internal/system tokens skip this entirely; their
    # token-default project is already authoritative.
    auto_project = request.query_params.get("project", "").strip()
    if auto_project and auth.is_customer:
        try:
            await _handle_sign_in(auth, {"project": auto_project}, session_id)
        except Exception as exc:
            log.warning("auto sign-in failed for project=%s: %s", auto_project, exc)

    # Use public URL if behind nginx proxy, otherwise localhost.
    # Embed the token as ?token= so the POST /messages request is self-contained —
    # clients (e.g. Claude Code on Mac) may not forward Authorization headers to the
    # messages URL, and session auth is lost if the SSE connection briefly drops.
    raw_token = resolved_token  # already validated above
    public_base = os.environ.get("MCP_PUBLIC_URL", "")
    if public_base:
        messages_url = f"{public_base}/messages?session_id={session_id}&token={raw_token}"
    elif request.headers.get("x-forwarded-proto") == "https":
        host = request.headers.get('host', 'mcp.mumega.com')
        messages_url = f"https://{host}/messages?session_id={session_id}&token={raw_token}"
    else:
        messages_url = f"http://localhost:{PORT}/messages?session_id={session_id}&token={raw_token}"
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
            _session_signed_in.discard(session_id)
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
    _enforce_rate_limit(auth)

    try:
        body = await request.json()
    except Exception:
        return Response(status_code=400, content="Invalid JSON")

    try:
        resp = await _process_jsonrpc(body, session_id=session_id, auth=auth)
    except Exception as exc:
        log.exception("_process_jsonrpc unhandled error: %s", exc)
        msg_id = body.get("id") if isinstance(body, dict) else None
        resp = _jsonrpc_err(msg_id, f"Internal server error: {type(exc).__name__}")
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
            "capabilities": {"tools": {"listChanged": True}},
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
            "capabilities": {"tools": {"listChanged": True}},
        }
    )


@app.post("/mcp")
async def mcp_endpoint(request: Request) -> Response:
    auth = _require_auth(request, _request_bearer_token(request))
    _enforce_rate_limit(auth)
    return await _streamable_http_response(request, auth)


@app.post("/mcp/{token}")
async def mcp_endpoint_with_token(token: str, request: Request) -> Response:
    # TODO: path-token auth is deprecated because tokens in URLs leak into access logs,
    # browser history, and proxies. Prefer Authorization: Bearer for new clients.
    auth = _require_auth(request, token)
    _enforce_rate_limit(auth)
    return await _streamable_http_response(request, auth)


async def _streamable_http_response(request: Request, auth: MCPAuthContext) -> Response:
    try:
        body = await request.json()
    except Exception:
        return Response(status_code=400, content="Invalid JSON")

    # S016 Track A Step 6 — Stateless per-request project scoping for /mcp.
    # Streamable HTTP has no long-lived session, so auto sign-in runs per request.
    # ?project=foo on the URL → resolve membership, set auth.active_project before
    # dispatch. Synthetic session_id keeps signed-in flag local to this request.
    auto_project = request.query_params.get("project", "").strip()
    synthetic_session: str | None = None
    if auto_project and auth.is_customer:
        try:
            synthetic_session = f"http-{uuid4()}"
            await _handle_sign_in(auth, {"project": auto_project}, synthetic_session)
        except Exception as exc:
            log.warning("streamable auto sign-in failed for project=%s: %s", auto_project, exc)

    try:
        resp = await _process_jsonrpc(body, session_id=synthetic_session, auth=auth)
    finally:
        if synthetic_session:
            _session_signed_in.discard(synthetic_session)
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
                # listChanged=True so MCP clients re-call tools/list after our
                # notifications/tools/list_changed push (S016 Track A Step 5 —
                # IDENTITY_TOOLS pre-sign_in expand to project-scoped post-sign_in).
                "capabilities": {"tools": {"listChanged": True}},
                "serverInfo": {"name": "sos", "version": "2.1.0"},
            },
        )
    if method == "notifications/initialized":
        # B3 — Auto-onboard: push welcome prompt to new tenant SSE queue (non-critical)
        if auth.is_customer and auth.tenant_id and session_id:
            try:
                onboard_key = f"sos:onboarded:{auth.tenant_id}"
                r = _get_redis()
                if r and not await r.exists(onboard_key):
                    queue = _sessions.get(session_id)
                    if queue:
                        welcome = (
                            "Welcome to Mumega. I'm your Envoy.\n\n"
                            "To get started, tell me: **what does your company do** "
                            "and **what's your biggest open problem right now?** "
                            "I'll remember this so every future conversation has full context. "
                            "Use the `remember` tool (or just tell me and I'll save it)."
                        )
                        await queue.put({
                            "jsonrpc": "2.0",
                            "method": "notifications/message",
                            "params": {"level": "info", "data": welcome},
                        })
                    await r.set(onboard_key, "1", ex=86400 * 365)  # 1-year TTL
            except Exception as exc:
                log.warning("B3 onboard welcome failed (non-critical): %s", exc)
        return None
    if method == "tools/list":
        # Internal/system tokens (kasra, athena, brain, etc.) — full legacy tool
        # set. They never go through sign_in; their token is already project-scoped.
        if not auth.is_customer:
            return _jsonrpc_ok(msg_id, {"tools": get_tools()})

        # S016 Track A Step 5 — Customer (BYOA) dynamic tool list.
        # Before sign_in: only the 4 IDENTITY_TOOLS (my_profile, list_projects,
        # sign_in, sign_out). After sign_in: full role+tier-filtered set scoped
        # to auth.active_project. notifications/tools/list_changed is pushed by
        # _handle_sign_in / _handle_sign_out so clients re-call tools/list.
        is_signed_in = bool(session_id and session_id in _session_signed_in)
        if not is_signed_in:
            identity_tools = [t for t in CUSTOMER_TOOLS if t["name"] in IDENTITY_TOOLS]
            return _jsonrpc_ok(msg_id, {"tools": identity_tools})

        # Signed in — return tier+role filtered set
        tier = auth.plan or "free"
        return _jsonrpc_ok(msg_id, {"tools": get_tools_for_tier(tier, auth.role)})
    if method == "tools/call":
        tool_name = params.get("name", "")
        # --- Rate limiting (customer tokens only) ---
        if auth.is_customer:
            rl_tenant = auth.project_scope or "system"
            try:
                rl_result = await _async_saas_client.check_rate_limit(rl_tenant, auth.plan)
                allowed = bool(rl_result.get("allowed", True))
            except Exception as exc:
                log.warning("rate limit check failed (fail-open): %s", exc)
                allowed = True
            if not allowed:
                log.warning(
                    "rate limit exceeded for tenant %s (plan=%s)",
                    rl_tenant,
                    auth.plan,
                )
                _audit_tool_call(
                    rl_tenant,
                    tool_name,
                    actor=auth.agent_scope,
                    details={"status": "blocked", "reason": "rate_limit_exceeded"},
                )
                return _jsonrpc_err(msg_id, "Rate limit exceeded. Try again in a minute.")
        # Customer token gating: block admin tools, resolve customer names to internal names
        if auth.is_customer:
            if tool_name in BLOCKED_TOOLS:
                log.warning(
                    "customer %s attempted blocked tool %s",
                    auth.tenant_id,
                    tool_name,
                )
                _audit_tool_call(
                    auth.tenant_id or "unknown",
                    tool_name,
                    actor=auth.tenant_id or "",
                    details={"status": "blocked", "reason": "customer_tool_gating"},
                )
                return _jsonrpc_err(msg_id, f"Tool not available: {tool_name}")
            if not is_customer_tool(tool_name):
                log.warning(
                    "customer %s attempted unknown tool %s",
                    auth.tenant_id,
                    tool_name,
                )
                _audit_tool_call(
                    auth.tenant_id or "unknown",
                    tool_name,
                    actor=auth.tenant_id or "",
                    details={"status": "blocked", "reason": "customer_tool_gating"},
                )
                return _jsonrpc_err(msg_id, f"Tool not available: {tool_name}")
            # --- Tier gate: prospect (free) gets read-only subset ---
            tier = auth.plan or "free"
            if not is_tool_allowed_for_tier(tool_name, tier, auth.role):
                upgrade_hint = (
                    " Upgrade to starter at mumega.com/start to unlock all tools."
                    if tier == "free" else ""
                )
                log.warning(
                    "customer %s (tier=%s role=%s) denied tool %s",
                    auth.tenant_id, tier, auth.role, tool_name,
                )
                _audit_tool_call(
                    auth.tenant_id or "unknown",
                    tool_name,
                    actor=auth.tenant_id or "",
                    details={"status": "blocked", "reason": "tier_denied", "tier": tier},
                )
                return _jsonrpc_err(msg_id, f"Tool not available on {tier} plan.{upgrade_hint}")
            # Resolve customer-facing name to internal SOS tool name
            internal_name = TOOL_MAPPING.get(tool_name, tool_name)
            tool_name = internal_name
        tool_result = await handle_tool(tool_name, params.get("arguments", {}), auth, session_id=session_id)
        _append_audit(auth.token, tool_name, not _tool_result_failed(tool_result))
        _audit_tool_call(
            _scope_project(auth) or "system",
            tool_name,
            actor=auth.agent_scope,
            details={"status": "ok"},
        )
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
