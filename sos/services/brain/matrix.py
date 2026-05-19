"""Skill-matrix agent selection — pure function, no I/O, no side effects.

Given a set of required task skills and a list of candidate agents, return
the best-fit agent by skill overlap, breaking ties by current in-flight load
(lowest first) and then lexicographically by name (deterministic).
"""
from __future__ import annotations

from sos.kernel.identity import AgentIdentity
from sos.services.brain.state import BrainState


def agent_load(agent_name: str, state: BrainState) -> int:
    """Effective load for routing: in-flight assignments + failure penalty.

    Failure penalty deprioritizes agents with recent failures so the brain
    routes around them (FSD adaptive weighting — S062 Track A).
    """
    raw = state.assignments_by_agent.get(agent_name, set())
    base_load = raw if isinstance(raw, int) else len(raw)
    failure_count = state.failure_counts_by_agent.get(agent_name, 0)
    penalty = failure_count * state.FAILURE_PENALTY_PER_MISS
    return base_load + penalty


def select_agent(
    required_skills: list[str],
    candidates: list[AgentIdentity],
    state: BrainState,
) -> AgentIdentity | None:
    """Return the candidate with the largest skill-overlap with required_skills.

    Ties broken by (a) lowest ``agent_load``, then (b) lexicographic agent
    name (deterministic). Returns ``None`` if no candidate has any skill
    overlap (score == 0). If ``required_skills`` is empty, returns the
    candidate with lowest ``agent_load``, lex name tiebreaker.
    """
    if not candidates:
        return None

    required_set = set(required_skills)

    if not required_set:
        # No skill requirement — pick by load, then lex name.
        return min(
            candidates,
            key=lambda c: (agent_load(c.name, state), c.name),
        )

    # Score candidates by skill-overlap size.
    scored: list[tuple[int, int, str, AgentIdentity]] = []
    for candidate in candidates:
        overlap = len(required_set.intersection(candidate.capabilities))
        if overlap == 0:
            continue
        scored.append((
            -overlap,  # higher overlap wins → negate for min()
            agent_load(candidate.name, state),
            candidate.name,
            candidate,
        ))

    if not scored:
        return None

    scored.sort(key=lambda row: (row[0], row[1], row[2]))
    return scored[0][3]
