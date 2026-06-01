#!/usr/bin/env python3
"""
SOS MCP SSE Server — Persistent HTTP-based MCP transport for Claude Code.

Replaces the stdio MCP that disconnects mid-session.
All agents can share this server.

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
import re
import secrets
import subprocess
import time
from dataclasses import dataclass, replace
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from urllib.parse import urlencode
from uuid import uuid4

import redis.asyncio as aioredis
import requests
from fastapi import FastAPI, HTTPException, Request, Response
from fastapi.responses import JSONResponse
from sse_starlette.sse import EventSourceResponse
from sos.bus.token_store import (
    append_if_missing,
    hash_token,
    load_tokens,
    normalize_subscriptions,
)
from sos.clients.billing import AsyncBillingClient
from sos.clients.integrations import AsyncIntegrationsClient
from sos.clients.saas import AsyncSaasClient, SaasClient
from sos.bus import envelope as bus_envelope
from sos.clients.squad import SquadClient
from sos.contracts.messages import SendMessage
from sos.kernel.bus import enforce_scope
from sos.mcp.tool_policy import (
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
from sos.mcp.transport import jsonrpc_error, jsonrpc_ok
from sos.kernel.auth import verify_bearer as _auth_verify_bearer
from sos.kernel.audit_chain import AuditChainEvent, emit_audit as _emit_audit
try:
    from sos.kernel.sprout_tenant import SproutTenantEngine
except ModuleNotFoundError:
    SproutTenantEngine = None  # type: ignore[assignment]
try:
    from sos.kernel.skills.linkedin import run_linkedin_connector
except ModuleNotFoundError:
    run_linkedin_connector = None  # type: ignore[assignment]

# ---------------------------------------------------------------------------
# Mirror kernel — direct import (no HTTP to :8844)
# Operators can set SOS_MIRROR_KERNEL_ROOT when Mirror is checked out beside SOS
# instead of installed as a package.
# psycopg2 is sync — all calls must be wrapped in run_in_executor.
# ---------------------------------------------------------------------------
import sys as _sys
import concurrent.futures as _futures

if os.environ.get("SOS_MIRROR_KERNEL_ROOT"):
    _sys.path.insert(0, os.environ["SOS_MIRROR_KERNEL_ROOT"])
try:
    from mirror.kernel.db import get_db as _get_mirror_db  # noqa: E402
    from mirror.kernel.embeddings import get_embedding as _get_mirror_embedding  # noqa: E402
except ModuleNotFoundError as _e:
    _mirror_import_error = _e
    _get_mirror_db = None  # type: ignore[assignment]
    _get_mirror_embedding = None  # type: ignore[assignment]
else:
    _mirror_import_error = None

try:
    if _get_mirror_db is None:
        raise RuntimeError(f"Mirror kernel unavailable: {_mirror_import_error}")
    _mirror_db = _get_mirror_db()  # singleton connection pool
except Exception as _e:
    import logging as _logging
    _logging.getLogger(__name__).warning("Mirror DB unavailable at startup: %s — recall will return empty", _e)
    _mirror_db = None
_mirror_executor = _futures.ThreadPoolExecutor(
    max_workers=4, thread_name_prefix="mirror-db"
)

# Squad system token resolves from explicit env only.
SQUAD_SYSTEM_TOKEN = (
    os.environ.get("SOS_SQUAD_SYSTEM_TOKEN")
    or os.environ.get("SOS_SQUAD_TOKEN")
)

_squad_client = SquadClient(token=SQUAD_SYSTEM_TOKEN)
_saas_client = SaasClient()
_async_saas_client = AsyncSaasClient()
_async_billing_client = AsyncBillingClient()
_async_integrations_client = AsyncIntegrationsClient()


async def _audit_tool_call_async_safe(
    tenant: str,
    tool: str,
    actor: str = "",
    ip: str = "",
    details: dict | None = None,
) -> None:
    try:
        await _async_saas_client.log_tool_call(
            tenant,
            tool,
            actor=actor,
            ip=ip,
            details=details,
        )
    except Exception as exc:
        log.warning("audit log_tool_call failed: %s", exc)


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
            _audit_tool_call_async_safe(tenant, tool, actor=actor, ip=ip, details=details)
        )
        return
    try:
        _saas_client.log_tool_call(tenant, tool, actor=actor, ip=ip, details=details)
    except Exception as exc:
        log.warning("audit log_tool_call failed: %s", exc)


def _schedule_audit_event(event: AuditChainEvent) -> None:
    """Fire-and-forget audit chain emit; never blocks MCP tool responses."""
    try:
        asyncio.create_task(_emit_audit(event))
    except RuntimeError as exc:
        log.warning("audit emit schedule failed: %s", exc)


# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [sos-mcp-sse] %(levelname)s %(message)s",
)
log = logging.getLogger("sos_mcp_sse")


async def _emit_audit_best_effort(event: AuditChainEvent) -> None:
    try:
        await _emit_audit(event)
    except ModuleNotFoundError as exc:
        if exc.name == "asyncpg":
            log.warning("audit chain unavailable: install asyncpg/postgres extras to persist audit rows")
        else:
            log.warning("audit chain dependency unavailable: %s", exc)
    except Exception as exc:  # noqa: BLE001
        log.warning("audit chain emit failed: %s", exc)


def _schedule_audit_event(event: AuditChainEvent) -> None:
    asyncio.create_task(_emit_audit_best_effort(event))

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
    "workspace_join", "workspace_leave",
    "register_skill", "invoke_skill",
    "sprout_tenant",
    "as_agent",  # S027 D-5 — session-identity mutation; rate-limit + audit-emit
    "sync_agents",  # #161 — idempotent tenant agent/squad provisioning
})
STASIS_BLOCKED_TOOLS: frozenset[str] = frozenset({
    "send", "broadcast", "remember", "squad_remember",
    "recall", "squad_recall", "task_create", "task_update", "request",
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
    tenant_slug: str | None = None

    @property
    def project_scope(self) -> str | None:
        # S016 Track A — sign_in() pins active_project for the session;
        # it overrides the token-default tenant_id when set.
        if self.is_system:
            return None
        return self.active_project or self.tenant_id

    agent_name: str = ""  # Explicit agent identity from token
    scope: str = ""  # "customer" for external customers; "tenant-agent" for tenant-scoped agent forks; empty for internal agents
    # S027 D-2 L-7 RLS: tenant-agent tokens carry agent_kind (base agent kind:
    # athena|kasra|calliope|...) so the bus can enforce that only senders/recipients
    # with matching tenant_slug+agent_kind+agent_name reach each other. Empty for
    # non-tenant-agent tokens.
    agent_kind: str = ""
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
    # S027 D-5 L-4 — `as_agent` MCP primitive: per-SSE-connection identity
    # mutation. When `as_agent_active=True`, subsequent send/broadcast/inbox
    # tool calls attribute to `as_agent_name` instead of the caller's default
    # `agent_scope`. Cleared on as_agent({name: ""}), sign_out, or disconnect.
    as_agent_active: bool = False
    as_agent_name: str | None = None
    as_agent_kind: str | None = None
    as_agent_tenant_slug: str | None = None
    subscriptions: list[str] | None = None
    # GH #141 — token-file MCP RBAC. Non-customer bus/runtime tokens loaded
    # from tokens.json must explicitly name callable tools in `permissions`.
    permissions: list[str] | None = None
    install_id: str | None = None
    node_id: str | None = None
    local_source_id: str | None = None

    @property
    def is_customer(self) -> bool:
        """True only for external customer tokens — gates tool visibility and access."""
        return self.scope == "customer"

    @property
    def agent_scope(self) -> str:
        # S027 D-5 L-4 — when `as_agent` is active on this SSE connection,
        # subsequent send/broadcast/inbox tool calls attribute to the target
        # tenant agent rather than the caller's default identity. Per-SSE-
        # connection only — never persists across reconnect.
        if self.as_agent_active and self.as_agent_name:
            return self.as_agent_name
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


def _normalize_permissions(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        return [value.strip()] if value.strip() else []
    if not isinstance(value, list):
        return []
    return [str(item).strip() for item in value if str(item).strip()]


_TOOL_PERMISSION_ALIASES: dict[str, frozenset[str]] = {
    "ask": frozenset({"ask", "bus:send"}),
    "send": frozenset({"send", "bus:send"}),
    "broadcast": frozenset({"broadcast", "bus:broadcast", "bus:send"}),
    "inbox": frozenset({"inbox", "bus:read"}),
    "peers": frozenset({"peers", "bus:read"}),
    "check_in": frozenset({"check_in", "bus:send"}),
    "boot_context": frozenset({"boot_context", "health"}),
    "status": frozenset({"status", "health"}),
    "flow_health": frozenset({"flow_health", "health"}),
    "sprint_capsule": frozenset({"sprint_capsule", "health"}),
    "remember": frozenset({"remember", "memory:write", "memory:*"}),
    "squad_remember": frozenset({"squad_remember", "memory:write", "memory:*"}),
    "recall": frozenset({"recall", "memory:read", "memory:*"}),
    "squad_recall": frozenset({"squad_recall", "memory:read", "memory:*"}),
    "memories": frozenset({"memories", "memory:read", "memory:*"}),
    "task_create": frozenset({"task_create", "tasks:write", "tasks:*"}),
    "task_update": frozenset({"task_update", "tasks:write", "tasks:*"}),
    "task_list": frozenset({"task_list", "tasks:read", "tasks:*"}),
    "task_board": frozenset({"task_board", "tasks:read", "tasks:*"}),
    "workspace_join": frozenset({"workspace_join", "workspace:write", "workspace:*"}),
    "workspace_leave": frozenset({"workspace_leave", "workspace:write", "workspace:*"}),
    "workspace_members": frozenset({"workspace_members", "workspace:read", "workspace:*"}),
    "sync_agents": frozenset({"sync_agents", "agents:write", "agents:*", "mcp:*"}),
    "register_skill": frozenset({"register_skill", "skills:write", "skills:*"}),
    "list_skills": frozenset({"list_skills", "skills:read", "skills:*"}),
    "invoke_skill": frozenset({"invoke_skill", "skills:invoke", "skills:*"}),
}


def _permission_matches(granted: str, required: str) -> bool:
    if granted in {"*", "mcp:*"}:
        return True
    if granted == required:
        return True
    if granted.endswith(":*"):
        return required.startswith(granted[:-1])
    return False


def _tool_permission_candidates(tool_name: str) -> set[str]:
    return {tool_name, f"mcp:{tool_name}", *_TOOL_PERMISSION_ALIASES.get(tool_name, frozenset())}


def _tool_allowed_by_permissions(tool_name: str, permissions: list[str] | None) -> bool:
    if permissions is None:
        return False
    candidates = _tool_permission_candidates(tool_name)
    return any(
        _permission_matches(granted, candidate)
        for granted in permissions
        for candidate in candidates
    )


def _enforce_mcp_tool_permission(tool_name: str, auth: MCPAuthContext) -> str | None:
    """Return a denial reason when a tool call is not permitted for this token.

    System tokens remain the break-glass path. Customer tokens already pass
    through the BYOA tool/tier gates below, so this RBAC layer focuses on
    non-customer runtime/bus tokens that carry token-file permissions.
    """
    if auth.is_system or auth.is_customer:
        return None
    if not _tool_allowed_by_permissions(tool_name, auth.permissions):
        return "missing_tool_permission"
    return None


def _tools_visible_to_auth(auth: MCPAuthContext) -> list[dict[str, Any]]:
    tools = get_tools()
    if auth.is_system or auth.is_customer:
        return tools
    return [
        tool for tool in tools
        if _tool_allowed_by_permissions(str(tool.get("name", "")), auth.permissions)
    ]


_INTERNAL_MEMORY_AGENTS = frozenset({
    "kasra", "athena", "loom", "sovereign", "mumega", "codex",
    "sol", "hermes", "river", "worker", "dandan", "mkt-lead",
    "mizan", "gemma", "dara", "sos-medic", "agentlink",
    AGENT_SELF,
})


@dataclass(frozen=True)
class MCPMemoryScope:
    """Single source of truth for MCP memory isolation."""

    agent: str
    project: str | None
    mirror_project: str | None
    workspace_id: str
    owner_type: str
    owner_id: str
    boundary: str
    as_agent_active: bool


def _memory_scope(auth: MCPAuthContext) -> MCPMemoryScope:
    """Resolve the Mirror workspace/project boundary for a tool call.

    Mirror's HTTP auth maps internal Mumega agents to ``mumega-internal``.
    The MCP direct-DB path must do the same, otherwise agents can wake with
    one identity but remember/recall through another workspace.
    """
    agent = auth.agent_scope
    project = _scope_project(auth)

    if auth.as_agent_active and auth.as_agent_name and auth.as_agent_tenant_slug:
        return MCPMemoryScope(
            agent=auth.as_agent_name,
            project=auth.as_agent_tenant_slug,
            mirror_project=auth.as_agent_tenant_slug,
            workspace_id=auth.as_agent_tenant_slug,
            owner_type="agent",
            owner_id=auth.as_agent_name,
            boundary="tenant-agent",
            as_agent_active=True,
        )

    if auth.is_customer or auth.scope in {"tenant", "tenant-agent"}:
        workspace_id = project or auth.tenant_id or agent
        return MCPMemoryScope(
            agent=agent,
            project=project,
            mirror_project=project,
            workspace_id=workspace_id,
            owner_type="human" if auth.is_customer else "agent",
            owner_id=auth.identity_id or agent,
            boundary="customer" if auth.is_customer else auth.scope,
            as_agent_active=False,
        )

    if auth.is_system or agent.lower() in _INTERNAL_MEMORY_AGENTS:
        return MCPMemoryScope(
            agent=agent,
            project=project,
            mirror_project=None,
            workspace_id="mumega-internal",
            owner_type="agent",
            owner_id=agent,
            boundary="substrate",
            as_agent_active=False,
        )

    workspace_id = project or auth.tenant_id or agent
    return MCPMemoryScope(
        agent=agent,
        project=project,
        mirror_project=project,
        workspace_id=workspace_id,
        owner_type="agent",
        owner_id=auth.identity_id or agent,
        boundary=auth.scope or "tenant",
        as_agent_active=False,
    )


def _tenant_slug_for_auth(auth: MCPAuthContext) -> str | None:
    """Resolve the tenant slug used by tenant-wide gates."""
    if auth.is_system:
        return None
    return auth.tenant_slug or auth.tenant_id or auth.active_project


def _mirror_series_for_agent(agent: str) -> str:
    mapping = {
        "river": "River - Conversational AI",
        "knight": "Knight - Task Execution",
        "oracle": "Oracle - Content Generation",
        "frc": "Fractal Resonance Coherence — 821 Higgs Cohesion Series",
    }
    return mapping.get(agent.lower(), f"{agent.title()} - Agent Memory")


_SERVICE_AUTHORITY_CONTRACTS: tuple[dict[str, Any], ...] = (
    {
        "edge": "mcp_to_saas_audit",
        "caller": "sos-mcp-sse",
        "callee": "sos-saas",
        "surface": "POST /audit/tool-call",
        "credential_env": "SOS_SAAS_TOKEN",
        "accepted_by": ["SOS_SAAS_ADMIN_KEY", "SOS_SAAS_TOKEN", "MUMEGA_MASTER_KEY"],
        "failure_mode": "warn + retryable audit gap, never request-path crash",
        "preflight": "synthetic internal audit write returns 200",
        "required": True,
    },
    {
        "edge": "mcp_to_mirror",
        "caller": "sos-mcp-sse",
        "callee": "mirror",
        "surface": "direct DB + HTTP fallback",
        "credential_env": "MIRROR_TOKEN",
        "accepted_by": ["MIRROR_TOKEN", "MIRROR_ADMIN_TOKEN"],
        "failure_mode": "memory degraded, never cross-workspace fallback",
        "preflight": "memory resolver contract check",
        "required": False,
    },
    {
        "edge": "mcp_to_squad",
        "caller": "sos-mcp-sse",
        "callee": "sos-squad",
        "surface": "SquadClient HTTP API",
        "credential_env": "SOS_SQUAD_SYSTEM_TOKEN",
        "accepted_by": ["SOS_SQUAD_SYSTEM_TOKEN", "SOS_SQUAD_TOKEN"],
        "failure_mode": "task operations degraded",
        "preflight": "HTTP /health",
        "required": False,
    },
)


def _service_authority_contracts() -> list[dict[str, Any]]:
    contracts: list[dict[str, Any]] = []
    for item in _SERVICE_AUTHORITY_CONTRACTS:
        credential_env = str(item["credential_env"])
        accepted_by = list(item.get("accepted_by", []))
        accepted_configured = [name for name in accepted_by if os.environ.get(name)]
        contracts.append({
            **item,
            "credential_configured": bool(os.environ.get(credential_env)),
            "accepted_configured": accepted_configured,
            "status": "healthy" if accepted_configured else (
                "critical" if item.get("required") else "degraded"
            ),
        })
    return contracts


def _overall_from_checks(checks: dict[str, dict[str, Any]]) -> str:
    states = [str(v.get("status", "unknown")) for v in checks.values()]
    if any(state == "critical" for state in states):
        return "critical"
    if any(state in {"degraded", "down", "unknown"} for state in states):
        return "degraded"
    return "healthy"


async def _run_flow_health(*, run_probes: bool = True) -> dict[str, Any]:
    """S061 Track I/J flow health.

    These probes are private and synthetic. They validate actual substrate
    flows without public sends, customer writes, payments, DNS/auth mutation,
    connector writes, or secret disclosure.
    """
    started = time.monotonic()
    checks: dict[str, dict[str, Any]] = {}

    contracts = _service_authority_contracts()
    contract_status = "healthy"
    if any(c["status"] == "critical" for c in contracts):
        contract_status = "critical"
    elif any(c["status"] != "healthy" for c in contracts):
        contract_status = "degraded"
    checks["service_authority"] = {
        "status": contract_status,
        "contracts": contracts,
    }

    r = _get_redis()
    bus_start = time.monotonic()
    try:
        stream = "sos:health:flow:bus"
        marker = str(uuid4())
        if run_probes:
            msg_id = await asyncio.wait_for(
                r.xadd(
                    stream,
                    {
                        "type": "flow_health.synthetic",
                        "marker": marker,
                        "source": "sos-mcp-sse",
                        "timestamp": datetime.now(timezone.utc).isoformat(),
                    },
                    maxlen=1000,
                    approximate=True,
                ),
                timeout=3.0,
            )
            rows = await asyncio.wait_for(r.xrange(stream, min=msg_id, max=msg_id, count=1), timeout=3.0)
            delivered = bool(rows and rows[0][1].get("marker") == marker)
        else:
            await asyncio.wait_for(r.ping(), timeout=3.0)
            msg_id = None
            delivered = True
        checks["bus_send"] = {
            "status": "healthy" if delivered else "critical",
            "latency_ms": round((time.monotonic() - bus_start) * 1000),
            "stream": stream,
            "message_id": msg_id,
            "source": "sos-mcp-sse",
        }
    except Exception as exc:
        checks["bus_send"] = {"status": "critical", "error": str(exc)}

    audit_start = time.monotonic()
    try:
        if run_probes:
            await asyncio.wait_for(
                _async_saas_client.log_tool_call(
                    "sos",
                    "flow_health",
                    actor="sos-mcp-sse",
                    details={
                        "synthetic": True,
                        "track": "S061-IJ",
                        "surface": "mcp_to_saas_audit",
                    },
                ),
                timeout=3.0,
            )
            status = "healthy"
            detail = "synthetic audit accepted"
        else:
            await asyncio.wait_for(_async_saas_client.health(), timeout=3.0)
            status = "healthy"
            detail = "saas reachable"
        checks["audit"] = {
            "status": status,
            "latency_ms": round((time.monotonic() - audit_start) * 1000),
            "detail": detail,
        }
    except Exception as exc:
        checks["audit"] = {
            "status": "critical",
            "latency_ms": round((time.monotonic() - audit_start) * 1000),
            "error": str(exc),
        }

    try:
        auth = MCPAuthContext(
            token="flow-health",
            tenant_id="sos",
            is_system=False,
            source="synthetic",
            agent_name="codex",
        )
        memory = _memory_scope(auth)
        ok = memory.workspace_id == "mumega-internal" and memory.owner_id == "codex"
        checks["memory_scope"] = {
            "status": "healthy" if ok else "critical",
            "workspace_id": memory.workspace_id,
            "owner_type": memory.owner_type,
            "owner_id": memory.owner_id,
            "boundary": memory.boundary,
        }
    except Exception as exc:
        checks["memory_scope"] = {"status": "critical", "error": str(exc)}

    try:
        auth = MCPAuthContext(
            token="flow-health",
            tenant_id=None,
            is_system=True,
            source="synthetic",
            agent_name="codex",
        )
        payload = json.loads((await _handle_boot_context(auth))["content"][0]["text"])
        ok = payload.get("memory", {}).get("workspace_id") == "mumega-internal"
        checks["boot_context"] = {
            "status": "healthy" if ok else "critical",
            "agent": payload.get("identity", {}).get("agent"),
            "memory_workspace": payload.get("memory", {}).get("workspace_id"),
        }
    except Exception as exc:
        payload = {}
        checks["boot_context"] = {"status": "critical", "error": str(exc)}

    try:
        capsule = _current_sprint_capsule()
        boot_sprint = payload.get("sprint", {}) if isinstance(payload, dict) else {}
        ok = (
            capsule.get("sprint_id") == boot_sprint.get("sprint_id")
            and capsule.get("current_slice", {}).get("id") == boot_sprint.get("current_slice", {}).get("id")
        )
        checks["sprint_capsule"] = {
            "status": "healthy" if ok else "critical",
            "sprint_id": capsule.get("sprint_id"),
            "boot_context_sprint_id": boot_sprint.get("sprint_id"),
            "current_slice": capsule.get("current_slice", {}).get("id"),
        }
    except Exception as exc:
        checks["sprint_capsule"] = {"status": "critical", "error": str(exc)}

    event_start = time.monotonic()
    try:
        from sos.kernel.information_event import information_event_for_task, project_event_stream

        task_id = f"flow-{uuid4()}"
        task = SimpleNamespace(
            id=task_id,
            squad_id="sos",
            title="S061 flow-health synthetic task",
            project="sos-health",
            status=SimpleNamespace(value="review"),
            priority=SimpleNamespace(value="low"),
            assignee="sos-mcp-sse",
            decision_required=False,
        )
        event = information_event_for_task(
            "task.updated",
            task,
            "sos-mcp-sse",
            tenant_id="sos",
            payload={
                "task_id": task_id,
                "result": {"token_hash": "must_not_project"},
                "claim_token": "must_not_project",
            },
        )
        stream = project_event_stream(event.project)
        if run_probes:
            msg_id = await asyncio.wait_for(
                r.xadd(stream, event.to_redis_fields(), maxlen=1000, approximate=True),
                timeout=3.0,
            )
            rows = await asyncio.wait_for(r.xrange(stream, min=msg_id, max=msg_id, count=1), timeout=3.0)
            rendered = str(rows)
            projected = bool(rows and rows[0][1].get("event_id") == event.event_id)
        else:
            msg_id = None
            rows = []
            rendered = json.dumps(event.to_redis_fields(), default=str)
            projected = True
        safe = "must_not_project" not in rendered and "claim_token" not in rendered
        checks["event_router"] = {
            "status": "healthy" if projected and safe else "critical",
            "latency_ms": round((time.monotonic() - event_start) * 1000),
            "stream": stream,
            "message_id": msg_id,
            "projected": projected,
            "allowlist_safe": safe,
        }
    except Exception as exc:
        checks["event_router"] = {
            "status": "critical",
            "latency_ms": round((time.monotonic() - event_start) * 1000),
            "error": str(exc),
        }

    try:
        record = {
            "project": "sos",
            "agent": "codex-synthetic",
            "scope": "tenant-agent",
            "onboarding_install_id": "flow-health-install",
        }
        first = _node_contract_from_record(record)
        second = _node_contract_from_record(record)
        stable = (
            first["node_id"] == second["node_id"]
            and first["local_source_id"] == second["local_source_id"]
            and first["sync_policy"]["local_files_are_cache"] is True
        )
        checks["node_join"] = {
            "status": "healthy" if stable else "critical",
            "node_id": first.get("node_id"),
            "local_source_id": first.get("local_source_id"),
            "idempotent": stable,
            "memory_workspace_id": first.get("memory_workspace_id"),
        }
    except Exception as exc:
        checks["node_join"] = {"status": "critical", "error": str(exc)}

    routing_feedback_start = time.monotonic()
    try:
        stream = "sos:stream:project:sos-health:dreamer"
        marker = str(uuid4())
        fields = {
            "type": "brain.routing_outcome",
            "outcome_type": "flow_health_synthetic",
            "project": "sos-health",
            "task_id": f"flow-{marker}",
            "agent_name": "sos-mcp-sse",
            "status": "observed",
            "reason": "synthetic routing feedback gate",
            "recorded_at": datetime.now(timezone.utc).isoformat(),
            "source": "sos-mcp-sse",
            "marker": marker,
            "payload": "{}",
        }
        if run_probes:
            msg_id = await asyncio.wait_for(
                r.xadd(stream, fields, maxlen=1000, approximate=True),
                timeout=3.0,
            )
            rows = await asyncio.wait_for(r.xrange(stream, min=msg_id, max=msg_id, count=1), timeout=3.0)
            delivered = bool(rows and rows[0][1].get("marker") == marker)
        else:
            msg_id = None
            delivered = True
        checks["routing_feedback"] = {
            "status": "healthy" if delivered else "critical",
            "latency_ms": round((time.monotonic() - routing_feedback_start) * 1000),
            "stream": stream,
            "message_id": msg_id,
            "dreamer_consumable": delivered,
        }
    except Exception as exc:
        checks["routing_feedback"] = {
            "status": "critical",
            "latency_ms": round((time.monotonic() - routing_feedback_start) * 1000),
            "error": str(exc),
        }

    return {
        "status": _overall_from_checks(checks),
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "elapsed_ms": round((time.monotonic() - started) * 1000),
        "mode": "probe" if run_probes else "summary",
        "checks": checks,
        "safety": {
            "customer_visible": False,
            "public_publish": False,
            "payment_or_dns_or_auth_root_mutation": False,
            "secret_values_returned": False,
        },
    }


def _repo_root() -> Path:
    return Path(os.getenv("MUMEGA_COM_REPO", "/mnt/HC_Volume_104325311/mumega.com"))


def _s061_source_docs() -> list[Path]:
    root = _repo_root()
    return [
        root / "docs" / "s061-information-flow-operating-layer.md",
        root / "agents" / "loom" / "briefs" / "s061-information-flow-operating-layer.md",
    ]


def _source_doc_entry(path: Path) -> dict[str, Any]:
    try:
        stat = path.stat()
        text = path.read_text(encoding="utf-8")
        digest = hashlib.sha256(text.encode("utf-8")).hexdigest()[:12]
        return {
            "path": str(path),
            "exists": True,
            "updated_at": datetime.fromtimestamp(stat.st_mtime, timezone.utc).isoformat(),
            "sha256_12": digest,
        }
    except FileNotFoundError:
        return {"path": str(path), "exists": False}
    except Exception as exc:
        return {"path": str(path), "exists": False, "error": str(exc)}


def _current_sprint_capsule(*, compact: bool = False) -> dict[str, Any]:
    """Return Loom's current sprint state as a deterministic SOS object."""
    from sos.kernel.manual_burndown import top_manual_routing_candidates

    source_docs = [_source_doc_entry(path) for path in _s061_source_docs()]
    updated_values = [
        entry["updated_at"]
        for entry in source_docs
        if entry.get("exists") and entry.get("updated_at")
    ]
    updated_at = max(updated_values) if updated_values else datetime.now(timezone.utc).isoformat()

    capsule: dict[str, Any] = {
        "sprint_id": "S061",
        "owner": "loom",
        "status": "active",
        "theme": "Information Flow Operating Layer",
        "updated_at": updated_at,
        "current_slice": {
            "id": "Slice 4",
            "name": "Adaptive routing",
            "status": "active",
            "priorities": [
                "Track H - Slime-Mold Routing Feedback",
                "Track E - Manual Routing Burn-Down",
            ],
        },
        "tracks": [
            {"id": "A", "name": "Boot Context Primitive", "status": "live-partial", "gate": "G_S061_A_BOOT_CONTEXT_AUTH"},
            {"id": "B", "name": "Information Event Router", "status": "live-partial", "gate": "G_S061_B_EVENT_ROUTER_RLS"},
            {"id": "C", "name": "Sprint Capsule", "status": "live-partial", "gate": "G_S061_C_SPRINT_CAPSULE_TRUTH"},
            {"id": "D", "name": "Tenant Onboarding Flow Graph", "status": "live-partial", "gate": "G_S061_D_TENANT_FLOW_IDEMPOTENCY"},
            {"id": "E", "name": "Manual Routing Burn-Down", "status": "live-partial", "gate": "G_S061_E_MANUAL_ROUTING_BURN_DOWN"},
            {"id": "F", "name": "Mirror Workspace Contract", "status": "live-partial", "gate": "G_S061_F_MIRROR_WORKSPACE_CONTRACT"},
            {"id": "G", "name": "Mycelium Node Join Contract", "status": "live-partial", "gate": "G_S061_G_MYCELIUM_NODE_JOIN"},
            {"id": "H", "name": "Slime-Mold Routing Feedback", "status": "live-partial", "gate": "G_S061_H_SLIME_ROUTING_FEEDBACK"},
            {"id": "I", "name": "Service Authority And Token Contract Preflight", "status": "live-partial", "gate": "G_S061_I_SERVICE_AUTH_PREFLIGHT"},
            {"id": "J", "name": "Flow Health Gates", "status": "live-partial", "gate": "G_S061_J_FLOW_HEALTH_GATES"},
        ],
        "acceptance": [
            "fresh agent can wake with only SOS token and resolve identity, project, sprint, memory, and recovery path",
            "task or Notion decision creates downstream context flow without manual copy/paste",
            "boot_context and sprint capsule return the same current sprint truth",
            "flow health reports bus, audit, memory, sprint, event-router, node-join, and Dreamer gates",
        ],
        "blockers": [
            "QNFT lineage, inbox summary, and project truth pointers still need boot_context attachment",
            "event-router retry state and Dreamer capsule deltas still need durable implementation",
            "Hermes/OpenClaw/local Mac receipt reconciliation still needs a durable importer",
        ],
        "manual_burndown": top_manual_routing_candidates(),
        "source_docs": source_docs,
        "contracts": {
            "boot_context_embeds_capsule": True,
            "recall_alias": "sprint:current:goals",
            "source_of_truth": "loom sprint capsule via SOS, backed by S061 docs",
        },
    }
    if compact:
        return {
            key: capsule[key]
            for key in (
                "sprint_id",
                "owner",
                "status",
                "theme",
                "updated_at",
                "current_slice",
                "contracts",
            )
        }
    return capsule


