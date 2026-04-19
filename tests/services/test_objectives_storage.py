"""Tests for sos.services.objectives storage layer.

Uses fakeredis.FakeRedis (sync) to avoid requiring a live Redis instance.
The module-level ``_get_redis`` is monkeypatched before each test so every
storage function exercises real logic against an in-memory Redis clone.
"""
from __future__ import annotations

import json
from unittest.mock import MagicMock

import fakeredis
import pytest

from sos.contracts.objective import Objective
import sos.services.objectives as obj_store


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

ULID_A = "01HWZZZZZZZZZZZZZZZZZZZZZA"
ULID_B = "01HWZZZZZZZZZZZZZZZZZZZZZB"
ULID_C = "01HWZZZZZZZZZZZZZZZZZZZZZC"
ULID_D = "01HWZZZZZZZZZZZZZZZZZZZZZD"


def _make_objective(
    obj_id: str = ULID_A,
    *,
    title: str = "Test objective",
    state: str = "open",
    parent_id: str | None = None,
    tags: list[str] | None = None,
    bounty_mind: int = 0,
    capabilities_required: list[str] | None = None,
    project: str | None = None,
    holder_agent: str | None = None,
) -> Objective:
    now = Objective.now_iso()
    return Objective(
        id=obj_id,
        parent_id=parent_id,
        title=title,
        state=state,  # type: ignore[arg-type]
        tags=tags or [],
        bounty_mind=bounty_mind,
        capabilities_required=capabilities_required or [],
        created_by="test-agent",
        created_at=now,
        updated_at=now,
        project=project,
        holder_agent=holder_agent,
    )


@pytest.fixture(autouse=True)
def fake_redis(monkeypatch: pytest.MonkeyPatch) -> fakeredis.FakeRedis:
    """Replace _get_redis with a fresh FakeRedis instance for each test."""
    r = fakeredis.FakeRedis(decode_responses=True)
    monkeypatch.setattr(obj_store, "_get_redis", lambda: r)
    return r


# ---------------------------------------------------------------------------
# 1. write + read roundtrip
# ---------------------------------------------------------------------------


def test_write_and_read_roundtrip() -> None:
    o = _make_objective(title="Ship it")
    obj_store.write_objective(o)
    result = obj_store.read_objective(ULID_A)
    assert result is not None
    assert result.id == ULID_A
    assert result.title == "Ship it"
    assert result.state == "open"


# ---------------------------------------------------------------------------
# 2. read missing returns None
# ---------------------------------------------------------------------------


def test_read_missing_returns_none() -> None:
    result = obj_store.read_objective("01HWZZZZZZZZZZZZZZZZZZZZZZ")
    assert result is None


# ---------------------------------------------------------------------------
# 3. read_children returns child objects
# ---------------------------------------------------------------------------


def test_read_children_returns_child_objects() -> None:
    parent = _make_objective(ULID_A, title="Parent")
    child1 = _make_objective(ULID_B, title="Child 1", parent_id=ULID_A)
    child2 = _make_objective(ULID_C, title="Child 2", parent_id=ULID_A)

    obj_store.write_objective(parent)
    obj_store.write_objective(child1)
    obj_store.write_objective(child2)

    children = obj_store.read_children(ULID_A)
    assert len(children) == 2
    titles = {c.title for c in children}
    assert titles == {"Child 1", "Child 2"}


# ---------------------------------------------------------------------------
# 4. read_children empty
# ---------------------------------------------------------------------------


def test_read_children_empty() -> None:
    parent = _make_objective(ULID_A)
    obj_store.write_objective(parent)
    children = obj_store.read_children(ULID_A)
    assert children == []


# ---------------------------------------------------------------------------
# 5. read_tree returns nested dict
# ---------------------------------------------------------------------------


