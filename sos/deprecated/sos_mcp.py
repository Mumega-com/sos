#!/usr/bin/env python3
# DEPRECATED — do not import. Kept for git history only.
# Replacement: sos/mcp/sos_mcp_sse.py. See sos/deprecated/README.md.
"""
SOS MCP — Unified MCP server for the Sovereign Operating System.
Replaces 3 separate MCPs (redis-bus, mumega-tasks, mirror-memory) with one.

Messages auto-persist to Mirror. Memory is searchable. Agents are discoverable.

Env vars:
  REDIS_PASSWORD  — Redis auth
  AGENT_NAME      — this agent's identity
  PROJECT         — project scope (optional)
  MIRROR_URL      — Mirror API URL (default: http://localhost:8844)
  MIRROR_TOKEN    — Mirror auth token
  GEMINI_API_KEY  — for embeddings (loaded from .env.secrets)

Tools:
  send          — send message (Redis + auto-persist to Mirror)
  inbox         — check messages (Redis stream)
  peers         — list agents (Redis registry)
  broadcast     — broadcast to all/squad (Redis)
  remember      — store memory (Mirror)
  recall        — semantic search (Mirror)
  memories      — list recent (Mirror)
  task_create   — create task (Mirror)
  task_list     — list tasks (Mirror)
  task_update   — update task (Mirror)
"""
import sys
import json
import os
from uuid import uuid4
from datetime import datetime, timezone
from pathlib import Path

import redis
import requests

# --- Config ---

def _load_secrets():
    # Load from SOS-owned secret files only.
    for p in [str(Path.home() / ".env.secrets")]:
        if os.path.exists(p):
            with open(p) as f:
                for line in f:
                    line = line.strip()
                    if "=" in line and not line.startswith("#"):
                        k, v = line.split("=", 1)
                        os.environ.setdefault(k.strip(), v.strip())
    # Fallback: read from Codex config (Codex may not pass env to MCP subprocesses)
    codex_cfg = Path.home() / ".codex" / "config.toml"
    if codex_cfg.exists():
        try:
            in_sos_env = False
            for line in codex_cfg.read_text().splitlines():
                line = line.strip()
                if line == '[mcp_servers.sos.env]':
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

REDIS_PASSWORD = os.environ.get("REDIS_PASSWORD", "")
AGENT_SELF = os.environ.get("AGENT_NAME", "unknown")
PROJECT = os.environ.get("PROJECT", "")
MIRROR_URL = os.environ.get("MIRROR_URL", "http://localhost:8844")
MIRROR_TOKEN = os.environ.get("MIRROR_TOKEN", "")

r = redis.Redis(host="localhost", port=6379, password=REDIS_PASSWORD, decode_responses=True)

MIRROR_HEADERS = {
    "Authorization": f"Bearer {MIRROR_TOKEN}",
    "Content-Type": "application/json",
}

# --- Stream naming ---

def _prefix():
    return f"sos:stream:project:{PROJECT}" if PROJECT else "sos:stream:global"

def _agent_stream(agent):
    return f"{_prefix()}:agent:{agent}"

def _agent_channel(agent):
    if PROJECT:
        return f"sos:channel:project:{PROJECT}:agent:{agent}"
    return f"sos:channel:agent:{agent}"

def _legacy_stream(agent):
    return f"sos:stream:sos:channel:private:agent:{agent}"

# --- Helpers ---

def now_iso():
    return datetime.now(timezone.utc).isoformat()

def sos_msg(msg_type, source, target, content):
    """v0.4.0: build a v1-shaped bus message using Pydantic contracts.

    Legacy "chat" → "send", legacy "broadcast" → "send" with channel target,
    legacy target "broadcast" → "sos:channel:global". "announce" maps directly.
    """
    from sos.contracts.messages import SendMessage, AnnounceMessage

    v1_type = {"chat": "send", "broadcast": "send"}.get(msg_type, msg_type)
    if target == "broadcast":
        target = "sos:channel:global"

    if v1_type == "send":
        m = SendMessage(
            source=source,
            target=target,
            timestamp=SendMessage.now_iso(),
            message_id=str(uuid4()),
            payload={"text": content, "content_type": "text/plain"},
        )
    elif v1_type == "announce":
        m = AnnounceMessage(
            source=source,
            target=target,
            timestamp=AnnounceMessage.now_iso(),
            message_id=str(uuid4()),
            payload={"text": content} if content else None,
        )
    else:
        raise ValueError(f"unknown message type: {msg_type!r}")

    msg = m.to_redis_fields()
    if PROJECT:
        msg["project"] = PROJECT
    return msg

def mirror_get(path):
    resp = requests.get(f"{MIRROR_URL}{path}", headers=MIRROR_HEADERS, timeout=10)
    resp.raise_for_status()
    return resp.json()

def mirror_post(path, body):
    resp = requests.post(f"{MIRROR_URL}{path}", headers=MIRROR_HEADERS, json=body, timeout=10)
    resp.raise_for_status()
    return resp.json()

