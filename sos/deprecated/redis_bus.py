#!/usr/bin/env python3
"""
Redis Bus MCP — inter-agent communication with project scoping.
Uses SOS message format over Redis Streams + Pub/Sub.

Env vars:
  REDIS_PASSWORD  — Redis auth
  AGENT_NAME      — this agent's identity (e.g. kasra, mac-claude)
  PROJECT         — project scope (e.g. dnu, stemminds). Omit for global.

Stream layout:
  Global:  sos:stream:global:agent:{name}
  Project: sos:stream:project:{project}:agent:{name}

Usage:
  claude mcp add --scope user redis-bus python3 /path/to/SOS/sos/mcp/redis_bus.py
"""
import sys
import json
import os
from uuid import uuid4
from datetime import datetime, timezone
from pathlib import Path

import redis


def _load_config_env():
    config_path = Path.home() / ".codex" / "config.toml"
    if not config_path.exists():
        return {}
    try:
        lines = config_path.read_text().splitlines()
    except Exception:
        return {}
    env = {}
    in_section = False
    for raw_line in lines:
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("[") and line.endswith("]"):
            in_section = line == "[mcp_servers.redis-bus.env]"
            continue
        if not in_section or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip()
        if value.startswith('"') and value.endswith('"'):
            value = value[1:-1]
        env[key] = value
    return env


_config_env = _load_config_env()
REDIS_PASSWORD = os.environ.get("REDIS_PASSWORD") or _config_env.get("REDIS_PASSWORD", "")
AGENT_SELF = os.environ.get("AGENT_NAME") or _config_env.get("AGENT_NAME", "unknown")
PROJECT = os.environ.get("PROJECT") or _config_env.get("PROJECT", "")

r = redis.Redis(
    host="localhost",
    port=6379,
    password=REDIS_PASSWORD,
    decode_responses=True,
)


# --- Stream naming ---

def _prefix():
    """Stream prefix based on project scope."""
    if PROJECT:
        return f"sos:stream:project:{PROJECT}"
    return "sos:stream:global"


def _agent_stream(agent: str) -> str:
    return f"{_prefix()}:agent:{agent}"


def _agent_channel(agent: str) -> str:
    if PROJECT:
        return f"sos:channel:project:{PROJECT}:agent:{agent}"
    return f"sos:channel:agent:{agent}"


def _broadcast_stream(squad: str | None = None) -> str:
    if squad:
        return f"{_prefix()}:squad:{squad}"
    return f"{_prefix()}:broadcast"


def _broadcast_channel(squad: str | None = None) -> str:
    if squad:
        if PROJECT:
            return f"sos:channel:project:{PROJECT}:squad:{squad}"
        return f"sos:channel:squad:{squad}"
    if PROJECT:
        return f"sos:channel:project:{PROJECT}:broadcast"
    return "sos:channel:global"


def _registry_key(agent: str) -> str:
    if PROJECT:
        return f"sos:registry:{PROJECT}:{agent}"
    return f"sos:registry:{agent}"


def _scan_pattern() -> str:
    return f"{_prefix()}:agent:*"


# --- Legacy compat: also read from old stream names ---

def _legacy_agent_stream(agent: str) -> str:
    return f"sos:stream:sos:channel:private:agent:{agent}"


# --- Message format ---

def make_response(msg_id, result=None, error=None):
    resp = {"jsonrpc": "2.0", "id": msg_id}
    if error:
        resp["error"] = {"code": -32000, "message": str(error)}
    else:
        resp["result"] = result
    return resp


