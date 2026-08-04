"""#594 — wake-daemon must actually defer when tmux is busy (Cursor mid-run).

Also locks Kasra gate properties from sos#211 BLOCK: idle panes that already
received a bus wake must classify idle; busy markers must not match
WORKING DIR / idle token chrome; busy chrome is last-line only.
"""

from __future__ import annotations

import json
from typing import Any

import pytest

from sos.services.bus import delivery


class FakeRedis:
    def __init__(self) -> None:
        self.zset: dict[str, float] = {}

    def zadd(self, key: str, mapping: dict[str, float]) -> int:
        assert key == delivery.DEFERRED_WAKE_ZSET
        self.zset.update(mapping)
        return len(mapping)

    def zrange(self, key: str, start: int, end: int) -> list[str]:
        assert key == delivery.DEFERRED_WAKE_ZSET
        ordered = sorted(self.zset.items(), key=lambda item: item[1])
        members = [member for member, _ in ordered]
        if end == -1:
            return members[start:]
        return members[start : end + 1]

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


# --- Kasra live / synthetic fixtures (sos#211 BLOCK comment) ---

FIXTURE_IDLE_AFTER_BUS_WAKE = """\
some prior agent output
> WORKING DIR: /home/mumega
> GIT BRANCH: master
> 
"""

FIXTURE_IDLE_CLAUDE_TOKEN_STATUS = """\
previous turn done
❯ 
  12.3k tokens
"""

FIXTURE_GENUINELY_RUNNING = """\
old prompt > still in scrollback
WORKING DIR: /home/mumega
 Running… 12.3k tokens · ctrl+c to stop
"""

FIXTURE_PLAIN_IDLE_PROMPT = """\
previous output
❯ 
"""

FIXTURE_IDLE_LOOM_WAKE_TEMPLATE = """\
> WORKING DIR: /home/mumega
> GIT BRANCH: master
> 
"""

# Kasra live river — bare '>' with no trailing space (BLOCK CLEARED follow-up).
FIXTURE_IDLE_RIVER_BARE_GT = """\
  Standing by. River holds coherence.
──────────────────────────────────────────────────
>
──────────────────────────────────────────────────
? for shortcuts
"""

# Kasra live athena — Cursor idle UI, no Claude prompt markers.
FIXTURE_IDLE_CURSOR_ATHENA = """\
  Holding here unless you want a next door.
  → Add a follow-up
  Cursor Grok 4.5 High Fast · 27.1% · 10 files edited            Run Everything
  /mnt/HC_Volume_104325311/mumega.com/agents/athena · fix/wire-docs-nav
"""


def test_idle_after_bus_wake_classifies_idle() -> None:
    """Property: prior bus-wake chrome must not permanently poison idle panes."""
    assert delivery.pane_at_prompt(FIXTURE_IDLE_AFTER_BUS_WAKE) is True
    assert delivery.pane_looks_busy(FIXTURE_IDLE_AFTER_BUS_WAKE) is False
    assert delivery.pane_at_prompt(FIXTURE_IDLE_LOOM_WAKE_TEMPLATE) is True


def test_idle_river_bare_gt_prompt_classifies_idle() -> None:
    assert delivery.pane_at_prompt(FIXTURE_IDLE_RIVER_BARE_GT) is True
    assert delivery.pane_looks_busy(FIXTURE_IDLE_RIVER_BARE_GT) is False


def test_idle_cursor_athena_classifies_idle() -> None:
    assert delivery.pane_at_prompt(FIXTURE_IDLE_CURSOR_ATHENA) is True
    assert delivery.pane_looks_busy(FIXTURE_IDLE_CURSOR_ATHENA) is False


def test_idle_claude_with_token_status_classifies_idle() -> None:
    assert delivery.pane_at_prompt(FIXTURE_IDLE_CLAUDE_TOKEN_STATUS) is True
    assert delivery.pane_looks_busy(FIXTURE_IDLE_CLAUDE_TOKEN_STATUS) is False


def test_genuinely_running_classifies_busy() -> None:
    assert delivery.pane_looks_busy(FIXTURE_GENUINELY_RUNNING) is True
    assert delivery.pane_at_prompt(FIXTURE_GENUINELY_RUNNING) is False
    assert delivery.pane_has_cursor_busy_chrome(FIXTURE_GENUINELY_RUNNING) is True


def test_plain_idle_prompt_classifies_idle() -> None:
    assert delivery.pane_at_prompt(FIXTURE_PLAIN_IDLE_PROMPT) is True
    assert delivery.pane_looks_busy(FIXTURE_PLAIN_IDLE_PROMPT) is False


def test_working_dir_substring_is_not_busy_chrome() -> None:
    assert delivery.pane_has_cursor_busy_chrome("> WORKING DIR: /home/mumega") is False
    assert "working" not in delivery._CURSOR_BUSY_MARKERS
    assert " tokens" not in delivery._CURSOR_BUSY_MARKERS


def test_busy_chrome_ignores_historical_running_in_scrollback() -> None:
    pane = "Running… ctrl+c to stop\n❯ "
    assert delivery.pane_has_cursor_busy_chrome(pane) is False
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


def test_enqueue_dedupes_same_agent_message() -> None:
    r: Any = FakeRedis()
    now = 1_700_000_000.0
    delivery.enqueue_deferred_wake(
        r,
        agent="athena",
        message="[agent:kasra] hello",
        now=now,
        attempts=0,
        enqueued_at=now,
    )
    delivery.enqueue_deferred_wake(
        r,
        agent="athena",
        message="[agent:kasra] hello",
        now=now + 5,
        attempts=0,
        enqueued_at=now + 5,
    )
    assert len(r.zset) == 1
    payload = json.loads(next(iter(r.zset)))
    assert payload["enqueued_at"] == now
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
    r.zset[member] = now - 1

    calls: list[tuple[str, str]] = []

    def fake_wake(agent: str, message: str, redis_client: Any) -> str:
        calls.append((agent, message))
        return "busy"

    delivered = delivery.process_deferred_wakes(r, now=now, wake_fn=fake_wake)
    assert delivered == 0
    assert calls == [("athena", "[agent:kasra] hello")]
    assert member not in r.zset
    assert len(r.zset) == 1
    remaining = next(iter(r.zset))
    payload = json.loads(remaining)
    assert payload["attempts"] == 1
    assert r.zset[remaining] == pytest.approx(now + delivery.busy_retry_delay_seconds(1))


def test_process_deferred_requeues_blocked() -> None:
    r: Any = FakeRedis()
    now = 1_700_000_000.0
    member = delivery.enqueue_deferred_wake(
        r,
        agent="athena",
        message="[agent:kasra] hello",
        now=now - 1,
        attempts=1,
        enqueued_at=now - 1,
    )
    r.zset[member] = now - 1
    delivered = delivery.process_deferred_wakes(
        r, now=now, wake_fn=lambda *a, **k: "blocked"
    )
    assert delivered == 0
    assert len(r.zset) == 1
    payload = json.loads(next(iter(r.zset)))
    assert payload["attempts"] == 2


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