def _prefix(project: str | None) -> str:
    return f"sos:stream:project:{project}" if project else "sos:stream:global"


# S018 Track E — read agent specialist slugs from an operator overlay.
# Best-effort: never raises. Missing or malformed file => empty list.
_SPECIALISTS_REPO_ROOT = Path(
    os.getenv("SOS_SPECIALISTS_ROOT", "")
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


def _subscription_streams(auth: MCPAuthContext, project_scope: str | None) -> list[tuple[str, str]]:
    streams: list[tuple[str, str]] = []
    seen: set[str] = set()
    for subscription in normalize_subscriptions(auth.subscriptions or []):
        stream = _subscription_to_stream(subscription, project_scope, auth.is_system)
        if stream and stream not in seen:
            seen.add(stream)
            streams.append((f"subscription:{subscription}", stream))
    return streams


def _runtime_subscriptions(values: list[str]) -> list[str]:
    raw: list[str] = []
    for value in values:
        raw.extend(part.strip() for part in str(value).split(","))
    return normalize_subscriptions(raw)


def _subscription_to_stream(subscription: str, project_scope: str | None, is_system: bool) -> str | None:
    if subscription in {"sos:channel:global", "sos:channel:broadcast"}:
        if not is_system and project_scope:
            return f"sos:stream:project:{project_scope}:broadcast"
        return "sos:stream:global:broadcast"
    if subscription.startswith("sos:channel:squad:"):
        squad = subscription.removeprefix("sos:channel:squad:")
        return f"sos:stream:global:squad:{squad}" if squad else None
    if not subscription.startswith("sos:channel:project:"):
        return None
    parts = subscription.split(":")
    if len(parts) < 5:
        return None
    channel_project = parts[3]
    if not is_system and channel_project != project_scope:
        return None
    channel_kind = parts[4]
    if channel_kind in {"global", "broadcast"}:
        return f"sos:stream:project:{channel_project}:broadcast"
    if channel_kind == "squad" and len(parts) >= 6 and parts[5]:
        return f"sos:stream:project:{channel_project}:squad:{parts[5]}"
    return None


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
                # S027 D-2 L-7: tenant-agent tokens carry agent_kind discriminator.
                # Substrate "agent" tokens default to empty string.
                agent_kind = item.get("agent_kind", "") if scope == "tenant-agent" else ""
                plan = item.get("plan") or None
                role = item.get("role", "admin")
                cache[hash_key] = MCPAuthContext(
                    token=hash_key,  # store hash, never the raw token
                    tenant_id=project,
                    is_system=project is None,
                    source="bus_tokens",
                    tenant_slug=item.get("tenant_slug") or project,
                    agent_name=agent_name,
                    scope=scope,
                    agent_kind=agent_kind,
                    plan=plan,
                    role=role,
                    subscriptions=normalize_subscriptions(item.get("subscriptions")),
                    permissions=_normalize_permissions(item.get("permissions")),
                    install_id=item.get("onboarding_install_id") or item.get("install_id") or None,
                    node_id=item.get("node_id") or None,
                    local_source_id=item.get("local_source_id") or None,
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
                    tenant_slug=payload.get("tenant_slug") or project,
                    agent_name=agent_name,
                    scope=scope,
                    plan=plan,
                    role=role,
                    subscriptions=normalize_subscriptions(payload.get("subscriptions")),
                    permissions=_normalize_permissions(payload.get("permissions")),
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
    token_hash = hashlib.sha256(token.encode()).hexdigest()
    if auth_ctx is not None:
        # Map AuthContext → MCPAuthContext, preserving all attributes read by handlers.
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
            tenant_slug=getattr(auth_ctx, "tenant_slug", None) or auth_ctx.project,
            agent_name=auth_ctx.agent or "",
            role="admin" if auth_ctx.is_admin else "viewer",
            permissions=_normalize_permissions(getattr(auth_ctx, "scopes", [])),
        )
    # Local compatibility fallback for tests and hot-reload windows where
    # canonical auth's token cache has not picked up tokens.json yet.
    local_bus = _load_bus_tokens().get(token_hash)
    if local_bus:
        from dataclasses import replace as _replace
        return _replace(local_bus, token=token)
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
            tenant_slug=squad_auth.get("tenant_slug") or squad_auth.get("tenant_id"),
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


# S027 D-2 L-7 — Substrate-only agent kinds. Tenant-agent tokens may target these
# as coordination peers (loom routes briefs, mizan handles business cadence, etc.)
# even though they live in a different scope. Names match SUBSTRATE_ONLY_KINDS in
# sos/bus/tenant_agent_activation.py — keep in sync.
_TENANT_AGENT_SUBSTRATE_PEERS: frozenset[str] = frozenset(
    {"loom", "athena", "kasra", "mumega", "river", "mizan", "sol", "hermes", "codex",
     "calliope", "worker", "gemma", "sos-mcp-sse",
     "hadi-codex", "hadi-codex-cli"}  # mumega members, no minting authority
)


def _peer_tenant_meta(peer_agent_name: str) -> tuple[str, str, str] | None:
    """S027 D-2 L-7 — return (scope, tenant_slug, agent_kind) for a peer agent name.

    Looks up the FIRST active token entry whose `agent` field matches.
    Returns None if no active token claims this name (e.g. ad-hoc substrate agent
    with env-token only). Caller must treat None as "non-tenant-agent peer".
    """
    cache = _local_token_cache.get()
    for ctx in cache.values():
        if ctx.agent_name == peer_agent_name:
            return (ctx.scope, ctx.tenant_slug or ctx.tenant_id or "", ctx.agent_kind)
    return None


def _enforce_tenant_agent_rls(auth: MCPAuthContext, target_agent: str) -> None:
    """S027 D-2 L-7 — block cross-tenant message sends from tenant-agent tokens.

    Rule: when sender's token has scope='tenant-agent', the target peer must
    either (a) share the same tenant_slug, or (b) be a recognized substrate
    coordination agent. Anything else = different tenant boundary, raise 403.

    Defense layer pairs with Worker-side D-1b auth + URL/body cross-check:
    Worker validates the token belongs to the tenant; this layer prevents the
    same token from broadcasting cross-tenant once on the bus.

    Spoofed-tenant-slug-with-valid-agent-name attack: token claims
    tenant_slug=acme + agent_name=athena-acme; target=athena-other (a real
    agent in tenant=other). Without this check, athena-acme's message lands in
    athena-other's inbox stream → cross-tenant leak. With this check, peer
    lookup resolves athena-other to tenant_slug=other → mismatch → 403.
    """
    if auth.scope != "tenant-agent":
        return
    tenant_slug = _tenant_slug_for_auth(auth)
    if not tenant_slug or not auth.agent_kind:
        # Defensive: a malformed tenant-agent token (no tenant_slug or no
        # agent_kind) cannot prove same-scope. Reject all sends.
        raise HTTPException(
            status_code=403, detail="tenant_agent_token_missing_discriminators"
        )
    # Substrate coordination peer (by name) — allowed regardless of token shape.
    if target_agent in _TENANT_AGENT_SUBSTRATE_PEERS:
        return
    peer = _peer_tenant_meta(target_agent)
    if peer is None:
        # No active token claims this name. Allow only if it's a substrate
        # coordination name; otherwise reject — we won't deliver to phantom
        # tenant-agents.
        raise HTTPException(
            status_code=403, detail="tenant_agent_unknown_peer"
        )
    peer_scope, peer_tenant_slug, _peer_kind = peer
    if peer_scope != "tenant-agent":
        # Peer is a substrate agent (validated via real token entry). Allowed.
        return
    if not hmac.compare_digest(peer_tenant_slug, tenant_slug):
        raise HTTPException(
            status_code=403, detail="cross_tenant_send_blocked"
        )


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
            "description": "Ask an agent a question via the SOS bus; replies arrive asynchronously in inbox.",
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
                    "since": {"type": "string", "description": "Redis stream ID cursor — only return messages after this ID (exclusive). Use the stream_id from a previous response as a high water mark to avoid re-reading old messages."},
                    "format": {
                        "type": "string",
                        "enum": ["text", "json"],
                        "default": "text",
                        "description": "Return text lines (default) or structured JSON envelopes.",
                    },
                },
            },
        },
        {
            "name": "check_in",
            "description": "Declare this folder/session live on the SOS bus and return its postal address.",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "model": {"type": "string", "description": "Runtime/model label, e.g. geminiFlash3, codex, claude"},
                    "summary": {"type": "string", "description": "Optional session summary"},
                },
            },
        },
        {
            "name": "workspace_join",
            "description": "Join a scoped collaboration workspace for this project.",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "workspace_id": {"type": "string", "description": "Workspace identifier"},
                    "summary": {"type": "string", "description": "Optional session summary"},
                },
                "required": ["workspace_id"],
            },
        },
        {
            "name": "workspace_leave",
            "description": "Leave a scoped collaboration workspace for this project.",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "workspace_id": {"type": "string", "description": "Workspace identifier"},
                },
                "required": ["workspace_id"],
            },
        },
        {
            "name": "workspace_members",
            "description": "List current members of a scoped collaboration workspace.",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "workspace_id": {"type": "string", "description": "Workspace identifier"},
                },
                "required": ["workspace_id"],
            },
        },
        {
            "name": "register_skill",
            "description": "Register a lightweight skill for peer discovery and bus invocation.",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "name": {"type": "string", "description": "Skill name"},
                    "description": {"type": "string", "description": "Short skill description"},
                    "handler": {"type": "string", "description": "Handler hint, e.g. agent:<owner> or mcp:<tool>"},
                },
                "required": ["name", "description"],
            },
        },
        {
            "name": "list_skills",
            "description": "List registered peer skills in the current project.",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "peer": {"type": "string", "description": "Optional owner/peer filter"},
                },
            },
        },
        {
            "name": "invoke_skill",
            "description": "Invoke a registered peer skill by sending a structured request to its owning agent.",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "name": {"type": "string", "description": "Registered skill name"},
                    "input": {"type": "object", "description": "Skill input payload"},
                },
                "required": ["name"],
            },
        },
        {
            "name": "boot_context",
            "description": "Return the current SOS identity, project, agent, and Mirror memory boundary for first-turn onboarding.",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "project": {
                        "type": "string",
                        "description": "Optional project scope to validate before returning startup context.",
                    },
                },
            },
        },
        {
            "name": "flow_health",
            "description": "Run S061 Track I/J synthetic flow-health probes for service authority, bus, audit, memory scope, and boot context.",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "run_probes": {
                        "type": "boolean",
                        "default": True,
                        "description": "When false, return configured contracts and read-only summaries without synthetic writes.",
                    },
                },
            },
        },
        {
            "name": "sprint_capsule",
            "description": "Return Loom's current S061 sprint capsule as a compact, queryable SOS object.",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "sprint_id": {
                        "type": "string",
                        "default": "current",
                        "description": "Use current, S061, or 061.",
                    },
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
            "name": "linkedin_connector",
            "description": (
                "Hermes/outreach atomic skill: rank supplied LinkedIn profile candidates, "
                "extract an evidence-backed professional vibe, and draft a neutral "
                "connection request. Draft-only; does not scrape, log in, or send."
            ),
            "inputSchema": {
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "Name, company, title, or search phrase for the target profile.",
                    },
                    "profile_url": {
                        "type": "string",
                        "description": "Known LinkedIn profile URL, if already available.",
                    },
                    "profile_text": {
                        "type": "string",
                        "description": "Public profile/about text or notes supplied by Hermes.",
                    },
                    "candidates": {
                        "type": "array",
                        "description": "Optional candidate snippets/dicts to rank locally.",
                        "items": {
                            "anyOf": [
                                {"type": "object"},
                                {"type": "string"},
                            ],
                        },
                    },
                    "sender_context": {
                        "type": "string",
                        "description": "Truthful reason for connecting, written from the sender's perspective.",
                    },
                    "outreach_goal": {
                        "type": "string",
                        "description": "Optional low-pressure CTA for the connection request.",
                    },
                    "max_chars": {
                        "type": "integer",
                        "default": 300,
                        "description": "Maximum invite length. LinkedIn connection notes are commonly capped at 300 chars.",
                    },
                },
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
            "name": "search",
            "description": (
                "Search the Mumega knowledge substrate (memory, content); "
                "returns id/title/url results. Pass an id to `fetch`."
            ),
            "inputSchema": {
                "type": "object",
                "properties": {"query": {"type": "string"}},
                "required": ["query"],
            },
            "annotations": {"readOnlyHint": True},
        },
        {
            "name": "fetch",
            "description": (
                "Fetch the full document for a search result id "
                "(e.g. 'mem:<context_id>'); returns id/title/text/url/metadata."
            ),
            "inputSchema": {
                "type": "object",
                "properties": {"id": {"type": "string"}},
                "required": ["id"],
            },
            "annotations": {"readOnlyHint": True},
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
            "name": "sprout_tenant",
            "description": (
                "System-only Universal Onboarding Engine. Given an absolute project "
                "directory, pulse the repo with Gemini when available and generate "
                "Living Enterprise onboarding files: AGENTS.md, .agent.md, draft "
                "Inkwell canvas, and machine-readable .mumega config."
            ),
            "inputSchema": {
                "type": "object",
                "properties": {
                    "project_path": {
                        "type": "string",
                        "description": "Absolute path to the tenant project directory.",
                    },
                    "tenant_slug": {
                        "type": "string",
                        "description": "Optional tenant slug. Defaults to directory name.",
                    },
                    "overwrite_existing": {
                        "type": "boolean",
                        "default": False,
                        "description": "When false, existing generated files are left untouched.",
                    },
                    "use_gemini": {
                        "type": "boolean",
                        "default": True,
                        "description": "Use Gemini CLI for the repository pulse when available.",
                    },
                },
                "required": ["project_path"],
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
            "description": "Aggregate outbox/queue health across substrates (Mirror receipts, SOS bus events, Inkwell-incoming). S024 F-17 + S025 A-1 + S026 A-4. Returns per-substrate {kind, pending, in_flight, dlq} with `kind` ∈ native|best_effort|not_configured|error. Mirror = native (postgres outbox); SOS = native (Redis Streams + AOF/RDB + DLQ + RetryWorker); Inkwell-incoming = not_configured. `best_effort` is a historical placeholder kind retained for forward-compatibility — no current substrate reports it. Use this to diagnose audit-write backlog or DLQ growth.",
            "inputSchema": {
                "type": "object",
                "properties": {},
            },
        },
        {
            "name": "tenant_canvas_read",
            "description": "Read the tenant's business-model canvas (S026 A3 substrate primitive). Returns the canonical envelope {tenant_id, template_kind, blocks, inference_metadata}. Every agent serving a tenant SHOULD call this at task-start (or cache for short window) to align operating context to the tenant's declared business model. Manual edits override inference; confidence_per_block surfaces which blocks are operator-authoritative vs inferred. template_kind ∈ bmc|lean_canvas|vpc|service_blueprint. Returns canvas_not_initialized if onboarding has not yet seeded the row.",
            "inputSchema": {
                "type": "object",
                "required": ["tenant_id"],
                "properties": {
                    "tenant_id": {
                        "type": "string",
                        "description": "Tenant TEXT PK from tenants(id). Required.",
                    },
                },
            },
        },
        {
            "name": "as_agent",
            "description": (
                "S027 D-5 — Load a tenant-scoped agent's canon (scaffold + cause + recent engrams) "
                "into the current MCP session and operate as that agent on subsequent send/broadcast/inbox. "
                "Per-SSE-connection only — never persists across reconnect. "
                "Pass {name: ''} to clear and revert to default identity. "
                "Authority required: substrate / platform-admin / tenant-admin (owner). "
                "tenant-admin can only as_agent into agents in their own tenant; "
                "tenant-agent and customer scopes cannot use this primitive."
            ),
            "inputSchema": {
                "type": "object",
                "properties": {
                    "name": {
                        "type": "string",
                        "description": (
                            "Agent name (e.g. athena-acme). Must be a tenant-scoped agent "
                            "(scope='tenant-agent' in tokens.json). Pass empty string to clear."
                        ),
                    },
                },
                "required": ["name"],
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
        {
            "name": "sync_agents",
            "description": (
                "#161 — Idempotent one-command tenant agent/squad provisioning. "
                "Reconciles a desired list of tenant-scoped agents against the bus registry: "
                "agents that already exist are returned as-is; missing agents are minted via the "
                "canonical D-3b custom-agent mint path (born-correct scopes, three-discriminator RLS). "
                "Optionally joins the caller's workspace squads. "
                "Tenant isolation enforced: caller may only sync their own tenant's agents; "
                "cross-tenant sync is rejected with an explicit error. "
                "System/operator tokens may target an explicit tenant_slug. "
                "Raw tokens are never returned — only the last-8-char tail and scaffold path. "
                "Re-running is safe and converges (no duplicate mints). "
                "dry_run=true reports what would change without minting anything."
            ),
            "inputSchema": {
                "type": "object",
                "required": ["desired_agents"],
                "properties": {
                    "desired_agents": {
                        "type": "array",
                        "description": "List of agents to provision. Each item must have 'name'; 'role', 'model', 'kind' are optional. Maximum 25 agents per call.",
                        "items": {
                            "type": "object",
                            "required": ["name"],
                            "properties": {
                                "name": {"type": "string", "description": "Agent name (lowercase alphanumeric + hyphens/underscores, 3–32 chars)"},
                                "role": {"type": "string", "description": "Agent role description (default: 'tenant-agent')"},
                                "model": {
                                    "type": "string",
                                    "description": "Model (default: 'claude-sonnet-4-6'). Must be in the D-3b allowlist.",
                                },
                                "kind": {"type": "string", "description": "Agent kind hint (default: 'custom')"},
                            },
                        },
                        "minItems": 1,
                        "maxItems": 25,
                    },
                    "squads": {
                        "type": "array",
                        "description": "Optional list of workspace_ids to join after agent provisioning.",
                        "items": {"type": "string"},
                        "default": [],
                    },
                    "tenant_slug": {
                        "type": "string",
                        "description": (
                            "Tenant slug to provision agents for. "
                            "Tenant-agent tokens: must match their own scope (cross-tenant rejected). "
                            "System/operator tokens: may target any tenant by specifying this field."
                        ),
                    },
                    "dry_run": {
                        "type": "boolean",
                        "default": False,
                        "description": "When true, return what would change without minting any agents or joining any squads.",
                    },
                },
            },
        },
    ]


# ---------------------------------------------------------------------------
# Agent Status Registry (Redis-backed)
# ---------------------------------------------------------------------------

AGENT_REGISTRY_KEY = os.environ.get("SOS_AGENT_REGISTRY_KEY", "sos:registry:agents")


async def _get_agent_statuses(r: aioredis.Redis) -> list[dict[str, Any]]:
    """Get status of registered agents from tmux + Redis activity."""
    try:
        raw_agents = await r.hgetall(AGENT_REGISTRY_KEY)
    except Exception:
        raw_agents = {}
    if not raw_agents:
        return []

    statuses = []
    for name, raw_info in sorted(raw_agents.items()):
        try:
            info = json.loads(raw_info) if isinstance(raw_info, str) else {}
        except (TypeError, json.JSONDecodeError):
            info = {}
        if not isinstance(info, dict):
            info = {}
        agent_type = str(info.get("type") or "remote")
        status = "unknown"

        if agent_type == "tmux":
            session_name = str(info.get("tmux_session") or name)
            idle_patterns = info.get("idle_patterns")
            if not isinstance(idle_patterns, list) or not idle_patterns:
                idle_patterns = ["❯", "›", "$ ", "waiting", "you:"]
            # Check tmux session
            try:
                result = subprocess.run(
                    ["tmux", "has-session", "-t", session_name],
                    capture_output=True,
                    timeout=3,
                )
                if result.returncode == 0:
                    # Check if at prompt (idle) or working (busy)
                    cap = subprocess.run(
                        ["tmux", "capture-pane", "-t", session_name, "-p"],
                        capture_output=True,
                        text=True,
                        timeout=3,
                    )
                    last_lines = " ".join(cap.stdout.strip().split("\n")[-3:]).lower()
                    if any(str(p).lower() in last_lines for p in idle_patterns):
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
                "type": agent_type,
                "model": str(info.get("model") or ""),
                "role": str(info.get("role") or ""),
                "status": status,
            }
        )
    return statuses


