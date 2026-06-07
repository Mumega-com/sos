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
from http.server import HTTPServer, ThreadingHTTPServer, BaseHTTPRequestHandler
from pathlib import Path
from urllib.parse import urlparse, parse_qs
from uuid import uuid4

import redis

REDIS_PASSWORD = os.environ.get("REDIS_PASSWORD", "")
REDIS_URL = os.environ.get("REDIS_URL")
REDIS_HOST = os.environ.get("REDIS_HOST", "localhost")
REDIS_PORT = int(os.environ.get("REDIS_PORT", "6379"))
PORT = int(os.environ.get("BUS_BRIDGE_PORT", "6380"))
TOKENS_PATH = Path(os.environ.get("SOS_BUS_TOKENS_PATH", Path(__file__).parent / "tokens.json"))

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


def _normalize_subscriptions(raw) -> list[str]:
    if not isinstance(raw, list):
        return []
    subscriptions: list[str] = []
    seen: set[str] = set()
    for item in raw:
        value = str(item).strip()
        if not value:
            continue
        if not value.startswith("sos:channel:"):
            value = f"sos:channel:{value}"
        if value not in seen:
            seen.add(value)
            subscriptions.append(value)
    return subscriptions


def _valid_stream_id(value: str) -> bool:
    import re
    return bool(re.fullmatch(r"\d+-\d+", str(value)))


def _stream_id_sort_key(value: str) -> tuple[int, int]:
    try:
        left, right = str(value).split("-", 1)
        return int(left), int(right)
    except Exception:
        return 0, 0


def _subscription_streams(token: dict, project: str | None) -> list[tuple[str, str]]:
    return _subscription_streams_for(token, project, token.get("subscriptions") or [])


def _subscription_streams_for(
    token: dict,
    project: str | None,
    subscriptions: list[str],
) -> list[tuple[str, str]]:
    streams: list[tuple[str, str]] = []
    seen: set[str] = set()
    token_project = token.get("project")
    is_system = not bool(token_project)
    for subscription in _normalize_subscriptions(subscriptions):
        stream = _subscription_to_stream(subscription, project, is_system)
        if stream and stream not in seen:
            seen.add(stream)
            streams.append((f"subscription:{subscription}", stream))
    return streams


def _runtime_subscriptions(values: list[str]) -> list[str]:
    raw: list[str] = []
    for value in values:
        raw.extend(part.strip() for part in str(value).split(","))
    return _normalize_subscriptions(raw)


def _subscription_to_stream(subscription: str, project: str | None, is_system: bool) -> str | None:
    if subscription in {"sos:channel:global", "sos:channel:broadcast"}:
        if not is_system and project:
            return f"sos:stream:project:{project}:broadcast"
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
    if not is_system and channel_project != project:
        return None
    channel_kind = parts[4]
    if channel_kind in {"global", "broadcast"}:
        return f"sos:stream:project:{channel_project}:broadcast"
    if channel_kind == "squad" and len(parts) >= 6 and parts[5]:
        return f"sos:stream:project:{channel_project}:squad:{parts[5]}"
    return None


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
# --- Boot Receipt System (LOCK-S063-E-1) ---
#
# Phase 1 (Receipt): /announce issues a signed receipt in Redis with 5-min TTL.
# Phase 2 (Coherence Violation): action endpoints shadow-check the receipt and
#   emit a coherence_violation field to sos:audit:bridge:v1 when absent.
# Phase 3 (Enforce): flip BUS_COHERENCE_ENFORCE=1 to hard-block violators.
#
# Receipt key: sos:boot-receipt:{project or 'global'}:{agent}
# TTL: 300s (refreshed on each /announce and /heartbeat)
BOOT_RECEIPT_TTL_SEC = 300


def _boot_receipt_key(agent: str, project: str | None) -> str:
    scope = project or "global"
    return f"sos:boot-receipt:{scope}:{agent}"


def _boot_receipt_issue(token: dict, agent: str, project: str | None) -> None:
    """Write a boot receipt to Redis. Called on successful /announce."""
    token_hash = str(token.get("token_hash") or token.get("hash", "") or "")
    key = _boot_receipt_key(agent, project)
    r.hset(key, mapping={
        "agent": agent,
        "project": project or "",
        "issued_at": now_iso(),
        "token_hash_short": token_hash[:16],
    })
    r.expire(key, BOOT_RECEIPT_TTL_SEC)