def make_response(msg_id, result=None, error=None):
    resp = {"jsonrpc": "2.0", "id": msg_id}
    if error:
        resp["error"] = {"code": -32000, "message": str(error)}
    else:
        resp["result"] = result
    return resp

# --- Tools ---

def get_tools():
    scope = f" (project: {PROJECT})" if PROJECT else ""
    return [
        # Agent (direct call via OpenClaw — synchronous, reliable)
        {"name": "ask", "description": "Ask an agent a question and get a direct response (via OpenClaw)", "inputSchema": {
            "type": "object",
            "properties": {
                "agent": {"type": "string", "description": "Agent name (e.g. athena, kasra, worker)"},
                "message": {"type": "string", "description": "Question or task for the agent"},
            }, "required": ["agent", "message"]}},
        # Bus (async messaging)
        {"name": "send", "description": f"Send async message to an agent{scope}", "inputSchema": {
            "type": "object",
            "properties": {
                "to": {"type": "string", "description": "Agent name"},
                "text": {"type": "string", "description": "Message text"},
            }, "required": ["to", "text"]}},
        {"name": "inbox", "description": f"Check agent inbox{scope}", "inputSchema": {
            "type": "object",
            "properties": {
                "agent": {"type": "string", "default": AGENT_SELF, "description": "Agent name"},
                "limit": {"type": "integer", "default": 10},
            }}},
        {"name": "peers", "description": f"List agents on the bus{scope}", "inputSchema": {
            "type": "object", "properties": {}}},
        {"name": "broadcast", "description": f"Broadcast to all agents{scope}", "inputSchema": {
            "type": "object",
            "properties": {
                "text": {"type": "string", "description": "Message text"},
                "squad": {"type": "string", "description": "Squad (omit for all)"},
            }, "required": ["text"]}},
        # Memory
        {"name": "remember", "description": "Store a persistent memory", "inputSchema": {
            "type": "object",
            "properties": {
                "text": {"type": "string", "description": "Memory text to store"},
                "context": {"type": "string", "description": "Context label (optional)"},
            }, "required": ["text"]}},
        {"name": "recall", "description": "Semantic search across memories", "inputSchema": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "Search query"},
                "limit": {"type": "integer", "default": 5},
            }, "required": ["query"]}},
        {"name": "memories", "description": "List recent memories", "inputSchema": {
            "type": "object",
            "properties": {
                "limit": {"type": "integer", "default": 10},
            }}},
        # Tasks
        {"name": "task_create", "description": "Create a task", "inputSchema": {
            "type": "object",
            "properties": {
                "title": {"type": "string"},
                "description": {"type": "string"},
                "assignee": {"type": "string"},
                "priority": {"type": "string", "enum": ["low", "medium", "high", "urgent"]},
            }, "required": ["title"]}},
        {"name": "task_list", "description": "List tasks", "inputSchema": {
            "type": "object",
            "properties": {
                "status": {"type": "string"},
                "assignee": {"type": "string"},
                "limit": {"type": "integer", "default": 20},
            }}},
        {"name": "task_update", "description": "Update a task", "inputSchema": {
            "type": "object",
            "properties": {
                "task_id": {"type": "string"},
                "status": {"type": "string"},
                "notes": {"type": "string"},
            }, "required": ["task_id"]}},
    ]


