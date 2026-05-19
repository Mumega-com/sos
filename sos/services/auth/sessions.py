"""Session management — Sprint 011 OmniA.

Redis-backed sessions with opaque session_id in HTTP-only signed cookie.
LOCK-1: Redis TTL is authoritative for invalidation. No JWT.

Session lifecycle:
  create_session(user_id, tenant_id, roles, mfa_verified) → session_id
  get_session(session_id) → SessionData | None
  refresh_session(session_id) → bool (slides TTL)
  rotate_session(old_session_id) → new_session_id (on privilege change)
  invalidate_session(session_id) → bool (logout)
  invalidate_all_sessions(user_id) → int (force logout all)
"""
from __future__ import annotations

import hashlib
import hmac
import json
import logging
import os
import secrets
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from typing import Any

import redis

log = logging.getLogger("sos.auth.sessions")

_SESSION_TTL = int(os.environ.get("SESSION_TTL_SECONDS", "86400"))  # 24h default
_SESSION_PREFIX = "sos:session:"
_USER_SESSIONS_PREFIX = "sos:user_sessions:"
_COOKIE_SECRET = os.environ.get("SESSION_COOKIE_SECRET", "")


# ---------------------------------------------------------------------------
# Data
# ---------------------------------------------------------------------------


@dataclass
class SessionData:
    user_id: str
    tenant_id: str
    roles: list[str] = field(default_factory=list)
    mfa_verified: bool = False
    created_at: str = ""
    last_activity: str = ""
    ip_address: str = ""

    def to_json(self) -> str:
        return json.dumps(asdict(self))

    @classmethod
    def from_json(cls, raw: str) -> "SessionData":
        return cls(**json.loads(raw))


# ---------------------------------------------------------------------------
# Cookie signing (HMAC-SHA256)
# ---------------------------------------------------------------------------


def sign_session_id(session_id: str) -> str:
    """Sign session_id with HMAC-SHA256 for HTTP-only cookie."""
    secret = _COOKIE_SECRET or os.environ.get("SESSION_COOKIE_SECRET", "")
    if not secret:
        raise RuntimeError("SESSION_COOKIE_SECRET not set")
    sig = hmac.new(secret.encode(), session_id.encode(), hashlib.sha256).hexdigest()[:16]
    return f"{session_id}.{sig}"


def verify_signed_session_id(signed: str) -> str | None:
    """Verify and extract session_id from signed cookie value."""
    if "." not in signed:
        return None
    session_id, sig = signed.rsplit(".", 1)
    expected = sign_session_id(session_id)
    if hmac.compare_digest(signed, expected):
        return session_id
    return None


# ---------------------------------------------------------------------------
# Session CRUD (Redis-backed)
# ---------------------------------------------------------------------------


def _get_redis() -> redis.Redis:
    pw = os.environ.get("REDIS_PASSWORD", "")
    return redis.Redis(host="localhost", port=6379, password=pw, decode_responses=True)


def create_session(
    user_id: str,
    tenant_id: str,
    roles: list[str] | None = None,
    mfa_verified: bool = False,
    ip_address: str = "",
) -> str:
    """Create a new session. Returns session_id."""
    r = _get_redis()
    session_id = secrets.token_urlsafe(32)  # ≥128 bits CSPRNG
    now = datetime.now(timezone.utc).isoformat()

    data = SessionData(
        user_id=user_id,
        tenant_id=tenant_id,
        roles=roles or [],
        mfa_verified=mfa_verified,
        created_at=now,
        last_activity=now,
        ip_address=ip_address,
    )

    key = f"{_SESSION_PREFIX}{session_id}"
    r.setex(key, _SESSION_TTL, data.to_json())

    # Track user's active sessions (for invalidate_all)
    user_key = f"{_USER_SESSIONS_PREFIX}{user_id}"
    r.sadd(user_key, session_id)
    r.expire(user_key, _SESSION_TTL * 2)  # keep slightly longer than sessions

    log.info("session created: user=%s tenant=%s mfa=%s", user_id, tenant_id, mfa_verified)
    return session_id


def get_session(session_id: str) -> SessionData | None:
    """Fetch session data from Redis. Returns None if expired/missing."""
    r = _get_redis()
    key = f"{_SESSION_PREFIX}{session_id}"
    raw = r.get(key)
    if not raw:
        return None
    return SessionData.from_json(raw)


def refresh_session(session_id: str) -> bool:
    """Slide session TTL on activity. Returns True if session exists."""
    r = _get_redis()
    key = f"{_SESSION_PREFIX}{session_id}"
    if not r.exists(key):
        return False
    r.expire(key, _SESSION_TTL)
    # Update last_activity
    raw = r.get(key)
    if raw:
        data = SessionData.from_json(raw)
        data.last_activity = datetime.now(timezone.utc).isoformat()
        r.setex(key, _SESSION_TTL, data.to_json())
    return True


def rotate_session(old_session_id: str) -> str | None:
    """Rotate session on privilege change. Returns new session_id or None."""
    data = get_session(old_session_id)
    if not data:
        return None
    invalidate_session(old_session_id)
    return create_session(
        user_id=data.user_id,
        tenant_id=data.tenant_id,
        roles=data.roles,
        mfa_verified=data.mfa_verified,
        ip_address=data.ip_address,
    )


def invalidate_session(session_id: str) -> bool:
    """Invalidate (logout) a specific session."""
    r = _get_redis()
    key = f"{_SESSION_PREFIX}{session_id}"
    raw = r.get(key)
    if raw:
        data = SessionData.from_json(raw)
        user_key = f"{_USER_SESSIONS_PREFIX}{data.user_id}"
        r.srem(user_key, session_id)
    deleted = r.delete(key)
    return deleted > 0


def invalidate_all_sessions(user_id: str) -> int:
    """Invalidate all sessions for a user (force logout all devices)."""
    r = _get_redis()
    user_key = f"{_USER_SESSIONS_PREFIX}{user_id}"
    session_ids = r.smembers(user_key)
    count = 0
    for sid in session_ids:
        if r.delete(f"{_SESSION_PREFIX}{sid}"):
            count += 1
    r.delete(user_key)
    log.info("invalidated %d sessions for user=%s", count, user_id)
    return count
