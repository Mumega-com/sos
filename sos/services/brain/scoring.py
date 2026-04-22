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

import os
import time
from dataclasses import dataclass
from typing import Any, Optional

URGENCY_WEIGHTS = {
    "critical": 4.0,
    "high": 2.0,
    "medium": 1.0,
    "low": 0.5,
}

# ── Squad tier multiplier (Phase 2) ──────────────────────────────────────────

TIER_MULTIPLIER: dict[str, float] = {
    "construct": 1.4,
    "fortress": 1.1,
    "nomad": 1.0,
}

# In-process TTL cache: squad_id → (tier, expires_at_unix)
_squad_tier_cache: dict[str, tuple[str, float]] = {}


async def get_squad_tier(squad_id: str) -> str:
    """Return the squad tier string for *squad_id*.

    Results are cached for 5 minutes to keep the hot scoring path cheap.
    Falls back to "nomad" on any error or cache miss.
    """
    import httpx  # local import — httpx may not be installed in all envs

    cached = _squad_tier_cache.get(squad_id)
    if cached and cached[1] > time.time():
        return cached[0]

    squad_url = os.environ.get("SQUAD_SERVICE_URL", "http://localhost:8060")
    try:
        async with httpx.AsyncClient(timeout=2.0) as client:
            r = await client.get(f"{squad_url}/squads/{squad_id}")
            if r.status_code == 200:
                tier: str = r.json().get("tier", "nomad")
                _squad_tier_cache[squad_id] = (tier, time.time() + 300)  # 5 min TTL
                return tier
    except Exception:
        pass
    return "nomad"


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


async def score_task_with_tier(
    task: dict[str, Any],
    impact: float,
    urgency: str,
    unblock_count: int,
    cost: float,
) -> float:
    """Score a task and apply a squad-tier multiplier if task has a squad_id.

    Wraps ``score_task`` — existing callers are not affected.
    """
    base_score = score_task(impact, urgency, unblock_count, cost)

    tier = "nomad"
    squad_id = task.get("squad_id")
    if squad_id:
        tier = await get_squad_tier(str(squad_id))

    multiplier = TIER_MULTIPLIER.get(tier, 1.0)
    return base_score * multiplier
