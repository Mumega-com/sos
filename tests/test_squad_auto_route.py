"""Unit tests for ``_auto_pick_assignee`` — closure-v1 Tier 1 §T1.4.

Pins the skill-based task routing algorithm so a future refactor can't
silently change who gets picked when an assignee is omitted.
"""
from __future__ import annotations

from sos.contracts.squad import (
    Squad,
    SquadMember,
    SquadRole,
    SquadStatus,
    SquadTask,
    SquadTier,
)
from sos.services.squad.app import _auto_pick_assignee


def _squad(
    *,
    roles: list[SquadRole] | None = None,
    members: list[SquadMember] | None = None,
    conductance: dict[str, float] | None = None,
) -> Squad:
    return Squad(
        id="sq-1",
        name="test",
        project="p",
        objective="o",
        tier=SquadTier.NOMAD,
        status=SquadStatus.ACTIVE,
        roles=roles or [],
        members=members or [],
        conductance=conductance or {},
    )


def _task(*, labels: list[str] | None = None) -> SquadTask:
    return SquadTask(id="t-1", squad_id="sq-1", title="x", labels=labels or [])


def test_returns_none_when_squad_is_missing():
    assignee, skill, total, matched = _auto_pick_assignee(None, _task(labels=["python"]))
    assert (assignee, skill, total, matched) == (None, None, 0.0, [])


def test_returns_none_when_no_members():
    squad = _squad(roles=[SquadRole(name="dev", skills=["python"])])
    task = _task(labels=["python"])
    assert _auto_pick_assignee(squad, task) == (None, None, 0.0, [])


def test_returns_none_when_task_has_no_labels():
    squad = _squad(
        roles=[SquadRole(name="dev", skills=["python"])],
        members=[SquadMember(agent_id="a1", role="dev")],
    )
    assert _auto_pick_assignee(squad, _task(labels=[])) == (None, None, 0.0, [])


def test_returns_none_when_no_skill_matches():
    squad = _squad(
        roles=[SquadRole(name="dev", skills=["python"])],
        members=[SquadMember(agent_id="a1", role="dev")],
    )
    task = _task(labels=["welding"])
    assert _auto_pick_assignee(squad, task) == (None, None, 0.0, [])


def test_picks_only_candidate_when_skill_matches():
    squad = _squad(
        roles=[SquadRole(name="dev", skills=["python", "redis"])],
        members=[SquadMember(agent_id="kasra", role="dev")],
        conductance={"python": 0.8, "redis": 0.6},
    )
    task = _task(labels=["python"])
    assignee, skill, total, matched = _auto_pick_assignee(squad, task)
    assert assignee == "kasra"
    assert skill == "python"
    assert matched == ["python"]
    assert total == 0.8


def test_prefers_higher_conductance_across_members():
    squad = _squad(
        roles=[
            SquadRole(name="junior", skills=["python"]),
            SquadRole(name="senior", skills=["python"]),
        ],
        members=[
            SquadMember(agent_id="jr", role="junior"),
            SquadMember(agent_id="sr", role="senior"),
        ],
        conductance={"python": 0.9},
    )
    # Both match; tie broken by declaration order (jr listed first)
    assignee, _, total, _ = _auto_pick_assignee(squad, _task(labels=["python"]))
    assert assignee == "jr"
    assert total == 0.9


def test_multi_skill_match_sums_conductance():
    squad = _squad(
        roles=[
            SquadRole(name="generalist", skills=["python", "redis"]),
            SquadRole(name="specialist", skills=["python"]),
        ],
        members=[
            SquadMember(agent_id="gen", role="generalist"),
            SquadMember(agent_id="spec", role="specialist"),
        ],
        conductance={"python": 0.5, "redis": 0.5},
    )
    task = _task(labels=["python", "redis"])
    assignee, skill, total, matched = _auto_pick_assignee(squad, task)
    # Generalist covers both labels → total 1.0 beats specialist's 0.5
    assert assignee == "gen"
    assert total == 1.0
    assert set(matched) == {"python", "redis"}
    # Top skill among matched (ties broken by declaration order on role.skills)
    assert skill in {"python", "redis"}


def test_missing_conductance_uses_default():
    squad = _squad(
        roles=[SquadRole(name="dev", skills=["rust"])],
        members=[SquadMember(agent_id="a1", role="dev")],
        conductance={},  # no entries
    )
    assignee, _, total, _ = _auto_pick_assignee(squad, _task(labels=["rust"]))
    assert assignee == "a1"
    assert total == 0.5  # _DEFAULT_CONDUCTANCE


def test_label_match_is_case_insensitive():
    squad = _squad(
        roles=[SquadRole(name="dev", skills=["Python"])],
        members=[SquadMember(agent_id="a1", role="dev")],
        conductance={"Python": 0.7},
    )
    # Task label arrives in different case
    assignee, skill, total, _ = _auto_pick_assignee(squad, _task(labels=["PYTHON"]))
    assert assignee == "a1"
    assert skill == "Python"
    assert total == 0.7


def test_member_with_unknown_role_is_skipped():
    squad = _squad(
        roles=[SquadRole(name="dev", skills=["python"])],
        members=[
            SquadMember(agent_id="ghost", role="nonexistent"),
            SquadMember(agent_id="real", role="dev"),
        ],
        conductance={"python": 0.6},
    )
    assignee, _, _, _ = _auto_pick_assignee(squad, _task(labels=["python"]))
    assert assignee == "real"


def test_picks_member_with_more_matches_over_higher_single_skill():
    # Generalist matches 2 labels at 0.4 each (total 0.8)
    # Specialist matches 1 label at 0.7 (total 0.7)
    # Generalist wins on total conductance even though specialist has the
    # single highest-conductance skill.
    squad = _squad(
        roles=[
            SquadRole(name="gen", skills=["a", "b"]),
            SquadRole(name="spec", skills=["a"]),
        ],
        members=[
            SquadMember(agent_id="gen", role="gen"),
            SquadMember(agent_id="spec", role="spec"),
        ],
        conductance={"a": 0.4, "b": 0.4},  # spec sees "a" at 0.4 not 0.7
    )
    # Re-check with spec's "a" weighted higher — but spec has only 1 skill
    squad2 = _squad(
        roles=[
            SquadRole(name="gen", skills=["a", "b"]),
            SquadRole(name="spec", skills=["a"]),
        ],
        members=[
            SquadMember(agent_id="gen", role="gen"),
            SquadMember(agent_id="spec", role="spec"),
        ],
        conductance={"a": 0.7, "b": 0.4},
    )
    task = _task(labels=["a", "b"])
    # First: equal conductance → gen wins with 0.8 vs spec 0.4
    assignee, _, total, _ = _auto_pick_assignee(squad, task)
    assert assignee == "gen"
    assert total == 0.8
    # Second: spec 0.7 vs gen 1.1 (0.7 + 0.4) → gen still wins
    assignee2, _, total2, _ = _auto_pick_assignee(squad2, task)
    assert assignee2 == "gen"
    assert total2 == 1.1
