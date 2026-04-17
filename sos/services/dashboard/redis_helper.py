"""Thin Redis connection factory for the dashboard service."""
from __future__ import annotations

import redis  # type: ignore[import-untyped]

from .config import REDIS_PASSWORD


def _get_redis() -> redis.Redis:  # type: ignore[type-arg]
    return redis.Redis(
        host="localhost",
        port=6379,
        password=REDIS_PASSWORD,
        decode_responses=True,
    )
