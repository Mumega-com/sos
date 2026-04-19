"""Tests for sos.services.objectives.bootstrap.

Verifies idempotency, correct node creation, reference to commit 742d307e,
and fail-soft behaviour on storage errors.
"""
from __future__ import annotations

import fakeredis
import pytest

import sos.services.objectives as obj_store
from sos.services.objectives.bootstrap import (
    _LEAF_MIGRATION_ID,
    _LEAF_PROTOCOL_ID,
    _ROOT_ID,
    bootstrap_reviews_subtree,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def fake_redis(monkeypatch: pytest.MonkeyPatch) -> fakeredis.FakeRedis:
    """Wire a fresh FakeRedis for every test."""
    r = fakeredis.FakeRedis(decode_responses=True)
    monkeypatch.setattr(obj_store, "_get_redis", lambda: r)
    return r


# ---------------------------------------------------------------------------
# 1. Root node is created when it is absent
# ---------------------------------------------------------------------------


def test_bootstrap_creates_root_when_missing() -> None:
    """First call must create the root 'reviews-primitive' objective."""
    bootstrap_reviews_subtree()

    root = obj_store.read_objective(_ROOT_ID)
    assert root is not None, "Root node must exist after bootstrap"
    assert root.id == _ROOT_ID
    assert root.title == "Reviews — structural primitive"
    assert root.state == "open"
    assert root.parent_id is None
    assert "reviews" in root.tags
    assert "canonical" in root.tags
    assert "root" in root.tags
    assert root.created_by == "system"


# ---------------------------------------------------------------------------
# 2. Both leaf nodes are created
# ---------------------------------------------------------------------------


def test_bootstrap_creates_both_leaves() -> None:
    """Both leaf nodes must be written with correct parent_id references."""
    bootstrap_reviews_subtree()

    leaf_protocol = obj_store.read_objective(_LEAF_PROTOCOL_ID)
    assert leaf_protocol is not None, "Protocol leaf must exist"
    assert leaf_protocol.parent_id == _ROOT_ID
    assert "writing" in leaf_protocol.capabilities_required
    assert "protocol-design" in leaf_protocol.capabilities_required
    assert leaf_protocol.bounty_mind == 100

    leaf_migration = obj_store.read_objective(_LEAF_MIGRATION_ID)
    assert leaf_migration is not None, "Migration leaf must exist"
    assert leaf_migration.parent_id == _ROOT_ID
    assert "python" in leaf_migration.capabilities_required
    assert "alembic" in leaf_migration.capabilities_required
    assert leaf_migration.bounty_mind == 200


# ---------------------------------------------------------------------------
# 3. Idempotency — calling twice leaves exactly one set of nodes
# ---------------------------------------------------------------------------


def test_bootstrap_is_idempotent() -> None:
    """Calling bootstrap twice must not duplicate nodes."""
    bootstrap_reviews_subtree()
    bootstrap_reviews_subtree()

    # Children set must have exactly two entries (not four)
    children = obj_store.read_children(_ROOT_ID)
    assert len(children) == 2, f"Expected 2 children, got {len(children)}"

    # Root must still be singular
    root = obj_store.read_objective(_ROOT_ID)
    assert root is not None


# ---------------------------------------------------------------------------
# 4. Root description references commit 742d307e
# ---------------------------------------------------------------------------


def test_bootstrap_root_references_commit_742d307e_in_description() -> None:
    """The root description must contain the load-bearing v0.6.2 commit hash."""
    bootstrap_reviews_subtree()

    root = obj_store.read_objective(_ROOT_ID)
    assert root is not None
    assert "742d307e" in root.description, (
        "Root description must reference commit 742d307e (the v0.6.2 column drop)"
    )
    assert "v0.6.2" in root.description


# ---------------------------------------------------------------------------
# 5. Fail-soft on storage error — bootstrap never raises
# ---------------------------------------------------------------------------


def test_bootstrap_fails_soft_on_storage_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """If storage raises, bootstrap must swallow the error — never re-raise."""
    import redis as _redis

    def _broken_redis() -> None:
        raise _redis.RedisError("simulated connection failure")

    monkeypatch.setattr(obj_store, "_get_redis", _broken_redis)

    # Must not raise
    bootstrap_reviews_subtree()
