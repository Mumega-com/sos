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
import time
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
    import hmac
    raw_hash = hashlib.sha256(raw_token.encode()).hexdigest()
    tokens = _load_tokens()
    for t in tokens:
        if not t.get("active", True):
            continue
        stored_hash = t.get("token_hash") or t.get("hash", "")
        if stored_hash and hmac.compare_digest(stored_hash, raw_hash):
            return t
        plaintext = t.get("token", "")
        if plaintext and hmac.compare_digest(plaintext, raw_token):
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


# LOCK-S028-B-1-bus-bridge-public-hardening — L-3 (audit log additive)
#
# Phase 1 (S028 B2) emits a structured record per sensitive-endpoint
# invocation to the Redis stream `sos:audit:bridge:v1`. Pure observation:
# no behavioral change. Enables (a) shadow-audit window for B-1.2 grants
# backfill, and (b) pre-binding observation for B-1.1 — see how often
# tokens claim agent != token.get("agent") today before the hard gate
# lands at Phase 3.
#
# Audit failures must NEVER block business logic in Phase 1 (observability,
# not enforcement). XADD failure is logged via `print` to stderr and the
# request continues. From Phase 3 onward, identity-binding is enforced by
# `_assert_caller`, not by audit-log success.
def _audit_emit(
    token: dict,
    endpoint: str,
    claimed: str | None = None,
    target: str | None = None,
    extra: dict | None = None,
) -> None:
    try:
        token_agent = str(token.get("agent", "") or "")
        token_hash = str(token.get("token_hash") or token.get("hash", "") or "")
        token_hash_short = token_hash[:16] if token_hash else ""
        claimed_str = str(claimed or "")
        record: dict[str, str] = {
            "ts": now_iso(),
            "endpoint": endpoint,
            "token_agent": token_agent,
            "token_hash_short": token_hash_short,
            "claimed_agent": claimed_str,
            "target": str(target or ""),
            # Phase-1 observation: would_block_at_phase_3 = caller's claimed
            # identity does not match token-bound identity. Empty (not
            # evaluable) when either side is absent — no identity claimed
            # (e.g. /heartbeat without body.agent) OR malformed token record
            # missing `agent`. "0" reserved strictly for evaluated mismatch.
            "binding_match": (
                "1" if (claimed_str and token_agent and claimed_str == token_agent)
                else ("0" if (claimed_str and token_agent) else "")
            ),
        }
        if extra:
            for k, v in extra.items():
                record[str(k)] = str(v)
        # MAXLEN cap: bridge is hot path; cap stream at ~100k entries
        # (approximate trim) to keep memory bounded. Operators can copy
        # to durable storage out-of-band.
        r.xadd("sos:audit:bridge:v1", record, maxlen=100000, approximate=True)
    except Exception as exc:  # pragma: no cover — audit must not block
        # Defense-in-depth: never raise from audit. Log to stderr so
        # operators see failures.
        print(f"[bridge] audit emit failed for {endpoint}: {exc}")


# LOCK-S028-B-1-bus-bridge-public-hardening — L-1 (rate-limit shadow)
#
# Phase 2 (S028 B2) computes a per-token rate verdict per request and
# records it in the same `sos:audit:bridge:v1` stream via _audit_emit's
# extra-fields slot. Pure observation; never blocks. Phase 4 flips to
# enforce: 429 with Retry-After once the shadow window confirms limits
# don't trip legitimate tokens.
#
# Window: fixed per-minute buckets keyed
#   bus:ratelimit:{token_hash}:{epoch_minute}
# INCR + EXPIRE 90s (1.5x window) so a request landing on the boundary
# does not lose state if a partner request hits the next bucket
# immediately.
#
# Caps: default 60/min. Tokens with top-level field
#   rate_limit_class: "elevated"
# raised to 600/min. `rate_limit_class` is a top-level token field
# (capacity), orthogonal to `grants` array (capability). Pinned by
# Athena P2 carry verdict 2026-05-05T03:00Z.
RATE_LIMIT_DEFAULT = 60
RATE_LIMIT_ELEVATED = 600
RATE_LIMIT_WINDOW_SEC = 60
RATE_LIMIT_TTL_SEC = 90  # > window so boundary state survives next-bucket race


