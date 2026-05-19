"""Governance policy for SOS Engine.

sos:policy:governance is a Redis key (JSON) written by the current governance
anchor (Loom in v1, River when she returns in v2). Sovereign reads it at dispatch
to cap fuel_grade and token_budget before a task is claimed.

Single source of truth for fuel grade ordering. Sovereign imports
FUEL_GRADE_ORDER from here — does not define its own.
"""
from __future__ import annotations

import json
import logging
import time
from typing import Any, Optional

from pydantic import BaseModel, Field

log = logging.getLogger("sos.engine.policy")

# Fuel grade ordering — lower index = cheaper/weaker.
# Used to enforce "max_fuel_grade" caps: task grade is capped to the
# highest grade that is <= the policy's allowed maximum.
FUEL_GRADE_ORDER: list[str] = ["diesel", "regular", "aviation", "supernova"]

POLICY_REDIS_KEY = "sos:policy:governance"
_CACHE_TTL_SECS = 60  # in-process cache lifetime

# ---------------------------------------------------------------------------
# Models
# ---------------------------------------------------------------------------


class SquadPolicy(BaseModel):
    max_fuel_grade: Optional[str] = None
    max_token_budget: Optional[int] = None


class GovernancePolicy(BaseModel):
    version: int = 1
    updated_at: str = ""
    updated_by: str = ""
    global_: SquadPolicy = Field(default_factory=SquadPolicy, alias="global")
    squads: dict[str, SquadPolicy] = Field(default_factory=dict)

    model_config = {"populate_by_name": True}


# ---------------------------------------------------------------------------
# In-process cache
# ---------------------------------------------------------------------------

_cached_policy: Optional[GovernancePolicy] = None
_cached_at: float = 0.0


def _invalidate_cache() -> None:
    global _cached_policy, _cached_at
    _cached_policy = None
    _cached_at = 0.0


# ---------------------------------------------------------------------------
# Load / write
# ---------------------------------------------------------------------------


def load_policy(redis_client: Any) -> Optional[GovernancePolicy]:
    """Read sos:policy:governance from Redis with a 60-second in-process cache.

    Returns None if the key is missing or Redis is unavailable.
    Callers must fail-open when None is returned.
    """
    global _cached_policy, _cached_at

    now = time.monotonic()
    if _cached_policy is not None and (now - _cached_at) < _CACHE_TTL_SECS:
        return _cached_policy

    try:
        raw = redis_client.get(POLICY_REDIS_KEY)
        if raw is None:
            return None
        data = json.loads(raw)
        policy = GovernancePolicy.model_validate(data)
        _cached_policy = policy
        _cached_at = now
        return policy
    except Exception as exc:
        log.warning("Failed to load governance policy from Redis — failing open: %s", exc)
        return None


def write_policy(redis_client: Any, policy: GovernancePolicy) -> None:
    """Write a validated GovernancePolicy to Redis and invalidate the cache."""
    raw = policy.model_dump_json(by_alias=True)
    redis_client.set(POLICY_REDIS_KEY, raw)
    _invalidate_cache()


# ---------------------------------------------------------------------------
# Cap application
# ---------------------------------------------------------------------------


def apply_caps(task: dict, policy: GovernancePolicy) -> dict:
    """Return a shallow copy of task with fuel_grade and token_budget capped.

    Resolution order: squad-level policy overrides global, both cap task values.
    If the policy allows a grade higher than the task's grade, the task is unchanged.
    Missing or unknown grades are left untouched (fail-open).
    """
    squad_id = task.get("squad_id", "")
    squad_policy = policy.squads.get(squad_id, SquadPolicy())

    effective_max_grade = squad_policy.max_fuel_grade or policy.global_.max_fuel_grade
    effective_max_budget = squad_policy.max_token_budget or policy.global_.max_token_budget

    task = dict(task)  # shallow copy — don't mutate caller's dict
    inputs = dict(task.get("inputs") or {})

    if effective_max_grade is not None:
        current_grade = inputs.get("fuel_grade", "diesel")
        if current_grade in FUEL_GRADE_ORDER and effective_max_grade in FUEL_GRADE_ORDER:
            current_idx = FUEL_GRADE_ORDER.index(current_grade)
            max_idx = FUEL_GRADE_ORDER.index(effective_max_grade)
            if current_idx > max_idx:
                log.info(
                    "Policy cap: fuel_grade %s → %s for task %s (squad %s)",
                    current_grade, effective_max_grade, task.get("id"), squad_id,
                )
                inputs["fuel_grade"] = effective_max_grade
        task["inputs"] = inputs

    if effective_max_budget is not None:
        current_budget = task.get("token_budget", 0)
        if current_budget > effective_max_budget:
            log.info(
                "Policy cap: token_budget %d → %d for task %s (squad %s)",
                current_budget, effective_max_budget, task.get("id"), squad_id,
            )
            task["token_budget"] = effective_max_budget

    return task