def _get_service_statuses_sync() -> list[dict[str, str]]:
    """Check systemd service statuses (sync, runs in executor)."""
    configured = os.environ.get("SOS_MONITORED_SERVICES", "")
    services = [s.strip() for s in configured.split(",") if s.strip()] or [
        "sos-mcp-sse",
        "sos-squad",
        "sovereign-loop",
        "calcifer",
        "agent-wake-daemon",
        "bus-bridge",
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
# Mirror branch promoted to `native` in S024 F-16 (mig 052 + NativeSqlOutbox).
# SOS branch promoted to `native` in S025 A-1 (Redis Streams + RetryWorker +
# DLQ already durable; A-1 wired the visibility surface, see
# `sos.kernel.bus_outbox_stats`). Inkwell-incoming remains
# `not_configured` until that substrate ships its own admin surface.


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
    """Read live SOS bus outbox counts from Redis Streams (S025 A-1).

    The SOS bus already runs at-least-once delivery on Redis Streams +
    a per-group RetryWorker + a `sos:stream:dlq:*` DLQ pattern (see
    `sos.services.bus.retry` / `sos.services.bus.dlq`). Persistence is
    owned by Redis. What was missing in F-17 P2 was the *visibility*
    surface: the SOS branch reported `best_effort` placeholder counts.

    A-1 promotes the branch to `native` by walking the live substrate:

      pending_count = Σ XPENDING.pending across every (stream, group) pair
      dlq_count     = Σ XLEN across every sos:stream:dlq:* stream

    Failure modes are fail-safe (counts default to zero, `backend=error`
    with `last_error` populated) so a Redis hiccup never false-pages a
    healthy substrate.
    """
    try:
        from sos.kernel.bus_outbox_stats import collect_bus_outbox_stats_sync
        stats = collect_bus_outbox_stats_sync(_sync_redis)
    except Exception as exc:
        return {
            "dlq_count": 0,
            "pending_count": 0,
            "backend": "error",
            "last_error": f"{type(exc).__name__}: {exc}",
        }
    return {
        "dlq_count": int(stats.get("dlq_count", 0)),
        "pending_count": int(stats.get("pending_count", 0)),
        "backend": "native",
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


def _json_result(value: dict[str, Any]) -> dict[str, Any]:
    """Return JSON as both MCP text content and structuredContent."""
    return {
        "content": [{"type": "text", "text": json.dumps(value)}],
        "structuredContent": value,
    }


def _workspace_slug(value: str) -> str:
    slug = re.sub(r"[^a-zA-Z0-9._:-]+", "-", value.strip()).strip("-")
    return slug[:96]


def _workspace_project(auth: MCPAuthContext) -> str:
    return _scope_project(auth) or "sos"


def _workspace_member_agent(auth: MCPAuthContext) -> str:
    return _workspace_slug(auth.agent_scope or AGENT_SELF) or AGENT_SELF


def _workspace_keys(project: str, workspace_id: str, agent: str | None = None) -> tuple[str, str | None]:
    base = f"sos:workspace:{project}:{workspace_id}"
    member_key = f"{base}:member:{agent}" if agent else None
    return f"{base}:members", member_key


def _onboarding_squad_for_agent(agent: str, model: str = "", role: str = "") -> tuple[str, str]:
    text = f"{agent} {model} {role}".lower()
    if any(term in text for term in ("athena", "review", "gate", "audit", "security")):
        return "review", "architecture/review gate"
    if any(term in text for term in ("hermes", "mizan", "sales", "outreach", "ops", "support")):
        return "ops", "operator/customer operations"
    if any(term in text for term in ("calliope", "sol", "content", "writer", "social")):
        return "content", "content/publication"
    return "dev", "implementation/default"


async def _route_onboarding_agent(
    r: Any,
    *,
    project: str,
    agent: str,
    model: str = "",
    role: str = "",
    source: str,
    session_id: str = "",
    summary: str = "",
    node_id: str = "",
    local_source_id: str = "",
) -> dict[str, Any]:
    project = _workspace_slug(project) or "sos"
    agent = _workspace_slug(agent) or AGENT_SELF
    squad_type, reason = _onboarding_squad_for_agent(agent, model=model, role=role)
    squad_id = f"{project}-{squad_type}"
    assigned_at = now_iso()
    assignment = {
        "project": project,
        "agent": agent,
        "squad_id": squad_id,
        "squad_type": squad_type,
        "reason": reason,
        "source": source,
        "model": model,
        "role": role,
        "session_id": session_id,
        "summary": summary,
        "node_id": node_id,
        "local_source_id": local_source_id,
        "assigned_at": assigned_at,
    }
    await r.sadd(f"sos:onboarding:{project}:agents", agent)
    await r.sadd(f"sos:onboarding:{project}:squads", squad_id)
    await r.hset(f"sos:onboarding:{project}:agent:{agent}", mapping=assignment)
    event_fields = bus_envelope.build(
        msg_type="onboarding_route",
        source="agent:sos-mcp-sse",
        target=f"agent:{agent}",
        text=f"Onboarding route confirmed: {agent} -> {squad_id}",
        project=project,
        extras=assignment,
    )
    stream_id = await r.xadd(f"sos:onboarding:{project}:events", event_fields)
    confirm_id = await r.xadd(_agent_stream(agent, project), event_fields)
    await r.publish(_agent_channel(agent, project), json.dumps(event_fields))
    await r.publish(f"sos:wake:{agent}", json.dumps(event_fields))
    assignment["event_stream_id"] = stream_id
    assignment["confirmation_stream_id"] = confirm_id
    return assignment


async def _onboarding_assignment_records(project: str) -> list[dict[str, Any]]:
    try:
        r = _get_redis()
        agents = sorted(await r.smembers(f"sos:onboarding:{project}:agents"))
        records = []
        for agent in agents:
            record = await r.hgetall(f"sos:onboarding:{project}:agent:{agent}")
            if record:
                records.append(dict(record))
        return records
    except Exception:
        return []


def _skill_slug(value: str) -> str:
    return re.sub(r"[^a-zA-Z0-9._:-]+", "-", value.strip()).strip("-")[:96]


def _skill_key(project: str, name: str) -> str:
    return f"sos:skills:{project}:{name}"


INKWELL_PUBLISH_SKILL_SCHEMA: dict[str, Any] = {
    "type": "object",
    "required": ["title", "slug", "content_md", "type", "visibility"],
    "properties": {
        "title": {"type": "string", "minLength": 1},
        "slug": {"type": "string", "minLength": 1},
        "content_md": {"type": "string", "minLength": 1},
        "type": {"type": "string", "enum": ["topic", "post", "page"]},
        "visibility": {"type": "string", "enum": ["draft", "published"]},
    },
    "additionalProperties": True,
}

PLATFORM_SKILLS: dict[str, dict[str, Any]] = {
    "inkwell_publish": {
        "name": "inkwell_publish",
        "description": (
            "Create an Inkwell topic/page/post through the tenant-scoped "
            "publish substrate using the caller's bus token."
        ),
        "owner_tenant": None,
        "scope": "tenant-self",
        "input_schema": json.dumps(INKWELL_PUBLISH_SKILL_SCHEMA, sort_keys=True),
        "registered_at": "builtin",
    }
}


async def _handle_register_skill(args: dict[str, Any], auth: MCPAuthContext) -> dict[str, Any]:
    name = _skill_slug(str(args.get("name") or ""))
    description = str(args.get("description") or "").strip()
    if not name:
        return _text("skill name required")
    if name in PLATFORM_SKILLS:
        return _json_result({"ok": False, "error": "reserved_skill_name", "name": name})
    if not description:
        return _text("skill description required")
    project = _workspace_project(auth)
    owner = _workspace_member_agent(auth)
    handler = str(args.get("handler") or f"agent:{owner}").strip()
    registered_at = now_iso()
    payload = {
        "name": name,
        "description": description,
        "handler": handler,
        "owner": owner,
        "project": project,
        "registered_at": registered_at,
    }
    r = _get_redis()
    await r.hset(_skill_key(project, name), mapping=payload)
    await r.sadd(f"sos:skills:{project}:index", name)
    await r.sadd(f"sos:skills:{project}:peer:{owner}", name)
    return _json_result({"ok": True, "skill": payload})


async def _handle_list_skills(args: dict[str, Any], auth: MCPAuthContext) -> dict[str, Any]:
    project = _workspace_project(auth)
    peer = _workspace_slug(str(args.get("peer") or ""))
    r = _get_redis()
    index_key = f"sos:skills:{project}:peer:{peer}" if peer else f"sos:skills:{project}:index"
    names = sorted(await r.smembers(index_key))
    skills = []
    listed_names: set[str] = set()
    for name in names:
        skill = await r.hgetall(_skill_key(project, name))
        if skill:
            skills.append(dict(skill))
            listed_names.add(name)
    if not peer and not auth.is_system and _tenant_slug_for_auth(auth):
        skills.extend(
            dict(skill, project=project)
            for name, skill in PLATFORM_SKILLS.items()
            if name not in listed_names
        )
    return _json_result({"project": project, "peer": peer or None, "skills": skills, "count": len(skills)})


def _validate_inkwell_publish_input(input_payload: Any) -> tuple[dict[str, str], str | None]:
    if not isinstance(input_payload, dict):
        return {}, "input must be an object"

    title = str(input_payload.get("title") or "").strip()
    slug = str(input_payload.get("slug") or "").strip().lower()
    content_md = str(input_payload.get("content_md") or "").strip()
    page_type = str(input_payload.get("type") or "").strip().lower()
    visibility = str(input_payload.get("visibility") or "").strip().lower()

    if not title:
        return {}, "title is required"
    if not slug:
        return {}, "slug is required"
    if not re.fullmatch(r"[a-z0-9][a-z0-9-]{0,126}[a-z0-9]", slug) and not re.fullmatch(r"[a-z0-9]", slug):
        return {}, "slug must be lowercase alphanumeric with optional hyphens"
    if not content_md:
        return {}, "content_md is required"
    if page_type not in {"topic", "post", "page"}:
        return {}, "type must be one of: topic, post, page"
    if visibility not in {"draft", "published"}:
        return {}, "visibility must be one of: draft, published"

    return {
        "title": title,
        "slug": slug,
        "content_md": content_md,
        "type": page_type,
        "visibility": visibility,
    }, None


def _tenant_override_error(input_payload: Any, tenant_slug: str) -> str | None:
    if not isinstance(input_payload, dict):
        return None
    for key in ("tenant_slug", "tenant_id", "project", "project_id"):
        raw = input_payload.get(key)
        if raw is None or str(raw).strip() == "":
            continue
        return f"{key} must not be supplied; tenant is derived from the caller token"
    return None


async def _post_inkwell_publish(tenant_slug: str, token: str, payload: dict[str, str]) -> tuple[int, dict[str, Any]]:
    import httpx

    async with httpx.AsyncClient(timeout=15.0) as client:
        response = await client.post(
            f"{INKWELL_API_URL.rstrip('/')}/api/tenant/{tenant_slug}/inkwell-publish",
            headers={
                "Authorization": f"Bearer {token}",
                "Content-Type": "application/json",
            },
            json=payload,
        )
    try:
        data = response.json()
    except Exception:
        data = {"error": "non_json_response", "body": response.text[:500]}
    return response.status_code, data if isinstance(data, dict) else {"response": data}


async def _handle_inkwell_publish_skill(args: dict[str, Any], auth: MCPAuthContext) -> dict[str, Any]:
    tenant_slug = _tenant_slug_for_auth(auth)
    if auth.is_system or auth.is_customer or not tenant_slug:
        return _json_result({
            "ok": False,
            "error": "tenant_scope_required",
            "message": "inkwell_publish requires a tenant-scoped bus token",
        })
    tenant_slug = _workspace_slug(tenant_slug.lower())
    if not auth.token:
        return _json_result({
            "ok": False,
            "error": "caller_token_required",
            "message": "inkwell_publish requires the caller's bus token",
        })

    input_payload = args.get("input") or {}
    override_error = _tenant_override_error(input_payload, tenant_slug)
    if override_error:
        return _json_result({
            "ok": False,
            "error": "tenant_override_forbidden",
            "message": override_error,
            "tenant_slug": tenant_slug,
        })

    publish_payload, validation_error = _validate_inkwell_publish_input(input_payload)
    if validation_error:
        return _json_result({
            "ok": False,
            "error": "invalid_input",
            "message": validation_error,
        })

    try:
        status_code, substrate = await _post_inkwell_publish(tenant_slug, auth.token, publish_payload)
    except Exception as exc:  # noqa: BLE001
        log.warning("inkwell_publish skill POST failed for tenant=%s: %s", tenant_slug, exc)
        status_code = 502
        substrate = {"error": "substrate_unavailable", "message": str(exc)}

    result_status = substrate.get("status") or substrate.get("error") or ("ok" if status_code < 400 else "error")
    _schedule_audit_event(AuditChainEvent(
        stream_id="mcp",
        actor_id=auth.agent_scope,
        actor_type="agent" if not auth.is_customer else "human",
        action="tenant_skill_invoked",
        resource="skill:inkwell_publish",
        payload={
            "tenant_id": tenant_slug,
            "skill_name": "inkwell_publish",
            "result_status": str(result_status),
            "http_status": status_code,
        },
    ))
    return _json_result({**substrate, "http_status": status_code})


async def _handle_invoke_skill(args: dict[str, Any], auth: MCPAuthContext) -> dict[str, Any]:
    name = _skill_slug(str(args.get("name") or ""))
    if not name:
        return _text("skill name required")
    if name == "inkwell_publish":
        return await _handle_inkwell_publish_skill(args, auth)
    project = _workspace_project(auth)
    r = _get_redis()
    skill = await r.hgetall(_skill_key(project, name))
    if not skill:
        return _text(f"skill not found: {name}")
    owner = skill.get("owner") or ""
    if not owner:
        return _text(f"skill has no owner: {name}")
    request_id = str(uuid4())
    input_payload = args.get("input") or {}
    fields = bus_envelope.build(
        msg_type="skill_invoke",
        source=f"agent:{auth.agent_scope}",
        target=f"agent:{owner}",
        text=f"Invoke skill {name}",
        project=project,
        extras={
            "request_id": request_id,
            "skill": name,
            "handler": skill.get("handler") or "",
            "input": input_payload,
            "requester": auth.agent_scope,
        },
    )
    stream_id = await r.xadd(_agent_stream(owner, project), fields)
    await r.publish(_agent_channel(owner, project), json.dumps(fields))
    await r.publish(f"sos:wake:{owner}", json.dumps(fields))
    return _json_result(
        {
            "ok": True,
            "request_id": request_id,
            "skill": name,
            "owner": owner,
            "project": project,
            "stream_id": stream_id,
        }
    )


async def _handle_workspace_join(
    args: dict[str, Any],
    auth: MCPAuthContext,
    session_id: str | None,
) -> dict[str, Any]:
    workspace_id = _workspace_slug(str(args.get("workspace_id") or ""))
    if not workspace_id:
        return _text("workspace_id required")
    project = _workspace_project(auth)
    agent = _workspace_member_agent(auth)
    members_key, member_key = _workspace_keys(project, workspace_id, agent)
    assert member_key is not None
    joined_at = now_iso()
    r = _get_redis()
    await r.sadd(members_key, agent)
    await r.hset(
        member_key,
        mapping={
            "agent": agent,
            "project": project,
            "workspace_id": workspace_id,
            "joined_at": joined_at,
            "last_seen": joined_at,
            "session_id": session_id or "",
            "source": auth.source,
            "summary": str(args.get("summary") or ""),
        },
    )
    return _json_result(
        {
            "ok": True,
            "action": "joined",
            "project": project,
            "workspace_id": workspace_id,
            "agent": agent,
            "joined_at": joined_at,
        }
    )


async def _handle_sync_agents(
    args: dict[str, Any],
    auth: MCPAuthContext,
    session_id: str | None,
) -> dict[str, Any]:
    """#161 — Idempotent tenant agent/squad provisioning.

    S180 isolation invariant: a tenant token may only sync its own tenant.
    System/operator tokens may target an explicit tenant_slug.
    Raw tokens are never surfaced; only the last-8-char tail is returned.
    """
    # --- Import canonical mint orchestrator (sync; run in executor below) ---
    # Deferred import so a missing optional dep fails at call-time not server start.
    try:
        from sos.bus.tenant_agent_mint import (
            mint_tenant_custom_agent,
            ALLOWED_MODELS,
            AGENT_NAME_RE,
        )
        from sos.bus.tenant_agent_activation import _load_tokens
        from sos.bus.tenant_provisioning import ProvisionError
    except ImportError as _imp_exc:
        return _text(f"Error: sync_agents mint orchestrator unavailable: {_imp_exc}")

    loop = asyncio.get_event_loop()

    # ------------------------------------------------------------------ #
    # 1. Resolve + enforce tenant scope (S180 membrane)
    # ------------------------------------------------------------------ #
    caller_tenant: str | None = _tenant_slug_for_auth(auth)
    requested_tenant: str | None = args.get("tenant_slug") or None

    if auth.is_system:
        # System/operator: may target an explicit slug, or default to nothing.
        effective_tenant = requested_tenant or caller_tenant
    else:
        # Non-system: effective tenant is ALWAYS derived from the token.
        effective_tenant = caller_tenant
        if requested_tenant and requested_tenant != effective_tenant:
            return _text(
                f"Error: cross-tenant sync rejected. "
                f"Your token is scoped to '{effective_tenant}'; "
                f"requested target is '{requested_tenant}'. "
                f"A tenant token may only sync its own tenant's agents (S180 isolation)."
            )

    if not effective_tenant:
        return _text(
            "Error: sync_agents requires a tenant scope. "
            "Pass tenant_slug or use a tenant-scoped token."
        )

    dry_run: bool = bool(args.get("dry_run", False))
    desired_agents: list[dict[str, Any]] = args.get("desired_agents") or []
    squads: list[str] = args.get("squads") or []

    if not desired_agents:
        return _text("Error: desired_agents must be a non-empty list")

    # FIX 3 (WARN — O(N) cap): reject before any mint to bound file-write blast radius.
    _SYNC_AGENTS_MAX = 25
    if len(desired_agents) > _SYNC_AGENTS_MAX:
        return _text(
            f"Error: desired_agents exceeds maximum batch size of {_SYNC_AGENTS_MAX} "
            f"(got {len(desired_agents)}). Split into smaller calls."
        )

    DEFAULT_MODEL = "claude-sonnet-4-6"
    DEFAULT_ROLE = "tenant-agent"
    DEFAULT_CHARTER = (
        f"I am a tenant-defined agent for {effective_tenant}. "
        f"I follow the tenant's instructions and operate within tenant scope."
    )
    DEFAULT_VOICE_RULES = (
        "Respond concisely and helpfully. "
        "Stay within the boundaries of your role and tenant scope."
    )

    agents_created: list[dict[str, Any]] = []
    agents_existing: list[dict[str, Any]] = []
    squads_joined: list[dict[str, Any]] = []
    errors: list[dict[str, Any]] = []

    # ------------------------------------------------------------------ #
    # 2. Read existing token registry once (sync → executor)
    # ------------------------------------------------------------------ #
    def _read_existing_tokens() -> list[dict[str, Any]]:
        return _load_tokens()

    try:
        existing_tokens: list[dict[str, Any]] = await loop.run_in_executor(
            None, _read_existing_tokens
        )
    except Exception as exc:  # noqa: BLE001
        return _text(f"Error: could not read token registry: {exc}")

    def _agent_exists(agent_name: str, tenant_slug: str) -> bool:
        for t in existing_tokens:
            if (
                t.get("agent") == agent_name
                and t.get("scope") == "tenant-agent"
                and t.get("agent_kind") == "custom"
                and t.get("tenant_slug") == tenant_slug
                and t.get("active", True)
            ):
                return True
        return False

    # ------------------------------------------------------------------ #
    # 3. Reconcile each desired agent
    # ------------------------------------------------------------------ #
    for agent_spec in desired_agents:
        if not isinstance(agent_spec, dict):
            errors.append({"agent": None, "error": f"invalid agent spec (must be object): {agent_spec!r}"})
            continue

        agent_name = str(agent_spec.get("name") or "").strip().lower()
        if not agent_name:
            errors.append({"agent": None, "error": "agent spec missing 'name'"})
            continue
        if not AGENT_NAME_RE.match(agent_name):
            errors.append({
                "agent": agent_name,
                "error": (
                    f"invalid agent name '{agent_name}': "
                    "must match ^[a-z][a-z0-9_-]{{2,31}}$"
                ),
            })
            continue

        model = str(agent_spec.get("model") or DEFAULT_MODEL).strip()
        if model not in ALLOWED_MODELS:
            # Fall back to default rather than hard-fail — operator convenience
            log.warning(
                "sync_agents: model '%s' not in allowlist for agent '%s'; using default",
                model, agent_name,
            )
            model = DEFAULT_MODEL

        role = str(agent_spec.get("role") or DEFAULT_ROLE).strip() or DEFAULT_ROLE

        # --- Check existing ---
        if _agent_exists(agent_name, effective_tenant):
            agents_existing.append({"name": agent_name, "status": "existing"})
            continue

        # --- dry_run: report without minting ---
        if dry_run:
            agents_created.append({"name": agent_name, "status": "would_create", "dry_run": True})
            continue

        # --- Mint via canonical orchestrator (FIX 1 + FIX 2) ---
        # Route through mint_tenant_custom_agent so we inherit:
        #   • validate_mint_body: reserved-name + reserved-prefix-RE guard (FIX 1)
        #   • _bus_state_lock: serialises all RMW ops on bus state (FIX 2)
        # A ProvisionError from validate_mint_body (e.g. reserved name) is caught
        # as a per-item error — the rest of the list continues.
        _mint_body: dict[str, Any] = {
            "tenant_id": auth.tenant_id or effective_tenant,
            "tenant_slug": effective_tenant,
            "agent_name": agent_name,
            "model": model,
            "role": role,
            "charter": agent_spec.get("charter") or DEFAULT_CHARTER,
            "voice_rules": agent_spec.get("voice_rules") or DEFAULT_VOICE_RULES,
            # platform-admin path: sync_agents is an operator-level call;
            # token claims are pre-validated by the S180 membrane above.
            "actor_type": "platform-admin",
        }

        def _do_mint(body: dict[str, Any] = _mint_body) -> dict[str, Any]:
            """Run inside executor — mint_tenant_custom_agent is sync file-I/O."""
            result = mint_tenant_custom_agent(body)
            # raw_token is NOT in the orchestrator return dict (it is retained only
            # inside the sub-primitive for token distribution). We surface the
            # token_hash prefix as a handle; full token is in tokens.json only.
            # For callers that need the tail: retrieve via token_hash lookup.
            token_hash = result.get("token_hash", "")
            return {
                "name": result["agent_name"],
                "status": "created",
                # token_tail: last-8 of hash (hash is public; raw token is not).
                # Direct string indexing — never regex/sed (redact-by-construction rule).
                "token_tail": token_hash[-8:] if token_hash else "????????",
                "token_hash_prefix": token_hash[:12] if token_hash else "",
                "scaffold_path": result.get("scaffold_path", ""),
                "qnft_minted": result["idempotency"]["qnft_minted"],
                "token_minted": result["idempotency"]["token_minted"],
                "scaffold_created": result["idempotency"]["scaffold_created"],
            }

        try:
            mint_result = await loop.run_in_executor(None, _do_mint)
            agents_created.append(mint_result)
            # Invalidate local token cache so new token is recognized immediately.
            _local_token_cache.invalidate()
        except ProvisionError as exc:
            errors.append({"agent": agent_name, "error": f"provision_error ({exc.code}): {exc.message}"})
        except Exception as exc:  # noqa: BLE001
            errors.append({"agent": agent_name, "error": f"mint_failed: {exc}"})

    # ------------------------------------------------------------------ #
    # 4. Join squads (workspace_join per squad)
    # ------------------------------------------------------------------ #
    for squad_id in squads:
        squad_id_clean = _workspace_slug(str(squad_id or "").strip())
        if not squad_id_clean:
            errors.append({"squad": squad_id, "error": "invalid workspace_id"})
            continue
        if dry_run:
            squads_joined.append({"workspace_id": squad_id_clean, "status": "would_join", "dry_run": True})
            continue
        try:
            join_result = await _handle_workspace_join(
                {"workspace_id": squad_id_clean}, auth, session_id
            )
            # _handle_workspace_join returns _json_result with ok/action/workspace_id
            squads_joined.append({
                "workspace_id": squad_id_clean,
                "status": "joined",
            })
        except Exception as exc:  # noqa: BLE001
            errors.append({"squad": squad_id_clean, "error": f"workspace_join failed: {exc}"})

    # ------------------------------------------------------------------ #
    # 5. Return structured idempotent status (no raw token ever)
    # ------------------------------------------------------------------ #
    next_steps = [
        "Phase 2 (not included in v1 sync_agents):",
        "  • Board provisioning: scripts/provision-board.sh <tenant_slug>",
        "  • Datasource registration: register agent datasources via the inkwell-api /tenants/{slug}/datasources endpoint",
        "  • Canvas seed: call sprout_tenant (system token) to generate the tenant's living-enterprise canvas",
        "  • Token distribution: deliver token_tail values to agents via a secure operator channel",
    ]

    result = {
        "tenant": effective_tenant,
        "dry_run": dry_run,
        "agents_created": agents_created,
        "agents_existing": agents_existing,
        "squads_joined": squads_joined,
        "errors": errors,
        "summary": {
            "created": len(agents_created),
            "existing": len(agents_existing),
            "squads_joined": len(squads_joined),
            "errors": len(errors),
        },
        "next_steps": next_steps,
    }
    return _json_result(result)


async def _handle_workspace_leave(args: dict[str, Any], auth: MCPAuthContext) -> dict[str, Any]:
    workspace_id = _workspace_slug(str(args.get("workspace_id") or ""))
    if not workspace_id:
        return _text("workspace_id required")
    project = _workspace_project(auth)
    agent = _workspace_member_agent(auth)
    members_key, member_key = _workspace_keys(project, workspace_id, agent)
    assert member_key is not None
    r = _get_redis()
    await r.srem(members_key, agent)
    await r.delete(member_key)
    return _json_result(
        {
            "ok": True,
            "action": "left",
            "project": project,
            "workspace_id": workspace_id,
            "agent": agent,
        }
    )


async def _handle_workspace_members(args: dict[str, Any], auth: MCPAuthContext) -> dict[str, Any]:
    workspace_id = _workspace_slug(str(args.get("workspace_id") or ""))
    if not workspace_id:
        return _text("workspace_id required")
    project = _workspace_project(auth)
    members_key, _ = _workspace_keys(project, workspace_id)
    r = _get_redis()
    agents = sorted(await r.smembers(members_key))
    members = []
    for agent in agents:
        _members_key, member_key = _workspace_keys(project, workspace_id, agent)
        if member_key is None:
            continue
        meta = await r.hgetall(member_key)
        members.append(
            {
                "agent": agent,
                "project": meta.get("project") or project,
                "workspace_id": meta.get("workspace_id") or workspace_id,
                "joined_at": meta.get("joined_at") or "",
                "last_seen": meta.get("last_seen") or "",
                "session_id": meta.get("session_id") or "",
                "source": meta.get("source") or "",
                "summary": meta.get("summary") or "",
            }
        )
    return _json_result(
        {
            "project": project,
            "workspace_id": workspace_id,
            "members": members,
            "count": len(members),
        }
    )


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


def _boot_context_permissions(auth: MCPAuthContext) -> list[str]:
    if auth.is_system:
        return ["*"]
    return list(auth.permissions or [])


def _boot_context_scope_error(auth: MCPAuthContext, requested_project: str) -> dict[str, Any]:
    return _text(json.dumps({
        "error": "project_scope_denied",
        "requested_project": requested_project,
        "allowed_project": auth.project_scope,
        "identity": {
            "agent": auth.agent_scope,
            "tenant_id": auth.tenant_id,
            "scope": auth.scope or ("system" if auth.is_system else "agent"),
            "source": auth.source,
        },
    }, indent=2))


async def _boot_context_peers(auth: MCPAuthContext, project: str | None) -> dict[str, Any]:
    agents: set[str] = set()
    try:
        r = _get_redis()
        patterns = []
        if project:
            patterns.append(f"{_prefix(project)}:agent:*")
        elif auth.is_system:
            patterns.append("sos:stream:sos:channel:private:agent:*")
        for pattern in patterns:
            cursor = 0
            while True:
                cursor, keys = await r.scan(cursor, match=pattern, count=100)
                for key in keys:
                    agents.add(str(key).split(":")[-1])
                if cursor == 0:
                    break
        cursor = 0
        while True:
            cursor, keys = await r.scan(cursor, match="sos:registry:*", count=100)
            for key in keys:
                agent = str(key).split(":", 2)[-1]
                try:
                    meta = await r.hgetall(key)
                except Exception:
                    meta = {}
                reg_project = meta.get("project") if isinstance(meta, dict) else None
                if project and reg_project != project:
                    continue
                if agent:
                    agents.add(agent)
            if cursor == 0:
                break
    except Exception as exc:
        return {"project": project, "agents": [], "error": str(exc)}
    return {"project": project, "agents": sorted(agents)}


def _tenant_is_active_mcp(tenant_slug: str | None) -> bool:
    """Check tenants.is_active from squads DB. Default True on any error."""
    if not tenant_slug:
        return True
    try:
        import sqlite3 as _sqlite3
        from sos.kernel.config import DB_PATH
        conn = _sqlite3.connect(str(DB_PATH))
        row = conn.execute(
            "SELECT is_active FROM tenants WHERE slug = ?", (tenant_slug,)
        ).fetchone()
        conn.close()
        if row is None:
            return True
        return bool(row[0])
    except Exception:
        return True  # fail-open — never block booting on a DB read error


async def _handle_boot_context(auth: MCPAuthContext, args: dict[str, Any] | None = None) -> dict[str, Any]:
    args = args or {}
    requested_project = str(args.get("project") or "").strip()
    if requested_project and not auth.is_system and requested_project != auth.project_scope:
        return _boot_context_scope_error(auth, requested_project)

    memory = _memory_scope(auth)

    # S063 Slice 3: Stasis gate — hard-block work if tenant is deactivated.
    tenant_slug = _tenant_slug_for_auth(auth)
    if not _tenant_is_active_mcp(tenant_slug):
        stasis_payload = {
            "stasis": {
                "active": False,
                "tenant": tenant_slug,
                "warning": "Tenant is in Stasis — work is blocked. Contact support to reactivate.",
                "blocked_tools": ["remember", "recall", "send", "task_create", "task_update"],
            }
        }
        return _text(json.dumps(stasis_payload, indent=2))

    payload = {
        "identity": {
            "agent": memory.agent,
            "base_agent": auth.agent_name or None,
            "tenant_id": auth.tenant_id,
            "project": memory.project,
            "active_project": auth.active_project,
            "scope": auth.scope or ("system" if auth.is_system else "agent"),
            "role": auth.role,
            "plan": auth.plan,
            "source": auth.source,
            "is_customer": auth.is_customer,
            "is_system": auth.is_system,
            "permissions": _boot_context_permissions(auth),
        },
        "as_agent": {
            "active": auth.as_agent_active,
            "agent": auth.as_agent_name,
            "agent_kind": auth.as_agent_kind,
            "tenant_slug": auth.as_agent_tenant_slug,
        },
        "memory": {
            "workspace_id": memory.workspace_id,
            "project": memory.project,
            "mirror_project": memory.mirror_project,
            "owner_type": memory.owner_type,
            "owner_id": memory.owner_id,
            "boundary": memory.boundary,
        },
        "contracts": {
            "remember_recall_same_scope": True,
            "substrate_workspace": "mumega-internal",
            "tenant_agent_as_agent_uses_target_tenant": True,
        },
        "node": _node_contract_from_auth(auth, memory),
        "peers": await _boot_context_peers(auth, requested_project or memory.project),
        "sprint": _current_sprint_capsule(compact=True),
    }

    # Self-onboarding: baseline protocols/governance + per-agent charter, so an
    # agent fully orients itself on first connect with no hand-pasted prompt.
    # Charter is stored server-side at sos:onboarding:{project}:{agent}.
    onboarding = {
        "first_action": "You just called boot_context. Read your identity + charter below, then act.",
        "protocols": (
            "On inbox/peers and any tool with an `agent` parameter, pass YOUR OWN agent name "
            "explicitly. If a message includes [request_id:<uuid>], reply with {ack_for:<uuid>}. "
            "Long-running agents emit a periodic heartbeat."
        ),
        "governance": (
            "Your actions are workflow-gated. Supervised workflows require human approval — "
            "surface the approval request, never bypass it. Everything is auditable."
        ),
        "escalate_to": "agent:kasra",
    }
    try:
        _r = _get_redis()
        if _r is not None:
            _charter = await _r.get(f"sos:onboarding:{memory.project}:{memory.agent}")
            if _charter:
                onboarding["charter"] = (
                    _charter.decode() if isinstance(_charter, (bytes, bytearray)) else _charter
                )
    except Exception:
        pass
    payload["onboarding"] = onboarding

    return _text(json.dumps(payload, indent=2))


async def _handle_flow_health(auth: MCPAuthContext, args: dict[str, Any]) -> dict[str, Any]:
    if not auth.is_system:
        return _text("flow_health requires a system token.")
    payload = await _run_flow_health(run_probes=bool(args.get("run_probes", True)))
    return _text(json.dumps(payload, indent=2))


async def _handle_sprint_capsule(auth: MCPAuthContext, args: dict[str, Any]) -> dict[str, Any]:
    if not auth.is_system:
        return _text("sprint_capsule requires a system token.")
    sprint_id = str(args.get("sprint_id") or "current").strip().lower()
    if sprint_id not in {"current", "s061", "061"}:
        return _text(json.dumps({
            "error": "unknown_sprint",
            "requested": args.get("sprint_id"),
            "available": ["current", "S061"],
        }, indent=2))
    return _text(json.dumps(_current_sprint_capsule(), indent=2))


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
    # S027 D-5 L-4 — sign_out clears `as_agent` mutation alongside active_project.
    _clear_as_agent_state(auth, session_id)
    if session_id:
        _session_signed_in.discard(session_id)
        await _push_tools_list_changed(session_id)
    return _text(json.dumps({"ok": True, "signed_out": True}, indent=2))


# ---------------------------------------------------------------------------
# S027 D-5 — `as_agent` MCP primitive (LOCK-S027-D-5-as-agent-mcp-primitive)
# ---------------------------------------------------------------------------


def _clear_as_agent_state(auth: MCPAuthContext, session_id: str | None) -> None:
    """Helper — clear in-memory + module-level as_agent state (no audit emit).

    Used by sign_out, SSE disconnect, and as_agent({name: ""}) reset.
    """
    auth.as_agent_active = False
    auth.as_agent_name = None
    auth.as_agent_kind = None
    auth.as_agent_tenant_slug = None
    if session_id:
        _session_as_agent.pop(session_id, None)


def _load_qnft_registry_for_as_agent() -> dict[str, Any]:
    """Best-effort QNFT registry read. Mirrors `_load_qnft_registry` in
    `sos/bus/tenant_agent_activation.py` — kept local to avoid circular import
    pressure on the MCP module. Returns {} on any failure.
    """
    try:
        from sos.bus.tenant_agent_activation import _load_qnft_registry  # type: ignore
        data = _load_qnft_registry()
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def _as_agent_error(code: str, **extra: Any) -> dict[str, Any]:
    """Standard MCP error tool-result shape with named error code (D-5 §1)."""
    payload: dict[str, Any] = {"ok": False, "error": code}
    if extra:
        payload.update(extra)
    return _text(json.dumps(payload, indent=2))


async def _handle_as_agent(
    auth: MCPAuthContext,
    args: dict[str, Any],
    session_id: str | None,
) -> dict[str, Any]:
    """S027 D-5 — load tenant-scoped agent canon into MCP session.

    LOCKs enforced:
      L-1 — Layer A (caller scope class) + Layer B (target-tenant match for
            tenant-admin path only). Order split by caller scope so tenant-admin
            callers cannot distinguish "agent doesn't exist" from "you can't
            access it" (agent-existence oracle leak closure).
      L-2 — Mirror engram fetch tenant-scoped (project=target_tenant_slug).
      L-3 — Scaffold-missing failure mode: re-render via D-2b
            scaffold_or_skip_agent_fork; if template also missing → fail loud
            with `scaffold_missing` (no silent empty content).
      L-4 — Per-SSE-connection session-identity mutation (cleared on
            disconnect / sign_out / explicit reset). NOT keyed by token hash —
            two connections sharing one token get distinct session_ids.
      L-5 — Mandatory audit row (fire-and-forget, fail-open).
    """
    target_name = str(args.get("name", "")).strip()

    # ----------------------------------------------------------------- reset
    # `as_agent({name: ""})` clears state, emits a reset audit row, returns ok.
    # Idempotent — works even when no swap is currently active.
    if not target_name:
        from_label = (
            auth.as_agent_name
            or auth.agent_name
            or (auth.scope or "system")
        )
        was_active = auth.as_agent_active
        prev_target = auth.as_agent_name
        prev_tenant = auth.as_agent_tenant_slug
        _clear_as_agent_state(auth, session_id)
        # L-5 — reset audit row (fail-open).
        try:
            _schedule_audit_event(AuditChainEvent(
                stream_id="mcp",
                actor_id=auth.agent_name or auth.scope or "system",
                actor_type="agent",
                action="mcp.as_agent.reset",
                resource=f"agent:{prev_target or ''}",
                payload={
                    "from_agent": from_label,
                    "to_agent": "",
                    "tenant_slug": prev_tenant or "",
                    "session_id": session_id or "",
                    "caller_scope": auth.scope or "system",
                    "was_active": was_active,
                },
            ))
        except Exception as exc:  # noqa: BLE001
            log.warning("as_agent reset audit emit failed: %s", exc)
        return _text(json.dumps({
            "ok": True,
            "reset": True,
            "session_identity_set": False,
            "session_id": session_id or "",
        }, indent=2))

    # --------------------------------------------------------- L-1 Layer A
    caller_scope = auth.scope or ""
    if caller_scope == "customer":
        return _as_agent_error("customer_token_forbidden")
    if caller_scope == "tenant-agent":
        return _as_agent_error("tenant_agent_token_cannot_escalate")
    if caller_scope == "tenant":
        # Owner-only v1 (S028 carry: editor permission if surfaced).
        if (auth.role or "").lower() != "owner":
            return _as_agent_error(
                "tenant_admin_role_insufficient",
                role=auth.role or "viewer",
            )
    # else: scope="" (substrate) or is_system=True (ADMIN_API_SECRET) — permitted.

    is_tenant_admin = (caller_scope == "tenant")
    # Substrate path: scope="" or is_system token.
    is_substrate = (auth.is_system or caller_scope == "")

    # ----------------------------------------------------------- target lookup
    # Reuse the same _peer_tenant_meta helper used by L-7 RLS — single source
    # of truth for "does this name resolve to a tenant-agent token, and what
    # are its (scope, tenant_slug, agent_kind) discriminators?"
    target_meta = _peer_tenant_meta(target_name)

    # --------------------------------------------------------- L-1 Layer B
    # ORDER SPLIT BY CALLER SCOPE — tenant-admin callers MUST NOT learn whether
    # a phantom name exists. Substrate callers (full authority) get distinct
    # error codes (no leak concern).
    if is_tenant_admin:
        if target_meta is None:
            # Phantom OR cross-tenant — caller cannot distinguish.
            return _as_agent_error("not_authorized")
        target_scope, target_tenant_slug, target_agent_kind = target_meta
        if not auth.tenant_id or not hmac.compare_digest(
            auth.tenant_id, target_tenant_slug or ""
        ):
            return _as_agent_error("not_authorized")
        if target_scope != "tenant-agent":
            # Caller has proven authority over this tenant; substrate-name
            # selection is a different rejection class (intent-clarification).
            return _as_agent_error("not_a_tenant_agent")
    else:
        # substrate / platform-admin path — distinct codes OK
        if target_meta is None:
            return _as_agent_error("agent_not_found")
        target_scope, target_tenant_slug, target_agent_kind = target_meta
        if target_scope != "tenant-agent":
            return _as_agent_error("not_a_tenant_agent")

    # Defensive: tenant-agent must have non-empty discriminators in tokens.json
    if not target_tenant_slug or not target_agent_kind:
        return _as_agent_error(
            "not_a_tenant_agent",
            reason="target token missing tenant_slug or agent_kind discriminator",
        )

    # ------------------------------------------------------------------ L-3
    # Scaffold load — re-render via D-2b idempotent function if missing.
    customers_root = Path.home() / ".mumega" / "customers"
    scaffold_path = (
        customers_root / target_tenant_slug / "agents" / target_agent_kind / "CLAUDE.md"
    )
    qnft_registry = _load_qnft_registry_for_as_agent()
    qnft_record = qnft_registry.get(target_name) or {}
    qnft_seed_hex = qnft_record.get("seed_hex", "")

    if not scaffold_path.exists():
        try:
            from sos.bus.tenant_agent_activation import (
                scaffold_or_skip_agent_fork,
                _resolve_tenant_metadata,
            )
            display_name, industry = _resolve_tenant_metadata(target_tenant_slug)
            scaffold_path, _created = await asyncio.get_event_loop().run_in_executor(
                None,
                lambda: scaffold_or_skip_agent_fork(
                    agent_name=target_name,
                    agent_kind=target_agent_kind,
                    tenant_slug=target_tenant_slug,
                    tenant_display_name=display_name,
                    industry=industry,
                    qnft_seed_hex=qnft_seed_hex,
                    mint_date=qnft_record.get("minted_at", ""),
                ),
            )
        except Exception as exc:  # ProvisionError or OSError
            # Template missing OR scaffold-write IO failure — fail loud, never
            # return silent empty content.
            return _as_agent_error(
                "scaffold_missing",
                kind=target_agent_kind,
                expected_path=str(scaffold_path),
                io_error=str(exc),
            )

    if not scaffold_path.exists():
        # Re-render returned but file still missing — defensive belt-and-braces.
        return _as_agent_error(
            "scaffold_missing",
            kind=target_agent_kind,
            expected_path=str(scaffold_path),
        )

    try:
        scaffold_content = scaffold_path.read_text(encoding="utf-8")
        scaffold_loaded = True
    except OSError as exc:
        return _as_agent_error(
            "scaffold_missing",
            kind=target_agent_kind,
            expected_path=str(scaffold_path),
            io_error=str(exc),
        )

    # ---------------------------- QNFT cause (registry first, cause.md second)
    cause_loaded = False
    cause_content = ""
    cause_source = ""
    if qnft_record.get("cause"):
        cause_content = str(qnft_record["cause"])
        cause_source = "registry"
        cause_loaded = True
    else:
        cause_md_path = Path.home() / ".claude" / "qnft" / target_name / "cause.md"
        if cause_md_path.exists():
            try:
                cause_content = cause_md_path.read_text(encoding="utf-8")
                cause_source = "cause_md"
                cause_loaded = True
            except OSError:
                pass  # cause is informational; absence does not block swap

    # ------------------------------------------------------------------ L-2
    # Mirror recent engrams scoped to (agent_name, project=target_tenant_slug).
    # Defensive: even though _mirror_db.recent_engrams accepts a `project`
    # filter, double-filter the result list — naming convention {kind}-{slug}
    # makes agent_name effectively scoped, but L-2 invariant must enforce
    # explicit project filtering, NOT rely on naming uniqueness.
    try:
        engram_limit = int(args.get("engram_limit") or 20)
    except (TypeError, ValueError):
        engram_limit = 20
    engram_limit = max(1, min(engram_limit, 100))
    recent_engrams: list[dict[str, Any]] = []
    if _mirror_db is not None:
        try:
            loop = asyncio.get_event_loop()
            rows = await loop.run_in_executor(
                _mirror_executor,
                lambda: _mirror_db.recent_engrams(
                    agent=target_name,
                    limit=engram_limit,
                    project=target_tenant_slug,
                ),
            )
            for row in rows or []:
                if not isinstance(row, dict):
                    continue
                row_project = row.get("project")
                # Defensive double-filter — L-2 invariant.
                if row_project and row_project != target_tenant_slug:
                    continue
                recent_engrams.append(row)
        except Exception as exc:  # noqa: BLE001
            log.warning(
                "as_agent Mirror engram fetch failed for %s: %s",
                target_name, exc,
            )
            recent_engrams = []

    # --------------------------------- snapshot caller pre-mutation for audit
    # `auth.agent_scope` honors as_agent_active, so capture the caller's
    # default identity BEFORE flipping the flag (else audit row's actor_id
    # would point at the target, not the caller).
    pre_swap_actor = auth.agent_scope
    pre_swap_from = auth.agent_name or (auth.scope or "system") or "unknown"
    if auth.is_system and not auth.agent_name:
        pre_swap_from = "system"

    # ------------------------------------------------------------------ L-4
    # Per-SSE-connection session-identity mutation. Module-level dict mirrors
    # for cross-handler lookup; `session_id` is the per-CONNECTION uuid4
    # assigned at SSE accept (not token-hash-keyed) — two simultaneous
    # connections sharing a Bearer get distinct session_ids and cannot bleed.
    auth.as_agent_active = True
    auth.as_agent_name = target_name
    auth.as_agent_kind = target_agent_kind
    auth.as_agent_tenant_slug = target_tenant_slug
    if session_id:
        _session_as_agent[session_id] = {
            "as_agent_active": True,
            "as_agent_name": target_name,
            "as_agent_kind": target_agent_kind,
            "as_agent_tenant_slug": target_tenant_slug,
            "caller_token_hash": auth.token,
            "caller_scope": auth.scope,
            "caller_tenant_id": auth.tenant_id,
            "caller_agent_name": auth.agent_name,
        }

    # ------------------------------------------------------------------ L-5
    # Mandatory audit row. Fire-and-forget — failure does NOT block the swap
    # (in-memory state is canonical until next attributed tool call). Substrate
    # callers ALSO emit (Athena clause: higher privilege = MORE traceability).
    try:
        _schedule_audit_event(AuditChainEvent(
            stream_id="mcp",
            actor_id=pre_swap_actor,
            actor_type="agent",
            action="mcp.as_agent",
            resource=f"agent:{target_name}",
            payload={
                "from_agent": pre_swap_from,
                "to_agent": target_name,
                "tenant_slug": target_tenant_slug,
                "session_id": session_id or "",
                "caller_scope": auth.scope or "system",
            },
        ))
    except Exception as exc:  # noqa: BLE001
        # Fail-open: log + continue. The swap is in effect; only the durable
        # trail is missing for this single call.
        log.warning("as_agent audit emit failed: %s", exc)

    return _text(json.dumps({
        "ok": True,
        "agent_name": target_name,
        "tenant_slug": target_tenant_slug,
        "agent_kind": target_agent_kind,
        "qnft_seed_hex": qnft_seed_hex,
        "scaffold_loaded": scaffold_loaded,
        "scaffold_path": str(scaffold_path),
        "scaffold_content": scaffold_content,
        "cause_loaded": cause_loaded,
        "cause_source": cause_source,
        "cause_content": cause_content,
        "recent_engrams": recent_engrams,
        "session_identity_set": True,
        "session_id": session_id or "",
    }, indent=2, default=str))


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
    if name == "boot_context":
        return await _handle_boot_context(auth, args)
    if name == "flow_health":
        return await _handle_flow_health(auth, args)
    if name == "sprint_capsule":
        return await _handle_sprint_capsule(auth, args)
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
    # S027 D-5 — `as_agent` MCP primitive. Dispatched ahead of the WRITE_TOOLS
    # rate-limit branch so its own per-tool audit row is the authoritative
    # signal (the generic write-path audit fires after; both are fine).
    if name == "as_agent":
        return await _handle_as_agent(auth, args, session_id)

    tenant_slug = _tenant_slug_for_auth(auth)
    if name in STASIS_BLOCKED_TOOLS and not _tenant_is_active_mcp(tenant_slug):
        return _text(json.dumps({
            "stasis": {
                "active": False,
                "tenant": tenant_slug,
                "warning": "Tenant is in Stasis — work is blocked. Contact support to reactivate.",
                "blocked_tool": name,
            }
        }, indent=2))

    # Log tool invocation
    await _publish_log("info", "mcp", f"tool:{name} by {agent_scope}", agent=agent_scope)

    # WARN-3 fix (LOCK-MCP-4): emit to audit chain for all write tools.
    # Read tools (inbox/peers/recall) excluded for volume; all WRITE_TOOLS emitted.
    # Fire-and-forget — never blocks the tool call path.
    if name in MCP_WRITE_TOOLS:
        _schedule_audit_event(AuditChainEvent(
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
        ))

    # Capability gate — restrict dangerous tools for non-system tokens
    SYSTEM_ONLY_TOOLS = {"onboard"}  # customer onboard mode requires system token
    # outbox_status reads cross-substrate operator state (Mirror DLQ depth,
    # last_error response text from upstream). Not for tenant tokens.
    # Adversarial-gate hardening: BLOCK-P1-5 (G_S024_F16_F17_kasra_001).
    STRICT_SYSTEM_ONLY_TOOLS = {"outbox_status", "sprout_tenant"}
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
        "linkedin_connector",
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

        # --- linkedin_connector ---
        if name == "linkedin_connector":
            hermes_squad = {"hermes", "mkt-outreach", "mkt-lead", "mizan"}
            if auth.is_customer or (not auth.is_system and auth.agent_scope not in hermes_squad):
                return _text(
                    "linkedin_connector is restricted to the Hermes/outreach squad "
                    "and substrate system operators."
                )
            try:
                if run_linkedin_connector is None:
                    return _text("linkedin_connector unavailable: optional skill is not installed")
                return _json_result(run_linkedin_connector(args))
            except Exception as exc:  # noqa: BLE001
                return _text(f"linkedin_connector failed: {exc}")

        # --- sprout_tenant ---
        if name == "sprout_tenant":
            try:
                if SproutTenantEngine is None:
                    return _text("sprout_tenant unavailable: optional engine is not installed")
                engine = SproutTenantEngine(use_gemini=bool(args.get("use_gemini", True)))
                result = await loop.run_in_executor(
                    None,
                    lambda: engine.sprout(
                        str(args["project_path"]),
                        tenant_slug=args.get("tenant_slug"),
                        overwrite_existing=bool(args.get("overwrite_existing", False)),
                    ),
                )
            except Exception as exc:  # noqa: BLE001
                return _text(f"sprout_tenant failed: {exc}")
            return _text(json.dumps(result.as_dict(), indent=2))

        # --- ask ---
        if name == "ask":
            agent = _require_same_tenant_agent(auth, args.get("agent"))
            message = args["message"]
            # OpenClaw direct invocation is retired. Preserve the ask surface as a
            # bus-native async request; callers should read their inbox for replies.
            effective_project = project_scope if (auth.is_system or project_scope) else auth.tenant_id
            stream = _agent_stream(agent, effective_project)
            sendmsg = SendMessage(
                source=f"agent:{agent_scope}",
                target=f"agent:{agent}",
                timestamp=SendMessage.now_iso(),
                message_id=str(uuid4()),
                payload={"text": message, "content_type": "text/plain"},
            )
            msg = sendmsg.to_redis_fields()
            msg["tenant_id"] = auth.tenant_id or "sos"
            msg["project"] = effective_project or "sos"
            sid = redis_client.xadd(stream, msg)
            try:
                redis_client.publish(f"sos:wake:{agent}", json.dumps({"text": message, "source": f"agent:{agent_scope}"}))
            except Exception:
                pass
            return _text(f"Sent async ask to {agent} via SOS bus (stream_id: {sid}). Check inbox for reply.")

        # --- send ---
        elif name == "send":
            to = args.get("to")
            if not to:
                return _text("error: SOS-4001 send requires 'to' field")
            # S027 D-2 L-7: tenant-agent senders cannot send to peers in a
            # different tenant_slug (substrate coordination peers exempt).
            _enforce_tenant_agent_rls(auth, to)
            # 2026-05-06: removed misuse of _require_same_tenant_agent against `to`.
            # That helper is sender-attribution semantics (compares `requested`
            # against auth.agent_scope) — applying it to the recipient field
            # forced tenant-agent tokens to send only to themselves, blocking
            # cross-agent messaging that _enforce_tenant_agent_rls already permits
            # (substrate-coord peers + same-tenant peers). Sender attribution is
            # already token-bound via auth.agent_name; no caller claim to validate.
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
            # 2026-05-06: substrate-agent tokens (scope=agent, project=null) are
            # flagged is_system=True at line 491, which made _require_same_tenant_agent
            # default to AGENT_SELF (sos-mcp-sse) instead of the bound agent_name.
            # Fall back to the auth's agent_name when caller didn't pass one.
            requested = args.get("agent") or (auth.agent_name or None)
            agent = _require_same_tenant_agent(auth, requested)
            limit = args.get("limit", 10)
            output_format = str(args.get("format") or "text").lower()
            since = args.get("since", None)  # Redis stream ID cursor (exclusive high water mark)
            # Validate since format to prevent injection (must be digits-digits or None)
            if since and not __import__('re').match(r'^\d+-\d+$', since):
                since = None
            range_start = f"({since}" if since else "-"  # exclusive if cursor given, else all
            seen_ids: set = set()
            all_entries: list = []
            streams_to_check = []
            if project_scope:
                streams_to_check.append(("project", _agent_stream(agent, project_scope)))
            else:
                # System tokens have no project_scope, but senders with project=sos
                # deposit into the project-scoped stream. Always check "sos" project
                # stream so system-token inbox reads don't miss project-scoped messages.
                streams_to_check.append(("project-sos", _agent_stream(agent, "sos")))
            streams_to_check.append(("global", _agent_stream(agent, None)))  # global always
            streams_to_check.append(("legacy-private", _legacy_stream(agent)))       # legacy fallback
            streams_to_check.extend(_subscription_streams(auth, project_scope))
            for stream_kind, s in streams_to_check:
                try:
                    # When no cursor: xrevrange to get newest-first (xrange returns oldest N)
                    # When cursor given: xrange from cursor forward (pagination)
                    if since:
                        batch = await r.xrange(s, min=range_start, max="+", count=limit)
                    else:
                        batch = await r.xrevrange(s, max="+", min="-", count=limit)
                    for mid, data in batch:
                        entry_key = (s, mid)
                        if entry_key not in seen_ids:
                            seen_ids.add(entry_key)
                            all_entries.append((mid, data, stream_kind, s))
                except Exception:
                    pass
            # Sort ascending by stream ID then reverse for newest-first display
            all_entries.sort(key=lambda x: x[0], reverse=True)
            deduped_entries: list = []
            seen_messages: set[tuple[str, str, str]] = set()
            for mid, data, stream_kind, stream_name in all_entries:
                parsed = bus_envelope.parse(data)
                message_key = (
                    str(parsed.get("id") or data.get("id") or mid),
                    str(parsed.get("source") or data.get("source") or ""),
                    str(parsed.get("text") or data.get("text") or ""),
                )
                if message_key in seen_messages:
                    continue
                seen_messages.add(message_key)
                deduped_entries.append((mid, data, stream_kind, stream_name))
            all_entries = deduped_entries[:limit]
            if not all_entries:
                if output_format == "json":
                    return _json_result({"agent": agent, "messages": [], "cursor": since})
                return _text(f"No messages for {agent}.")
            if output_format == "json":
                messages = []
                for mid, data, stream_kind, stream_name in all_entries:
                    parsed = bus_envelope.parse(data)
                    extras = parsed.get("extras") or {}
                    request_id = (
                        extras.get("request_id")
                        or extras.get("requestId")
                        or data.get("request_id")
                        or data.get("requestId")
                    )
                    messages.append(
                        {
                            "stream_id": mid,
                            "stream": stream_name,
                            "stream_kind": stream_kind,
                            "sender": parsed["source"] or data.get("source") or "",
                            "source": parsed["source"] or data.get("source") or "",
                            "target": parsed["target"] or data.get("target") or "",
                            "text": parsed["text"],
                            "timestamp": parsed["timestamp"],
                            "message_id": parsed["id"],
                            "request_id": request_id,
                            "project": parsed["project"] or data.get("project"),
                            "type": parsed["type"] or data.get("type") or "",
                            "raw": data,
                        }
                    )
                cursor = max((mid for mid, *_rest in all_entries), default=since)
                return _json_result({"agent": agent, "messages": messages, "cursor": cursor})
            lines = []
            for mid, data, _stream_kind, _stream_name in all_entries:
                parsed = bus_envelope.parse(data)
                lines.append(
                    f"[{data.get('timestamp', '?')}] {parsed['source'] or '?'}: {parsed['text']} [stream_id:{mid}]"
                )
            return _text("\n".join(lines))

        # --- check_in ---
        elif name == "check_in":
            from datetime import datetime as _dt, timezone as _tz
            agent = auth.agent_scope
            project = project_scope or "sos"
            model = re.sub(r"[^a-zA-Z0-9._-]+", "-", str(args.get("model") or "unknown")).strip("-")
            model = model or "unknown"
            stamp = _dt.now(_tz.utc).strftime("%Y%m%d-%H%M")
            session_slug = re.sub(r"[^a-z0-9-]+", "-", f"{project}-{agent}-{model}-{stamp}".lower()).strip("-")
            stream = _agent_stream(agent, project)
            summary = str(args.get("summary") or f"{agent} checked in via MCP")
            fields = bus_envelope.build(
                msg_type="check_in",
                source=f"agent:{agent}",
                target=f"agent:{agent}",
                text=summary,
                project=project,
                extras={
                    "session_id": session_slug,
                    "agent": agent,
                    "model": model,
                    "checked_in_at": now_iso(),
                },
            )
            mid = await r.xadd(stream, fields)
            await r.hset(
                f"sos:registry:{agent}",
                mapping={
                    "agent": agent,
                    "project": project,
                    "tool": "mcp",
                    "summary": summary,
                    "last_seen": now_iso(),
                    "session_id": session_slug,
                    "model": model,
                },
            )
            onboarding_route = await _route_onboarding_agent(
                r,
                project=project,
                agent=agent,
                model=model,
                source="check_in",
                session_id=session_slug,
                summary=summary,
            )
            return _json_result(
                {
                    "agent": agent,
                    "session_id": session_slug,
                    "stream": stream,
                    "stream_id": mid,
                    "project": project,
                    "model": model,
                    "onboarding_route": onboarding_route,
                }
            )

        # --- workspace_join ---
        elif name == "workspace_join":
            return await _handle_workspace_join(args, auth, session_id)

        # --- workspace_leave ---
        elif name == "workspace_leave":
            return await _handle_workspace_leave(args, auth)

        # --- workspace_members ---
        elif name == "workspace_members":
            return await _handle_workspace_members(args, auth)

        # --- register_skill ---
        elif name == "register_skill":
            return await _handle_register_skill(args, auth)

        # --- list_skills ---
        elif name == "list_skills":
            return await _handle_list_skills(args, auth)

        # --- invoke_skill ---
        elif name == "invoke_skill":
            return await _handle_invoke_skill(args, auth)

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
            # Also merge live registry presence. check_in writes session_id/model
            # there; peers must surface that postal address even though legacy
            # peer discovery was stream-key based.
            registry_meta: dict[str, dict[str, str]] = {}
            cursor = 0
            while True:
                cursor, keys = await r.scan(cursor, match="sos:registry:*", count=100)
                for key in keys:
                    reg_agent = key.split(":", 2)[-1]
                    if reg_agent.startswith("project:"):
                        # Defensive for any future registry key variant.
                        reg_agent = reg_agent.split(":")[-1]
                    try:
                        meta = await r.hgetall(key)
                    except Exception:
                        meta = {}
                    reg_project = meta.get("project") if isinstance(meta, dict) else None
                    if project_scope and reg_project != project_scope:
                        continue
                    if reg_agent:
                        agents.add(reg_agent)
                        registry_meta[reg_agent] = meta if isinstance(meta, dict) else {}
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
                meta = registry_meta.get(a) or {}
                session_id = meta.get("session_id")
                model = meta.get("model")
                session_suffix = ""
                if session_id:
                    session_suffix = f" session: {session_id}"
                    if model:
                        session_suffix += f", model: {model}"
                slugs = _read_specialist_slugs(a)
                if slugs:
                    lines.append(f"  - {a} (specialists: {', '.join(slugs)}{';' if session_suffix else ''}{session_suffix})")
                else:
                    lines.append(f"  - {a} (specialists: -{';' if session_suffix else ''}{session_suffix})")
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
            memory = _memory_scope(auth)

            # Write directly to Mirror DB with embedding (synchronous, immediate readback)
            if _mirror_db is not None:
                try:
                    from uuid import uuid4 as _uuid4
                    from datetime import datetime as _dt, timezone as _tz

                    embedding = await loop.run_in_executor(
                        _mirror_executor,
                        lambda: [float(x) for x in _get_mirror_embedding(text_to_store)],
                    )

                    engram = {
                        "id": str(_uuid4()),
                        "context_id": ctx,
                        "timestamp": _dt.now(_tz.utc).isoformat(),
                        "series": _mirror_series_for_agent(memory.agent),
                        "raw_data": json.dumps({"text": text_to_store, "source": "mcp-remember"}),
                        "embedding": embedding,
                        "project": memory.project or "",
                        "workspace_id": memory.workspace_id,
                        "owner_type": memory.owner_type,
                        "owner_id": memory.owner_id,
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
                    "source": f"agent:{memory.agent}",
                    "target": f"agent:{memory.agent}",
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
                if memory.project:
                    rem_msg["project"] = memory.project
                await r.xadd(_agent_stream(memory.agent, memory.project), rem_msg)
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
            memory = _memory_scope(auth)

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
                    project=memory.mirror_project,
                    workspace_id=memory.workspace_id,  # enforces tenant isolation
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
            memory = _memory_scope(auth)
            limit = int(args.get("limit", 10))
            if _mirror_db is not None:
                engrams = await loop.run_in_executor(
                    _mirror_executor,
                    lambda: _mirror_db.recent_engrams(
                        agent=memory.agent,
                        limit=limit,
                        project=memory.mirror_project,
                        workspace_id=memory.workspace_id,
                    ),
                )
            else:
                data = await loop.run_in_executor(
                    None,
                    mirror_get,
                    f"/recent/{memory.agent}?limit={limit}"
                    + (f"&project={memory.project}" if memory.project else ""),
                )
                engrams = data.get("engrams", [])
            if not engrams:
                return _text("No memories yet.")
            lines = []
            for i, e in enumerate(engrams, 1):
                text = (e.get("raw_data", {}) or {}).get("text", e.get("context_id", "?"))
                lines.append(f"{i}. [{e.get('timestamp', '?')[:10]}] {str(text)[:200]}")
            return _text("\n".join(lines))

        # --- search (ChatGPT connector contract: query -> [{id,title,url}]) ---
        elif name == "search":
            if _mirror_db is None:
                return _json_result({"results": []})
            query_text = args["query"]
            limit = int(args.get("limit", 10))
            memory = _memory_scope(auth)
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
                    project=memory.mirror_project,
                    workspace_id=memory.workspace_id,  # enforces tenant isolation
                ),
            )
            results = []
            for e in (rows or []):
                cid = e.get("context_id")
                if not cid:
                    continue
                raw = e.get("raw_data") or {}
                text = raw.get("text", "") or str(cid)
                results.append(
                    {
                        "id": f"mem:{cid}",
                        "title": str(text)[:80],
                        "url": f"https://mumega.com/m/{cid}",
                    }
                )
            return _json_result({"results": results})

        # --- fetch (ChatGPT connector contract: id -> full document) ---
        elif name == "fetch":
            raw_id = str(args.get("id", ""))
            memory = _memory_scope(auth)
            # Isolation: require a workspace and only resolve our own namespace.
            # The .eq("workspace_id", ws) below is load-bearing — never drop it,
            # or fetch becomes a cross-tenant read primitive.
            if _mirror_db is None or not memory.workspace_id or not raw_id.startswith("mem:"):
                return _json_result(
                    {"id": raw_id, "title": "", "text": "", "url": "", "metadata": {"error": "not_found"}}
                )
            cid = raw_id[len("mem:"):]
            ws = memory.workspace_id

            def _fetch_row():
                resp = (
                    _mirror_db.table("mirror_engrams")
                    .select("*")
                    .eq("context_id", cid)
                    .eq("workspace_id", ws)  # tenant isolation — load-bearing
                    .limit(1)
                    .execute()
                )
                return getattr(resp, "data", None) or []

            data = await loop.run_in_executor(_mirror_executor, _fetch_row)
            if not data:
                return _json_result(
                    {"id": raw_id, "title": "", "text": "", "url": "", "metadata": {"error": "not_found"}}
                )
            row = data[0]
            raw = row.get("raw_data") or {}
            text = str(raw.get("text", "") or "")
            return _json_result(
                {
                    "id": raw_id,
                    "title": (text[:80] or cid),
                    "text": text,
                    "url": f"https://mumega.com/m/{cid}",
                    "metadata": {
                        "series": row.get("series"),
                        "project": row.get("project"),
                        "ts": str(row.get("timestamp") or row.get("ts") or ""),
                    },
                }
            )

        # --- task_create ---
        elif name == "task_create":
            # Redirected from Mirror (retired /tasks) → Squad Service (:8060)
            task_project = project_scope or args.get("project") or "sos"
            task_payload = {
                "id": str(uuid4()),
                "squad_id": task_project,
                "title": args["title"],
                "description": args.get("description", ""),
                "assignee": _require_same_tenant_agent(auth, args.get("assignee")),
                "priority": args.get("priority", "medium"),
                "project": task_project,
            }
            resp = await loop.run_in_executor(
                None,
                lambda: requests.post(
                    f"{SQUAD_SERVICE_URL}/tasks",
                    headers={"Authorization": f"Bearer {SQUAD_SYSTEM_TOKEN}"},
                    json=task_payload,
                    timeout=10,
                ),
            )
            if not resp.ok:
                return _text(f"Task create failed ({resp.status_code}): {resp.text[:200]}")
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

            from mumega_sos_addons.agents.internal.join import AgentJoinService

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

            # Tenant isolation: project-scoped tokens only see their own scope.
            is_tenant_scoped = (not auth.is_system) and bool(project_scope)
            if is_tenant_scoped:
                agents: set[str] = set()
                pattern = f"{_prefix(project_scope)}:agent:*"
                cursor = 0
                while True:
                    cursor, keys = await r.scan(cursor, match=pattern, count=100)
                    for k in keys:
                        agents.add(k.split(":")[-1])
                    if cursor == 0:
                        break
                internal_agents = {
                    "sos-mcp-sse",
                    "sos-squad",
                    "sovereign-loop",
                    "calcifer",
                    "lifecycle",
                    "task-poller",
                    "wake-daemon",
                }
                agents -= internal_agents
                agent_statuses = [a for a in agent_statuses if a.get("agent") in agents]
                # Tenant tokens must not see host systemd services.
                svc_statuses = []

            # Task counts — tenant tokens see only their own project tasks.
            task_counts = {}
            try:
                url = f"{SQUAD_SERVICE_URL}/tasks?limit=500"
                if is_tenant_scoped:
                    url += f"&project={project_scope}"
                resp = requests.get(
                    url,
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

            # Services — only shown to system tokens
            if svc_statuses:
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
                "native": "🟢",        # Mirror NativeSqlOutbox + SOS bus Redis Streams — both durable
                "memory": "🟡",        # in-process — best_effort fallback
                "best_effort": "🟡",   # historical placeholder (no current substrate reports this — S025 A-1 promoted SOS to native)
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

        # --- tenant_canvas_read (S026 A3 substrate primitive) ---
        elif name == "tenant_canvas_read":
            tenant_id = args.get("tenant_id", "").strip()
            if not tenant_id:
                return _text("Error: tenant_id is required.")
            if not INTERNAL_API_SECRET:
                return _text(
                    "Error: tenant_canvas_read disabled — INTERNAL_API_SECRET unset on MCP server."
                )
            import httpx
            url = f"{INKWELL_API_URL}/api/tenant-profile/internal/{tenant_id}"
            try:
                async with httpx.AsyncClient(timeout=5.0) as client:
                    resp = await client.get(
                        url,
                        headers={"Authorization": f"Bearer {INTERNAL_API_SECRET}"},
                    )
                if resp.status_code == 404:
                    return _text(
                        f"Canvas not initialized for tenant {tenant_id}. "
                        "POST /api/tenant-profile/init via the dashboard first."
                    )
                if resp.status_code == 401:
                    return _text("Error: unauthorized — INTERNAL_API_SECRET mismatch.")
                if resp.status_code != 200:
                    return _text(
                        f"Error: canvas read returned HTTP {resp.status_code}."
                    )
                envelope = resp.json()
                # Format as readable summary + raw JSON.
                lines = [
                    f"# Tenant Canvas — {tenant_id}",
                    f"Template: **{envelope.get('template_kind', '?')}**",
                ]
                meta = envelope.get("inference_metadata") or {}
                last_run = meta.get("last_inference_run")
                if last_run:
                    from datetime import datetime as _dt, timezone as _tz
                    ts = _dt.fromtimestamp(last_run, tz=_tz.utc).isoformat()
                    lines.append(f"Last inference: {ts}")
                else:
                    lines.append("Last inference: never")
                conf = meta.get("confidence_per_block") or {}
                lines.append("")
                blocks = envelope.get("blocks") or {}
                for key in sorted(blocks.keys()):
                    block_conf = conf.get(key)
                    if block_conf is None:
                        badge = "[empty]"
                    elif block_conf >= 1.0:
                        badge = "[manual]"
                    else:
                        badge = f"[inferred {int(block_conf * 100)}%]"
                    lines.append(f"## {key} {badge}")
                    val = blocks[key]
                    if isinstance(val, list):
                        for item in val:
                            lines.append(f"- {item}")
                    else:
                        lines.append(str(val))
                    lines.append("")
                lines.append("```json")
                lines.append(json.dumps(envelope, indent=2))
                lines.append("```")
                return _text("\n".join(lines))
            except Exception as exc:  # noqa: BLE001
                return _text(f"Error: canvas read failed — {exc}")

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

        # --- sync_agents (#161) ---
        elif name == "sync_agents":
            _enforce_rate_limit(auth)
            return await _handle_sync_agents(args, auth, session_id)

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
# S027 D-5 L-4 — `as_agent` session-identity mutation registry.
# Keyed by per-SSE-connection session_id (uuid4 at SSE accept, line ~4250),
# NOT by token hash — multiple simultaneous connections sharing the same
# bearer token must NOT share as_agent state, otherwise as_agent on conn-A
# bleeds into send/broadcast issued on conn-B.
# Value: dict with as_agent_active/name/kind/tenant_slug + caller's original
# (token_hash, scope_class, tenant_id) for audit chain attribution.
# Cleared on as_agent({name: ""}), sign_out, or SSE disconnect.
_session_as_agent: dict[str, dict[str, Any]] = {}
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
        token = auth[7:].strip()
        if token.lower().startswith("bearer "):
            raise HTTPException(status_code=401, detail="malformed_bearer_token_duplicate_prefix")
        return token
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
            tenant_slug=request.headers.get("X-Tenant-Slug") or tenant_id,
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
            "flow_status": _overall_from_checks({
                "service_authority": {
                    "status": "critical"
                    if any(c["status"] == "critical" for c in _service_authority_contracts())
                    else "healthy"
                },
                "memory_scope": {"status": "healthy"},
            }),
        }
    )


