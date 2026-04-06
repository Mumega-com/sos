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
import json
import logging
import os
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from uuid import uuid4

import redis.asyncio as aioredis
import requests
from fastapi import FastAPI, HTTPException, Request, Response
from fastapi.responses import JSONResponse
from sse_starlette.sse import EventSourceResponse

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


# ---------------------------------------------------------------------------
# Stream helpers (mirrored from sos_mcp.py)
# ---------------------------------------------------------------------------

AGENT_SELF = "sos-mcp-sse"
PROJECT = os.environ.get("PROJECT", "")


def _prefix() -> str:
    return f"sos:stream:project:{PROJECT}" if PROJECT else "sos:stream:global"


def _agent_stream(agent: str) -> str:
    return f"{_prefix()}:agent:{agent}"


def _agent_channel(agent: str) -> str:
    if PROJECT:
        return f"sos:channel:project:{PROJECT}:agent:{agent}"
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
    ]


# ---------------------------------------------------------------------------
# Tool execution (async)
# ---------------------------------------------------------------------------


def _text(t: str) -> dict[str, Any]:
    return {"content": [{"type": "text", "text": t}]}


async def handle_tool(name: str, args: dict[str, Any]) -> dict[str, Any]:
    loop = asyncio.get_event_loop()
    r = _get_redis()

    try:
        # --- ask ---
        if name == "ask":
            agent = args["agent"]
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
            to, text = args["to"], args["text"]
            stream = _agent_stream(to)
            msg = sos_msg("chat", f"agent:{AGENT_SELF}", f"agent:{to}", text)
            mid = await r.xadd(stream, msg)
            await r.publish(_agent_channel(to), json.dumps(msg))
            await r.publish(f"sos:wake:{to}", json.dumps(msg))
            try:
                await loop.run_in_executor(
                    None,
                    mirror_post,
                    "/store",
                    {
                        "text": f"[{AGENT_SELF} -> {to}] {text}",
                        "agent": AGENT_SELF,
                        "context_id": f"msg_{mid}",
                    },
                )
            except Exception:
                pass
            return _text(f"Sent to {to} (id: {mid})")

        # --- inbox ---
        elif name == "inbox":
            agent = args.get("agent", AGENT_SELF)
            limit = args.get("limit", 10)
            stream = _agent_stream(agent)
            entries = await r.xrevrange(stream, count=limit)
            if not entries and not PROJECT:
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
            pattern = f"{_prefix()}:agent:*"
            agents: set[str] = set()
            cursor = 0
            while True:
                cursor, keys = await r.scan(cursor, match=pattern, count=100)
                for k in keys:
                    agents.add(k.split(":")[-1])
                if cursor == 0:
                    break
            if not PROJECT:
                cursor = 0
                while True:
                    cursor, keys = await r.scan(
                        cursor, match="sos:stream:sos:channel:private:agent:*", count=100
                    )
                    for k in keys:
                        agents.add(k.split(":")[-1])
                    if cursor == 0:
                        break
            scope = f"project:{PROJECT}" if PROJECT else "global"
            return _text(
                f"Agents ({scope}): {', '.join(sorted(agents))}" if agents else "No agents found."
            )

        # --- broadcast ---
        elif name == "broadcast":
            text = args["text"]
            squad = args.get("squad")
            if squad:
                stream = f"{_prefix()}:squad:{squad}"
                channel = f"sos:channel:{'project:' + PROJECT + ':' if PROJECT else ''}squad:{squad}"
            else:
                stream = f"{_prefix()}:broadcast"
                channel = f"sos:channel:{'project:' + PROJECT + ':' if PROJECT else ''}global"
            msg = sos_msg("broadcast", f"agent:{AGENT_SELF}", channel, text)
            mid = await r.xadd(stream, msg)
            await r.publish(channel, json.dumps(msg))
            return _text(f"Broadcast to {channel} (id: {mid})")

        # --- remember ---
        elif name == "remember":
            ctx = args.get("context", f"mcp-{int(datetime.now().timestamp())}")
            await loop.run_in_executor(
                None,
                mirror_post,
                "/store",
                {"text": args["text"], "agent": AGENT_SELF, "context_id": ctx},
            )
            return _text(f"Stored: {ctx}")

        # --- recall ---
        elif name == "recall":
            results = await loop.run_in_executor(
                None,
                mirror_post,
                "/search",
                {"query": args["query"], "top_k": args.get("limit", 5), "agent_filter": AGENT_SELF},
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
                None, mirror_get, f"/recent/{AGENT_SELF}?limit={args.get('limit', 10)}"
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
                    "assignee": args.get("assignee", AGENT_SELF),
                    "priority": args.get("priority", "medium"),
                    "status": "pending",
                    "agent": AGENT_SELF,
                },
            )
            return _text(f"Task created: {args['title']}")

        # --- task_list ---
        elif name == "task_list":
            params = f"?limit={args.get('limit', 20)}"
            if args.get("status"):
                params += f"&status={args['status']}"
            if args.get("assignee"):
                params += f"&assignee={args['assignee']}"
            result = await loop.run_in_executor(None, mirror_get, f"/tasks{params}")
            tasks = result if isinstance(result, list) else result.get("tasks", [])
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
            body: dict[str, Any] = {"task_id": args["task_id"]}
            if args.get("status"):
                body["status"] = args["status"]
            if args.get("notes"):
                body["notes"] = args["notes"]
            await loop.run_in_executor(None, mirror_post, f"/tasks/{args['task_id']}", body)
            return _text(f"Task {args['task_id']} updated")

        else:
            return _text(f"Unknown tool: {name}")

    except Exception as e:
        log.exception("Tool %s failed", name)
        return _text(f"Error: {e}")