def _rate_limit_for(token: dict) -> int:
    cls = str(token.get("rate_limit_class", "") or "")
    if cls == "elevated":
        return RATE_LIMIT_ELEVATED
    return RATE_LIMIT_DEFAULT


def _rate_check(token: dict, endpoint: str) -> dict:
    """Compute Phase-2 rate verdict (shadow). NEVER blocks.

    Returns a dict suitable to spread into `_audit_emit(extra=...)` so the
    verdict lands in the same audit record as the endpoint event.

    Verdicts:
      allow         — count <= limit
      would_block   — count > limit (Phase 4 will return 429 here)
      skip          — observability surface unable to evaluate (no
                      token_hash, or Redis error). Defense-in-depth: never
                      raise from rate-check; never let it block traffic.
    """
    try:
        token_hash = str(token.get("token_hash") or token.get("hash") or "")
        if not token_hash:
            return {"rate_verdict": "skip", "rate_reason": "no_token_hash"}
        bucket = int(time.time()) // RATE_LIMIT_WINDOW_SEC
        key = f"bus:ratelimit:{token_hash}:{bucket}"
        count = r.incr(key)
        # Set TTL only on first INCR of the bucket — saves a write per
        # subsequent request in the same window.
        if count == 1:
            r.expire(key, RATE_LIMIT_TTL_SEC)
        limit = _rate_limit_for(token)
        verdict = "allow" if count <= limit else "would_block"
        return {
            "rate_verdict": verdict,
            "rate_count": str(count),
            "rate_limit": str(limit),
            "rate_endpoint": endpoint,
        }
    except Exception as exc:  # pragma: no cover — observability never blocks
        return {"rate_verdict": "skip", "rate_reason": f"err:{type(exc).__name__}"}


