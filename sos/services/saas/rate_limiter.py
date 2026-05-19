"""Per-tenant rate limiting using sliding window counter."""
from __future__ import annotations

import time
import threading
from collections import defaultdict

# Limits per plan (requests per minute)
PLAN_LIMITS: dict[str | None, int] = {
    "starter": 100,
    "growth": 500,
    "scale": 2000,
    None: 60,  # system/unknown tokens
}


class RateLimiter:
    """Thread-safe sliding window rate limiter."""

    def __init__(self) -> None:
        self._windows: dict[str, list[float]] = defaultdict(list)
        self._lock = threading.Lock()

    def check(self, tenant: str, plan: str | None = None) -> tuple[bool, int]:
        """Check if request is allowed. Returns (allowed, remaining)."""
        limit = PLAN_LIMITS.get(plan, PLAN_LIMITS[None])
        now = time.time()
        window_start = now - 60  # 1-minute sliding window

        with self._lock:
            # Evict timestamps outside the current window
            self._windows[tenant] = [
                t for t in self._windows[tenant] if t > window_start
            ]

            current = len(self._windows[tenant])
            if current >= limit:
                return False, 0

            self._windows[tenant].append(now)
            return True, limit - current - 1

    def get_usage(self, tenant: str) -> int:
        """Get current request count in window."""
        now = time.time()
        window_start = now - 60
        with self._lock:
            self._windows[tenant] = [
                t for t in self._windows[tenant] if t > window_start
            ]
            return len(self._windows[tenant])


# Module-level singleton — one limiter for the whole process
_limiter = RateLimiter()


def check_rate_limit(tenant: str, plan: str | None = None) -> tuple[bool, int]:
    """Convenience wrapper around the singleton limiter."""
    return _limiter.check(tenant, plan)
