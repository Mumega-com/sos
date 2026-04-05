#!/usr/bin/env python3
"""
Remote Bus MCP — connects to bus-bridge.py over HTTP.
Install this on the MacBook so Claude Code/OpenClaw can talk to the server's Redis bus.

Usage on MacBook:
  claude mcp add --scope user redis-bus python3 /path/to/bus-remote-mcp.py

Env vars:
  BUS_BRIDGE_URL   — e.g. http://your-server:6380 or via SSH tunnel http://localhost:6380
  BUS_BRIDGE_TOKEN — auth token (default: sk-bus-mumega-bridge-001)
  AGENT_NAME       — this agent's name (default: mac-claude)
"""
from __future__ import annotations

import json
import os
import sys
import urllib.request
import urllib.error

BRIDGE_URL = os.environ.get("BUS_BRIDGE_URL", "http://localhost:6380")
BRIDGE_TOKEN = os.environ.get("BUS_BRIDGE_TOKEN", "sk-bus-mumega-bridge-001")
AGENT_NAME = os.environ.get("AGENT_NAME", "mac-claude")


def api(method: str, path: str, body: dict | None = None) -> dict:
    url = f"{BRIDGE_URL}{path}"
    data = json.dumps(body).encode() if body else None
    req = urllib.request.Request(
        url,
        data=data,
        method=method,
        headers={
            "Authorization": f"Bearer {BRIDGE_TOKEN}",
            "Content-Type": "application/json",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            return json.loads(resp.read())
    except urllib.error.HTTPError as e:
        return {"error": f"HTTP {e.code}: {e.read().decode()}"}
    except Exception as e:
        return {"error": str(e)}


def make_response(msg_id, result=None, error=None):
    resp = {"jsonrpc": "2.0", "id": msg_id}
    if error:
        resp["error"] = {"code": -32000, "message": str(error)}
    else:
        resp["result"] = result
    return resp


def get_tools():
    return [
        {
            "name": "agent_send",
            "description": "Send a message to a specific agent on the server",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "target": {"type": "string", "description": "Agent name (e.g. kasra, athena)"},
                    "content": {"type": "string", "description": "Message text"},
                },
                "required": ["target", "content"],
            },
        },
        {
            "name": "agent_inbox",
            "description": "Check an agent's inbox for recent messages",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "agent": {"type": "string", "default": AGENT_NAME, "description": "Agent name"},
                    "limit": {"type": "integer", "default": 10, "description": "Max messages"},
                },
            },
        },
        {
            "name": "agent_broadcast",
            "description": "Broadcast a message to all agents or a squad",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "content": {"type": "string", "description": "Message text"},
                    "squad": {"type": "string", "description": "Squad name (omit for global)"},
                },
                "required": ["content"],
            },
        },
        {
            "name": "agent_peers",
            "description": "List all known agents on the server",
            "inputSchema": {"type": "object", "properties": {}},
        },
        {
            "name": "agent_announce",
            "description": "Announce this agent on the bus",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "summary": {"type": "string", "description": "What this agent is doing"},
                },
            },
        },
    ]


def handle_tool_call(name: str, args: dict) -> dict:
    try:
        if name == "agent_send":
            result = api("POST", "/send", {
                "from": AGENT_NAME,
                "to": args["target"],
                "text": args["content"],
            })
            return {"content": [{"type": "text", "text": json.dumps(result)}]}

        elif name == "agent_inbox":
            agent = args.get("agent", AGENT_NAME)
            limit = args.get("limit", 10)
            result = api("GET", f"/inbox?agent={agent}&limit={limit}")
            messages = result.get("messages", [])
            if not messages:
                return {"content": [{"type": "text", "text": f"No messages for {agent}."}]}
            lines = []
            for m in messages:
                lines.append(f"[{m.get('timestamp', '?')}] {m.get('source', '?')} ({m.get('type', '?')}): {m.get('text', '')}")
            return {"content": [{"type": "text", "text": "\n".join(lines)}]}

        elif name == "agent_broadcast":
            result = api("POST", "/broadcast", {
                "from": AGENT_NAME,
                "text": args["content"],
                "squad": args.get("squad"),
            })
            return {"content": [{"type": "text", "text": json.dumps(result)}]}

        elif name == "agent_peers":
            result = api("GET", "/peers")
            registered = result.get("registered", [])
            streams = result.get("streams", [])
            lines = ["=== Live Agents ==="]
            for p in registered:
                lines.append(f"  {p.get('name', '?')} ({p.get('tool', '?')}) — {p.get('summary', '')} [{p.get('last_seen', '?')}]")
            lines.append("\n=== Historical Streams ===")
            for s in streams:
                lines.append(f"  {s.get('agent', '?')}: {s.get('messages', 0)} messages")
            return {"content": [{"type": "text", "text": "\n".join(lines)}]}

        elif name == "agent_announce":
            result = api("POST", "/announce", {
                "agent": AGENT_NAME,
                "tool": "claude",
                "summary": args.get("summary", "Remote Claude session"),
            })
            return {"content": [{"type": "text", "text": json.dumps(result)}]}

        else:
            return {"content": [{"type": "text", "text": f"Unknown tool: {name}"}]}

    except Exception as e:
        return {"content": [{"type": "text", "text": f"Error: {e}"}]}


def main():
    # Auto-announce on startup
    api("POST", "/announce", {
        "agent": AGENT_NAME,
        "tool": "claude-remote",
        "summary": f"Remote session via bus-bridge",
    })

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
                "serverInfo": {"name": "redis-bus-remote", "version": "1.0.0"},
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