def _coherence_check(agent: str, project: str | None, endpoint: str) -> dict:
    """Shadow-check for a valid boot receipt. Returns audit extra fields.

    Never raises. Always returns a dict safe to pass to _audit_emit extra.
    coherence_violation='1' means no valid receipt was found for this agent.
    coherence_violation='0' means receipt present and valid.
    """
    try:
        key = _boot_receipt_key(agent, project)
        receipt = r.hgetall(key)
        if receipt:
            return {"coherence_violation": "0"}
        return {
            "coherence_violation": "1",
            "coherence_reason": f"no boot receipt for agent={agent} project={project or 'global'}",
        }
    except Exception:
        # Never raise from coherence check — bus must stay live
        return {"coherence_violation": "err"}


def _coherence_enforce_enabled() -> bool:
    return os.environ.get("BUS_COHERENCE_ENFORCE", "0") == "1"


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


# LOCK-S028-B-1 L-2 — /ask concurrency cap (Phase 4 prerequisite + flip)
#
# /ask runs `subprocess.run(..., timeout=120)` (openclaw agent invocation).
# Without a per-token concurrency cap a single token can flood the bridge
# with N parallel /ask calls, holding N agent subprocesses for up to 120s
# each. This is the highest-amplification surface on the bridge.
#
# Pattern:
#   key = bus:ask:inflight:{token_hash}
#   INCR + EXPIRE 150s on first INCR (TTL > 120s subprocess timeout +
#     30s post-subprocess work; defense-in-depth against bridge crash —
#     try/finally is the primary release path)
#   if count > ASK_CONCURRENCY_CAP and enforce flag: 429 + DECR
#   else: subprocess.run inside try/finally that DECRs on every exit path
#
# Cap selection: 2 per token. With timeout=120s a token can hold at most
# 2 agent subprocesses concurrently. The ADV-b case (50 concurrent /ask
# from one token) collapses to "first 2 succeed, 48 receive 429".
# Token override via top-level field `ask_concurrency_cap` (mirror of
# rate_limit_class pattern; capacity, not capability — orthogonal to
# grants array).
#
# Enforcement gate: BUS_PHASE4_ASK_ENFORCE=1 flips 429. Pre-flip the
# counter is INCRed/DECRed (shadow observability; ops can read inflight
# distribution from Redis); 429 only fires post-Athena-Phase-4-gate.
ASK_CONCURRENCY_DEFAULT = 2
ASK_INFLIGHT_TTL_SEC = 150  # > subprocess timeout 120s + post-work margin


def _ask_concurrency_for(token: dict) -> int:
    """Per-token cap. Top-level `ask_concurrency_cap` field overrides
    default. Unknown / non-positive → fall back to default (fail-closed
    on capacity, mirror of rate_limit_class)."""
    raw = token.get("ask_concurrency_cap")
    try:
        cap = int(raw) if raw is not None else ASK_CONCURRENCY_DEFAULT
    except (TypeError, ValueError):
        return ASK_CONCURRENCY_DEFAULT
    return cap if cap > 0 else ASK_CONCURRENCY_DEFAULT


def _ask_inflight_key(token: dict) -> str | None:
    token_hash = str(token.get("token_hash") or token.get("hash") or "")
    if not token_hash:
        return None
    return f"bus:ask:inflight:{token_hash}"


