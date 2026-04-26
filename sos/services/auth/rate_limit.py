"""Rate limiting — Sprint 011 OmniB.

Redis sliding window. Three-tuple key: (user_id|ip, tenant_id, endpoint).
Pre-auth: key on CF-Connecting-IP (never X-Forwarded-For).
Post-auth: key on session user_id.
"""
from __future__ import annotations

import logging
import os
import time
from typing import Any

log = logging.getLogger("sos.auth.rate_limit")

_DEFAULT_WINDOW = 60  # seconds
_DEFAULT_MAX_REQUESTS = 60  # per window


class RateLimitExceededError(RuntimeError):
    """Rate limit exceeded."""

    def __init__(self, message: str, *, retry_after: int = 0):
        super().__init__(message)
        self.retry_after = retry_after


def _get_redis():
    import redis
    pw = os.environ.get("REDIS_PASSWORD", "")
    return redis.Redis(host="localhost", port=6379, password=pw, decode_responses=True)


def check_rate_limit(
    identifier: str,
    tenant_id: str = "",
    endpoint: str = "",
    max_requests: int = _DEFAULT_MAX_REQUESTS,
    window_seconds: int = _DEFAULT_WINDOW,
) -> dict[str, Any]:
    """Check and increment rate limit counter.

    Returns {allowed: bool, remaining: int, reset_at: float}.
    Raises RateLimitExceededError if limit exceeded.
    """
    r = _get_redis()
    # Three-tuple key
    key = f"sos:ratelimit:{identifier}:{tenant_id}:{endpoint}"
    now = time.time()
    window_start = now - window_seconds

    pipe = r.pipeline()
    # Remove old entries outside window
    pipe.zremrangebyscore(key, "-inf", window_start)
    # Add current request
    pipe.zadd(key, {f"{now}:{os.urandom(4).hex()}": now})
    # Count requests in window
    pipe.zcard(key)
    # Set TTL on key
    pipe.expire(key, window_seconds * 2)
    results = pipe.execute()

    current_count = results[2]
    remaining = max(0, max_requests - current_count)

    if current_count > max_requests:
        retry_after = int(window_seconds - (now - window_start))
        raise RateLimitExceededError(
            f"Rate limit exceeded: {current_count}/{max_requests} in {window_seconds}s window",
            retry_after=max(1, retry_after),
        )

    return {
        "allowed": True,
        "remaining": remaining,
        "current": current_count,
        "limit": max_requests,
        "window": window_seconds,
    }


def get_client_ip(headers: dict[str, str]) -> str:
    """Extract real client IP. Uses CF-Connecting-IP (never X-Forwarded-For).

    CF-Connecting-IP is set by Cloudflare and cannot be spoofed by the client.
    X-Forwarded-For CAN be spoofed — never use for rate limiting.
    """
    # Cloudflare sets this authoritatively
    cf_ip = headers.get("cf-connecting-ip") or headers.get("CF-Connecting-IP")
    if cf_ip:
        return cf_ip.strip()

    # Fallback for non-CF environments (dev)
    return headers.get("x-real-ip", headers.get("remote-addr", "unknown"))
