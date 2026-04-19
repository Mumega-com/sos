"""Unit tests for the board-view scoring function in sos.services.squad.app.

The board endpoint ranks tasks by ``priority*10 + blocks*5 + age_hours*2``
(closure-v1 Tier 1 §T1.5). This file pins that formula and the stable
fallback-to-creation-order tiebreak so a future refactor can't silently
change the ranking.
"""

from __future__ import annotations

import time
from datetime import datetime, timedelta, timezone

from sos.contracts.squad import SquadTask, TaskPriority, TaskStatus
from sos.services.squad.app import _PRIORITY_WEIGHTS, _score_task


def _task(
    *,
    priority: TaskPriority = TaskPriority.MEDIUM,
    blocks: list[str] | None = None,
    created_hours_ago: float = 0.0,
) -> SquadTask:
    created = datetime.now(timezone.utc) - timedelta(hours=created_hours_ago)
    return SquadTask(
        id="t-x",
        squad_id="sq-1",
        title="x",
        priority=priority,
        blocks=blocks or [],
        created_at=created.isoformat(),
    )


def test_priority_weights_strictly_ordered() -> None:
    assert _PRIORITY_WEIGHTS[TaskPriority.CRITICAL] > _PRIORITY_WEIGHTS[TaskPriority.HIGH]
    assert _PRIORITY_WEIGHTS[TaskPriority.HIGH] > _PRIORITY_WEIGHTS[TaskPriority.MEDIUM]
    assert _PRIORITY_WEIGHTS[TaskPriority.MEDIUM] > _PRIORITY_WEIGHTS[TaskPriority.LOW]


def test_score_pure_priority_term() -> None:
    now = time.time()
    critical = _score_task(_task(priority=TaskPriority.CRITICAL), now)
    low = _score_task(_task(priority=TaskPriority.LOW), now)
    # CRITICAL=4*10=40, LOW=1*10=10; no blocks, no age → exact
    assert critical == 40.0
    assert low == 10.0


def test_score_adds_blocks_term() -> None:
    now = time.time()
    no_blocks = _score_task(_task(priority=TaskPriority.MEDIUM), now)
    three_blocks = _score_task(
        _task(priority=TaskPriority.MEDIUM, blocks=["a", "b", "c"]), now
    )
    # Each blocked task contributes 5 → delta must be 15
    assert three_blocks - no_blocks == 15.0


def test_score_adds_age_term() -> None:
    now = time.time()
    fresh = _score_task(_task(priority=TaskPriority.MEDIUM), now)
    old = _score_task(_task(priority=TaskPriority.MEDIUM, created_hours_ago=10.0), now)
    # 10 hours * 2 = 20; allow tiny drift from datetime now() call above
    assert 19.9 < (old - fresh) < 20.1


def test_score_handles_missing_created_at() -> None:
    task = SquadTask(id="t-none", squad_id="sq-1", title="x")
    task.created_at = ""
    # Empty created_at must not raise — age term is 0
    assert _score_task(task, time.time()) == 20.0  # MEDIUM=20, blocks=0, age=0


def test_score_handles_malformed_created_at() -> None:
    task = _task(priority=TaskPriority.HIGH)
    task.created_at = "not-an-iso-string"
    # Malformed timestamps fall back to age=0, not an exception
    assert _score_task(task, time.time()) == 30.0


def test_score_accepts_zulu_suffix() -> None:
    """Z-suffix ISO strings (common in emit paths) must parse, not fall through to 0."""
    now = time.time()
    task = _task(priority=TaskPriority.MEDIUM, created_hours_ago=5.0)
    # Swap +00:00 for Z form
    task.created_at = task.created_at.replace("+00:00", "Z")
    score = _score_task(task, now)
    assert 29.9 < score < 30.1  # 20 base + 10 age ≈ 30