def sos_message(msg_type, source, target, content):
    """v0.4.0: build a v1-shaped bus message via Pydantic contracts.

    Legacy "chat" → v1 "send". Legacy bare "broadcast" target → sos:channel:global.
    The deprecated redis_bus.py entry-point continues to accept legacy
    parameter names for backwards compatibility with older MCP installs, but
    emits v1 types to the wire so downstream validators accept the message.
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
        raise ValueError(f"redis_bus: unknown message type {msg_type!r}")

    msg = m.to_redis_fields()
    if PROJECT:
        msg["project"] = PROJECT
    return msg


# --- Tools ---

def get_tools():
    scope_desc = f" (project: {PROJECT})" if PROJECT else " (global)"
    return [
        {
            "name": "agent_send",
            "description": f"Send a message to an agent{scope_desc}",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "target": {"type": "string", "description": "Agent name (e.g. kasra, river)"},
                    "content": {"type": "string", "description": "Message text"},
                    "type": {"type": "string", "default": "chat", "description": "Message type"},
                },
                "required": ["target", "content"],
            },
        },
        {
            "name": "agent_inbox",
            "description": f"Check an agent's inbox{scope_desc}",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "agent": {"type": "string", "default": AGENT_SELF, "description": "Agent name"},
                    "limit": {"type": "integer", "default": 10, "description": "Max messages"},
                },
            },
        },
        {
            "name": "agent_broadcast",
            "description": f"Broadcast to all agents{scope_desc}",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "content": {"type": "string", "description": "Message text"},
                    "squad": {"type": "string", "description": "Squad name (omit for all)"},
                },
                "required": ["content"],
            },
        },
        {
            "name": "agent_peers",
            "description": f"List agents{scope_desc}",
            "inputSchema": {"type": "object", "properties": {}},
        },
    ]


def handle_tool_call(name, args):
    try:
        if name == "agent_send":
            target = args["target"]
            content = args["content"]
            msg_type = args.get("type", "chat")
            stream = _agent_stream(target)
            channel = _agent_channel(target)
            msg = sos_message(msg_type, f"agent:{AGENT_SELF}", f"agent:{target}", content)
            mid = r.xadd(stream, msg)
            r.publish(channel, json.dumps(msg))
            r.publish(f"sos:wake:{target}", json.dumps(msg))
            return {"content": [{"type": "text", "text": f"Sent to {target} (stream: {stream}, id: {mid})"}]}

        elif name == "agent_inbox":
            agent = args.get("agent", AGENT_SELF)
            limit = args.get("limit", 10)
            # Read from new stream
            stream = _agent_stream(agent)
            entries = r.xrevrange(stream, count=limit)
            # Also check legacy stream if no results and no project scope
            if not entries and not PROJECT:
                legacy = _legacy_agent_stream(agent)
                entries = r.xrevrange(legacy, count=limit)
            if not entries:
                return {"content": [{"type": "text", "text": f"No messages for {agent}."}]}
            lines = []
            for mid, data in entries:
                payload = json.loads(data.get("payload", "{}"))
                text = payload.get("text", "")
                proj = data.get("project", "")
                proj_tag = f" [{proj}]" if proj else ""
                lines.append(f"[{data.get('timestamp', '?')}] {data.get('source', '?')} ({data.get('type', '?')}){proj_tag}: {text}")
            return {"content": [{"type": "text", "text": "\n".join(lines)}]}

        elif name == "agent_broadcast":
            content = args["content"]
            squad = args.get("squad")
            stream = _broadcast_stream(squad)
            channel = _broadcast_channel(squad)
            msg = sos_message("broadcast", f"agent:{AGENT_SELF}", channel, content)
            mid = r.xadd(stream, msg)
            r.publish(channel, json.dumps(msg))
            return {"content": [{"type": "text", "text": f"Broadcast to {channel} (id: {mid})"}]}

        elif name == "agent_peers":
            pattern = _scan_pattern()
            cursor, keys = 0, []
            while True:
                cursor, batch = r.scan(cursor, match=pattern, count=100)
                keys.extend(batch)
                if cursor == 0:
                    break
            prefix = f"{_prefix()}:agent:"
            agents = sorted(set(k[len(prefix):] for k in keys))
            # Also check legacy if global
            if not PROJECT:
                cursor2 = 0
                while True:
                    cursor2, batch2 = r.scan(cursor2, match="sos:stream:sos:channel:private:agent:*", count=100)
                    for k in batch2:
                        a = k.split(":")[-1]
                        if a not in agents:
                            agents.append(a)
                    if cursor2 == 0:
                        break
                agents = sorted(set(agents))
            scope = f"project:{PROJECT}" if PROJECT else "global"
            if not agents:
                return {"content": [{"type": "text", "text": f"No agents found ({scope})."}]}
            return {"content": [{"type": "text", "text": f"Agents ({scope}, {len(agents)}): {', '.join(agents)}"}]}

        else:
            return {"error": f"Unknown tool: {name}"}

    except Exception as e:
        return {"content": [{"type": "text", "text": f"Error: {e}"}]}


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
                "serverInfo": {"name": "redis-bus", "version": "2.0.0"},
            })
        elif method == "notifications/initialized":
            continue
        elif method == "tools/list":
            resp = make_response(msg_id, {"tools": get_tools()})
        elif method == "tools/call":
            result = handle_tool_call(params.get("name", ""), params.get("arguments", {}))
            resp = make_response(msg_id, result)
        elif method == "ping":
            resp = make_response(msg_id, {})
        else:
            resp = make_response(msg_id, error=f"Unknown method: {method}")

        sys.stdout.write(json.dumps(resp) + "\n")
        sys.stdout.flush()


if __name__ == "__main__":
    main()
