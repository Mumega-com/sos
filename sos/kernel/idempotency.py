"""Canonical idempotency helper for write endpoints.

Redis-backed replay cache with tenant-scoped keys. Usage::

    @app.post("/signup")
    async def signup(req: SignupReq, idempotency_key: Optional[str] = Header(None)):
        async def _do():
            return await _real_signup(req)
        return await with_idempotency(
            key=idempotency_key,
            tenant=req.tenant_slug,
            request_body=req.model_dump(),
            fn=_do,
        )

Semantics:

- **Missing key** (``key is None``): helper never forces the header — call
  ``fn`` and return its result unchanged.
- **Miss**: call ``fn``, store ``{request_fingerprint, response_status,
  response_body, stored_at}`` under ``sos:idem:<tenant>:<key>`` with TTL,
  return ``fn``'s result.
- **Hit, same fingerprint**: return stored response verbatim (status + body)
  without calling ``fn``.
- **Hit, different fingerprint**: raise ``HTTPException(409)`` —
  "idempotency key reuse with different payload".

Keys are tenant-scoped to prevent cross-tenant collisions. When no tenant
is supplied (system/admin paths) the namespace falls back to ``_system``.

The Redis client is injectable via the ``redis`` kwarg (tests pass a
fakeredis instance). When not supplied, the helper builds a client from
``SOS_REDIS_URL`` / ``REDIS_URL`` + ``REDIS_PASSWORD`` — matching the
convention used elsewhere in ``sos.kernel`` (see ``audit.py``).
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
from datetime import datetime, timezone
from typing import Any, Awaitable, Callable, Optional

logger = logging.getLogger("sos.kernel.idempotency")

_DEFAULT_TTL_S = 86400  # 24h


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _canonical_json(body: Any) -> str:
    """Stable JSON representation for fingerprinting.

    ``sort_keys=True`` so dict ordering doesn't change the hash;
    ``default=str`` so non-JSON-native types (datetime, UUID, Decimal)
    stringify deterministically rather than raising.
    """
    return json.dumps(body, sort_keys=True, default=str)


def _fingerprint(body: Any) -> str:
    return hashlib.sha256(_canonical_json(body).encode("utf-8")).hexdigest()


def _idem_key(tenant: Optional[str], key: str) -> str:
    ns = tenant if tenant else "_system"
    return f"sos:idem:{ns}:{key}"


async def _default_redis():
    """Build a Redis client from environment — matches sos.kernel.audit convention."""
    import redis.asyncio as aioredis  # lazy import; tests pass their own client
    from sos.kernel.settings import get_settings as _get_settings

    _s = _get_settings()
    # Historical lookup order: SOS_REDIS_URL → REDIS_URL → built-from-password.
    redis_url = _s.redis.legacy_sos_url or _s.redis.resolved_url
    return aioredis.from_url(redis_url, decode_responses=True)


async def with_idempotency(
    *,
    key: Optional[str],
    tenant: Optional[str],
    request_body: Any,
    fn: Callable[[], Awaitable[Any]],
    ttl_s: int = _DEFAULT_TTL_S,
    redis: Any = None,
) -> Any:
    """Wrap ``fn`` with a Redis-backed idempotency cache.

    Parameters
    ----------
    key:
        Raw ``Idempotency-Key`` header value. If ``None``, ``fn`` is called
        directly — this helper never forces the header onto callers.
    tenant:
        Tenant slug. ``None`` maps to the ``_system`` namespace.
    request_body:
        JSON-serialisable request payload. Used to compute the fingerprint
        that detects key reuse with a different body.
    fn:
        The actual handler body. Must return a JSON-serialisable value; on
        replay we store it verbatim and return it unchanged.
    ttl_s:
        Cache lifetime in seconds. Defaults to 24h.
    redis:
        Optional injected redis client. Tests pass ``fakeredis``. When
        omitted, a client is built from env.

    Raises
    ------
    HTTPException
        ``409 Conflict`` when the key has been seen before with a
        different request fingerprint.
    """
    if key is None:
        return await fn()

    owns_client = redis is None
    client = redis if redis is not None else await _default_redis()

    try:
        full_key = _idem_key(tenant, key)
        fingerprint = _fingerprint(request_body)

        raw = await client.get(full_key)
        if raw is not None:
            try:
                record = json.loads(raw)
            except (ValueError, TypeError):
                logger.warning("corrupt idempotency record at %s, overwriting", full_key)
                record = None

            if record is not None:
                if record.get("request_fingerprint") != fingerprint:
                    # Lazy import so the helper has no hard FastAPI dependency
                    # at module import time — keeps kernel lean.
                    from fastapi import HTTPException

                    raise HTTPException(
                        status_code=409,
                        detail="idempotency key reuse with different payload",
                    )
                return record.get("response_body")

        result = await fn()

        record = {
            "request_fingerprint": fingerprint,
            "response_status": 200,
            "response_body": result,
            "stored_at": _now_iso(),
        }
        try:
            await client.set(full_key, json.dumps(record, default=str), ex=ttl_s)
        except Exception as exc:  # pragma: no cover — best-effort storage
            logger.warning("idempotency store failed for %s: %s", full_key, exc)

        return result
    finally:
        if owns_client:
            try:
                await client.aclose()
            except Exception:  # pragma: no cover
                pass


__all__ = ["with_idempotency"]