def sos_msg(msg_type: str, source: str, target: str, text: str, project: str | None = None) -> dict:
    """v0.4.0: build a v1-shaped bus message using Pydantic contracts.

    Legacy callers passed msg_type="chat" or "broadcast" — those are translated
    to v1 "send" with the appropriate target (agent: for chat, sos:channel: for
    broadcast). "announce" maps directly to AnnounceMessage. Anything else is
    rejected.

    Returns a dict ready for `redis.xadd` (payload JSON-encoded into a string).
    """
    from sos.contracts.messages import SendMessage, AnnounceMessage

    # Legacy → v1 type mapping
    v1_type = {"chat": "send", "broadcast": "send"}.get(msg_type, msg_type)

    # Legacy target normalization: bare "broadcast" → sos:channel:global
    if target == "broadcast":
        target = "sos:channel:global"

    if v1_type == "send":
        m = SendMessage(
            source=source,
            target=target,
            timestamp=SendMessage.now_iso(),
            message_id=str(uuid4()),
            payload={"text": text, "content_type": "text/plain"},
        )
    elif v1_type == "announce":
        m = AnnounceMessage(
            source=source,
            target=target,
            timestamp=AnnounceMessage.now_iso(),
            message_id=str(uuid4()),
            payload={"text": text} if text else None,
        )
    else:
        raise ValueError(f"unknown message type: {msg_type!r}")

    msg = m.to_redis_fields()
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
            # LOCK-S028-B-1 L-3 audit log + L-1 rate verdict (Phase 1+2; shadow).
            _audit_emit(token, "/inbox", claimed=agent, target=agent,
                        extra=_rate_check(token, "/inbox"))
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

        # S027 D-1b — internal tenant provisioning endpoint.
        # Auth domain split from tokens.json Bearer (identity domain): this endpoint
        # uses INTERNAL_API_SECRET env var (s2s domain). Per Loom routing approval
        # 2026-05-04 21:46Z. LOCK-D-1b-internal-bearer-fail-closed.
        if path == "/api/internal/tenants/provision":
            self._handle_tenant_provision()
            return

        # S027 D-2b — internal tenant agent activation endpoint.
        # Path: /api/internal/tenants/:id/agents/activate (parameterized URL).
        # Auth: INTERNAL_API_SECRET (same s2s domain as D-1b).
        # Athena brief-shape gate iter-2 GREEN 2026-05-04T22:33Z.
        import re as _re
        _activate_match = _re.match(r"^/api/internal/tenants/([^/]+)/agents/activate$", path)
        if _activate_match:
            self._handle_tenant_agent_activate(_activate_match.group(1))
            return

        token = self._auth()
        if not token:
            return

        body = self._body()

        if path == "/announce":
            agent = body.get("agent", "unknown")
            tool = body.get("tool", "remote")
            summary = body.get("summary", f"{tool} session")
            project = self._project(token, body.get("project"))
            # LOCK-S028-B-1 L-3 audit log (Phase 1; observation only).
            _audit_emit(token, "/announce", claimed=agent, target=agent, extra={"tool": tool})
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
            wait_for_delivery = body.get("wait_for_delivery", False)
            # LOCK-S028-B-1 L-3 audit log + L-1 rate verdict (Phase 1+2; shadow).
            _audit_emit(token, "/send", claimed=from_agent, target=to_agent,
                        extra=_rate_check(token, "/send"))
            if not to_agent or not text:
                self._json(400, {"error": "Missing 'to' or 'text'"})
                return
            stream = _agent_stream(to_agent, project)
            channel = _agent_channel(to_agent, project)
            msg = sos_msg("chat", f"agent:{from_agent}", f"agent:{to_agent}", text, project)
            message_id = msg.get("message_id", "")
            try:
                entry_id = r.xadd(stream, msg)
            except Exception as exc:
                self._json(500, {"ok": False, "status": "dropped", "error": str(exc)})
                return
            r.publish(channel, json.dumps(msg))
            r.publish(f"sos:wake:{to_agent}", json.dumps(msg))

            result = {
                "ok": True,
                "status": "queued",
                "message_id": message_id,
                "stream": stream,
                "entry_id": entry_id,
                "project": project,
            }

            # OmniB BUS-DELIVERY V1: honest about limitation.
            # XADD entry_id proves stream-write succeeded (catches bus-layer drops).
            # True receiver-side delivery confirmation requires app-layer ACK (S010).
            if wait_for_delivery:
                result["delivered"] = False
                result["status"] = "queued"

            self._json(200, result)

        elif path == "/broadcast":
            from_agent = body.get("from", "unknown")
            text = body.get("text", "")
            squad = body.get("squad")
            project = self._project(token, body.get("project"))
            # LOCK-S028-B-1 L-3 audit log + L-1 rate verdict (Phase 1+2; shadow).
            _audit_emit(
                token, "/broadcast",
                claimed=from_agent,
                target=(f"squad:{squad}" if squad else "broadcast"),
                extra=_rate_check(token, "/broadcast"),
            )
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
            # LOCK-S028-B-1 L-1 rate verdict on highest-amplification endpoint.
            # /ask is not in the L-3 5-endpoint identity-binding scope (no
            # caller-asserted identity claim), but rate observation is required
            # before Phase 4 enforce-flip + L-2 concurrency cap.
            _audit_emit(token, "/ask", claimed=None, target=agent,
                        extra=_rate_check(token, "/ask"))
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
            # LOCK-S028-B-1 L-3 audit log (Phase 1; observation only).
            _audit_emit(token, "/heartbeat", claimed=agent, target=agent)
            reg_key = _registry_key(agent, project)
            r.hset(reg_key, "last_seen", now_iso())
            r.expire(reg_key, 600)
            self._json(200, {"status": "ok"})

        else:
            self._json(404, {"error": "Not found"})

    def _handle_tenant_agent_activate(self, url_tenant_id: str) -> None:
        """S027 D-2b — POST /api/internal/tenants/:id/agents/activate.

        Auth: INTERNAL_API_SECRET env-var Bearer.
        Body: {tenant_id, tenant_slug, agent_kind, actor_token_hash}
              OR {tenant_id, tenant_slug, agent_kind, actor_type: "platform-admin"}
        Returns 200 with {agent_name, qnft_seed_hex, token_hash, scaffold_path,
                          idempotency: {qnft_minted, token_minted, routing_registered, scaffold_created}}.

        7 invariants enforced (L-1..L-7). Athena REFINE-1: D-2b is the real claim
        validator. URL :id is cross-checked against body.tenant_id (defense-in-depth
        per Athena P2-note from kasra_s027_d2_d2b_brief_gate_002 GREEN).
        """
        from sos.bus.tenant_agent_activation import (
            activate_tenant_agent,
        )
        from sos.bus.tenant_provisioning import (
            authenticate_bearer,
            get_internal_secret,
            ProvisionError,
        )

        # 1. Substrate misconfiguration check — fail-closed BEFORE any work
        if not get_internal_secret():
            self._json(503, {"error": "internal_secret_unconfigured"})
            return

        # 2. Bearer auth — constant-time compare
        auth = self.headers.get("Authorization", "")
        if not authenticate_bearer(auth):
            self._json(401, {"error": "unauthorized"})
            return

        # 3. Body parse
        try:
            body = self._body()
        except (json.JSONDecodeError, ValueError):
            self._json(422, {"error": "invalid_json_body"})
            return

        # 4. URL :id ↔ body.tenant_id consistency (defense-in-depth per Athena P2)
        if not isinstance(body, dict):
            self._json(422, {"error": "invalid_body", "message": "body must be a JSON object"})
            return
        if body.get("tenant_id") != url_tenant_id:
            self._json(
                403,
                {
                    "error": "tenant_id_url_body_mismatch",
                    "message": "URL :id and body.tenant_id must match",
                },
            )
            return

        # 5. Activate (validation + 4 idempotent substrate steps + 1 claim validator)
        try:
            result = activate_tenant_agent(body)
            self._json(200, result)
        except ProvisionError as e:
            self._json(e.status, {"error": e.code, "message": e.message})

    def _handle_tenant_provision(self) -> None:
        """S027 D-1b — POST /api/internal/tenants/provision.

        Auth: INTERNAL_API_SECRET env-var Bearer (NOT tokens.json — separate s2s domain).
        Body: { tenant_id, slug, display_name, industry }
        Returns 200 with { mirror_key, bus_token, scaffold_path, idempotency: {...} }.

        LOCK-D-1b-internal-bearer-fail-closed: missing env → 503 BEFORE any disk read.
        Bad/missing Bearer → 401 BEFORE body parse. Body validation → 422 BEFORE disk write.
        """
        from sos.bus.tenant_provisioning import (
            authenticate_bearer,
            get_internal_secret,
            provision_tenant,
            ProvisionError,
        )

        # 1. Substrate misconfiguration check — fail-closed BEFORE any work
        if not get_internal_secret():
            self._json(503, {"error": "internal_secret_unconfigured"})
            return

        # 2. Bearer auth — constant-time compare in authenticate_bearer
        auth = self.headers.get("Authorization", "")
        if not authenticate_bearer(auth):
            self._json(401, {"error": "unauthorized"})
            return

        # 3. Body parse + validate (validation lives inside provision_tenant)
        try:
            body = self._body()
        except (json.JSONDecodeError, ValueError):
            self._json(422, {"error": "invalid_json_body"})
            return

        # 4. Provision (validation + 3 idempotent steps)
        try:
            result = provision_tenant(body)
            self._json(200, result)
        except ProvisionError as e:
            self._json(e.status, {"error": e.code, "message": e.message})

    def log_message(self, format, *args) -> None:
        pass


def main() -> None:
    secrets_path = str(Path.home() / ".env.secrets")
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
