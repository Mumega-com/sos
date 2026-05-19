"""Per-tenant rate limiting — Redis-backed, per-minute sliding window."""
from __future__ import annotations

import os
import time
from dataclasses import dataclass
from typing import Optional

import redis


PLAN_LIMITS_RPM: dict[str, int] = {
    "starter": 10,
    "growth": 100,
    "scale": 1000,
    "enterprise": 10_000,  # effectively unlimited for most traffic
}


@dataclass
class RateLimitDecision:
    allowed: bool
    remaining: int
    retry_after_s: int


def _redis() -> redis.Redis:  # type: ignore[type-arg]
    password = os.environ.get("REDIS_PASSWORD", "")
    host = os.environ.get("REDIS_HOST", "127.0.0.1")
    port = int(os.environ.get("REDIS_PORT", "6379"))
    return redis.Redis(
        host=host, port=port, password=password, decode_responses=True,
        socket_connect_timeout=1.0, socket_timeout=1.0,
    )


def check_rate_limit(tenant_id: Optional[str], plan: Optional[str]) -> RateLimitDecision:
    """Check and increment per-tenant request counter. Fail-open on Redis errors."""
    if not tenant_id:
        # Admin/internal agents — no rate limit
        return RateLimitDecision(allowed=True, remaining=-1, retry_after_s=0)

    limit = PLAN_LIMITS_RPM.get(plan or "starter", PLAN_LIMITS_RPM["starter"])
    minute = int(time.time()) // 60
    key = f"sos:ratelimit:{tenant_id}:{minute}"

    try:
        r = _redis()
        pipe = r.pipeline()
        pipe.incr(key)
        pipe.expire(key, 120)  # 2 min TTL keeps a small rolling history
        count, _ = pipe.execute()
        count = int(count)
    except Exception:
        # Fail open — don't block traffic on Redis issues
        return RateLimitDecision(allowed=True, remaining=-1, retry_after_s=0)

    if count > limit:
        # Compute seconds until this minute ends
        retry_after = 60 - (int(time.time()) % 60)
        return RateLimitDecision(allowed=False, remaining=0, retry_after_s=retry_after)

    return RateLimitDecision(allowed=True, remaining=limit - count, retry_after_s=0)
