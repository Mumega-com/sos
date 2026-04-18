"""Brain scoring formula — stub in Sprint 1; populated in Sprint 2.

Per docs/docs/architecture/brain.md:

    score = (impact × urgency × unblock_value) / cost

    urgency   = { critical: 4.0, high: 2.0, medium: 1.0, low: 0.5 }
    impact    ∈ [1, 10]
    unblock   = tasks waiting on this one to complete
    cost      ∈ [0.1, 10]

Sprint 2 adds the FRC constraint:
    dispatch is rejected if
        dS > k* × d(ln C)
    where dS is the entropy cost of the dispatch and d(ln C) is the
    expected coherence gain (via CoherencePhysics on the target agent's
    PhysicsState).
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

URGENCY_WEIGHTS = {
    "critical": 4.0,
    "high": 2.0,
    "medium": 1.0,
    "low": 0.5,
}


@dataclass
class ScoringContext:
    """Inputs to the scoring formula beyond the task itself."""
    unblock_count: int = 0        # how many tasks are blocked_by this task
    agent_coherence: float = 0.5  # target agent's current C (0..1)


def score_task(
    impact: float,
    urgency: str,
    unblock_count: int,
    cost: float,
) -> float:
    """Raw score per brain.md formula. Sprint 2 adds the FRC constraint wrapper."""
    if cost <= 0:
        cost = 0.1
    urgency_weight = URGENCY_WEIGHTS.get(urgency, 1.0)
    unblock_term = max(1.0, float(unblock_count))
    return (float(impact) * urgency_weight * unblock_term) / float(cost)
