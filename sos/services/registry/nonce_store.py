"""Single-use nonce store for /mesh/challenge → /mesh/enroll anti-replay.

Redis-backed: keys live at ``sos:mesh:nonce:{agent_id}:{nonce}`` with a
60-second TTL. `consume` uses Redis ``GETDEL`` so a nonce is valid exactly
once — a replay attempt finds nothing.

No in-process fallback: if Redis is down, mesh enroll fails closed. The
registry already depends on Redis for cards + identities.
"""

from __future__ import annotations

import logging
import secrets
import time
from typing import Optional

log = logging.getLogger("sos.registry.nonce_store")

NONCE_TTL_SECONDS = 60
_KEY_PREFIX = "sos:mesh:nonce:"


def _get_redis():  # pragma: no cover - trivial
    import redis  # type: ignore[import-untyped]
    from sos.kernel.settings import get_settings

    s = get_settings().redis
    return redis.Redis(
        host=s.host,
        port=s.port,
        password=s.password_str or None,
        decode_responses=True,
        socket_connect_timeout=2,
        socket_timeout=2,
    )


def _key(agent_id: str, nonce: str) -> str:
    return f"{_KEY_PREFIX}{agent_id}:{nonce}"


def issue(agent_id: str, ttl_s: int = NONCE_TTL_SECONDS) -> tuple[str, int]:
    """Return ``(nonce, expires_at_unix)``.

    Nonce is 24 bytes of secrets.token_urlsafe. Stored in Redis with the
    given TTL. The value "1" is a sentinel — GETDEL on consume just needs
    existence, not content.
    """
    nonce = secrets.token_urlsafe(24)
    expires_at = int(time.time()) + ttl_s
    try:
        r = _get_redis()
        r.set(_key(agent_id, nonce), "1", ex=ttl_s)
    except Exception as exc:
        log.warning("nonce_store.issue redis failure: %s", exc)
        raise
    return nonce, expires_at


def consume(agent_id: str, nonce: str) -> bool:
    """Return True iff the nonce was live and was just deleted.

    Uses ``GETDEL`` so the operation is atomic — two concurrent enrolls
    racing on the same nonce will see exactly one success.
    """
    if not nonce:
        return False
    try:
        r = _get_redis()
        val: Optional[str] = r.getdel(_key(agent_id, nonce))
        return val is not None
    except Exception as exc:
        log.warning("nonce_store.consume redis failure: %s", exc)
        return False