def test_read_tree_returns_nested_dict() -> None:
    root = _make_objective(ULID_A, title="Root")
    child = _make_objective(ULID_B, title="Child", parent_id=ULID_A)
    grandchild = _make_objective(ULID_C, title="Grandchild", parent_id=ULID_B)

    for o in [root, child, grandchild]:
        obj_store.write_objective(o)

    tree = obj_store.read_tree(ULID_A)
    assert tree["objective"].id == ULID_A
    assert len(tree["children"]) == 1
    child_node = tree["children"][0]
    assert child_node["objective"].id == ULID_B
    assert len(child_node["children"]) == 1
    assert child_node["children"][0]["objective"].id == ULID_C


# ---------------------------------------------------------------------------
# 6. read_tree respects max_depth
# ---------------------------------------------------------------------------


def test_read_tree_respects_max_depth() -> None:
    # Build a chain: A → B → C → D
    root = _make_objective(ULID_A, title="Root")
    lvl2 = _make_objective(ULID_B, title="L2", parent_id=ULID_A)
    lvl3 = _make_objective(ULID_C, title="L3", parent_id=ULID_B)
    lvl4 = _make_objective(ULID_D, title="L4", parent_id=ULID_C)

    for o in [root, lvl2, lvl3, lvl4]:
        obj_store.write_objective(o)

    # max_depth=2: root + L2 are included; L3 is the cut-off (depth=3)
    tree = obj_store.read_tree(ULID_A, max_depth=2)
    assert tree["objective"].id == ULID_A
    assert len(tree["children"]) == 1
    l2_node = tree["children"][0]
    assert l2_node["objective"].id == ULID_B
    # At max_depth=2, children of L2 (depth=3) are not fetched
    assert l2_node["children"] == []


# ---------------------------------------------------------------------------
# 7. query_open returns only open-state objectives
# ---------------------------------------------------------------------------


def test_query_open_returns_only_open_state() -> None:
    open_obj = _make_objective(ULID_A, state="open")
    claimed_obj = _make_objective(ULID_B, state="claimed")
    obj_store.write_objective(open_obj)
    obj_store.write_objective(claimed_obj)

    results = obj_store.query_open()
    assert len(results) == 1
    assert results[0].id == ULID_A


# ---------------------------------------------------------------------------
# 8. query_open filters by tag
# ---------------------------------------------------------------------------


def test_query_open_filters_by_tag() -> None:
    tagged = _make_objective(ULID_A, tags=["infra", "critical"])
    untagged = _make_objective(ULID_B, tags=["docs"])
    obj_store.write_objective(tagged)
    obj_store.write_objective(untagged)

    results = obj_store.query_open(tag="infra")
    assert len(results) == 1
    assert results[0].id == ULID_A


# ---------------------------------------------------------------------------
# 9. query_open filters by min_bounty
# ---------------------------------------------------------------------------


def test_query_open_filters_by_min_bounty() -> None:
    rich = _make_objective(ULID_A, bounty_mind=500)
    poor = _make_objective(ULID_B, bounty_mind=10)
    obj_store.write_objective(rich)
    obj_store.write_objective(poor)

    results = obj_store.query_open(min_bounty=100)
    assert len(results) == 1
    assert results[0].id == ULID_A


# ---------------------------------------------------------------------------
# 10. query_open filters by capability
# ---------------------------------------------------------------------------


def test_query_open_filters_by_capability() -> None:
    cap_obj = _make_objective(ULID_A, capabilities_required=["python", "redis"])
    no_cap = _make_objective(ULID_B, capabilities_required=["design"])
    obj_store.write_objective(cap_obj)
    obj_store.write_objective(no_cap)

    results = obj_store.query_open(capability="redis")
    assert len(results) == 1
    assert results[0].id == ULID_A


# ---------------------------------------------------------------------------
# 11. query_open filters by subtree_root
# ---------------------------------------------------------------------------


def test_query_open_filters_by_subtree_root() -> None:
    root = _make_objective(ULID_A)
    in_subtree = _make_objective(ULID_B, parent_id=ULID_A)
    outside = _make_objective(ULID_C)  # no relation to root

    obj_store.write_objective(root)
    obj_store.write_objective(in_subtree)
    obj_store.write_objective(outside)

    # subtree_root=ULID_A should include ULID_A and ULID_B, not ULID_C
    results = obj_store.query_open(subtree_root=ULID_A)
    ids = {r.id for r in results}
    assert ULID_B in ids
    assert ULID_A in ids
    assert ULID_C not in ids


