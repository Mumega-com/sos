"""Tests for sos.services.registry — typed agent registry read/write API.

Uses fakeredis for hermetic testing (no live Redis required).
"""
from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

import pytest

try:
    import fakeredis  # type: ignore[import-untyped]
    HAS_FAKEREDIS = True
except ImportError:
    HAS_FAKEREDIS = False

from sos.kernel.identity import AgentDNA, AgentIdentity, PhysicsState, VerificationStatus
from sos.services import registry as agent_registry


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_fake_redis() -> "fakeredis.FakeRedis":
    return fakeredis.FakeRedis(decode_responses=True)


def _make_ident(name: str, squad: str | None = None, project: str | None = None) -> AgentIdentity:
    ident = AgentIdentity(
        name=name,
        model="claude-sonnet-4-6",
        squad_id=squad,
        edition="business",
    )
    ident.metadata["status"] = "online"
    ident.metadata["project"] = project or ""
    return ident


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


@pytest.mark.skipif(not HAS_FAKEREDIS, reason="fakeredis not installed")
def test_read_all_empty() -> None:
    """Redis has nothing → read_all returns empty list."""
    fake_r = _make_fake_redis()
    with patch.object(agent_registry, "_get_redis", return_value=fake_r):
        result = agent_registry.read_all()
    assert result == []


@pytest.mark.skipif(not HAS_FAKEREDIS, reason="fakeredis not installed")
def test_write_then_read_one() -> None:
    """Write an AgentIdentity, read it back, fields match."""
    fake_r = _make_fake_redis()
    ident = _make_ident("kasra")
    ident.verification_status = VerificationStatus.VERIFIED

    with patch.object(agent_registry, "_get_redis", return_value=fake_r):
        agent_registry.write(ident, ttl_seconds=0)
        result = agent_registry.read_one("kasra")

    assert result is not None
    assert result.name == "kasra"
    assert result.model == "claude-sonnet-4-6"
    assert result.verification_status == VerificationStatus.VERIFIED
    assert result.edition == "business"


@pytest.mark.skipif(not HAS_FAKEREDIS, reason="fakeredis not installed")
def test_read_all_with_project_scope() -> None:
    """Write 2 agents in project A, 1 in project B; scoping works."""
    fake_r = _make_fake_redis()
    a1 = _make_ident("river", project="alpha")
    a2 = _make_ident("kasra", project="alpha")
    b1 = _make_ident("meridian", project="beta")

    with patch.object(agent_registry, "_get_redis", return_value=fake_r):
        agent_registry.write(a1, project="alpha", ttl_seconds=0)
        agent_registry.write(a2, project="alpha", ttl_seconds=0)
        agent_registry.write(b1, project="beta", ttl_seconds=0)

        alpha_agents = agent_registry.read_all(project="alpha")
        beta_agents = agent_registry.read_all(project="beta")

    alpha_names = {a.name for a in alpha_agents}
    beta_names = {a.name for a in beta_agents}

    assert alpha_names == {"river", "kasra"}
    assert beta_names == {"meridian"}


@pytest.mark.skipif(not HAS_FAKEREDIS, reason="fakeredis not installed")
def test_read_handles_missing_fields_gracefully() -> None:
    """Redis hash with only {name, model} deserializes to AgentIdentity with defaults."""
    fake_r = _make_fake_redis()
    # Write a bare-minimum hash directly (old-shape entry)
    fake_r.hset("sos:registry:oldagent", mapping={"name": "oldagent", "model": "gpt-4"})

    with patch.object(agent_registry, "_get_redis", return_value=fake_r):
        result = agent_registry.read_one("oldagent")

    assert result is not None
    assert result.name == "oldagent"
    assert result.model == "gpt-4"
    # Defaults
    assert result.verification_status == VerificationStatus.UNVERIFIED
    assert result.edition == "business"
    assert result.dna is not None
    assert result.dna.physics.C == 0.95  # default coherence


@pytest.mark.skipif(not HAS_FAKEREDIS, reason="fakeredis not installed")
def test_redis_unreachable_returns_empty_list() -> None:
    """Mock connection failure → read_all returns [] without raising."""
    import redis  # type: ignore[import-untyped]

    broken = MagicMock()
    broken.keys.side_effect = redis.exceptions.ConnectionError("refused")

    with patch.object(agent_registry, "_get_redis", return_value=broken):
        result = agent_registry.read_all()

    assert result == []