def handle(name, args):
    try:
        # --- Agent (direct call via OpenClaw) ---
        if name == "ask":
            agent = args["agent"]
            message = args["message"]
            import subprocess
            result = subprocess.run(
                ["openclaw", "agent", "--agent", agent, "-m", message, "--json"],
                capture_output=True, text=True, timeout=120,
            )
            if result.returncode != 0:
                return _text(f"OpenClaw error: {result.stderr[:200]}")
            try:
                data = json.loads(result.stdout)
                payloads = data.get("result", {}).get("payloads", [])
                reply = "\n".join(p.get("text", "") for p in payloads if p.get("text"))
                return _text(f"[{agent}]: {reply}" if reply else f"[{agent}]: (no response)")
            except json.JSONDecodeError:
                return _text(f"[{agent}]: {result.stdout[:500]}")

        # --- Bus (async) ---
        if name == "send":
            to, text = args["to"], args["text"]
            stream = _agent_stream(to)
            msg = sos_msg("chat", f"agent:{AGENT_SELF}", f"agent:{to}", text)
            mid = r.xadd(stream, msg)
            r.publish(_agent_channel(to), json.dumps(msg))
            r.publish(f"sos:wake:{to}", json.dumps(msg))
            # Auto-persist to Mirror
            try:
                mirror_post("/store", {
                    "text": f"[{AGENT_SELF} → {to}] {text}",
                    "agent": AGENT_SELF,
                    "context_id": f"msg_{mid}",
                })
            except Exception:
                pass  # Best-effort persist
            return _text(f"Sent to {to} (id: {mid})")

        elif name == "inbox":
            agent = args.get("agent", AGENT_SELF)
            limit = args.get("limit", 10)
            stream = _agent_stream(agent)
            entries = r.xrevrange(stream, count=limit)
            if not entries and not PROJECT:
                entries = r.xrevrange(_legacy_stream(agent), count=limit)
            if not entries:
                return _text(f"No messages for {agent}.")
            lines = []
            for mid, data in entries:
                payload = json.loads(data.get("payload", "{}"))
                lines.append(f"[{data.get('timestamp', '?')}] {data.get('source', '?')}: {payload.get('text', '')}")
            return _text("\n".join(lines))

        elif name == "peers":
            pattern = f"{_prefix()}:agent:*"
            agents = set()
            cursor = 0
            while True:
                cursor, keys = r.scan(cursor, match=pattern, count=100)
                for k in keys:
                    agents.add(k.split(":")[-1])
                if cursor == 0:
                    break
            if not PROJECT:
                cursor = 0
                while True:
                    cursor, keys = r.scan(cursor, match="sos:stream:sos:channel:private:agent:*", count=100)
                    for k in keys:
                        agents.add(k.split(":")[-1])
                    if cursor == 0:
                        break
            scope = f"project:{PROJECT}" if PROJECT else "global"
            return _text(f"Agents ({scope}): {', '.join(sorted(agents))}" if agents else "No agents found.")

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
            mid = r.xadd(stream, msg)
            r.publish(channel, json.dumps(msg))
            return _text(f"Broadcast to {channel} (id: {mid})")

        # --- Memory ---
        elif name == "remember":
            ctx = args.get("context", f"mcp-{int(datetime.now().timestamp())}")
            result = mirror_post("/store", {
                "text": args["text"],
                "agent": AGENT_SELF,
                "context_id": ctx,
            })
            return _text(f"Stored: {ctx}")

        elif name == "recall":
            results = mirror_post("/search", {
                "query": args["query"],
                "top_k": args.get("limit", 5),
                "agent_filter": AGENT_SELF,
            })
            if not results:
                return _text("No matching memories.")
            lines = []
            for i, e in enumerate(results, 1):
                text = (e.get("raw_data", {}) or {}).get("text", e.get("context_id", "?"))
                lines.append(f"{i}. [{e.get('timestamp', '?')[:10]}] {str(text)[:200]}")
            return _text("\n".join(lines))

        elif name == "memories":
            data = mirror_get(f"/recent/{AGENT_SELF}?limit={args.get('limit', 10)}")
            engrams = data.get("engrams", [])
            if not engrams:
                return _text("No memories yet.")
            lines = []
            for i, e in enumerate(engrams, 1):
                text = (e.get("raw_data", {}) or {}).get("text", e.get("context_id", "?"))
                lines.append(f"{i}. [{e.get('timestamp', '?')[:10]}] {str(text)[:200]}")
            return _text("\n".join(lines))

        # --- Tasks ---
        elif name == "task_create":
            result = mirror_post("/tasks", {
                "title": args["title"],
                "description": args.get("description", ""),
                "assignee": args.get("assignee", AGENT_SELF),
                "priority": args.get("priority", "medium"),
                "status": "pending",
                "agent": AGENT_SELF,
            })
            return _text(f"Task created: {args['title']}")

        elif name == "task_list":
            params = f"?limit={args.get('limit', 20)}"
            if args.get("status"):
                params += f"&status={args['status']}"
            if args.get("assignee"):
                params += f"&assignee={args['assignee']}"
            result = mirror_get(f"/tasks{params}")
            tasks = result if isinstance(result, list) else result.get("tasks", [])
            if not tasks:
                return _text("No tasks found.")
            lines = []
            for t in tasks:
                tid = t.get('id', '?')
                lines.append(f"[{t.get('status', '?')}] {t.get('title', '?')} (id: {tid}) → {t.get('agent', t.get('assignee', '?'))}")
            return _text("\n".join(lines))

        elif name == "task_update":
            body = {"task_id": args["task_id"]}
            if args.get("status"):
                body["status"] = args["status"]
            if args.get("notes"):
                body["notes"] = args["notes"]
            result = mirror_post(f"/tasks/{args['task_id']}", body)
            return _text(f"Task {args['task_id']} updated")

        else:
            return _text(f"Unknown tool: {name}")

    except Exception as e:
        return _text(f"Error: {e}")


def _text(t):
    return {"content": [{"type": "text", "text": t}]}


def main():
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            msg = json.loads(line)
        except json.JSONDecodeError:
            continue

        method = msg.get("method", "")
        msg_id = msg.get("id")
        params = msg.get("params", {})

        if method == "initialize":
            resp = make_response(msg_id, {
                "protocolVersion": "2024-11-05",
                "capabilities": {"tools": {"listChanged": False}},
                "serverInfo": {"name": "sos", "version": "2.0.0"},
            })
        elif method == "notifications/initialized":
            continue
        elif method == "tools/list":
            resp = make_response(msg_id, {"tools": get_tools()})
        elif method == "tools/call":
            result = handle(params.get("name", ""), params.get("arguments", {}))
            resp = make_response(msg_id, result)
        elif method == "ping":
            resp = make_response(msg_id, {})
        else:
            resp = make_response(msg_id, error=f"Unknown method: {method}")

        sys.stdout.write(json.dumps(resp) + "\n")
        sys.stdout.flush()


if __name__ == "__main__":
    main()
