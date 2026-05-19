"""Agent kill-switch — operator stop-the-world for a named agent.

A single Redis key per agent: ``sos:agent:{name}:killed = "1"`` with a TTL.
Agents / auth middleware consult :func:`is_agent_killed` to decide whether
to refuse work. Writes are operator-initiated (via the dashboard API) and
are allowed to bubble errors up. Reads fail-soft — infra flake must never
cascade into a false-positive "kill" that blocks live agents.

Ship targets:
- :func:`is_agent_killed` — read; returns False on any Redis error.
- :func:`kill_agent`      — write + TTL; returns ISO timestamp of the
  ``killed_until`` moment (UTC).
- :func:`unkill_agent`    — delete; used by tests and manual recovery.

In v0.8.1 this module is consumed only by the dashboard API endpoint. The
auth middleware wiring (reject killed tokens at the gate) is deferred to
S6 or a later sprint.
"""

from __future__ import annotations

import logging
import os
from datetime import datetime, timedelta, timezone

logger = logging.getLogger("sos.kernel.kill_switch")

_DEFAULT_TTL_SECONDS = 24 * 60 * 60  # 24h


def _kill_key(name: str) -> str:
    return f"sos:agent:{name}:killed"


def _resolve_redis_url(redis_url: str | None) -> str:
    """Resolve a Redis URL. Prefer explicit arg, then env, then default."""
    if redis_url:
        return redis_url
    host = os.environ.get("REDIS_HOST", "127.0.0.1")
    port = os.environ.get("REDIS_PORT", "6379")
    password = os.environ.get("REDIS_PASSWORD", "")
    auth = f":{password}@" if password else ""
    return f"redis://{auth}{host}:{port}/0"


def _get_client(redis_url: str | None = None):  # type: ignore[no-untyped-def]
    """Build a fresh sync Redis client. Local import keeps the kernel optional."""
    import redis  # type: ignore[import-untyped]

    url = _resolve_redis_url(redis_url)
    return redis.Redis.from_url(
        url,
        decode_responses=True,
        socket_connect_timeout=2,
        socket_timeout=2,
    )


def is_agent_killed(name: str, *, redis_url: str | None = None) -> bool:
    """Return True if an operator has killed this agent.

    Fail-soft: any Redis error (connection refused, timeout, wrong password)
    returns ``False``. The auth path must never be blocked by infra flake;
    better to let a killed agent through one more request than to take the
    whole system offline on a DNS blip.
    """
    try:
        client = _get_client(redis_url)
        raw = client.get(_kill_key(name))
        return raw == "1"
    except Exception as exc:
        logger.debug("kill_switch read failed, returning False: %s", exc)
        return False


def kill_agent(
    name: str,
    *,
    ttl_seconds: int = _DEFAULT_TTL_SECONDS,
    redis_url: str | None = None,
) -> str:
    """Kill an agent for the next ``ttl_seconds`` (default 24h).

    Returns the ``killed_until`` timestamp as an ISO-8601 UTC string (``Z``
    suffix). Unlike :func:`is_agent_killed`, errors here propagate — kill is
    operator-initiated and the caller wants to know if the write failed.
    """
    if ttl_seconds <= 0:
        raise ValueError(f"ttl_seconds must be > 0, got {ttl_seconds}")
    client = _get_client(redis_url)
    client.set(_kill_key(name), "1", ex=ttl_seconds)
    killed_until = datetime.now(timezone.utc) + timedelta(seconds=ttl_seconds)
    return killed_until.strftime("%Y-%m-%dT%H:%M:%SZ")


def unkill_agent(name: str, *, redis_url: str | None = None) -> None:
    """Clear the kill marker for ``name``. Idempotent — no error if absent.

    Mainly used by tests and manual recovery. Errors propagate.
    """
    client = _get_client(redis_url)
    client.delete(_kill_key(name))


__all__ = ["is_agent_killed", "kill_agent", "unkill_agent"]