def _ask_acquire(token: dict) -> dict:
    """Atomic INCR + EXPIRE on first INCR. Returns dict with concurrency
    verdict suitable for `_audit_emit(extra=...)`.

    Verdicts:
      allow                    — count <= cap (subprocess proceeds)
      would_block_or_block     — count > cap (post-gate 429; pre-gate
                                  shadow observation only)
      skip                     — token has no token_hash, or Redis error.
                                  Defense-in-depth: never raise from the
                                  acquire path; never block traffic on
                                  observability failure.

    On success the caller MUST pair with `_ask_release(token)` in
    try/finally — otherwise the inflight counter leaks until TTL expiry.
    """
    try:
        key = _ask_inflight_key(token)
        if key is None:
            return {"ask_concurrency_verdict": "skip", "ask_reason": "no_token_hash"}
        count = r.incr(key)
        if count == 1:
            r.expire(key, ASK_INFLIGHT_TTL_SEC)
        cap = _ask_concurrency_for(token)
        if count > cap:
            return {
                "ask_concurrency_verdict": "would_block_or_block",
                "ask_count": str(count),
                "ask_cap": str(cap),
                "ask_inflight_key": key,
            }
        return {
            "ask_concurrency_verdict": "allow",
            "ask_count": str(count),
            "ask_cap": str(cap),
            "ask_inflight_key": key,
        }
    except Exception as exc:  # pragma: no cover — observability never blocks
        return {
            "ask_concurrency_verdict": "skip",
            "ask_reason": f"err:{type(exc).__name__}",
        }


def _ask_release(token: dict) -> None:
    """DECR the inflight counter. Never raises (pairs with `_ask_acquire`
    in try/finally). If the counter drops to 0, leave the key for TTL
    cleanup — DELETE here would race with a concurrent INCR landing in
    the same window."""
    try:
        key = _ask_inflight_key(token)
        if key is None:
            return
        r.decr(key)
    except Exception:  # pragma: no cover — release path never raises
        pass


def _ask_enforce_enabled() -> bool:
    """Phase 4 enforce-flip. False until Athena gate-flip; reads env at
    call time so ops can flip without code change."""
    return os.environ.get("BUS_PHASE4_ASK_ENFORCE", "").strip() in ("1", "true", "yes")


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


# LOCK-S028-B-1.1-caller-identity-binding (sub-LOCK; Phase 3 hard gate)
#
# Phase 3 (S028 B2) enforces that the caller-asserted identity in the
# request body/query equals the token-bound identity. Five endpoints:
#   /send       body.from
#   /broadcast  body.from
#   /announce   body.agent
#   /inbox      query.agent
#   /heartbeat  body.agent
#
# Mismatch → 403 identity_binding_mismatch BEFORE any business logic.
# Malformed token (token.agent missing/empty) → 403 malformed_token_record
# UNCONDITIONALLY. AGD canon: silent-fail-open-at-contract-boundaries
# is a third-instance violation; never log-and-continue here.
#
# No backwards-compat shim. Hard cutover at Phase 3 (Athena verdict
# 2026-05-05T02:56Z, brief stub L-1.1.d). The Phase 1+2 audit stream
# binding_match field made the would-block delta observable across the
# shadow window; Phase 3 trips the gate.
class _IdentityBindingError(Exception):
    """Raised by `_assert_caller` on identity-binding violation.

    Attributes:
        code:    "identity_binding_mismatch" or "malformed_token_record"
        message: human-readable explanation; safe to surface to caller.
    """

    def __init__(self, code: str, message: str = "") -> None:
        super().__init__(message or code)
        self.code = code
        self.message = message


