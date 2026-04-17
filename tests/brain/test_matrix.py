"""Tests for sos.services.brain.matrix — skill-matrix agent selector."""
from __future__ import annotations

import pytest

from sos.kernel.identity import AgentIdentity
from sos.services.brain.matrix import agent_load, select_agent
from sos.services.brain.state import BrainState


def _agent(name: str, caps: list[str]) -> AgentIdentity:
    """Build an AgentIdentity with the given capabilities."""
    a = AgentIdentity(name=name)
    a.capabilities.extend(caps)
    return a


def test_select_returns_none_when_no_candidates() -> None:
    state = BrainState()
    assert select_agent(["py"], [], state) is None


def test_select_returns_none_when_no_skill_overlap() -> None:
    state = BrainState()
    candidates = [_agent("rusty", ["rust"])]
    assert select_agent(["kubernetes"], candidates, state) is None


def test_select_picks_highest_overlap() -> None:
    state = BrainState()
    low = _agent("low", ["py"])
    high = _agent("high", ["py", "docker", "k8s"])
    picked = select_agent(["py", "docker", "k8s"], [low, high], state)
    assert picked is not None
    assert picked.name == "high"


def test_select_tiebreaks_by_load() -> None:
    # agent_load currently returns 0 for all agents until Sprint 3 extends
    # BrainState with per-agent assignment tracking. The stub honours an
    # optional ``assignments_by_agent`` dict when present, so we attach one
    # dynamically here to exercise the tiebreak.
    state = BrainState()
    # Dynamic attribute — matrix.agent_load reads this when available.
    state.assignments_by_agent = {"alpha": 3, "beta": 0}  # type: ignore[attr-defined]

    # Sanity: if the stub still returns 0 for all, skip per brief.
    if agent_load("alpha", state) == 0 and agent_load("beta", state) == 0:
        pytest.skip("agent_load stub returns 0; Sprint 3 extends")

    alpha = _agent("alpha", ["py", "docker"])
    beta = _agent("beta", ["py", "docker"])
    picked = select_agent(["py", "docker"], [alpha, beta], state)
    assert picked is not None
    assert picked.name == "beta"


def test_select_tiebreaks_lex_when_loads_equal() -> None:
    state = BrainState()
    alpha = _agent("alpha", ["py", "docker"])
    beta = _agent("beta", ["py", "docker"])
    picked = select_agent(["py", "docker"], [beta, alpha], state)
    assert picked is not None
    assert picked.name == "alpha"


def test_select_with_no_required_skills_picks_lowest_lex() -> None:
    state = BrainState()
    a = _agent("a", ["x"])
    b = _agent("b", ["y"])
    picked = select_agent([], [b, a], state)
    assert picked is not None
    assert picked.name == "a"


def test_select_is_deterministic_across_calls() -> None:
    state = BrainState()
    c1 = _agent("zulu", ["py", "k8s"])
    c2 = _agent("yankee", ["py"])
    c3 = _agent("xray", ["py", "k8s", "docker"])
    required = ["py", "k8s", "docker"]

    results = [select_agent(required, [c1, c2, c3], state) for _ in range(10)]
    assert all(r is not None for r in results)
    names = {r.name for r in results if r is not None}
    assert names == {"xray"}
