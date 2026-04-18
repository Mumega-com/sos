"""Canonical auth module for SOS services.

Exposes a single ``verify_bearer(authorization)`` function that all services
should use to verify Bearer tokens.  The function returns an ``AuthContext``
(a Pydantic model) on success, or ``None`` for invalid / missing tokens.

Verification order
------------------
1. Env-var system tokens (``SOS_SYSTEM_TOKEN``, ``MIRROR_TOKEN``, …) checked
   first — no file I/O needed.
2. tokens.json lookup via sha-256 hash, raw-token equality, or bcrypt.
3. In-process cache (30-second TTL) prevents redundant file reads within a
   single request/event loop iteration.

DO NOT add service-specific logic here.  Keep it generic.
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
import time
from enum import Enum
from pathlib import Path
from typing import Any

from pydantic import BaseModel

try:
    import bcrypt as _bcrypt  # type: ignore[import-untyped]

    _HAS_BCRYPT = True
except ImportError:  # pragma: no cover
    _HAS_BCRYPT = False

logger = logging.getLogger("sos.auth")

# ---------------------------------------------------------------------------
# Paths & constants
# ---------------------------------------------------------------------------

TOKENS_PATH = Path(__file__).resolve().parent.parent / "bus" / "tokens.json"

# Env vars whose values count as "system-level" tokens when matched.
# Tuple of (env_var_name, is_admin_scope).
_ENV_TOKENS: tuple[tuple[str, bool], ...] = (
    ("SOS_SYSTEM_TOKEN", True),
    ("MIRROR_TOKEN", True),
    ("BUS_BRIDGE_TOKEN", True),
    ("CYRUS_BUS_TOKEN", False),
)

# Admin agents (token-file agents that always get is_admin=True).
_ADMIN_AGENTS: frozenset[str] = frozenset({"kasra", "mumega"})

_CACHE_TTL: float = 30.0  # seconds


# ---------------------------------------------------------------------------
# Public models
# ---------------------------------------------------------------------------


class AuthContext(BaseModel):
    """Describes a successfully authenticated caller."""

    agent: str | None = None
    project: str | None = None
    tenant_slug: str | None = None
    is_system: bool = False
    is_admin: bool = False
    label: str = ""
    raw_token_hash: str | None = None
    env_source: str | None = None  # which env var matched, if any


class AuthResult(Enum):
    """High-level outcome of an auth check."""

    Accepted = "accepted"
    Rejected = "rejected"
    Unknown = "unknown"


# ---------------------------------------------------------------------------
# In-process token cache
# ---------------------------------------------------------------------------


class _Cache:
    """Tiny thread-unsafe TTL cache for the token list."""

    def __init__(self) -> None:
        self._tokens: list[dict[str, Any]] = []
        self._loaded_at: float = 0.0
        self._mtime: float = 0.0

    def _is_stale(self) -> bool:
        now = time.monotonic()
        if now - self._loaded_at > _CACHE_TTL:
            return True
        # Also invalidate if the file has been modified on disk.
        try:
            mtime = TOKENS_PATH.stat().st_mtime
            if mtime != self._mtime:
                return True
        except OSError:
            pass
        return False

    def get_tokens(self) -> list[dict[str, Any]]:
        if self._is_stale():
            self._reload()
        return self._tokens

    def _reload(self) -> None:
        try:
            raw = TOKENS_PATH.read_text(encoding="utf-8")
            self._tokens = json.loads(raw)
            try:
                self._mtime = TOKENS_PATH.stat().st_mtime
            except OSError:
                self._mtime = 0.0
        except FileNotFoundError:
            logger.debug("tokens.json not found at %s", TOKENS_PATH)
            self._tokens = []
        except Exception:
            logger.exception("Failed to load tokens.json")
            self._tokens = []
        self._loaded_at = time.monotonic()

    def invalidate(self) -> None:
        """Force a reload on the next access (used in tests)."""
        self._loaded_at = 0.0


_cache = _Cache()


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _sha256(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _check_env_tokens(raw_token: str) -> AuthContext | None:
    """Return an AuthContext if *raw_token* matches any configured env-var token."""
    for env_var, is_admin in _ENV_TOKENS:
        env_val = os.environ.get(env_var, "")
        if env_val and env_val == raw_token:
            return AuthContext(
                agent=None,
                project=None,
                tenant_slug=None,
                is_system=True,
                is_admin=is_admin,
                label=f"env:{env_var}",
                raw_token_hash=_sha256(raw_token),
                env_source=env_var,
            )
    return None


def _check_tokens_json(raw_token: str) -> AuthContext | None:
    """Return an AuthContext if *raw_token* matches an active entry in tokens.json."""
    sha_hash = _sha256(raw_token)
    token_bytes = raw_token.encode("utf-8")

    for entry in _cache.get_tokens():
        if not entry.get("active", True):
            continue

        # 1. Raw-token equality (legacy / unmigrated entries).
        stored_raw = entry.get("token") or ""
        if stored_raw and stored_raw == raw_token:
            return _entry_to_ctx(entry, sha_hash)

        # 2. SHA-256 token_hash (post-SEC-001 standard).
        token_hash = entry.get("token_hash") or ""
        if token_hash and token_hash == sha_hash:
            return _entry_to_ctx(entry, sha_hash)

        # 3. Bcrypt hash (older rotation scheme).
        bcrypt_hash = entry.get("hash") or ""
        if bcrypt_hash and _HAS_BCRYPT and bcrypt_hash.startswith(("$2a$", "$2b$", "$2y$")):
            try:
                if _bcrypt.checkpw(token_bytes, bcrypt_hash.encode("utf-8")):
                    return _entry_to_ctx(entry, sha_hash)
            except (ValueError, Exception):
                continue

    return None


def _entry_to_ctx(entry: dict[str, Any], raw_token_hash: str) -> AuthContext:
    agent: str | None = entry.get("agent") or None
    project: str | None = entry.get("project") or None
    tenant_slug: str | None = entry.get("tenant_slug") or project or None
    label: str = entry.get("label") or ""
    is_admin = bool(agent and agent in _ADMIN_AGENTS) or bool(entry.get("is_admin"))
    is_system = bool(entry.get("is_system"))
    return AuthContext(
        agent=agent,
        project=project,
        tenant_slug=tenant_slug,
        is_system=is_system,
        is_admin=is_admin,
        label=label,
        raw_token_hash=raw_token_hash,
        env_source=None,
    )


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def verify_bearer(authorization: str | None) -> AuthContext | None:
    """Verify a Bearer token and return an :class:`AuthContext`, or ``None``.

    Args:
        authorization: The raw ``Authorization`` header value, e.g.
            ``"Bearer sk-sos-abcdef"``.  Pass ``None`` or an empty string to
            get ``None`` back (missing auth).

    Returns:
        :class:`AuthContext` on success, ``None`` on failure.
    """
    if not authorization:
        return None

    parts = authorization.split(None, 1)
    if len(parts) != 2 or parts[0].lower() != "bearer":
        return None

    raw_token = parts[1].strip()
    if not raw_token:
        return None

    # 1. Env-var fast path — no file I/O.
    ctx = _check_env_tokens(raw_token)
    if ctx is not None:
        return ctx

    # 2. tokens.json lookup (cached).
    return _check_tokens_json(raw_token)


def get_cache() -> _Cache:
    """Expose the internal cache for testing purposes."""
    return _cache