def _assert_caller(token: dict, claimed_agent: str | None) -> None:
    """Raise _IdentityBindingError if caller-asserted identity does not
    match the token-bound identity.

    Returns None on match (callable as an assertion).

    Raises:
        _IdentityBindingError(code="malformed_token_record")
            when token.get("agent") is None/empty (fail-closed; AGD canon).
        _IdentityBindingError(code="identity_binding_mismatch")
            when claimed_agent is None/empty OR != token.agent.
    """
    token_agent = token.get("agent") if isinstance(token, dict) else None
    if not token_agent:
        # Fail-closed: token records lacking an agent field cannot be
        # used to assert any identity. Never log-and-continue.
        raise _IdentityBindingError(
            "malformed_token_record",
            "token record missing 'agent' field",
        )
    if not claimed_agent:
        # Phase 3 hard gate: every sensitive endpoint MUST claim an
        # identity. Empty/None claim is a binding mismatch (cannot be
        # verified to match the token).
        raise _IdentityBindingError(
            "identity_binding_mismatch",
            "request did not claim an agent identity",
        )
    if claimed_agent != token_agent:
        raise _IdentityBindingError(
            "identity_binding_mismatch",
            f"token bound to '{token_agent}', request claims '{claimed_agent}'",
        )


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

    def _enforce_caller(self, token: dict, claimed: str | None) -> bool:
        """LOCK-S028-B-1.1 hard gate. Returns True if caller-asserted
        identity matches token-bound identity. On mismatch, emits 403 and
        returns False; the handler MUST `return` immediately on False.

        Audit emission for the violation is the caller's responsibility —
        most handlers already emit before this gate (Phase 1+2 audit). The
        405-style sequence is: rate-check → audit → enforce → business.
        """
        try:
            _assert_caller(token, claimed)
            return True
        except _IdentityBindingError as exc:
            self._json(403, {"error": exc.code, "message": exc.message})
            return False

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

    def _read_inbox_messages(
        self,
        *,
        token: dict,
        agent: str,
        project: str | None,
        since: str | None,
        limit: int,
        subscriptions: list[str],
        newest_first: bool = True,
    ) -> list[dict]:
        """Read inbox streams using the bridge /inbox semantics.

        Shared by /inbox and /watch so project/global/legacy/subscription stream
        coverage does not drift between poll and SSE delivery paths.
        """
        range_start = f"({since}" if since and _valid_stream_id(since) else "-"
        streams_to_check: list[tuple[str, str]] = []
        streams_to_check.append(
            ("project", _agent_stream(agent, project))
            if project
            else ("project-sos", _agent_stream(agent, "sos"))
        )
        streams_to_check.append(("global", _agent_stream(agent, None)))
        streams_to_check.append(("legacy-private", _legacy_stream(agent)))
        if subscriptions:
            streams_to_check.extend(_subscription_streams_for(token, project, subscriptions))
        else:
            streams_to_check.extend(_subscription_streams(token, project))

        entries: list[tuple[str, dict, str, str]] = []
        seen: set[tuple[str, str]] = set()
        # Forward-poll (`since` cursor) wants ascending-from-cursor; xrange is right.
        # But a fresh snapshot (/inbox, no cursor, newest_first) with
        # xrange(min="-", count=limit) returns the OLDEST `limit` entries and never
        # reaches recent messages once the buffer is deeper than `limit` — so the
        # caller sees stale messages. Fetch the newest `limit` via xrevrange in that
        # case; the sort below re-orders regardless. /watch (newest_first=False)
        # keeps ascending replay.
        snapshot_newest = range_start == "-" and newest_first
        for stream_kind, stream in streams_to_check:
            try:
                if snapshot_newest:
                    batch = r.xrevrange(stream, max="+", min="-", count=limit)
                else:
                    batch = r.xrange(stream, min=range_start, max="+", count=limit)
            except TypeError:
                if snapshot_newest:
                    batch = r.xrevrange(stream, "+", "-", limit)
                else:
                    batch = r.xrange(stream, range_start, "+", limit)
            except Exception:
                continue
            for mid, data in batch:
                key = (stream, str(mid))
                if key in seen:
                    continue
                seen.add(key)
                entries.append((str(mid), data, stream_kind, stream))

        entries.sort(key=lambda item: _stream_id_sort_key(item[0]), reverse=newest_first)
        messages: list[dict] = []
        seen_messages: set[tuple[str, str, str]] = set()
        for mid, data, stream_kind, stream in entries:
            parsed_payload = data.get("payload", "{}")
            try:
                payload = json.loads(parsed_payload) if isinstance(parsed_payload, str) else {}
            except json.JSONDecodeError:
                payload = {}
            source = data.get("source", "?")
            text = payload.get("text") or data.get("text", "")
            message_key = (str(data.get("id") or mid), str(source), str(text))
            if message_key in seen_messages:
                continue
            seen_messages.add(message_key)
            messages.append({
                "id": mid,
                "stream_id": mid,
                "stream": stream,
                "stream_kind": stream_kind,
                "source": source,
                "sender": source,
                "target": data.get("target", ""),
                "type": data.get("type", "?"),
                "text": text,
                "timestamp": data.get("timestamp", "?"),
                "project": data.get("project", ""),
            })
            if len(messages) >= limit:
                break
        return messages

    def _write_sse_comment(self, text: str) -> None:
        self.wfile.write(f": {text}\n\n".encode())
        self.wfile.flush()

    def _write_sse_message(self, message: dict) -> None:
        self.wfile.write(f"data: {json.dumps(message)}\n\n".encode())
        self.wfile.flush()

    def _handle_watch(
        self,
        *,
        token: dict,
        agent: str,
        project: str | None,
        since: str | None,
        limit: int,
        subscriptions: list[str],
    ) -> None:
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Cache-Control", "no-cache")
        self.send_header("Connection", "keep-alive")
        self.send_header("X-Accel-Buffering", "no")
        self.end_headers()

        cursor = since if since and _valid_stream_id(since) else None
        redis_password = os.environ.get("REDIS_PASSWORD", REDIS_PASSWORD)
        redis_url = os.environ.get("REDIS_URL", REDIS_URL)
        if redis_url:
            pubsub_client = redis.Redis.from_url(redis_url, decode_responses=True)
        else:
            redis_host = os.environ.get("REDIS_HOST", REDIS_HOST)
            redis_port = int(os.environ.get("REDIS_PORT", str(REDIS_PORT)))
            pubsub_client = redis.Redis(
                host=redis_host,
                port=redis_port,
                password=redis_password,
                decode_responses=True,
            )
        pubsub = pubsub_client.pubsub(ignore_subscribe_messages=True)
        wake_channel = f"sos:wake:{agent}"

        def flush_pending() -> None:
            nonlocal cursor
            messages = self._read_inbox_messages(
                token=token,
                agent=agent,
                project=project,
                since=cursor,
                limit=limit,
                subscriptions=subscriptions,
                newest_first=False,
            )
            for message in messages:
                self._write_sse_message(message)
            if messages:
                cursor = max(
                    (message["stream_id"] for message in messages),
                    key=_stream_id_sort_key,
                )

        try:
            pubsub.subscribe(wake_channel)
            # Required synthetic wake: flush messages that arrived before the
            # client connected, then enter the pubsub wait loop.
            flush_pending()
            last_keepalive = time.monotonic()
            while True:
                event = pubsub.get_message(timeout=1.0)
                if event and event.get("type") == "message":
                    flush_pending()
                    last_keepalive = time.monotonic()
                    continue
                now = time.monotonic()
                if now - last_keepalive >= 30:
                    self._write_sse_comment("keepalive")
                    last_keepalive = now
        except (BrokenPipeError, ConnectionError, OSError):
            return
        finally:
            try:
                pubsub.unsubscribe(wake_channel)
            except Exception:
                pass
            try:
                pubsub.close()
            except Exception:
                pass
            try:
                pubsub_client.close()
            except Exception:
                pass

    def do_GET(self) -> None:
        path = urlparse(self.path).path

        if path == "/openapi.yaml":
            spec_path = Path(__file__).parent / "openapi.yaml"
            if not spec_path.exists():
                self._json(404, {"error": "openapi.yaml not found"})
                return
            body = spec_path.read_bytes()
            self.send_response(200)
            self.send_header("Content-Type", "application/yaml")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Access-Control-Allow-Origin", "*")
            self.send_header("Cache-Control", "public, max-age=3600")
            self.end_headers()
            self.wfile.write(body)
            return

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
            output_format = str(params.get("format") or "text").lower()
            since = params.get("since")
            query = parse_qs(urlparse(self.path).query)
            override_subscriptions = _runtime_subscriptions(
                query.get("subscription", []) + query.get("subscriptions", [])
            )
            # LOCK-S028-B-1 L-3 audit log + L-1 rate verdict (Phase 1+2; shadow).
            _audit_emit(token, "/inbox", claimed=agent, target=agent,
                        extra=_rate_check(token, "/inbox"))
            # LOCK-S028-B-1.1 Phase 3 hard gate — must follow audit so that
            # binding violations land in the audit stream BEFORE we 403.
            if not self._enforce_caller(token, agent):
                return
            messages = self._read_inbox_messages(
                token=token,
                agent=agent,
                project=project,
                since=since,
                limit=limit,
                subscriptions=override_subscriptions,
                newest_first=True,
            )
            self._json(200, {"agent": agent, "project": project, "messages": messages})

        elif path == "/watch":
            agent = params.get("agent", "unknown")
            limit = int(params.get("limit", "20"))
            project = self._project(token, params.get("project"))
            since = params.get("since")
            query = parse_qs(urlparse(self.path).query)
            override_subscriptions = _runtime_subscriptions(
                query.get("subscription", []) + query.get("subscriptions", [])
            )
            _audit_emit(token, "/watch", claimed=agent, target=agent,
                        extra=_rate_check(token, "/watch"))
            if not self._enforce_caller(token, agent):
                return
            self._handle_watch(
                token=token,
                agent=agent,
                project=project,
                since=since,
                limit=limit,
                subscriptions=override_subscriptions,
            )

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

        # S028 D-3b (C-1b) — internal tenant CUSTOM-agent mint endpoint.
        # Path: /api/internal/tenants/:id/agents/mint (parameterized URL).
        # Auth: INTERNAL_API_SECRET (same s2s domain as D-1b/D-2b).
        # Athena brief-shape gate GREEN 2026-05-05T10:09:53Z (kasra_s028_c1_brief_gate_001).
        _mint_match = _re.match(r"^/api/internal/tenants/([^/]+)/agents/mint$", path)
        if _mint_match:
            self._handle_tenant_agent_mint(_mint_match.group(1))
            return

        token = self._auth()
        if not token:
            return

        body = self._body()

        if path == "/announce":
            agent = body.get("agent", "unknown").removeprefix("agent:")
            tool = body.get("tool", "remote")
            summary = body.get("summary", f"{tool} session")
            project = self._project(token, body.get("project"))
            # LOCK-S028-B-1 L-3 audit log (Phase 1; observation only).
            _audit_emit(token, "/announce", claimed=agent, target=agent, extra={"tool": tool})
            # LOCK-S028-B-1.1 Phase 3 hard gate.
            if not self._enforce_caller(token, agent):
                return
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
            # LOCK-S063-E-1 Phase 1: issue boot receipt on successful announce.
            _boot_receipt_issue(token, agent, project)
            msg = sos_msg("announce", f"agent:{agent}", "broadcast", f"{agent} ({tool}) online: {summary}", project)
            broadcast_stream = f"{_prefix(project)}:broadcast"
            r.xadd(broadcast_stream, msg)
            self._json(200, {"status": "announced", "agent": agent, "project": project})

        elif path == "/send":
            # Accept both bare names and "agent:name" — callers routinely pass
            # the prefixed form; double-prefixing crashed sos_msg validation.
            from_agent = body.get("from", "unknown").removeprefix("agent:")
            to_agent = body.get("to", "").removeprefix("agent:")
            text = body.get("text", "")
            project = self._project(token, body.get("project"))
            wait_for_delivery = body.get("wait_for_delivery", False)
            # LOCK-S028-B-1 L-3 audit log + L-1 rate verdict (Phase 1+2; shadow).
            # LOCK-S063-E-1 Phase 2: coherence violation shadow-check.
            coherence = _coherence_check(from_agent, project, "/send")
            _audit_emit(token, "/send", claimed=from_agent, target=to_agent,
                        extra={**_rate_check(token, "/send"), **coherence})
            # LOCK-S028-B-1.1 Phase 3 hard gate.
            if not self._enforce_caller(token, from_agent):
                return
            # LOCK-S063-E-1 Phase 3 (future): flip BUS_COHERENCE_ENFORCE=1 to block.
            if coherence.get("coherence_violation") == "1" and _coherence_enforce_enabled():
                self._json(403, {"error": "coherence_violation", "message": coherence.get("coherence_reason", "")})
                return
            if not to_agent or not text:
                self._json(400, {"error": "Missing 'to' or 'text'"})
                return
            stream = _agent_stream(to_agent, project)
            channel = _agent_channel(to_agent, project)
            try:
                msg = sos_msg("chat", f"agent:{from_agent}", f"agent:{to_agent}", text, project)
            except Exception as exc:
                # Pydantic contract rejection (bad agent name etc.) must be a
                # 400, not an unhandled crash that drops the connection.
                self._json(400, {"error": "invalid_message", "message": str(exc)})
                return
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
            from_agent = body.get("from", "unknown").removeprefix("agent:")
            text = body.get("text", "")
            squad = body.get("squad")
            project = self._project(token, body.get("project"))
            # LOCK-S028-B-1 L-3 audit log + L-1 rate verdict (Phase 1+2; shadow).
            # LOCK-S063-E-1 Phase 2: coherence violation shadow-check.
            coherence_b = _coherence_check(from_agent, project, "/broadcast")
            _audit_emit(
                token, "/broadcast",
                claimed=from_agent,
                target=(f"squad:{squad}" if squad else "broadcast"),
                extra={**_rate_check(token, "/broadcast"), **coherence_b},
            )
            # LOCK-S028-B-1.1 Phase 3 hard gate.
            if not self._enforce_caller(token, from_agent):
                return
            # LOCK-S063-E-1 Phase 3 (future): flip BUS_COHERENCE_ENFORCE=1 to block.
            if coherence_b.get("coherence_violation") == "1" and _coherence_enforce_enabled():
                self._json(403, {"error": "coherence_violation", "message": coherence_b.get("coherence_reason", "")})
                return
            if not text:
                self._json(400, {"error": "Missing 'text'"})
                return
            if squad:
                channel = f"sos:channel:project:{project}:squad:{squad}" if project else f"sos:channel:squad:{squad}"
            else:
                channel = f"sos:channel:project:{project}:global" if project else "sos:channel:global"
            stream = f"{_prefix(project)}:{'squad:' + squad if squad else 'broadcast'}"
            try:
                msg = sos_msg("broadcast", f"agent:{from_agent}", channel, text, project)
            except Exception as exc:
                self._json(400, {"error": "invalid_message", "message": str(exc)})
                return
            mid = r.xadd(stream, msg)
            r.publish(channel, json.dumps(msg))
            self._json(200, {"status": "broadcast", "channel": channel, "stream_id": mid, "project": project})

        elif path == "/ask":
            agent = body.get("agent", "")
            message = body.get("message", "")
            # LOCK-S028-B-1 L-1 rate verdict + L-2 concurrency cap (Phase 4
            # prerequisite). Counter INCR happens BEFORE input validation so
            # adversarial empty-body floods still register inflight pressure.
            # Emit audit event with both rate and concurrency verdicts in
            # the same record.
            rate = _rate_check(token, "/ask")
            ask_verdict = _ask_acquire(token)
            extra = {**rate, **ask_verdict}
            _audit_emit(token, "/ask", claimed=None, target=agent, extra=extra)
            # Concurrency cap enforcement (Phase 4 flip). Pre-flip the
            # verdict is shadow-only; post-flip a `would_block_or_block`
            # verdict returns 429 BEFORE subprocess.run.
            if (
                ask_verdict.get("ask_concurrency_verdict") == "would_block_or_block"
                and _ask_enforce_enabled()
            ):
                # Release immediately on rejection — we never ran the
                # subprocess so the counter must reflect the rejection.
                _ask_release(token)
                self._json(
                    429,
                    {
                        "error": "ask_concurrency_exceeded",
                        "message": (
                            f"token has {ask_verdict.get('ask_count')} /ask "
                            f"requests in flight; cap is {ask_verdict.get('ask_cap')}"
                        ),
                        "retry_after_seconds": ASK_INFLIGHT_TTL_SEC,
                    },
                )
                return
            if not agent or not message:
                # Validation failure: release counter before bailing so the
                # try/finally invariant holds across all exit paths.
                _ask_release(token)
                self._json(400, {"error": "Missing 'agent' or 'message'"})
                return
            import subprocess
            # try/finally is the primary DECR path — guarantees release on
            # TimeoutExpired, Exception, and normal exit. EXPIRE 150s TTL
            # is defense-in-depth against bridge process crash (SIGKILL
            # only — try/finally covers all in-process exit paths).
            try:
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
            finally:
                _ask_release(token)

        elif path == "/heartbeat":
            agent = body.get("agent", "unknown")
            project = self._project(token, body.get("project"))
            # LOCK-S028-B-1 L-3 audit log (Phase 1; observation only).
            # LOCK-S063-E-1 Phase 2: coherence shadow-check before identity gate.
            coherence_h = _coherence_check(agent, project, "/heartbeat")
            _audit_emit(token, "/heartbeat", claimed=agent, target=agent, extra=coherence_h)
            # LOCK-S028-B-1.1 Phase 3 hard gate.
            if not self._enforce_caller(token, agent):
                return
            reg_key = _registry_key(agent, project)
            r.hset(reg_key, "last_seen", now_iso())
            r.expire(reg_key, 600)
            # LOCK-S063-E-1: refresh receipt TTL on heartbeat so active agents stay valid.
            _boot_receipt_issue(token, agent, project)
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

    def _handle_tenant_agent_mint(self, url_tenant_id: str) -> None:
        """S028 D-3b (C-1b) — POST /api/internal/tenants/:id/agents/mint.

        Auth: INTERNAL_API_SECRET env-var Bearer.
        Body: {tenant_id, tenant_slug, agent_name, model, role, charter,
               voice_rules, actor_token_hash}                  (tenant-admin path)
              OR {..., actor_type: "platform-admin"}            (platform-admin path)
        Returns 200 with {agent_name, qnft_seed_hex, token_hash, scaffold_path,
                          tier, signer, model, role,
                          idempotency: {qnft_minted, token_minted,
                                        routing_registered, scaffold_created}}.

        9 invariants enforced (L-1b..L-9b). Worker = opaque pipe contract: this
        site is the charter-injection-defense location (raw markdown to FS, no
        template substitution applied to tenant content). URL :id is cross-
        checked against body.tenant_id (defense-in-depth).
        """
        from sos.bus.tenant_agent_mint import (
            mint_tenant_custom_agent,
        )
        from sos.bus.tenant_provisioning import (
            authenticate_bearer,
            get_internal_secret,
            ProvisionError,
        )

        # 1. Substrate misconfiguration check — fail-closed BEFORE any work.
        if not get_internal_secret():
            self._json(503, {"error": "internal_secret_unconfigured"})
            return

        # 2. Bearer auth — constant-time compare.
        auth = self.headers.get("Authorization", "")
        if not authenticate_bearer(auth):
            self._json(401, {"error": "unauthorized"})
            return

        # 3. Body parse.
        try:
            body = self._body()
        except (json.JSONDecodeError, ValueError):
            self._json(422, {"error": "invalid_json_body"})
            return

        # 4. URL :id ↔ body.tenant_id consistency (defense-in-depth).
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

        # 5. Mint (validation + 4 idempotent substrate steps + 1 claim validator).
        try:
            result = mint_tenant_custom_agent(body)
            self._json(200, result)
        except ProvisionError as e:
            self._json(e.status, {"error": e.code, "message": e.message})

    def _handle_tenant_provision(self) -> None:
        """S027 D-1b — POST /api/internal/tenants/provision.

        Auth: INTERNAL_API_SECRET env-var Bearer (NOT tokens.json — separate s2s domain).
        Body: { tenant_id, slug, display_name, industry, charter? }
          charter (optional): per-agent boot_context charter STRING. When present and
          non-empty it is written to sos:onboarding:{slug}:{slug}-admin so the
          provisioned tenant admin self-orients on first boot_context call.
        Returns 200 with { mirror_key, bus_token, scaffold_path, charter_written, idempotency: {...} }.

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
    redis_url = os.environ.get("REDIS_URL", REDIS_URL)
    if redis_url:
        r = redis.Redis.from_url(redis_url, decode_responses=True)
    else:
        host = os.environ.get("REDIS_HOST", REDIS_HOST)
        port = int(os.environ.get("REDIS_PORT", str(REDIS_PORT)))
        r = redis.Redis(host=host, port=port, password=pw, decode_responses=True)

    # LOCK-S028-B-1 L-2 prerequisite: ThreadingHTTPServer required before
    # /ask concurrency cap can function. HTTPServer is single-threaded;
    # subprocess.run(..., timeout=120) blocks the entire server, so at
    # most one request is ever in-flight and the INCR/DECR cap has nothing
    # to measure. Athena P1 WARN 2026-05-05T03:00Z: this is a Phase 4
    # prerequisite. Redis INCR is atomic — thread-safety covered.
    server = ThreadingHTTPServer(("0.0.0.0", PORT), BusHandler)
    print(f"Bus bridge listening on :{PORT} (tokens from {TOKENS_PATH})")
    server.serve_forever()


if __name__ == "__main__":
    main()