# ---------------------------------------------------------------------------
# Session registry: session_id -> asyncio.Queue for SSE push
# ---------------------------------------------------------------------------

_sessions: dict[str, asyncio.Queue[dict[str, Any] | None]] = {}


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


def _valid_tokens() -> set[str]:
    tokens = os.environ.get("MCP_ACCESS_TOKENS", "").split(",")
    return {token.strip() for token in tokens if token.strip()}


def _request_bearer_token(request: Request) -> str:
    auth = request.headers.get("authorization", "").strip()
    if auth.lower().startswith("bearer "):
        return auth[7:].strip()
    return ""


def _is_authorized(request: Request, token: str | None = None) -> bool:
    candidate = token or _request_bearer_token(request) or request.query_params.get("token", "").strip()
    return bool(candidate) and candidate in _valid_tokens()


def _require_auth(request: Request, token: str | None = None) -> None:
    if not _is_authorized(request, token):
        raise HTTPException(status_code=401, detail="invalid token")


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
    _require_auth(request, token)
    return await sse_endpoint(request)


@app.get("/sse")
async def sse_endpoint(request: Request) -> EventSourceResponse:
    """
    MCP SSE transport: client connects here and receives a session endpoint,
    then sends JSON-RPC requests to POST /messages?session_id=<id>.
    """
    session_id = str(uuid4())
    queue: asyncio.Queue[dict[str, Any] | None] = asyncio.Queue()
    _sessions[session_id] = queue

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

    try:
        body = await request.json()
    except Exception:
        return Response(status_code=400, content="Invalid JSON")

    resp = await _process_jsonrpc(body, session_id=session_id)
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
    _require_auth(request)
    return await _streamable_http_response(request)


@app.post("/mcp/{token}")
async def mcp_endpoint_with_token(token: str, request: Request) -> Response:
    _require_auth(request, token)
    return await _streamable_http_response(request)


async def _streamable_http_response(request: Request) -> Response:
    try:
        body = await request.json()
    except Exception:
        return Response(status_code=400, content="Invalid JSON")

    resp = await _process_jsonrpc(body, session_id=None)
    if resp is None:
        return Response(status_code=202)
    return JSONResponse(resp)


async def _process_jsonrpc(body: dict[str, Any], session_id: str | None) -> dict[str, Any] | None:
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
        tool_result = await handle_tool(params.get("name", ""), params.get("arguments", {}))
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
