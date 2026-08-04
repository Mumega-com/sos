"""#594 — wake-daemon must actually defer when tmux is busy (Cursor mid-run).

The log line said "queuing" but returned False with no Redis write and no retry.
These tests lock the property: busy → deferred entry with a future score; due
entries are re-attempted; aged-out / over-attempted entries are dropped.
"""

from __future__ import annotations

import json
import time
from typing import Any

import pytest

from sos.services.bus import delivery


class FakeRedis:
    def __init__(self) -> None:
        self.zset: dict[str, float] = {}
        self.hashes: dict[str, dict[str, str]] = {}

    def zadd(self, key: str, mapping: dict[str, float]) -> int:
        assert key == delivery.DEFERRED_WAKE_ZSET
        self.zset.update(mapping)
        return len(mapping)

    def zrangebyscore(
        self, key: str, min_score: Any, max_score: Any, start: int = 0, num: int = 100
    ) -> list[str]:
        assert key == delivery.DEFERRED_WAKE_ZSET
        lo = float("-inf") if min_score == "-inf" else float(min_score)
        hi = float("inf") if max_score == "+inf" else float(max_score)
        due = sorted(
            (
                (member, score)
                for member, score in self.zset.items()
                if lo <= score <= hi
            ),
            key=lambda item: item[1],
        )
        return [member for member, _ in due[start : start + num]]

    def zrem(self, key: str, *members: str) -> int:
        assert key == delivery.DEFERRED_WAKE_ZSET
        removed = 0
        for member in members:
            if member in self.zset:
                del self.zset[member]
                removed += 1
        return removed

    def zscore(self, key: str, member: str) -> float | None:
        assert key == delivery.DEFERRED_WAKE_ZSET
        return self.zset.get(member)


def test_cursor_busy_chrome_forces_busy_even_if_gt_in_scrollback() -> None:
    pane = "some old > line\n Running… 12.3k tokens · ctrl+c to stop"
    assert delivery.pane_looks_busy(pane) is True
    assert delivery.pane_at_prompt(pane) is False


def test_claude_prompt_is_idle() -> None:
    pane = "previous output\n❯ "
    assert delivery.pane_looks_busy(pane) is False
    assert delivery.pane_at_prompt(pane) is True


def test_enqueue_schedules_future_score() -> None:
    r: Any = FakeRedis()
    now = 1_700_000_000.0
    member = delivery.enqueue_deferred_wake(
        r,
        agent="athena",
        message="[agent:kasra] hello",
        now=now,
        attempts=0,
        enqueued_at=None,
    )
    assert member in r.zset
    assert r.zset[member] == pytest.approx(now + delivery.busy_retry_delay_seconds(0))
    payload = json.loads(member)
    assert payload["agent"] == "athena"
    assert payload["attempts"] == 0


def test_process_deferred_retries_due_and_requeues_on_busy() -> None:
    r: Any = FakeRedis()
    now = 1_700_000_000.0
    member = delivery.enqueue_deferred_wake(
        r,
        agent="athena",
        message="[agent:kasra] hello",
        now=now - 1,
        attempts=0,
        enqueued_at=now - 1,
    )
    # Force score into the past so it is due.
    r.zset[member] = now - 1

    calls: list[tuple[str, str]] = []

    def fake_wake(agent: str, message: str, redis_client: Any) -> str:
        calls.append((agent, message))
        return "busy"

    delivered = delivery.process_deferred_wakes(r, now=now, wake_fn=fake_wake)
    assert delivered == 0
    assert calls == [("athena", "[agent:kasra] hello")]
    # Original member removed; new member with attempts=1 present.
    assert member not in r.zset
    assert len(r.zset) == 1
    remaining = next(iter(r.zset))
    payload = json.loads(remaining)
    assert payload["attempts"] == 1
    assert r.zset[remaining] == pytest.approx(now + delivery.busy_retry_delay_seconds(1))


def test_process_deferred_drops_after_max_attempts() -> None:
    r: Any = FakeRedis()
    now = 1_700_000_000.0
    member = delivery.enqueue_deferred_wake(
        r,
        agent="athena",
        message="[agent:kasra] hello",
        now=now - 1,
        attempts=delivery.DEFERRED_WAKE_MAX_ATTEMPTS,
        enqueued_at=now - 1,
    )
    r.zset[member] = now - 1

    delivered = delivery.process_deferred_wakes(
        r, now=now, wake_fn=lambda *a, **k: "busy"
    )
    assert delivered == 0
    assert r.zset == {}


def test_process_deferred_success_removes_entry() -> None:
    r: Any = FakeRedis()
    now = 1_700_000_000.0
    member = delivery.enqueue_deferred_wake(
        r,
        agent="athena",
        message="[agent:kasra] hello",
        now=now - 1,
        attempts=2,
        enqueued_at=now - 1,
    )
    r.zset[member] = now - 1
    delivered = delivery.process_deferred_wakes(
        r, now=now, wake_fn=lambda *a, **k: "sent"
    )
    assert delivered == 1
    assert r.zset == {}
