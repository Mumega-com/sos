"""Tests for BrainState's min-heap-backed priority queue."""
from __future__ import annotations

import random

from sos.services.brain.state import BrainState


def test_enqueue_then_pop_returns_tuple() -> None:
    state = BrainState()
    state.enqueue("t1", 5.0)
    assert state.pop_highest() == ("t1", 5.0)


def test_pop_empty_returns_none() -> None:
    state = BrainState()
    assert state.pop_highest() is None


def test_highest_score_pops_first() -> None:
    state = BrainState()
    state.enqueue("low", 1.0)
    state.enqueue("high", 5.0)
    state.enqueue("mid", 2.0)

    assert state.pop_highest() == ("high", 5.0)
    assert state.pop_highest() == ("mid", 2.0)
    assert state.pop_highest() == ("low", 1.0)


def test_fifo_stable_on_equal_scores() -> None:
    state = BrainState()
    state.enqueue("t1", 3.0)
    state.enqueue("t2", 3.0)
    state.enqueue("t3", 3.0)

    assert state.pop_highest() == ("t1", 3.0)
    assert state.pop_highest() == ("t2", 3.0)
    assert state.pop_highest() == ("t3", 3.0)


def test_queue_size_reflects_pending() -> None:
    state = BrainState()
    assert state.queue_size() == 0

    state.enqueue("t1", 1.0)
    state.enqueue("t2", 2.0)
    state.enqueue("t3", 3.0)
    assert state.queue_size() == 3

    state.pop_highest()
    assert state.queue_size() == 2


def test_heap_invariant_after_random_ops() -> None:
    state = BrainState()
    rng = random.Random(42)
    scores = [rng.uniform(-1000.0, 1000.0) for _ in range(100)]
    for i, score in enumerate(scores):
        state.enqueue(f"task_{i}", score)

    popped_scores: list[float] = []
    while state.queue_size() > 0:
        result = state.pop_highest()
        assert result is not None
        _task_id, score = result
        popped_scores.append(score)

    assert popped_scores == sorted(scores, reverse=True)
    assert state.pop_highest() is None
