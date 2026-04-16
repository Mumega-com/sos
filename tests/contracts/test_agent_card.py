"""Contract tests for the Agent Card schema.

These tests are the freeze point: if they pass, any implementation (Python,
Rust, TypeScript) that emits records passing them is wire-compatible.
"""
from __future__ import annotations

import pytest

from sos.contracts.agent_card import AgentCard, AGENT_CARD_VERSION


def _valid_card_kwargs() -> dict:
    return {
        "name": "sos-medic",
        "tool": "claude-code",
        "type": "tmux",
        "role": "medic",
        "model": "sonnet-4.6",
        "session": "sos-medic",
        "skills": ["connectivity", "auth", "routing"],
        "squads": ["core", "infrastructure"],
        "warm_policy": "cold",
        "cache_ttl_s": 300,
        "registered_at": AgentCard.now_iso(),
        "last_seen": AgentCard.now_iso(),
    }


def test_minimal_valid_card_roundtrips_through_redis_hash():
    card = AgentCard(**_valid_card_kwargs())
    h = card.to_redis_hash()

    # Redis hashes are flat strings — this is the invariant that matters
    assert all(isinstance(v, str) for v in h.values())

    # Arrays became comma-joined strings
    assert h["skills"] == "connectivity,auth,routing"

    # Null optional fields were dropped
    assert "project" not in h
    assert "tenant_subdomain" not in h

    restored = AgentCard.from_redis_hash(h)
    assert restored.name == card.name
    assert restored.skills == card.skills
    assert restored.squads == card.squads
    assert restored.agent_card_version == AGENT_CARD_VERSION


def test_invalid_name_rejected():
    with pytest.raises(ValueError):
        AgentCard(**{**_valid_card_kwargs(), "name": "Bad Name With Spaces"})
    with pytest.raises(ValueError):
        AgentCard(**{**_valid_card_kwargs(), "name": "X"})  # too short


def test_unknown_tool_rejected():
    with pytest.raises(ValueError):
        AgentCard(**{**_valid_card_kwargs(), "tool": "emacs"})


def test_unknown_role_rejected():
    with pytest.raises(ValueError):
        AgentCard(**{**_valid_card_kwargs(), "role": "boss"})


def test_duplicate_skill_rejected():
    with pytest.raises(ValueError):
        AgentCard(**{**_valid_card_kwargs(), "skills": ["seo", "seo"]})


def test_cache_ttl_bounds():
    with pytest.raises(ValueError):
        AgentCard(**{**_valid_card_kwargs(), "cache_ttl_s": -1})
    with pytest.raises(ValueError):
        AgentCard(**{**_valid_card_kwargs(), "cache_ttl_s": 86401})


def test_timestamp_must_be_iso():
    with pytest.raises(ValueError):
        AgentCard(**{**_valid_card_kwargs(), "registered_at": "yesterday"})


def test_tenant_agent_card_shape():
    """The shape I registered for trop today should validate."""
    trop = AgentCard(
        name="trop",
        tool="claude-code",
        type="tmux",
        role="executor",
        model="sonnet-4.6",
        session="trop",
        skills=["content", "growth-loops", "glass-commerce", "seo"],
        squads=["trop", "content"],
        project="trop",
        tenant_subdomain="trop.mumega.com",
        plan="growth",
        warm_policy="cold",
        cache_ttl_s=300,
        registered_at=AgentCard.now_iso(),
        last_seen=AgentCard.now_iso(),
    )
    h = trop.to_redis_hash()
    assert h["project"] == "trop"
    assert h["plan"] == "growth"
    assert h["tenant_subdomain"] == "trop.mumega.com"


def test_coordinator_agent_card_shape():
    """The shape I registered for kasra today should validate."""
    kasra = AgentCard(
        name="kasra",
        tool="claude-code",
        type="tmux",
        role="coordinator",
        model="sonnet-4.6",
        session="kasra",
        skills=["backend", "frontend", "infrastructure", "api", "database"],
        squads=["core", "engineering"],
        warm_policy="warm",
        cache_ttl_s=300,
        registered_at=AgentCard.now_iso(),
        last_seen=AgentCard.now_iso(),
    )
    assert kasra.role == "coordinator"
    assert kasra.warm_policy == "warm"


def test_schema_file_parses():
    """The published JSON Schema must itself be valid JSON."""
    from sos.contracts.agent_card import load_schema

    schema = load_schema()
    assert schema["$id"].endswith("agent_card_v1.json")
    assert schema["type"] == "object"
    assert "name" in schema["required"]