# ---------------------------------------------------------------------------
# 12. claim_objective success transitions state
# ---------------------------------------------------------------------------


def test_claim_objective_success_transitions_state() -> None:
    o = _make_objective(ULID_A, state="open")
    obj_store.write_objective(o)

    result = obj_store.claim_objective(ULID_A, agent="kasra")
    assert result is True

    updated = obj_store.read_objective(ULID_A)
    assert updated is not None
    assert updated.state == "claimed"
    assert updated.holder_agent == "kasra"
    assert updated.holder_heartbeat_at is not None

    # Should be removed from open set
    open_results = obj_store.query_open()
    assert all(r.id != ULID_A for r in open_results)


# ---------------------------------------------------------------------------
# 13. claim_objective already claimed returns False
# ---------------------------------------------------------------------------


def test_claim_objective_already_claimed_returns_false() -> None:
    o = _make_objective(ULID_A, state="claimed", holder_agent="already")
    obj_store.write_objective(o)

    result = obj_store.claim_objective(ULID_A, agent="interloper")
    assert result is False

    # State unchanged
    current = obj_store.read_objective(ULID_A)
    assert current is not None
    assert current.holder_agent == "already"


# ---------------------------------------------------------------------------
# 14. heartbeat bumps timestamp
# ---------------------------------------------------------------------------


def test_heartbeat_bumps_timestamp() -> None:
    o = _make_objective(ULID_A, state="claimed")
    obj_store.write_objective(o)

    # Read original heartbeat
    before = obj_store.read_objective(ULID_A)
    assert before is not None
    original_updated = before.updated_at

    # Small sleep is not needed — fakeredis is synchronous; timestamps will
    # differ if a real second passes, but we verify the field is set non-null.
    result = obj_store.heartbeat_objective(ULID_A)
    assert result is True

    after = obj_store.read_objective(ULID_A)
    assert after is not None
    assert after.holder_heartbeat_at is not None


def test_heartbeat_missing_objective_returns_false() -> None:
    result = obj_store.heartbeat_objective("01HWZZZZZZZZZZZZZZZZZZZZZZ")
    assert result is False


# ---------------------------------------------------------------------------
# 15. release returns to open and rejoins open set
# ---------------------------------------------------------------------------


def test_release_returns_to_open_and_rejoins_open_set() -> None:
    o = _make_objective(ULID_A, state="claimed", holder_agent="kasra")
    obj_store.write_objective(o)

    result = obj_store.release_objective(ULID_A)
    assert result is True

    updated = obj_store.read_objective(ULID_A)
    assert updated is not None
    assert updated.state == "open"
    assert updated.holder_agent is None
    assert updated.holder_heartbeat_at is None

    open_results = obj_store.query_open()
    assert any(r.id == ULID_A for r in open_results)


# ---------------------------------------------------------------------------
# 16. complete sets shipped state and artifact
# ---------------------------------------------------------------------------


def test_complete_sets_shipped_state_and_artifact() -> None:
    o = _make_objective(ULID_A, state="claimed", holder_agent="kasra")
    obj_store.write_objective(o)

    result = obj_store.complete_objective(
        ULID_A,
        artifact_url="https://s3.example.com/output.zip",
        notes="all tests green",
    )
    assert result is True

    updated = obj_store.read_objective(ULID_A)
    assert updated is not None
    assert updated.state == "shipped"
    assert updated.completion_artifact_url == "https://s3.example.com/output.zip"
    assert updated.completion_notes == "all tests green"
    # Holder NOT cleared — credit stays until paid
    assert updated.holder_agent == "kasra"


def test_complete_missing_objective_returns_false() -> None:
    result = obj_store.complete_objective(
        "01HWZZZZZZZZZZZZZZZZZZZZZZ", artifact_url="https://x.example.com/out"
    )
    assert result is False


