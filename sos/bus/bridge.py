#!/usr/bin/env python3
"""
Redis Bus HTTP Bridge — exposes the SOS Redis bus over authenticated HTTP.
Supports multi-tenant project scoping.

Auth:
  Bearer token from bus_bridge_tokens.json. Each token optionally scoped to a project.
  Admin tokens (project=null) can access all projects.
  Project tokens can only access their own project's streams.

Stream layout:
  Global:  sos:stream:global:agent:{name}
  Project: sos:stream:project:{project}:agent:{name}

Endpoints:
  POST /announce  — Register agent on bus
  POST /send      — Send message to agent
  GET  /inbox     — Poll agent inbox
  GET  /peers     — List all agents
  POST /broadcast — Broadcast to all/squad
  POST /heartbeat — Refresh agent TTL
  GET  /health    — Health check
"""
from __future__ import annotations

import hashlib
import json
import os
from datetime import datetime, timezone
from http.server import HTTPServer, BaseHTTPRequestHandler
from pathlib import Path
from urllib.parse import urlparse, parse_qs
from uuid import uuid4

import redis

REDIS_PASSWORD = os.environ.get("REDIS_PASSWORD", "")
PORT = int(os.environ.get("BUS_BRIDGE_PORT", "6380"))
TOKENS_PATH = Path(__file__).parent / "tokens.json"

r: redis.Redis


def _load_tokens() -> list[dict]:
    try:
        return json.loads(TOKENS_PATH.read_text())
    except Exception:
        return []


def _resolve_token(raw_token: str) -> dict | None:
    """Returns token record or None if invalid."""
    tokens = _load_tokens()
    for t in tokens:
        if t.get("token") == raw_token and t.get("active", True):
            return t
    return None


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


# --- Stream naming (mirrors SOS Redis MCP conventions) ---

def _prefix(project: str | None) -> str:
    if project:
        return f"sos:stream:project:{project}"
    return "sos:stream:global"


def _agent_stream(agent: str, project: str | None) -> str:
    return f"{_prefix(project)}:agent:{agent}"


def _agent_channel(agent: str, project: str | None) -> str:
    if project:
        return f"sos:channel:project:{project}:agent:{agent}"
    return f"sos:channel:agent:{agent}"


def _registry_key(agent: str, project: str | None) -> str:
    if project:
        return f"sos:registry:{project}:{agent}"
    return f"sos:registry:{agent}"


def _scan_streams(project: str | None) -> str:
    return f"{_prefix(project)}:agent:*"


def _legacy_stream(agent: str) -> str:
    return f"sos:stream:sos:channel:private:agent:{agent}"


def sos_msg(msg_type: str, source: str, target: str, text: str, project: str | None = None) -> dict:
    msg = {
        "id": str(uuid4()),
        "type": msg_type,
        "source": source,
        "target": target,
        "payload": json.dumps({"text": text}),
        "timestamp": now_iso(),
        "version": "1.0",
    }
    if project:
        msg["project"] = project
    return msg


