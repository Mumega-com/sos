"""Tests for HeartbeatPruner (phase3/W3).

Covers the 6 acceptance cases from the brief:
1. Fresh card (age < 300s): unchanged after _tick.
2. Card aged 301s, stale=False: after _tick, card in Redis has stale=True,
   TTL reduced, pruner.staled_count == 1.
3. Card aged 301s, stale=True already: _tick is a no-op (don't re-write).
4. Card aged 901s: after _tick, the Redis key is gone, pruner.removed_count == 1.
5. Malformed last_seen (not ISO8601): skip gracefully, log warning, don't raise.
6. Empty keyspace: _tick returns cleanly, counters stay 0.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from unittest.mock import patch

import pytest

from sos.contracts.agent_card import AgentCard
from sos.services.registry.pruner import HeartbeatPruner

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_NOW = datetime(2026, 4, 19, 12, 0, 0, tzinfo=timezone.utc)


def _make_card_hash(
    name: str = "test-agent",
    age_seconds: int = 0,
    stale: bool = False,
    project: str | None = None,
) -> dict[str, str]:
    """Build a minimal Redis hash dict for a card."""
    last_seen = _NOW - timedelta(seconds=age_seconds)
    h: dict[str, str] = {
        "identity_id": f"agent:{name}",
        "name": name,
        "role": "executor",
        "tool": "service",
        "type": "service",
        "skills": "",
        "squads": "",
        "warm_policy": "cold",
        "cache_ttl_s": "300",
        "agent_card_version": "1.0.0",
        "registered_at": _NOW.isoformat(),
        "last_seen": last_seen.isoformat(),
        "stale": "true" if stale else "false",
    }
    if project:
        h["project"] = project
    return h


def _make_pruner(clock_offset_seconds: int = 0) -> HeartbeatPruner:
    """Return a pruner whose clock is pinned to _NOW."""
    now = _NOW
    return HeartbeatPruner(
        interval_seconds=60,
        stale_after=300,
        remove_after=900,
        clock=lambda: now,
    )


# ---------------------------------------------------------------------------
# Fake Redis
# ---------------------------------------------------------------------------


class FakeRedis:
    """Minimal Redis stub covering keys/hgetall/delete/hset/expire."""

    def __init__(self, initial: dict[str, dict[str, str]] | None = None) -> None:
        self._store: dict[str, dict[str, str]] = dict(initial or {})
        self._ttls: dict[str, int] = {}
        self.hset_calls: list[tuple[str, dict[str, str]]] = []
        self.expire_calls: list[tuple[str, int]] = []
        self.deleted_keys: list[str] = []

    def keys(self, pattern: str) -> list[str]:
        # Simple prefix match by stripping the trailing '*'
        prefix = pattern.rstrip("*")
        return [k for k in self._store if k.startswith(prefix)]

    def hgetall(self, key: str) -> dict[str, str]:
        return dict(self._store.get(key, {}))

    def hset(self, key: str, mapping: dict[str, str]) -> None:
        self._store[key] = dict(mapping)
        self.hset_calls.append((key, dict(mapping)))

    def expire(self, key: str, ttl: int) -> None:
        self._ttls[key] = ttl
        self.expire_calls.append((key, ttl))

    def delete(self, key: str) -> None:
        self._store.pop(key, None)
        self.deleted_keys.append(key)


# ---------------------------------------------------------------------------
# Case 1 — Fresh card: no-op
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_fresh_card_is_unchanged() -> None:
    key = "sos:cards:test-agent"
    initial_hash = _make_card_hash(age_seconds=100)
    fake_r = FakeRedis({key: initial_hash})

    pruner = _make_pruner()

    with patch("sos.services.registry.pruner._get_redis", return_value=fake_r):
        await pruner._tick()

    assert pruner.staled_count == 0
    assert pruner.removed_count == 0
    assert key not in fake_r.deleted_keys
    assert fake_r.hset_calls == []


# ---------------------------------------------------------------------------
# Case 2 — Card aged 301s, stale=False → gets stale=True, TTL reduced
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_aged_card_becomes_stale() -> None:
    key = "sos:cards:test-agent"
    initial_hash = _make_card_hash(age_seconds=301, stale=False)
    fake_r = FakeRedis({key: initial_hash})

    pruner = _make_pruner()

    with patch("sos.services.registry.pruner._get_redis", return_value=fake_r):
        with patch("sos.services.registry.pruner.write_card") as mock_write:
            await pruner._tick()

    assert pruner.staled_count == 1
    assert pruner.removed_count == 0
    assert key not in fake_r.deleted_keys

    # write_card must have been called with stale=True and reduced TTL
    assert mock_write.call_count == 1
    written_card: AgentCard = mock_write.call_args[0][0]
    assert written_card.stale is True

    kwargs = mock_write.call_args[1]
    ttl = kwargs.get(
        "ttl_seconds", mock_write.call_args[0][2] if len(mock_write.call_args[0]) > 2 else None
    )
    # remaining = 900 - 301 = 599
    assert ttl is not None
    assert 598 <= ttl <= 600


# ---------------------------------------------------------------------------
# Case 3 — Card aged 301s, stale=True already → no-op
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_already_stale_card_is_not_rewritten() -> None:
    key = "sos:cards:test-agent"
    initial_hash = _make_card_hash(age_seconds=301, stale=True)
    fake_r = FakeRedis({key: initial_hash})

    pruner = _make_pruner()

    with patch("sos.services.registry.pruner._get_redis", return_value=fake_r):
        with patch("sos.services.registry.pruner.write_card") as mock_write:
            await pruner._tick()

    assert pruner.staled_count == 0
    assert mock_write.call_count == 0


# ---------------------------------------------------------------------------
# Case 4 — Card aged 901s → key deleted
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_expired_card_is_deleted() -> None:
    key = "sos:cards:test-agent"
    initial_hash = _make_card_hash(age_seconds=901)
    fake_r = FakeRedis({key: initial_hash})

    pruner = _make_pruner()

    with patch("sos.services.registry.pruner._get_redis", return_value=fake_r):
        await pruner._tick()

    assert pruner.removed_count == 1
    assert pruner.staled_count == 0
    assert key in fake_r.deleted_keys


# ---------------------------------------------------------------------------
# Case 5 — Malformed last_seen → skip gracefully
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_malformed_last_seen_skips_gracefully() -> None:
    key = "sos:cards:bad-agent"
    bad_hash = _make_card_hash()
    bad_hash["last_seen"] = "not-a-timestamp"
    fake_r = FakeRedis({key: bad_hash})

    pruner = _make_pruner()

    with patch("sos.services.registry.pruner._get_redis", return_value=fake_r):
        # Should not raise
        await pruner._tick()

    assert pruner.staled_count == 0
    assert pruner.removed_count == 0
    assert key not in fake_r.deleted_keys


# ---------------------------------------------------------------------------
# Case 6 — Empty keyspace → counters stay 0
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_empty_keyspace_is_noop() -> None:
    fake_r = FakeRedis({})

    pruner = _make_pruner()

    with patch("sos.services.registry.pruner._get_redis", return_value=fake_r):
        await pruner._tick()

    assert pruner.staled_count == 0
    assert pruner.removed_count == 0