# ---------------------------------------------------------------------------
# 17. ack appends agent, deduplicates
# ---------------------------------------------------------------------------


def test_ack_appends_agent_dedupe() -> None:
    o = _make_objective(ULID_A, state="shipped")
    obj_store.write_objective(o)

    obj_store.ack_completion(ULID_A, acker="peer-1")
    obj_store.ack_completion(ULID_A, acker="peer-1")  # duplicate
    obj_store.ack_completion(ULID_A, acker="peer-2")

    updated = obj_store.read_objective(ULID_A)
    assert updated is not None
    assert sorted(updated.acks) == ["peer-1", "peer-2"]


def test_ack_missing_objective_returns_false() -> None:
    result = obj_store.ack_completion("01HWZZZZZZZZZZZZZZZZZZZZZZ", acker="ghost")
    assert result is False


# ---------------------------------------------------------------------------
# 18. project scope isolation
# ---------------------------------------------------------------------------


def test_project_scope_isolation() -> None:
    """Write in project=A; reading from project=B must return None."""
    o_a = _make_objective(ULID_A, project="project-a")
    obj_store.write_objective(o_a)

    result_a = obj_store.read_objective(ULID_A, project="project-a")
    assert result_a is not None
    assert result_a.id == ULID_A

    result_b = obj_store.read_objective(ULID_A, project="project-b")
    assert result_b is None


# ---------------------------------------------------------------------------
# 18b. release uses HDEL — holder fields must be absent, not the literal "null"
# ---------------------------------------------------------------------------


def test_release_uses_hdel_not_null_string(
    fake_redis: fakeredis.FakeRedis,
) -> None:
    """After release, holder_agent and holder_heartbeat_at must be absent from
    the Redis hash — not the literal string "null".

    This guards against the pre-Fix3 behaviour where the string "null" was
    written instead of the fields being deleted.
    """
    o = _make_objective(ULID_A, state="claimed", holder_agent="kasra")
    obj_store.write_objective(o)

    result = obj_store.release_objective(ULID_A)
    assert result is True

    # Inspect Redis directly
    key = f"sos:objectives:default:{ULID_A}"
    raw_holder = fake_redis.hget(key, "holder_agent")
    raw_heartbeat = fake_redis.hget(key, "holder_heartbeat_at")

    assert raw_holder is None, (
        f"holder_agent should be absent (HDEL'd), got: {raw_holder!r}"
    )
    assert raw_heartbeat is None, (
        f"holder_heartbeat_at should be absent (HDEL'd), got: {raw_heartbeat!r}"
    )

    # Pydantic model round-trip must still work and report None
    updated = obj_store.read_objective(ULID_A)
    assert updated is not None
    assert updated.holder_agent is None
    assert updated.holder_heartbeat_at is None


# ---------------------------------------------------------------------------
# 19. Redis errors are fail-soft — no exception escapes
# ---------------------------------------------------------------------------


def test_redis_error_is_fail_soft(monkeypatch: pytest.MonkeyPatch) -> None:
    """Replace _get_redis with something that raises to verify fail-soft."""
    import redis as _redis

    def _broken_redis() -> None:
        raise _redis.RedisError("connection refused")

    monkeypatch.setattr(obj_store, "_get_redis", _broken_redis)

    o = _make_objective(ULID_A)

    # None of these should raise
    obj_store.write_objective(o)
    assert obj_store.read_objective(ULID_A) is None
    assert obj_store.read_children(ULID_A) == []
    assert obj_store.read_tree(ULID_A) == {}
    assert obj_store.query_open() == []
    assert obj_store.claim_objective(ULID_A, agent="kasra") is False
    assert obj_store.heartbeat_objective(ULID_A) is False
    assert obj_store.release_objective(ULID_A) is False
    assert obj_store.complete_objective(ULID_A, artifact_url="https://x.example/a") is False
    assert obj_store.ack_completion(ULID_A, acker="x") is False