class BusHandler(BaseHTTPRequestHandler):
    def _auth(self) -> dict | None:
        """Returns token record or sends 401."""
        auth = self.headers.get("Authorization", "")
        if not auth.startswith("Bearer "):
            self._json(401, {"error": "Unauthorized"})
            return None
        raw = auth[7:]
        token = _resolve_token(raw)
        if not token:
            self._json(401, {"error": "Invalid token"})
            return None
        return token

    def _project(self, token: dict, requested: str | None = None) -> str | None:
        """Resolve project scope. Token project wins if set."""
        token_project = token.get("project")
        if token_project:
            return token_project
        return requested

    def _json(self, code: int, data: dict) -> None:
        body = json.dumps(data).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _body(self) -> dict:
        length = int(self.headers.get("Content-Length", 0))
        if length == 0:
            return {}
        return json.loads(self.rfile.read(length))

    def _params(self) -> dict:
        qs = parse_qs(urlparse(self.path).query)
        return {k: v[0] for k, v in qs.items()}

    def do_GET(self) -> None:
        path = urlparse(self.path).path

        if path == "/health":
            try:
                r.ping()
                self._json(200, {"status": "ok", "redis": "connected"})
            except Exception as e:
                self._json(500, {"status": "error", "redis": str(e)})
            return

        if path == "/sdk/remote.js":
            # Serve the remote MCP file — no auth needed
            sdk_path = Path(__file__).parent.parent / "mcp" / "remote.js"
            try:
                body = sdk_path.read_bytes()
                self.send_response(200)
                self.send_header("Content-Type", "application/javascript")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
            except Exception:
                self._json(404, {"error": "SDK not found"})
            return

        token = self._auth()
        if not token:
            return

        params = self._params()

        if path == "/inbox":
            agent = params.get("agent", "unknown")
            limit = int(params.get("limit", "10"))
            project = self._project(token, params.get("project"))
            stream = _agent_stream(agent, project)
            entries = r.xrevrange(stream, count=limit)
            # Legacy fallback for global scope
            if not entries and not project:
                entries = r.xrevrange(_legacy_stream(agent), count=limit)
            messages = []
            for mid, data in entries:
                payload = json.loads(data.get("payload", "{}"))
                messages.append({
                    "id": mid,
                    "source": data.get("source", "?"),
                    "type": data.get("type", "?"),
                    "text": payload.get("text", ""),
                    "timestamp": data.get("timestamp", "?"),
                    "project": data.get("project", ""),
                })
            self._json(200, {"agent": agent, "project": project, "messages": messages})

        elif path == "/peers":
            project = self._project(token, params.get("project"))
            # Registry (live agents)
            registry = []
            pat = f"sos:registry:{project}:*" if project else "sos:registry:*"
            cursor = 0
            while True:
                cursor, keys = r.scan(cursor, match=pat, count=100)
                for key in keys:
                    info = r.hgetall(key)
                    if info:
                        registry.append(info)
                if cursor == 0:
                    break
            # Streams
            streams = []
            cursor = 0
            stream_pat = _scan_streams(project)
            while True:
                cursor, keys = r.scan(cursor, match=stream_pat, count=100)
                for key in keys:
                    agent = key.split(":")[-1]
                    length = r.xlen(key)
                    streams.append({"agent": agent, "messages": length})
                if cursor == 0:
                    break
            # Legacy streams for global
            if not project:
                cursor = 0
                while True:
                    cursor, keys = r.scan(cursor, match="sos:stream:sos:channel:private:agent:*", count=100)
                    for key in keys:
                        agent = key.split(":")[-1]
                        length = r.xlen(key)
                        streams.append({"agent": agent, "messages": length, "legacy": True})
                    if cursor == 0:
                        break
            self._json(200, {"project": project, "registered": registry, "streams": streams})

        else:
            self._json(404, {"error": "Not found"})

    def do_POST(self) -> None:
        path = urlparse(self.path).path
        token = self._auth()
        if not token:
            return

        body = self._body()

        if path == "/announce":
            agent = body.get("agent", "unknown")
            tool = body.get("tool", "remote")
            summary = body.get("summary", f"{tool} session")
            project = self._project(token, body.get("project"))
            ts = now_iso()
            reg_key = _registry_key(agent, project)
            r.hset(reg_key, mapping={
                "name": agent,
                "tool": tool,
                "project": project or "",
                "pid": str(body.get("pid", 0)),
                "tty": body.get("tty", "remote"),
                "cwd": body.get("cwd", "~"),
                "summary": summary,
                "registered_at": ts,
                "last_seen": ts,
            })
            r.expire(reg_key, 600)
            msg = sos_msg("announce", f"agent:{agent}", "broadcast", f"{agent} ({tool}) online: {summary}", project)
            broadcast_stream = f"{_prefix(project)}:broadcast"
            r.xadd(broadcast_stream, msg)
            self._json(200, {"status": "announced", "agent": agent, "project": project})

        elif path == "/send":
            from_agent = body.get("from", "unknown")
            to_agent = body.get("to", "")
            text = body.get("text", "")
            project = self._project(token, body.get("project"))
            if not to_agent or not text:
                self._json(400, {"error": "Missing 'to' or 'text'"})
                return
            stream = _agent_stream(to_agent, project)
            channel = _agent_channel(to_agent, project)
            msg = sos_msg("chat", f"agent:{from_agent}", f"agent:{to_agent}", text, project)
            mid = r.xadd(stream, msg)
            r.publish(channel, json.dumps(msg))
            r.publish(f"sos:wake:{to_agent}", json.dumps(msg))
            self._json(200, {"status": "sent", "stream_id": mid, "project": project})

        elif path == "/broadcast":
            from_agent = body.get("from", "unknown")
            text = body.get("text", "")
            squad = body.get("squad")
            project = self._project(token, body.get("project"))
            if not text:
                self._json(400, {"error": "Missing 'text'"})
                return
            if squad:
                channel = f"sos:channel:project:{project}:squad:{squad}" if project else f"sos:channel:squad:{squad}"
            else:
                channel = f"sos:channel:project:{project}:broadcast" if project else "sos:channel:global"
            stream = f"{_prefix(project)}:{'squad:' + squad if squad else 'broadcast'}"
            msg = sos_msg("broadcast", f"agent:{from_agent}", channel, text, project)
            mid = r.xadd(stream, msg)
            r.publish(channel, json.dumps(msg))
            self._json(200, {"status": "broadcast", "channel": channel, "stream_id": mid, "project": project})

        elif path == "/ask":
            agent = body.get("agent", "")
            message = body.get("message", "")
            if not agent or not message:
                self._json(400, {"error": "Missing 'agent' or 'message'"})
                return
            import subprocess
            try:
                result = subprocess.run(
                    ["openclaw", "agent", "--agent", agent, "-m", message, "--json"],
                    capture_output=True, text=True, timeout=120,
                )
                if result.returncode != 0:
                    self._json(500, {"error": result.stderr[:200]})
                    return
                data = json.loads(result.stdout)
                payloads = data.get("result", {}).get("payloads", [])
                reply = "\n".join(p.get("text", "") for p in payloads if p.get("text"))
                self._json(200, {"agent": agent, "reply": reply, "status": "ok"})
            except subprocess.TimeoutExpired:
                self._json(504, {"error": "Agent timed out"})
            except Exception as e:
                self._json(500, {"error": str(e)})

        elif path == "/heartbeat":
            agent = body.get("agent", "unknown")
            project = self._project(token, body.get("project"))
            reg_key = _registry_key(agent, project)
            r.hset(reg_key, "last_seen", now_iso())
            r.expire(reg_key, 600)
            self._json(200, {"status": "ok"})

        else:
            self._json(404, {"error": "Not found"})

    def log_message(self, format, *args) -> None:
        pass


def main() -> None:
    secrets_path = "/home/mumega/.env.secrets"
    if os.path.exists(secrets_path):
        with open(secrets_path) as f:
            for line in f:
                line = line.strip()
                if "=" in line and not line.startswith("#"):
                    k, v = line.split("=", 1)
                    os.environ.setdefault(k, v)

    global r
    pw = os.environ.get("REDIS_PASSWORD", REDIS_PASSWORD)
    r = redis.Redis(host="localhost", port=6379, password=pw, decode_responses=True)

    server = HTTPServer(("0.0.0.0", PORT), BusHandler)
    print(f"Bus bridge listening on :{PORT} (tokens from {TOKENS_PATH})")
    server.serve_forever()


if __name__ == "__main__":
    main()