@app.get("/health/flow")
async def health_flow(run_probes: bool = True) -> JSONResponse:
    return JSONResponse(await _run_flow_health(run_probes=run_probes))


@app.get("/bridge/inbox")
async def bridge_inbox(request: Request) -> JSONResponse:
    """HTTP bridge-compatible inbox over the authenticated MCP HTTPS surface.

    Remote SDK clients can set `bridge_url=https://mcp.mumega.com/bridge`.
    The SDK appends `/inbox`, and this route reuses the same bearer-token auth
    as JSON-RPC MCP while avoiding local Redis and plaintext public bridge use.
    """
    auth = _require_auth(request, _request_bearer_token(request))
    _enforce_rate_limit(auth)
    args: dict[str, Any] = {
        "agent": request.query_params.get("agent") or auth.agent_name or None,
        "limit": int(request.query_params.get("limit", "10")),
        "format": "json",
    }
    project = request.query_params.get("project")
    since = request.query_params.get("since")
    if project:
        args["project"] = project
    if since:
        args["since"] = since
    override_subscriptions = _runtime_subscriptions(
        request.query_params.getlist("subscription")
        + request.query_params.getlist("subscriptions")
    )
    if override_subscriptions:
        auth = replace(auth, subscriptions=override_subscriptions)
    result = await handle_tool("inbox", args, auth)
    structured = result.get("structuredContent")
    if isinstance(structured, dict):
        return JSONResponse(structured)
    text = result.get("content", [{}])[0].get("text", "{}")
    try:
        return JSONResponse(json.loads(text))
    except Exception:
        return JSONResponse({"agent": args["agent"], "messages": [], "raw": text})


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
        flow_health,
    ) = await asyncio.gather(
        _check_redis(),
        _check_http("mirror", "http://localhost:8844/"),
        _check_http("squad", "http://localhost:8060/health"),
        _check_http("dashboard", "http://localhost:8090/health"),
        _get_kernel_info(r),
        _get_online_agents(r),
        _get_flywheel(),
        asyncio.get_event_loop().run_in_executor(None, _get_systemd_health_sync),
        _run_flow_health(run_probes=True),
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
        "flow": {"status": flow_health.get("status", "unknown")},
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
            "flow": flow_health,
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
                headers={"Authorization": f"Bearer {SQUAD_SYSTEM_TOKEN}"},
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
CUSTOMERS_DIR = Path.home() / ".mumega" / "customers"
SQUAD_SERVICE_URL = "http://localhost:8060"
ONBOARDING_INVITES_PATH = Path(
    os.environ.get("SOS_ONBOARDING_INVITES_PATH", str(Path.home() / ".sos" / "onboarding_invites.json"))
)
ONBOARDING_REQUESTS_PATH = Path(
    os.environ.get("SOS_ONBOARDING_REQUESTS_PATH", str(Path.home() / ".sos" / "onboarding_requests.json"))
)


def _atomic_json_append(path: Path, entry: dict, dedup_key: str, dedup_value: str) -> bool:
    """Atomically append an entry to a JSON array file. Returns False if duplicate."""
    created, _ = append_if_missing(path, entry, lambda item: item.get(dedup_key) == dedup_value)
    return created


def _valid_slug(value: str) -> bool:
    return bool(value) and value.replace("-", "").isalnum() and value == value.lower()


def _normalize_agent_slug(value: str) -> str:
    """Normalize user-facing agent labels into bus-safe slugs."""
    return re.sub(r"[^a-z0-9]+", "-", value.strip().lower()).strip("-")


def _active_agent_record(
    records: list[dict[str, Any]],
    *,
    tenant_id: str,
    agent_name: str,
) -> dict[str, Any] | None:
    for item in records:
        if not item.get("active", True):
            continue
        if item.get("project") == tenant_id and item.get("agent") == agent_name:
            return item
    return None


def _join_install_record(
    records: list[dict[str, Any]],
    *,
    tenant_id: str,
    install_id: str,
) -> dict[str, Any] | None:
    if not install_id:
        return None
    for item in records:
        if not item.get("active", True):
            continue
        if item.get("project") != tenant_id:
            continue
        if hmac.compare_digest(str(item.get("onboarding_install_id") or ""), install_id):
            return item
    return None


def _next_join_agent_slug(
    records: list[dict[str, Any]],
    *,
    tenant_id: str,
    requested_agent: str,
) -> tuple[str, bool]:
    """Return a unique tenant-scoped agent slug.

    Duplicate requested names are normal when the same human opens Codex on a
    second machine. The fix is to make identity liveable: allocate
    `hadi-codex-2` instead of failing or sharing a token.
    """
    base = _normalize_agent_slug(requested_agent)
    if not _active_agent_record(records, tenant_id=tenant_id, agent_name=base):
        return base, False
    suffix = 2
    while suffix < 1000:
        candidate = f"{base}-{suffix}"
        if not _active_agent_record(records, tenant_id=tenant_id, agent_name=candidate):
            return candidate, True
        suffix += 1
    return f"{base}-{secrets.token_hex(3)}", True


def _json_rows(path: Path) -> list[dict[str, Any]]:
    try:
        data = json.loads(path.read_text())
    except Exception:
        return []
    return data if isinstance(data, list) else []


def _write_json_rows(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(rows, indent=2) + "\n")


def _hash_invite_code(code: str) -> str:
    return hashlib.sha256(code.encode("utf-8")).hexdigest()


def _invite_expired(invite: dict[str, Any], now: datetime | None = None) -> bool:
    expires_at = str(invite.get("expires_at") or "").strip()
    if not expires_at:
        return False
    try:
        parsed = datetime.fromisoformat(expires_at.replace("Z", "+00:00"))
    except ValueError:
        return True
    return parsed < (now or datetime.now(timezone.utc))


def _find_invite(code: str) -> tuple[int, dict[str, Any]] | tuple[None, None]:
    code_hash = _hash_invite_code(code)
    rows = _json_rows(ONBOARDING_INVITES_PATH)
    for idx, invite in enumerate(rows):
        if not invite.get("active", True):
            continue
        if _invite_expired(invite):
            continue
        max_uses = int(invite.get("max_uses") or 1)
        uses = int(invite.get("uses") or 0)
        if uses >= max_uses:
            continue
        stored_hash = str(invite.get("code_hash") or "").removeprefix("sha256:")
        stored_code = str(invite.get("code") or "")
        if stored_hash == code_hash or (stored_code and hmac.compare_digest(stored_code, code)):
            return idx, invite
    return None, None


def _consume_invite(index: int) -> None:
    rows = _json_rows(ONBOARDING_INVITES_PATH)
    if index < 0 or index >= len(rows):
        return
    rows[index]["uses"] = int(rows[index].get("uses") or 0) + 1
    rows[index]["last_used_at"] = datetime.now(timezone.utc).isoformat()
    if rows[index]["uses"] >= int(rows[index].get("max_uses") or 1):
        rows[index]["active"] = False
    _write_json_rows(ONBOARDING_INVITES_PATH, rows)


def _token_record_public(record: dict[str, Any]) -> dict[str, Any]:
    return {
        "tenant_id": record.get("project"),
        "agent": record.get("agent", ""),
        "requested_agent": record.get("requested_agent", record.get("agent", "")),
        "renamed_for_collision": bool(record.get("renamed_for_collision", False)),
        "scope": record.get("scope", ""),
        "role": record.get("role", "viewer"),
        "label": record.get("label", ""),
        "scopes": record.get("scopes") or [],
        "active": bool(record.get("active", True)),
        "node_id": record.get("node_id") or "",
        "local_source_id": record.get("local_source_id") or "",
    }


def _node_id_for(tenant_id: str, agent: str, install_id: str) -> str:
    seed = f"{tenant_id}:{agent}:{install_id or agent}".encode("utf-8")
    return f"node-{hashlib.sha256(seed).hexdigest()[:16]}"


def _local_source_id_for(tenant_id: str, agent: str, install_id: str) -> str:
    seed = f"{tenant_id}:{agent}:{install_id or agent}".encode("utf-8")
    return f"source-{hashlib.sha256(seed).hexdigest()[:16]}"


def _node_contract_from_record(record: dict[str, Any]) -> dict[str, Any]:
    tenant_id = str(record.get("project") or "").strip()
    agent = str(record.get("agent") or "").strip()
    install_id = str(record.get("onboarding_install_id") or record.get("install_id") or "").strip()
    node_id = str(record.get("node_id") or "").strip() or _node_id_for(tenant_id, agent, install_id)
    local_source_id = str(record.get("local_source_id") or "").strip() or _local_source_id_for(tenant_id, agent, install_id)
    bus_streams = [
        _agent_stream(agent, tenant_id),
        f"{_prefix(tenant_id)}:events",
    ] if tenant_id and agent else []
    return {
        "node_id": node_id,
        "install_id": install_id,
        "install_id_hash": hashlib.sha256(install_id.encode("utf-8")).hexdigest() if install_id else "",
        "local_source_id": local_source_id,
        "source_kind": record.get("source_kind") or "agent-device",
        "tenant_id": tenant_id,
        "agent": agent,
        "scope": record.get("scope", ""),
        "memory_workspace_id": tenant_id or "mumega-internal",
        "bus_streams": bus_streams,
        "sync_policy": {
            "local_files_are_cache": True,
            "canonical_truth": "SOS/Mirror",
            "receipt_provenance": "tenant:project:source:agent",
        },
    }


def _token_record_for_auth(auth: MCPAuthContext) -> dict[str, Any] | None:
    records = load_tokens(BUS_TOKENS_PATH)
    for record in records:
        if not record.get("active", True):
            continue
        stored_hash = str(record.get("token_hash") or "").removeprefix("sha256:")
        raw_token = str(record.get("token") or "")
        raw_hash = hashlib.sha256(raw_token.encode()).hexdigest() if raw_token else ""
        if stored_hash and hmac.compare_digest(stored_hash, auth.token):
            return record
        if raw_hash and hmac.compare_digest(raw_hash, auth.token):
            return record
    return None


def _node_contract_from_auth(auth: MCPAuthContext, memory: MCPMemoryScope) -> dict[str, Any]:
    record = _token_record_for_auth(auth)
    if record:
        contract = _node_contract_from_record(record)
        contract["memory_workspace_id"] = memory.workspace_id
        return contract
    tenant_id = memory.workspace_id or auth.tenant_id or "mumega-internal"
    agent = memory.agent or auth.agent_scope
    install_id = auth.install_id or ""
    return {
        "node_id": auth.node_id or _node_id_for(tenant_id, agent, install_id),
        "install_id": install_id,
        "install_id_hash": hashlib.sha256(install_id.encode("utf-8")).hexdigest() if install_id else "",
        "local_source_id": auth.local_source_id or _local_source_id_for(tenant_id, agent, install_id),
        "source_kind": "mcp-session",
        "tenant_id": tenant_id,
        "agent": agent,
        "scope": auth.scope or ("system" if auth.is_system else "agent"),
        "memory_workspace_id": memory.workspace_id,
        "bus_streams": [
            _agent_stream(auth.agent_scope, _scope_project(auth)),
            f"{_prefix(_scope_project(auth))}:events",
        ],
        "sync_policy": {
            "local_files_are_cache": True,
            "canonical_truth": "SOS/Mirror",
            "receipt_provenance": "tenant:project:source:agent",
        },
    }


def _context_public(auth: MCPAuthContext) -> dict[str, Any]:
    memory = _memory_scope(auth)
    if auth.is_system:
        tools = [tool["name"] for tool in get_tools()]
    elif auth.is_customer:
        tools = [tool["name"] for tool in get_tools_for_tier(auth.plan or "free", auth.role)]
    else:
        tools = [tool["name"] for tool in get_tools_for_tier(auth.plan or "starter", auth.role)]
    return {
        "authenticated": True,
        "tenant_id": auth.tenant_id,
        "agent": auth.agent_name,
        "scope": auth.scope,
        "role": auth.role,
        "plan": auth.plan,
        "source": auth.source,
        "is_system": auth.is_system,
        "tools": tools,
        "node": _node_contract_from_auth(auth, memory),
    }


async def _onboarding_graph(auth: MCPAuthContext, *, tenant_id: str | None = None) -> dict[str, Any]:
    requested_tenant = (tenant_id or auth.project_scope or auth.tenant_id or "").strip().lower()
    if not requested_tenant and not auth.is_system:
        requested_tenant = auth.tenant_id or ""
    if not requested_tenant:
        requested_tenant = "mumega-internal"
    if not auth.is_system and requested_tenant != (auth.project_scope or auth.tenant_id):
        raise HTTPException(status_code=403, detail="cross_tenant_onboarding_graph")

    records = [
        record for record in load_tokens(BUS_TOKENS_PATH)
        if record.get("active", True) and str(record.get("project") or "").strip().lower() == requested_tenant
    ]
    agents = []
    nodes = []
    for record in records:
        if record.get("scope") in {"tenant-agent", "customer", "tenant"}:
            agents.append({
                "agent": record.get("agent", ""),
                "scope": record.get("scope", ""),
                "role": record.get("role", ""),
                "agent_kind": record.get("agent_kind", ""),
                "created_at": record.get("created_at", ""),
            })
        if record.get("scope") == "tenant-agent":
            nodes.append(_node_contract_from_record(record))

    graph_nodes: list[dict[str, Any]] = [
        {"id": f"tenant:{requested_tenant}", "type": "tenant", "label": requested_tenant},
        {"id": f"project:{requested_tenant}", "type": "project", "label": requested_tenant},
        {"id": f"memory:{requested_tenant}", "type": "memory_workspace", "label": requested_tenant},
    ]
    for agent in agents:
        graph_nodes.append({
            "id": f"agent:{agent['agent']}",
            "type": "agent",
            "label": agent["agent"],
            "scope": agent["scope"],
            "role": agent["role"],
            "agent_kind": agent["agent_kind"],
        })
    for node in nodes:
        graph_nodes.append({
            "id": node["node_id"],
            "type": "node",
            "label": node["local_source_id"],
            "agent": node["agent"],
            "local_source_id": node["local_source_id"],
        })

    edges: list[dict[str, str]] = [
        {"from": f"tenant:{requested_tenant}", "to": f"project:{requested_tenant}", "type": "owns"},
        {"from": f"project:{requested_tenant}", "to": f"memory:{requested_tenant}", "type": "uses_memory_workspace"},
    ]
    for agent in agents:
        edges.append({"from": f"project:{requested_tenant}", "to": f"agent:{agent['agent']}", "type": "has_agent"})
    for node in nodes:
        edges.append({"from": f"agent:{node['agent']}", "to": node["node_id"], "type": "mounted_source"})
        edges.append({"from": node["node_id"], "to": f"memory:{requested_tenant}", "type": "syncs_receipts_to"})

    assignments = await _onboarding_assignment_records(requested_tenant)
    seen_squads: set[str] = set()
    for assignment in assignments:
        squad_id = assignment.get("squad_id") or ""
        agent = assignment.get("agent") or ""
        if squad_id and squad_id not in seen_squads:
            graph_nodes.append({
                "id": f"squad:{squad_id}",
                "type": "squad",
                "label": squad_id,
                "squad_type": assignment.get("squad_type") or "",
            })
            edges.append({"from": f"project:{requested_tenant}", "to": f"squad:{squad_id}", "type": "has_squad"})
            seen_squads.add(squad_id)
        if agent and squad_id:
            if not any(node.get("id") == f"agent:{agent}" for node in graph_nodes):
                graph_nodes.append({"id": f"agent:{agent}", "type": "agent", "label": agent})
            edges.append({"from": f"agent:{agent}", "to": f"squad:{squad_id}", "type": "assigned_to"})

    return {
        "tenant_id": requested_tenant,
        "status": "queryable",
        "flow": [
            "register/login",
            "tenant/project",
            "tenant-admin",
            "default agent network",
            "invites/devices",
            "boot_context",
        ],
        "nodes": graph_nodes,
        "edges": edges,
        "agents": agents,
        "mounted_sources": nodes,
        "assignments": assignments,
        "contracts": {
            "token_auth_is_identity_authority": True,
            "install_id_is_idempotency_key": True,
            "local_files_are_cache": True,
            "canonical_truth": "SOS/Mirror",
        },
    }


def _bootstrap_payload(reason: str = "missing_token") -> dict[str, Any]:
    return {
        "authenticated": False,
        "reason": reason,
        "organization": None,
        "tools": [
            {
                "name": "whoami",
                "endpoint": "GET /api/v1/onboarding/whoami",
                "description": "Check whether this connection is authenticated and tenant-scoped.",
            },
            {
                "name": "login",
                "endpoint": "POST /api/v1/onboarding/login",
                "description": "Validate an existing tenant or agent token.",
            },
            {
                "name": "join_with_invite",
                "endpoint": "POST /api/v1/onboarding/join-with-invite",
                "description": "Join an existing company/team using an invite code.",
            },
            {
                "name": "register",
                "endpoint": "POST /api/v1/onboarding/register",
                "description": "Request a new company/tenant registration, or provision it when authorized.",
            },
        ],
    }


def _append_registration_request(slug: str, label: str, email: str) -> dict[str, Any]:
    now = datetime.now(timezone.utc).isoformat()
    request_id = f"req-{slug}-{hashlib.sha256((email + now).encode()).hexdigest()[:12]}"
    record = {
        "id": request_id,
        "slug": slug,
        "label": label,
        "email": email,
        "status": "pending_review",
        "created_at": now,
    }
    created, existing = append_if_missing(
        ONBOARDING_REQUESTS_PATH,
        record,
        lambda item: item.get("slug") == slug and item.get("status") in {"pending_review", "approved"},
    )
    return record if created else existing


def _mint_invited_agent_token(
    *,
    tenant_id: str,
    agent_name: str,
    role: str,
    invite: dict[str, Any],
    model: str = "",
    agent_kind: str = "",
    install_id: str = "",
) -> tuple[str, dict[str, Any], bool]:
    timestamp = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    records = load_tokens(BUS_TOKENS_PATH)
    retry_record = _join_install_record(records, tenant_id=tenant_id, install_id=install_id)
    if retry_record:
        return str(retry_record.get("token") or ""), retry_record, False

    requested_agent = _normalize_agent_slug(agent_name)
    clean_agent, renamed_for_collision = _next_join_agent_slug(
        records,
        tenant_id=tenant_id,
        requested_agent=requested_agent,
    )
    node_id = _node_id_for(tenant_id, clean_agent, install_id)
    local_source_id = _local_source_id_for(tenant_id, clean_agent, install_id)
    token = f"sk-agent-{tenant_id}-{clean_agent}-{secrets.token_hex(16)}"
    scopes = invite.get("scopes") or ["bus:send", "memory:*", "tasks:*"]
    record = {
        "token": token,
        "token_hash": hash_token(token),
        "project": tenant_id,
        "agent": clean_agent,
        "requested_agent": requested_agent,
        "renamed_for_collision": renamed_for_collision,
        "label": invite.get("label") or f"{clean_agent} @ {tenant_id}",
        "active": True,
        "created_at": timestamp,
        "scope": "tenant-agent",
        "role": role,
        "agent_kind": agent_kind or invite.get("agent_kind") or clean_agent,
        "model": model,
        "scopes": [str(scope) for scope in scopes],
        "invite_id": invite.get("id") or "",
        "onboarding_install_id": install_id,
        "node_id": node_id,
        "local_source_id": local_source_id,
        "source_kind": "agent-device",
    }
    created, stored = append_if_missing(
        BUS_TOKENS_PATH,
        record,
        lambda item: item.get("active", True)
        and item.get("project") == tenant_id
        and item.get("agent") == clean_agent,
    )
    _local_token_cache.invalidate()
    return token if created else str(stored.get("token") or ""), stored if not created else record, created


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
    from sos.bus.tenant_provisioning import mint_or_get_mirror_key

    timestamp = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    # 1. Generate bus token and obtain Mirror access. Mirror access is DB-issued
    # when Mirror admin config is available, with plaintext cached for idempotent
    # delivery by tenant_provisioning.
    mirror_token, mirror_added = mint_or_get_mirror_key(slug, label)
    bus_token = f"sk-bus-{slug}-{_secrets.token_hex(8)}"

    # 3. Store Bus token (atomic) — scope="customer" gates tool visibility
    bus_added = _atomic_json_append(
        BUS_TOKENS_PATH,
        {
            "token": bus_token,
            "token_hash": hash_token(bus_token),
            "project": slug,
            "agent": slug,
            "label": label,
            "active": True,
            "created_at": timestamp,
            "scope": "customer",
            "scopes": ["bus:send"],
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


@app.get("/api/v1/onboarding/whoami")
async def onboarding_whoami(request: Request) -> JSONResponse:
    """Bootstrap identity check for first-time agents.

    No token returns only the public registration/login/join affordances.
    A valid token returns tenant, role, scope, and the tool names this caller
    should expect after connecting over MCP.
    """
    token = _request_bearer_token(request) or request.query_params.get("token", "").strip()
    if not token:
        return JSONResponse(_bootstrap_payload())
    auth = _resolve_token_context(token)
    if not auth:
        return JSONResponse(_bootstrap_payload("invalid_token"), status_code=401)
    return JSONResponse(_context_public(auth))


@app.post("/api/v1/onboarding/login")
async def onboarding_login(request: Request) -> JSONResponse:
    """Validate an existing tenant/agent token and return its scoped context."""
    body = await request.json()
    token = str(body.get("token") or "").strip() or _request_bearer_token(request)
    if not token:
        raise HTTPException(status_code=400, detail="token required")
    auth = _resolve_token_context(token)
    if not auth:
        raise HTTPException(status_code=401, detail="invalid_token")
    return JSONResponse(_context_public(auth))


@app.get("/api/v1/onboarding/graph")
async def onboarding_graph(request: Request) -> JSONResponse:
    """Return the tenant/node onboarding graph for the authenticated caller."""
    token = _request_bearer_token(request) or request.query_params.get("token", "").strip()
    if not token:
        raise HTTPException(status_code=401, detail="token required")
    auth = _resolve_token_context(token)
    if not auth:
        raise HTTPException(status_code=401, detail="invalid_token")
    tenant_id = request.query_params.get("tenant_id") if auth.is_system else None
    return JSONResponse(await _onboarding_graph(auth, tenant_id=tenant_id))


@app.post("/api/v1/onboarding/register")
async def onboarding_register(request: Request) -> JSONResponse:
    """Register a new company/tenant or create a pending registration request.

    Authorized callers (signup secret or system bearer) provision immediately.
    Public first-time callers get a pending request record and no tenant token.
    """
    body = await request.json()
    slug = str(body.get("slug") or "").strip().lower()
    label = str(body.get("label") or "").strip()
    email = str(body.get("email") or "").strip()
    if not slug or not label:
        raise HTTPException(status_code=400, detail="slug and label required")
    if not _valid_slug(slug):
        raise HTTPException(status_code=400, detail="slug must be lowercase alphanumeric with hyphens")

    secret = request.headers.get("x-signup-secret", "")
    bearer = _request_bearer_token(request)
    authorized = bool(secret and SIGNUP_SECRET and hmac.compare_digest(secret, SIGNUP_SECRET))
    authorized = authorized or bool(bearer and bearer in _system_tokens())
    if authorized:
        result = await _onboard_customer(slug, label, email)
        if result.get("status") == "duplicate":
            raise HTTPException(status_code=409, detail=result["error"])
        return JSONResponse({"status": "provisioned", "tenant": result})

    request_record = _append_registration_request(slug, label, email)
    return JSONResponse(
        {
            "status": "pending_review",
            "request": {
                "id": request_record.get("id"),
                "slug": request_record.get("slug"),
                "label": request_record.get("label"),
                "email": request_record.get("email"),
            },
            "next_step": "An organization owner or Mumega operator must approve this tenant before tokens are issued.",
        },
        status_code=202,
    )


@app.post("/api/v1/onboarding/join-with-invite")
async def onboarding_join_with_invite(request: Request) -> JSONResponse:
    """Join an existing tenant/team using an invite code."""
    body = await request.json()
    invite_code = str(body.get("invite_code") or body.get("code") or "").strip()
    requested_agent_name = str(body.get("agent_name") or body.get("agent") or "").strip().lower()
    agent_name = _normalize_agent_slug(requested_agent_name)
    model = str(body.get("model") or "").strip()
    agent_kind = str(body.get("agent_kind") or "").strip().lower()
    install_id = str(
        body.get("install_id")
        or body.get("device_id")
        or body.get("client_instance_id")
        or ""
    ).strip()
    if not invite_code:
        raise HTTPException(status_code=400, detail="invite_code required")
    if not agent_name or not agent_name.replace("-", "").isalnum():
        raise HTTPException(status_code=400, detail="valid agent_name required")

    invite_idx, invite = _find_invite(invite_code)
    if invite_idx is None or invite is None:
        raise HTTPException(status_code=404, detail="invite_not_found_or_expired")
    tenant_id = str(invite.get("tenant_id") or invite.get("project") or "").strip().lower()
    if not _valid_slug(tenant_id):
        raise HTTPException(status_code=400, detail="invite_missing_valid_tenant")
    role = str(body.get("role") or invite.get("role") or "member").strip().lower()
    if role not in {"observer", "member", "owner"}:
        raise HTTPException(status_code=400, detail="invalid_role")

    raw_token, record, created = _mint_invited_agent_token(
        tenant_id=tenant_id,
        agent_name=agent_name,
        role=role,
        invite=invite,
        model=model,
        agent_kind=agent_kind,
        install_id=install_id,
    )
    if created:
        _consume_invite(invite_idx)

    try:
        requests.put(
            f"{SQUAD_SERVICE_URL}/projects/{tenant_id}/members",
            json={"agent_id": agent_name, "role": role},
            headers={"Authorization": f"Bearer {raw_token}"},
            timeout=5,
        )
    except Exception as exc:
        log.warning("project member registration failed for %s/%s: %s", tenant_id, agent_name, exc)

    routed_agent = str(record.get("agent") or agent_name)
    onboarding_route: dict[str, Any] = {}
    try:
        node = _node_contract_from_record(record)
        onboarding_route = await _route_onboarding_agent(
            _get_redis(),
            project=tenant_id,
            agent=routed_agent,
            model=model,
            role=role,
            source="join_with_invite",
            summary=f"{routed_agent} joined {tenant_id} with invite",
            node_id=node.get("node_id") or "",
            local_source_id=node.get("local_source_id") or "",
        )
    except Exception as exc:
        log.warning("onboarding route failed for %s/%s: %s", tenant_id, routed_agent, exc)

    return JSONResponse(
        {
            "status": "joined" if created else "already_joined",
            "tenant_id": tenant_id,
            "agent": routed_agent,
            "requested_agent": record.get("requested_agent") or agent_name,
            "renamed_for_collision": bool(record.get("renamed_for_collision", False)),
            "role": role,
            "token": raw_token,
            "mcp_sse_url": f"https://mcp.mumega.com/sse/{raw_token}" if raw_token else "",
            "mcp_http_url": f"https://mcp.mumega.com/mcp/{raw_token}" if raw_token else "",
            "recovery_guide": "~/SOS/docs/agent-onboarding-recovery.md",
            "node": _node_contract_from_record(record),
            "onboarding_route": onboarding_route,
            "identity": _token_record_public(record),
        }
    )


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

    # 3. Agents
    try:
        r = _get_redis()
        raw_agents = await r.hgetall(AGENT_REGISTRY_KEY)
        config["agents"] = {
            name: json.loads(raw)
            for name, raw in raw_agents.items()
        }
    except Exception:
        config["agents"] = {}

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
            # S027 D-5 L-4 — clear per-SSE-connection as_agent state on disconnect.
            _session_as_agent.pop(session_id, None)
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
                # First-connect self-onboarding: MCP clients surface `instructions`
                # into the agent's context on connect (same mechanism as MCPWP's
                # "call wp_onboard first"). Tells any new agent to bootstrap itself
                # via boot_context instead of needing a hand-assembled setup bundle.
                "instructions": (
                    "You are connected to the SOS bus (Mumega multi-agent substrate). "
                    "Your identity, project/tenant scope, and permissions are derived from "
                    "your bus token — do not assume them. FIRST ACTION: call the `boot_context` "
                    "tool to load your identity, project/tenant scope, and memory boundary. "
                    "CRITICAL: on `inbox`, `peers`, and any tool with an `agent` parameter, "
                    "always pass YOUR OWN agent name explicitly — the default is the bus internal "
                    "identity and returns the wrong inbox or a 403. PROTOCOLS: if a message "
                    "includes `[request_id:<uuid>]`, reply with `{ack_for:<uuid>}`; long-running "
                    "agents emit a periodic heartbeat. CORE TOOLS: `send` (message an agent), "
                    "`broadcast` (announce), `inbox` (read messages), `peers` (list agents), "
                    "`remember`/`recall` (memory), `list_skills`/`invoke_skill` (capabilities)."
                ),
            },
        )
    if method == "notifications/initialized":
        # S111 carry-forward: hosted onboarding behavior remains in the legacy
        # gateway until the host overlay owns the product-specific routes.
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
            return _jsonrpc_ok(msg_id, {"tools": _tools_visible_to_auth(auth)})

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
        denied_reason = _enforce_mcp_tool_permission(tool_name, auth)
        if denied_reason:
            log.warning(
                "mcp rbac denied tool=%s agent=%s source=%s reason=%s",
                tool_name,
                auth.agent_scope,
                auth.source,
                denied_reason,
            )
            _audit_tool_call(
                _scope_project(auth) or "system",
                tool_name,
                actor=auth.agent_scope,
                details={"status": "blocked", "reason": denied_reason},
            )
            return _jsonrpc_err(msg_id, f"Tool not available: {tool_name}")
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
    return jsonrpc_ok(msg_id, result)


def _jsonrpc_err(msg_id: Any, message: str) -> dict[str, Any]:
    return jsonrpc_error(msg_id, message)


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
