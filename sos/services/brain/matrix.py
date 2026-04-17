"""Skill-matrix agent selection — pure function, no I/O, no side effects.

Given a set of required task skills and a list of candidate agents, return
the best-fit agent by skill overlap, breaking ties by current in-flight load
(lowest first) and then lexicographically by name (deterministic).
"""
from __future__ import annotations

from sos.kernel.identity import AgentIdentity
from sos.services.brain.state import BrainState


def agent_load(agent_name: str, state: BrainState) -> int:
    """Count of in-flight tasks assigned to this agent.

    Stub: returns 0 when BrainState does not yet track per-agent task
    assignment (Sprint 3 will extend state if needed). For now, returns 0
    if ``agent_name`` is not present in a separate assignments dict;
    otherwise that count.

    Since ``BrainState.tasks_in_flight`` is a flat set of task_ids with no
    agent mapping yet, this function accepts the state but returns 0 for
    all agents until richer state is wired. Kept in the public API so later
    wiring does not change callers.
    """
    assignments = getattr(state, "assignments_by_agent", None)
    if isinstance(assignments, dict):
        value = assignments.get(agent_name, 0)
        if isinstance(value, int):
            return value
        # Allow a set/list of task_ids in the mapping as a natural shape.
        try:
            return len(value)  # type: ignore[arg-type]
        except TypeError:
            return 0
    return 0


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
