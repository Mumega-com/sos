"""Token validation — reads sos/bus/tokens.json, matches sha256 hash."""
from __future__ import annotations

import hashlib
import json
import time
from pathlib import Path
from typing import Optional

from pydantic import BaseModel, Field


TOKENS_PATH = Path(__file__).resolve().parent.parent.parent / "bus" / "tokens.json"


class AuthContext(BaseModel):
    tenant_id: Optional[str] = None
    agent: str
    scope: str = "agent"  # agent | customer | admin
    plan: Optional[str] = None  # starter | growth | scale | enterprise | None
    role: str = "admin"


class _TokenCache:
    def __init__(self, ttl_s: float = 10.0) -> None:
        self._cache: dict[str, AuthContext] = {}
        self._loaded_at: float = 0.0
        self._mtime: float = 0.0
        self.ttl_s = ttl_s

    def _should_reload(self) -> bool:
        now = time.monotonic()
        if now - self._loaded_at < self.ttl_s:
            return False
        try:
            current_mtime = TOKENS_PATH.stat().st_mtime
            if current_mtime != self._mtime:
                self._mtime = current_mtime
                return True
        except OSError:
            return False
        self._loaded_at = now
        return False

    def _reload(self) -> None:
        cache: dict[str, AuthContext] = {}
        try:
            entries = json.loads(TOKENS_PATH.read_text())
            for entry in entries:
                if not entry.get("active"):
                    continue
                # Prefer stored token_hash; bcrypt hashes are also supported
                # but are per-entry so they can't be used as a dict key. For
                # bcrypt, the caller must fall through to a linear scan.
                sha_hash = entry.get("token_hash", "")
                if sha_hash and not sha_hash.startswith(("$2a$", "$2b$", "$2y$")):
                    cache[sha_hash] = AuthContext(
                        tenant_id=entry.get("project") or None,
                        agent=entry.get("agent", "") or "",
                        scope=entry.get("scope") or "agent",
                        plan=entry.get("plan") or None,
                        role=entry.get("role", "admin"),
                    )
        except Exception:  # pragma: no cover — file should always be readable
            pass
        self._cache = cache
        self._loaded_at = time.monotonic()

    def lookup(self, token: str) -> Optional[AuthContext]:
        if self._should_reload():
            self._reload()
        sha_hash = hashlib.sha256(token.encode("utf-8")).hexdigest()
        return self._cache.get(sha_hash)


_token_cache = _TokenCache()


def resolve_token(token: str) -> Optional[AuthContext]:
    """Validate a raw token and return AuthContext, or None if invalid."""
    if not token:
        return None
    return _token_cache.lookup(token)


def identity_headers(ctx: AuthContext) -> dict[str, str]:
    """Produce X-SOS-* headers for the upstream request."""
    return {
        "X-SOS-Identity": f"agent:{ctx.agent}",
        "X-SOS-Tenant-Id": ctx.tenant_id or "",
        "X-SOS-Scope": ctx.scope,
        "X-SOS-Plan": ctx.plan or "",
        "X-SOS-Role": ctx.role,
        "X-SOS-Source": "dispatcher-py",
    }
