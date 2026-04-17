"""Shared /health helper — all SOS services import this for a consistent response shape.

Canonical shape:
{
    "status": "ok" | "degraded" | "down",
    "service": "<name>",
    "version": "<sos version>",
    "uptime_seconds": <float>,
    "dependencies": [
        {"name": "redis", "status": "ok" | "down"},
        ...
    ]
}
"""
from __future__ import annotations

import time
from typing import Any


def health_response(
    service_name: str,
    start_time: float,
    deps: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Return a canonical /health dict.

    Args:
        service_name: Human-readable service identifier (e.g. ``"dashboard"``).
        start_time: ``time.time()`` captured at process start (module-level constant).
        deps: Optional list of dependency status dicts, each with ``name`` and
              ``status`` keys.  Defaults to ``[]``.

    Returns:
        Dict ready to be serialised as JSON.  ``status`` is ``"degraded"`` when
        any dependency reports ``"down"``, otherwise ``"ok"``.
    """
    resolved_deps: list[dict[str, Any]] = deps or []
    any_down = any(d.get("status") == "down" for d in resolved_deps)
    overall = "degraded" if any_down else "ok"

    import sos  # local import keeps module load-order safe

    return {
        "status": overall,
        "service": service_name,
        "version": sos.__version__,
        "uptime_seconds": time.time() - start_time,
        "dependencies": resolved_deps,
    }


async def _ping_redis(url: str, timeout: float = 0.5) -> bool:
    """Return True if Redis is reachable within *timeout* seconds."""
    try:
        import redis.asyncio as aioredis  # type: ignore[import]

        r = aioredis.from_url(url, socket_connect_timeout=timeout, decode_responses=True)
        await r.ping()
        await r.aclose()
        return True
    except Exception:
        return False


async def _ping_http(url: str, timeout: float = 0.5) -> bool:
    """Return True if the HTTP endpoint returns a 2xx status within *timeout* seconds."""
    try:
        import httpx

        async with httpx.AsyncClient(timeout=timeout) as client:
            r = await client.get(url)
            return r.is_success
    except Exception:
        return False


async def redis_dep(url: str) -> dict[str, str]:
    """Return a dependency dict for Redis."""
    ok = await _ping_redis(url)
    return {"name": "redis", "status": "ok" if ok else "down"}


async def mirror_dep(url: str) -> dict[str, str]:
    """Return a dependency dict for Mirror."""
    ok = await _ping_http(f"{url.rstrip('/')}/health")
    return {"name": "mirror", "status": "ok" if ok else "down"}
